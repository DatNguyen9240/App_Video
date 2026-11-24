import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import * as crypto from 'crypto';

@Injectable()
export class EncryptionService {
  private readonly logger = new Logger(EncryptionService.name);
  private readonly algorithm = 'aes-256-gcm';
  private readonly key: Buffer;

  constructor(private readonly configService: ConfigService) {
    const rawKey = this.configService.get<string>('ENCRYPTION_KEY') || '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
    if (rawKey.length === 64) {
      // 64-char hex string = 32 bytes
      this.key = Buffer.from(rawKey, 'hex');
    } else {
      // Fallback hash to 32 bytes
      this.key = crypto.createHash('sha256').update(rawKey).digest();
    }
  }

  /**
   * Encrypts plaintext using AES-256-GCM with a random 12-byte IV and 16-byte authentication tag.
   * Format: ivHex:authTagHex:ciphertextHex
   */
  encrypt(plaintext: string): string {
    if (!plaintext) {
      return '';
    }
    const iv = crypto.randomBytes(12); // GCM recommended IV length is 12 bytes
    const cipher = crypto.createCipheriv(this.algorithm, this.key, iv);
    
    let encrypted = cipher.update(plaintext, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    const authTag = cipher.getAuthTag().toString('hex');

    return `${iv.toString('hex')}:${authTag}:${encrypted}`;
  }

  /**
   * Decrypts ciphertext formatted as ivHex:authTagHex:ciphertextHex using AES-256-GCM.
   */
  decrypt(ciphertextPayload: string): string {
    if (!ciphertextPayload) {
      return '';
    }
    const parts = ciphertextPayload.split(':');
    if (parts.length !== 3) {
      throw new Error('Invalid ciphertext payload format. Expected iv:authTag:ciphertext');
    }

    const [ivHex, authTagHex, encryptedHex] = parts;
    const iv = Buffer.from(ivHex, 'hex');
    const authTag = Buffer.from(authTagHex, 'hex');
    
    const decipher = crypto.createDecipheriv(this.algorithm, this.key, iv);
    decipher.setAuthTag(authTag);

    let decrypted = decipher.update(encryptedHex, 'hex', 'utf8');
    decrypted += decipher.final('utf8');

    return decrypted;
  }
}
