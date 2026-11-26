import { Test, TestingModule } from '@nestjs/testing';
import { NotFoundException } from '@nestjs/common';
import { SocialAccountService } from './social-account.service';
import { PrismaService } from '../prisma/prisma.service';
import { EncryptionService } from '../common/services/encryption.service';
import { ConfigService } from '@nestjs/config';
import { Platform, AccountStatus } from '@prisma/client';
import axios from 'axios';

jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

const mockSocialAccount = (overrides: Partial<any> = {}) => ({
  id: 'acc-1',
  userId: 'user-demo',
  platform: Platform.FACEBOOK,
  platformAccountId: '109283746512',
  accountName: 'My Facebook Page',
  avatarUrl: null,
  tokenExpiresAt: new Date(Date.now() + 3600000),
  scopes: 'pages_manage_posts',
  status: AccountStatus.CONNECTED,
  metadata: null,
  createdAt: new Date(),
  updatedAt: new Date(),
  ...overrides,
});

describe('SocialAccountService', () => {
  let service: SocialAccountService;
  let prisma: { socialAccount: jest.Mocked<any>; oAuthState: jest.Mocked<any> };
  let encryptionService: jest.Mocked<EncryptionService>;
  let configService: jest.Mocked<ConfigService>;

  beforeEach(async () => {
    prisma = {
      socialAccount: {
        findMany: jest.fn(),
        findFirst: jest.fn(),
        upsert: jest.fn(),
        create: jest.fn(),
        update: jest.fn(),
        delete: jest.fn(),
      },
      oAuthState: {
        create: jest.fn(),
        findUnique: jest.fn(),
        delete: jest.fn(),
        deleteMany: jest.fn(),
      },
    };

    encryptionService = {
      encrypt: jest.fn().mockImplementation((s) => `enc_${s}`),
      decrypt: jest.fn().mockImplementation((s) => s.replace('enc_', '')),
    } as any;

    configService = {
      get: jest.fn().mockImplementation((key: string, def?: any) => {
        const cfg: Record<string, any> = {
          META_APP_ID: '1439429214842948',
          META_APP_SECRET: 'mock_meta_secret',
          META_API_VERSION: 'v21.0',
          META_OAUTH_REDIRECT_URI: 'http://localhost:3000/api/social-accounts/facebook/callback',
          GOOGLE_CLIENT_ID: 'mock_google_client_id.apps.googleusercontent.com',
          GOOGLE_CLIENT_SECRET: 'mock_google_secret',
          GOOGLE_OAUTH_REDIRECT_URI: 'http://localhost:3000/api/social-accounts/youtube/callback',
          TIKTOK_CLIENT_KEY: 'aw4ibbzdhg1b3fwd',
          TIKTOK_CLIENT_SECRET: 'mock_tiktok_secret',
          TIKTOK_OAUTH_REDIRECT_URI: 'http://localhost:3000/api/social-accounts/tiktok/callback',
          THREADS_APP_ID: '1483570960379643',
          THREADS_APP_SECRET: 'mock_threads_secret',
          THREADS_OAUTH_REDIRECT_URI: 'http://localhost:3000/api/social-accounts/threads/callback',
        };
        return cfg[key] ?? def;
      }),
    } as any;

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        SocialAccountService,
        { provide: PrismaService, useValue: prisma },
        { provide: EncryptionService, useValue: encryptionService },
        { provide: ConfigService, useValue: configService },
      ],
    }).compile();

    service = module.get<SocialAccountService>(SocialAccountService);
    jest.clearAllMocks();
  });

  // ─── getAccounts ─────────────────────────────────────────────────────────────
  describe('getAccounts()', () => {
    it('should return list of accounts without encrypted token fields', async () => {
      prisma.socialAccount.findMany.mockResolvedValue([
        mockSocialAccount(),
        mockSocialAccount({ id: 'acc-2', platform: Platform.YOUTUBE }),
      ]);

      const result = await service.getAccounts('user-demo');
      expect(result).toHaveLength(2);
      // Should NOT expose encrypted tokens
      result.forEach((acc: any) => {
        expect(acc.accessTokenEncrypted).toBeUndefined();
        expect(acc.refreshTokenEncrypted).toBeUndefined();
      });
    });
  });

  // ─── getAccountById ───────────────────────────────────────────────────────────
  describe('getAccountById()', () => {
    it('should return account for the correct user', async () => {
      prisma.socialAccount.findFirst.mockResolvedValue(mockSocialAccount());
      const result = await service.getAccountById('user-demo', 'acc-1');
      expect(result.id).toBe('acc-1');
    });

    it('should throw NotFoundException for wrong user or nonexistent account', async () => {
      prisma.socialAccount.findFirst.mockResolvedValue(null);
      await expect(service.getAccountById('user-demo', 'wrong-id'))
        .rejects.toThrow(NotFoundException);
    });
  });

  // ─── generateFacebookAuthUrl ─────────────────────────────────────────────────
  describe('generateFacebookAuthUrl()', () => {
    it('should return URL containing app_id, redirect_uri, and scope', async () => {
      prisma.oAuthState.create.mockResolvedValue({
        id: 'state-1',
        state: 'mock_state_xyz',
        userId: 'user-demo',
        platform: Platform.FACEBOOK,
        expiresAt: new Date(),
        redirectUri: null,
        createdAt: new Date(),
      });

      const result = await service.generateFacebookAuthUrl('user-demo');
      expect(result.url).toBeDefined();
      expect(result.url).toContain('facebook.com');
      expect(result.url).toContain('1439429214842948');  // app_id
      expect(result.url).toContain('redirect_uri');
      expect(result.state).toBeDefined();
    });
  });

  // ─── generateYouTubeAuthUrl ───────────────────────────────────────────────────
  describe('generateYouTubeAuthUrl()', () => {
    it('should return URL containing Google accounts domain and client_id', async () => {
      prisma.oAuthState.create.mockResolvedValue({
        id: 'state-2',
        state: 'mock_google_state',
        userId: 'user-demo',
        platform: Platform.YOUTUBE,
        expiresAt: new Date(),
        redirectUri: null,
        createdAt: new Date(),
      });

      const result = await service.generateYouTubeAuthUrl('user-demo');
      expect(result.url).toContain('accounts.google.com');
      expect(result.url).toContain('mock_google_client_id.apps.googleusercontent.com');
    });
  });

  // ─── handleFacebookCallback ───────────────────────────────────────────────────
  describe('handleFacebookCallback()', () => {
    it('should exchange code for token, encrypt it, and upsert account to DB', async () => {
      // Mock OAuth state lookup
      prisma.oAuthState.findUnique.mockResolvedValue({
        id: 'state-1',
        state: 'valid_state_123',
        userId: 'user-demo',
        platform: Platform.FACEBOOK,
        expiresAt: new Date(Date.now() + 10000),
      });
      prisma.oAuthState.deleteMany.mockResolvedValue({ count: 1 });

      // Mock Meta token exchange
      mockedAxios.get
        .mockResolvedValueOnce({
          data: {
            access_token: 'fb_short_lived_token',
            token_type: 'bearer',
          },
        })
        // Exchange for long-lived token
        .mockResolvedValueOnce({
          data: {
            access_token: 'fb_long_lived_token_xyz',
            expires_in: 5183944,
          },
        })
        // Get user pages
        .mockResolvedValueOnce({
          data: {
            data: [{
              id: '109283746512',
              name: 'My Facebook Page',
              access_token: 'page_access_token_xyz',
              picture: { data: { url: 'https://graph.facebook.com/photo.jpg' } },
            }],
          },
        });

      prisma.socialAccount.upsert.mockResolvedValue(mockSocialAccount());

      const result = await service.handleFacebookCallback('user-demo', 'valid_auth_code', 'valid_state_123');

      // Token must be encrypted before storing
      expect(encryptionService.encrypt).toHaveBeenCalled();
      expect(prisma.socialAccount.upsert).toHaveBeenCalled();
    });

    it('should throw when OAuth state is expired or not found', async () => {
      prisma.oAuthState.findUnique.mockResolvedValue(null);

      await expect(service.handleFacebookCallback('user-demo', 'code', 'invalid_state'))
        .rejects.toThrow();
    });
  });

  // ─── disconnectAccount ────────────────────────────────────────────────────────
  describe('disconnectAccount()', () => {
    it('should delete account on disconnect', async () => {
      prisma.socialAccount.findFirst.mockResolvedValue(mockSocialAccount());
      prisma.socialAccount.delete.mockResolvedValue({});

      const result = await service.disconnectAccount('user-demo', 'acc-1');

      expect(prisma.socialAccount.delete).toHaveBeenCalledWith({
        where: { id: 'acc-1' },
      });
      expect(result.success).toBe(true);
    });

    it('should throw NotFoundException for wrong account', async () => {
      prisma.socialAccount.findFirst.mockResolvedValue(null);
      await expect(service.disconnectAccount('user-demo', 'wrong-id'))
        .rejects.toThrow(NotFoundException);
    });
  });
});
