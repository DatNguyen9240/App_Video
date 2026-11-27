import { Test, TestingModule } from '@nestjs/testing';
import { ConfigService } from '@nestjs/config';
import { BadRequestException } from '@nestjs/common';
import { Platform } from '@prisma/client';
import { SettingsService } from './settings.service';
import { PrismaService } from '../prisma/prisma.service';
import { EncryptionService } from '../common/services/encryption.service';

describe('SettingsService (DB PlatformAppConfig)', () => {
  let service: SettingsService;
  let prisma: {
    platformAppConfig: {
      findMany: jest.Mock;
      findUnique: jest.Mock;
      upsert: jest.Mock;
      deleteMany: jest.Mock;
    };
  };
  let encryption: { encrypt: jest.Mock; decrypt: jest.Mock };

  beforeEach(async () => {
    prisma = {
      platformAppConfig: {
        findMany: jest.fn().mockResolvedValue([]),
        findUnique: jest.fn().mockResolvedValue(null),
        upsert: jest.fn(),
        deleteMany: jest.fn().mockResolvedValue({ count: 1 }),
      },
    };
    encryption = {
      encrypt: jest.fn((v: string) => `enc:${v}`),
      decrypt: jest.fn((v: string) => v.replace(/^enc:/, '')),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        SettingsService,
        { provide: PrismaService, useValue: prisma },
        { provide: EncryptionService, useValue: encryption },
        {
          provide: ConfigService,
          useValue: {
            get: (key: string, def?: string) => {
              const env: Record<string, string> = {
                META_APP_ID: 'env_meta_id',
                META_APP_SECRET: 'env_meta_secret',
                META_OAUTH_REDIRECT_URI: 'http://localhost:3000/fb/cb',
              };
              return env[key] ?? def;
            },
          },
        },
      ],
    }).compile();

    service = module.get<SettingsService>(SettingsService);
  });

  it('should return DB configs merged with .env fallback', async () => {
    prisma.platformAppConfig.findMany.mockResolvedValue([
      {
        platform: Platform.TIKTOK,
        appId: 'db_tt_key',
        appSecretEncrypted: 'enc:db_tt_sec',
        redirectUri: 'http://localhost:3000/tt/cb',
        isActive: true,
        updatedAt: new Date(),
      },
    ]);

    const res = await service.getPlatformConfigs('user-1');
    const fb = res.find((r) => r.platform === Platform.FACEBOOK);
    const tt = res.find((r) => r.platform === Platform.TIKTOK);

    expect(fb?.source).toBe('ENV');
    expect(fb?.appId).toBe('env_meta_id');
    expect(tt?.source).toBe('DATABASE');
    expect(tt?.appId).toBe('db_tt_key');
  });

  it('should encrypt appSecret when upserting to DB', async () => {
    prisma.platformAppConfig.upsert.mockResolvedValue({
      platform: Platform.FACEBOOK,
      appId: 'new_fb_id',
      redirectUri: null,
      isActive: true,
      updatedAt: new Date(),
    });

    const saved = await service.upsertPlatformConfig('user-1', Platform.FACEBOOK, {
      appId: 'new_fb_id',
      appSecret: 'plain_secret',
    });

    expect(encryption.encrypt).toHaveBeenCalledWith('plain_secret');
    expect(saved.source).toBe('DATABASE');
  });

  it('should throw BadRequestException if creating config without appSecret', async () => {
    await expect(
      service.upsertPlatformConfig('user-1', Platform.FACEBOOK, { appId: 'only_id' }),
    ).rejects.toThrow(BadRequestException);
  });

  it('should resolve DB credentials first and fall back to .env', async () => {
    prisma.platformAppConfig.findUnique.mockResolvedValueOnce({
      platform: Platform.FACEBOOK,
      appId: 'db_fb_id',
      appSecretEncrypted: 'enc:db_fb_secret',
      redirectUri: null,
      isActive: true,
    });

    const fromDb = await service.resolveCredentials('user-1', Platform.FACEBOOK);
    expect(fromDb?.source).toBe('DATABASE');
    expect(fromDb?.appSecret).toBe('db_fb_secret');

    prisma.platformAppConfig.findUnique.mockResolvedValueOnce(null);
    const fromEnv = await service.resolveCredentials('user-1', Platform.FACEBOOK);
    expect(fromEnv?.source).toBe('ENV');
    expect(fromEnv?.appId).toBe('env_meta_id');
  });
});
