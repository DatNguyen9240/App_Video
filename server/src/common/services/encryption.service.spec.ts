import { Test, TestingModule } from '@nestjs/testing';
import { ConfigService } from '@nestjs/config';
import { EncryptionService } from './encryption.service';

describe('EncryptionService (AES-256-GCM)', () => {
  let service: EncryptionService;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        EncryptionService,
        {
          provide: ConfigService,
          useValue: {
            get: (key: string, defaultVal?: string) => {
              if (key === 'ENCRYPTION_KEY') {
                return '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
              }
              return defaultVal;
            },
          },
        },
      ],
    }).compile();

    service = module.get<EncryptionService>(EncryptionService);
  });

  it('should be defined', () => {
    expect(service).toBeDefined();
  });

  it('should encrypt and decrypt plaintext accurately', () => {
    const secret = 'EAABwzLixnjYBO123456789LongLivedFacebookPageAccessTokenExample';
    const encrypted = service.encrypt(secret);

    expect(encrypted).toBeDefined();
    expect(encrypted).not.toEqual(secret);
    expect(encrypted.split(':')).toHaveLength(3); // iv:authTag:ciphertext

    const decrypted = service.decrypt(encrypted);
    expect(decrypted).toEqual(secret);
  });

  it('should generate different ciphertexts for the same plaintext due to random 12-byte IV', () => {
    const secret = 'ya29.a0AcM612345GoogleYouTubeOAuthToken';
    const enc1 = service.encrypt(secret);
    const enc2 = service.encrypt(secret);

    expect(enc1).not.toEqual(enc2);
    expect(service.decrypt(enc1)).toEqual(secret);
    expect(service.decrypt(enc2)).toEqual(secret);
  });

  it('should throw an error if the authentication tag or ciphertext is tampered with', () => {
    const secret = 'SensitiveData';
    const encrypted = service.encrypt(secret);
    const parts = encrypted.split(':');
    
    // Tamper with the ciphertext
    const tampered = `${parts[0]}:${parts[1]}:${parts[2].slice(0, -2)}ff`;

    expect(() => service.decrypt(tampered)).toThrow();
  });

  it('should handle empty or null strings gracefully', () => {
    expect(service.encrypt('')).toEqual('');
    expect(service.decrypt('')).toEqual('');
  });

  it('should throw an error for invalid ciphertext format (not 3 parts)', () => {
    expect(() => service.decrypt('invalid-payload-without-colons')).toThrow();
    expect(() => service.decrypt('part1:part2')).toThrow();
  });

  it('should support short non-64-hex ENCRYPTION_KEY via SHA-256 fallback', async () => {
    const shortKeyModule: TestingModule = await Test.createTestingModule({
      providers: [
        EncryptionService,
        {
          provide: ConfigService,
          useValue: {
            get: (key: string, defaultVal?: string) =>
              key === 'ENCRYPTION_KEY' ? 'short-dev-passphrase' : defaultVal,
          },
        },
      ],
    }).compile();

    const shortKeyService = shortKeyModule.get<EncryptionService>(EncryptionService);
    const secret = 'fallback-key-secret-token';
    const encrypted = shortKeyService.encrypt(secret);
    expect(shortKeyService.decrypt(encrypted)).toEqual(secret);
  });
});
