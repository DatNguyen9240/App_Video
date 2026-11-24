import { Test, TestingModule } from '@nestjs/testing';
import { ConfigService } from '@nestjs/config';
import { FacebookAdapter } from './facebook.adapter';
import { EncryptionService } from '../../common/services/encryption.service';
import { StorageService } from '../../storage/storage.service';
import { Platform, Content, SocialAccount, Media } from '@prisma/client';
import axios from 'axios';

jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

describe('FacebookAdapter (Meta Graph API v21.0)', () => {
  let adapter: FacebookAdapter;
  let encryptionService: EncryptionService;
  let storageService: StorageService;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        FacebookAdapter,
        {
          provide: ConfigService,
          useValue: {
            get: (key: string, def?: string) => (key === 'META_API_VERSION' ? 'v21.0' : def),
          },
        },
        {
          provide: EncryptionService,
          useValue: {
            decrypt: jest.fn().mockReturnValue('mock_decrypted_page_access_token'),
          },
        },
        {
          provide: StorageService,
          useValue: {
            getFileBuffer: jest.fn().mockResolvedValue(Buffer.from('fake_image_or_video_buffer')),
          },
        },
      ],
    }).compile();

    adapter = module.get<FacebookAdapter>(FacebookAdapter);
    encryptionService = module.get<EncryptionService>(EncryptionService);
    storageService = module.get<StorageService>(StorageService);
    jest.clearAllMocks();
  });

  it('should validate that empty content without media is invalid', async () => {
    const emptyContent: Content = {
      id: 'c-1',
      userId: 'u-1',
      title: null,
      caption: '',
      description: null,
      hashtags: null,
      status: 'DRAFT',
      targetPlatforms: ['FACEBOOK'],
      targetAccountIds: null,
      scheduledAt: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const res = await adapter.validateContent(emptyContent, []);
    expect(res.valid).toBe(false);
    expect(res.errors[0]).toContain('Facebook post must contain either text content or at least one media attachment');
  });

  it('should publish text post to /{page_id}/feed successfully', async () => {
    const account: SocialAccount = {
      id: 'acc-1',
      userId: 'u-1',
      platform: Platform.FACEBOOK,
      platformAccountId: '1092837465',
      accountName: 'Tech Today Page',
      avatarUrl: null,
      accessTokenEncrypted: 'enc_token',
      refreshTokenEncrypted: null,
      tokenExpiresAt: null,
      scopes: 'pages_manage_posts',
      status: 'CONNECTED',
      metadata: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const content: Content = {
      id: 'c-1',
      userId: 'u-1',
      title: 'Exciting News',
      caption: 'We just launched our new AI video auto publishing tool!',
      description: null,
      hashtags: '#ai #tech',
      status: 'DRAFT',
      targetPlatforms: ['FACEBOOK'],
      targetAccountIds: null,
      scheduledAt: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    mockedAxios.post.mockResolvedValueOnce({
      data: { id: '1092837465_9876543210' },
    });

    const result = await adapter.publishPost(account, content, []);

    expect(result.success).toBe(true);
    expect(result.externalPostId).toEqual('1092837465_9876543210');
    expect(result.externalPostUrl).toContain('facebook.com');
    expect(mockedAxios.post).toHaveBeenCalledWith(
      expect.stringContaining('/1092837465/feed'),
      null,
      expect.objectContaining({
        params: expect.objectContaining({
          access_token: 'mock_decrypted_page_access_token',
        }),
      }),
    );
  });

  it('should handle Meta Graph API errors (e.g. token expired code 190) cleanly', async () => {
    const account: SocialAccount = {
      id: 'acc-1',
      userId: 'u-1',
      platform: Platform.FACEBOOK,
      platformAccountId: '1092837465',
      accountName: 'Tech Today Page',
      avatarUrl: null,
      accessTokenEncrypted: 'enc_token',
      refreshTokenEncrypted: null,
      tokenExpiresAt: null,
      scopes: 'pages_manage_posts',
      status: 'CONNECTED',
      metadata: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const content: Content = {
      id: 'c-1',
      userId: 'u-1',
      title: 'Hello',
      caption: 'Test post',
      description: null,
      hashtags: null,
      status: 'DRAFT',
      targetPlatforms: ['FACEBOOK'],
      targetAccountIds: null,
      scheduledAt: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    mockedAxios.post.mockRejectedValueOnce({
      response: {
        data: {
          error: {
            message: 'Error validating access token: Session has expired.',
            type: 'OAuthException',
            code: 190,
            error_subcode: 463,
          },
        },
      },
    });

    const result = await adapter.publishPost(account, content, []);

    expect(result.success).toBe(false);
    expect(result.errorCode).toEqual('190');
    expect(result.errorMessage).toContain('Session has expired');
  });
});
