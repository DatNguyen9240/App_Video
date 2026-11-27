import { Injectable, BadRequestException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Platform } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { EncryptionService } from '../common/services/encryption.service';

export interface ResolvedPlatformCredentials {
  appId: string;
  appSecret: string;
  redirectUri: string;
  source: 'DATABASE' | 'ENV';
}

export interface UpsertPlatformConfigDto {
  appId: string;
  appSecret?: string;
  redirectUri?: string;
  isActive?: boolean;
}

const SUPPORTED_PLATFORMS: Platform[] = [
  Platform.FACEBOOK,
  Platform.YOUTUBE,
  Platform.THREADS,
  Platform.TIKTOK,
];

@Injectable()
export class SettingsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly configService: ConfigService,
    private readonly encryptionService: EncryptionService,
  ) {}

  private getEnvDefaults(platform: Platform): {
    appId?: string;
    appSecret?: string;
    redirectUri?: string;
  } {
    switch (platform) {
      case Platform.FACEBOOK:
        return {
          appId: this.configService.get<string>('META_APP_ID'),
          appSecret: this.configService.get<string>('META_APP_SECRET'),
          redirectUri: this.configService.get<string>('META_OAUTH_REDIRECT_URI'),
        };
      case Platform.YOUTUBE:
        return {
          appId: this.configService.get<string>('GOOGLE_CLIENT_ID'),
          appSecret: this.configService.get<string>('GOOGLE_CLIENT_SECRET'),
          redirectUri: this.configService.get<string>('GOOGLE_OAUTH_REDIRECT_URI'),
        };
      case Platform.THREADS:
        return {
          appId:
            this.configService.get<string>('THREADS_APP_ID') ||
            this.configService.get<string>('META_APP_ID'),
          appSecret:
            this.configService.get<string>('THREADS_APP_SECRET') ||
            this.configService.get<string>('META_APP_SECRET'),
          redirectUri: this.configService.get<string>(
            'THREADS_OAUTH_REDIRECT_URI',
            'http://localhost:3000/api/social-accounts/threads/callback',
          ),
        };
      case Platform.TIKTOK:
        return {
          appId: this.configService.get<string>('TIKTOK_CLIENT_KEY'),
          appSecret: this.configService.get<string>('TIKTOK_CLIENT_SECRET'),
          redirectUri: this.configService.get<string>(
            'TIKTOK_OAUTH_REDIRECT_URI',
            'http://localhost:3000/api/social-accounts/tiktok/callback',
          ),
        };
      default:
        return {};
    }
  }

  async getPlatformConfigs(userId: string) {
    const dbConfigs = await this.prisma.platformAppConfig.findMany({
      where: { userId },
    });
    const byPlatform = new Map(dbConfigs.map((c) => [c.platform, c]));

    return SUPPORTED_PLATFORMS.map((platform) => {
      const dbCfg = byPlatform.get(platform);
      const envCfg = this.getEnvDefaults(platform);

      if (dbCfg) {
        return {
          platform,
          appId: dbCfg.appId,
          redirectUri: dbCfg.redirectUri || envCfg.redirectUri || '',
          hasSecret: Boolean(dbCfg.appSecretEncrypted),
          isActive: dbCfg.isActive,
          source: 'DATABASE' as const,
          updatedAt: dbCfg.updatedAt,
        };
      }

      const hasEnv = Boolean(envCfg.appId && envCfg.appSecret);
      return {
        platform,
        appId: envCfg.appId || '',
        redirectUri: envCfg.redirectUri || '',
        hasSecret: Boolean(envCfg.appSecret),
        isActive: hasEnv,
        source: hasEnv ? ('ENV' as const) : ('UNCONFIGURED' as const),
        updatedAt: null,
      };
    });
  }

  async upsertPlatformConfig(
    userId: string,
    platform: Platform,
    dto: UpsertPlatformConfigDto,
  ) {
    if (!SUPPORTED_PLATFORMS.includes(platform)) {
      throw new BadRequestException(`Unsupported platform: ${platform}`);
    }
    if (!dto.appId?.trim()) {
      throw new BadRequestException('appId / Client Key is required.');
    }

    const existing = await this.prisma.platformAppConfig.findUnique({
      where: { userId_platform: { userId, platform } },
    });

    let appSecretEncrypted = existing?.appSecretEncrypted || '';
    if (dto.appSecret && dto.appSecret.trim()) {
      appSecretEncrypted = this.encryptionService.encrypt(dto.appSecret.trim());
    }

    if (!appSecretEncrypted) {
      throw new BadRequestException('appSecret / Client Secret is required when creating config.');
    }

    const saved = await this.prisma.platformAppConfig.upsert({
      where: { userId_platform: { userId, platform } },
      update: {
        appId: dto.appId.trim(),
        appSecretEncrypted,
        redirectUri: dto.redirectUri?.trim() || null,
        isActive: dto.isActive ?? true,
      },
      create: {
        userId,
        platform,
        appId: dto.appId.trim(),
        appSecretEncrypted,
        redirectUri: dto.redirectUri?.trim() || null,
        isActive: dto.isActive ?? true,
      },
    });

    return {
      platform: saved.platform,
      appId: saved.appId,
      redirectUri: saved.redirectUri || '',
      hasSecret: true,
      isActive: saved.isActive,
      source: 'DATABASE' as const,
      updatedAt: saved.updatedAt,
    };
  }

  async deletePlatformConfig(userId: string, platform: Platform) {
    await this.prisma.platformAppConfig.deleteMany({
      where: { userId, platform },
    });
    return { success: true, message: `${platform} DB config removed (reverted to .env if set).` };
  }

  /**
   * Resolves OAuth App credentials for a user & platform:
   * 1. Checks DB (`platform_app_configs`) for active user config.
   * 2. Falls back to `.env` via ConfigService.
   */
  async resolveCredentials(
    userId: string,
    platform: Platform,
  ): Promise<ResolvedPlatformCredentials | null> {
    const dbCfg = await this.prisma.platformAppConfig.findUnique({
      where: { userId_platform: { userId, platform } },
    });

    const envCfg = this.getEnvDefaults(platform);

    if (dbCfg && dbCfg.isActive && dbCfg.appId && dbCfg.appSecretEncrypted) {
      return {
        appId: dbCfg.appId,
        appSecret: this.encryptionService.decrypt(dbCfg.appSecretEncrypted),
        redirectUri: dbCfg.redirectUri || envCfg.redirectUri || '',
        source: 'DATABASE',
      };
    }

    if (envCfg.appId && envCfg.appSecret) {
      return {
        appId: envCfg.appId,
        appSecret: envCfg.appSecret,
        redirectUri: envCfg.redirectUri || '',
        source: 'ENV',
      };
    }

    return null;
  }
}
