import { Test, TestingModule } from '@nestjs/testing';
import { ConfigService } from '@nestjs/config';
import { BadRequestException, UnauthorizedException } from '@nestjs/common';
import { TikTokAdapter } from './tiktok.adapter';
import { EncryptionService } from '../../common/services/encryption.service';
import { StorageService } from '../../storage/storage.service';
import { Platform, Content, SocialAccount, Media } from '@prisma/client';
import axios from 'axios';

jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

const mockAccount: SocialAccount = {
  id: 'acc-tiktok-1',
  userId: 'user-1',
  platform: Platform.TIKTOK,
  platformAccountId: 'tiktok_user_123',
  accountName: '@my_tiktok_channel',
  avatarUrl: null,
  accessTokenEncrypted: 'encrypted_tiktok_token',
  refreshTokenEncrypted: null,
  tokenExpiresAt: null,
  scopes: 'video.upload',
  status: 'CONNECTED',
  metadata: null,
  createdAt: new Date(),
  updatedAt: new Date(),
};

const mockContent: Content = {
  id: 'content-1',
  userId: 'user-1',
  title: 'TikTok Viral Video 2026 🔥',
  caption: 'Chuyện văn phòng drama nhất năm – xem ngay!',
  description: null,
  hashtags: '#viral #drama #vanonline #trending',
  status: 'DRAFT',
  targetPlatforms: ['TIKTOK'],
  targetAccountIds: null,
  scheduledAt: null,
  createdAt: new Date(),
  updatedAt: new Date(),
};

const mockVideoMedia: Media = {
  id: 'media-vid-1',
  contentId: 'content-1',
  storageKey: 'media/user-1/video_drama.mp4',
  originalName: 'video_drama.mp4',
  mimeType: 'video/mp4',
  fileSize: 25 * 1024 * 1024, // 25MB
  duration: 58.5,
  width: 1080,
  height: 1920,
  thumbnailKey: null,
  createdAt: new Date(),
};

describe('TikTokAdapter (Content Posting API)', () => {
  let adapter: TikTokAdapter;
  let encryptionService: EncryptionService;
  let storageService: StorageService;

  let setTimeoutSpy: jest.SpyInstance;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        TikTokAdapter,
        {
          provide: ConfigService,
          useValue: {
            get: (key: string, def?: any) => {
              const config: Record<string, any> = {
                TIKTOK_CLIENT_KEY: 'aw4ibbzdhg1b3fwd',
                TIKTOK_CLIENT_SECRET: 'mock_secret',
                TIKTOK_OAUTH_REDIRECT_URI: 'http://localhost:3000/api/social-accounts/tiktok/callback',
              };
              return config[key] ?? def;
            },
          },
        },
        {
          provide: EncryptionService,
          useValue: {
            decrypt: jest.fn().mockReturnValue('mock_tiktok_access_token_bearer_xyz'),
          },
        },
        {
          provide: StorageService,
          useValue: {
            getPublicUrl: jest.fn().mockReturnValue('https://cdn.example.com/social-media-assets/media/user-1/video_drama.mp4'),
            getFileBuffer: jest.fn().mockResolvedValue(Buffer.alloc(1024, 0)),
          },
        },
      ],
    }).compile();

    adapter = module.get<TikTokAdapter>(TikTokAdapter);
    encryptionService = module.get<EncryptionService>(EncryptionService);
    storageService = module.get<StorageService>(StorageService);
    mockedAxios.post.mockReset();
    mockedAxios.put.mockReset();
    setTimeoutSpy = jest.spyOn(global, 'setTimeout').mockImplementation((cb: any) => {
      if (typeof cb === 'function') cb();
      return 0 as any;
    });
  });

  afterEach(() => {
    setTimeoutSpy?.mockRestore();
  });

  // ─── Platform Identity ──────────────────────────────────────────────────────
  it('should have platform set to TIKTOK', () => {
    expect(adapter.platform).toBe(Platform.TIKTOK);
  });

  // ─── Content Validation ─────────────────────────────────────────────────────
  it('should mark content without media as invalid for TikTok', async () => {
    const noMediaContent = { ...mockContent, caption: '', title: null };
    const result = await adapter.validateContent(noMediaContent as Content, []);
    expect(result.valid).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
  });

  it('should validate video-only content as valid', async () => {
    const result = await adapter.validateContent(mockContent, [mockVideoMedia]);
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  it('should reject non-video media for TikTok', async () => {
    const imageMedia: Media = { ...mockVideoMedia, mimeType: 'image/jpeg' };
    const result = await adapter.validateContent(mockContent, [imageMedia]);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.toLowerCase().includes('video'))).toBe(true);
  });

  // ─── Publish via PULL_FROM_URL ───────────────────────────────────────────────
  it('should publish via PULL_FROM_URL for public MinIO URLs and return success', async () => {
    mockedAxios.post
      .mockResolvedValueOnce({
        // POST /v2/post/publish/video/init/ → returns publish_id
        data: { data: { publish_id: 'tiktok_pub_111' } },
      })
      .mockResolvedValueOnce({
        // POST /v2/post/publish/status/fetch/ → PROCESSING
        data: { data: { status: 'PROCESSING' } },
      })
      .mockResolvedValueOnce({
        // Second poll → SUCCESS
        data: { data: { status: 'SUCCESS_POST_TO_FEED' } },
      });

    const result = await adapter.publishPost(mockAccount, mockContent, [mockVideoMedia]);

    expect(result.success).toBe(true);
    expect(result.externalPostId).toBe('tiktok_pub_111');
    expect(result.externalPostUrl).toContain('tiktok.com');
    expect(result.metadata?.publishId).toBe('tiktok_pub_111');
  });

  // ─── Publish via FILE_UPLOAD ─────────────────────────────────────────────────
  it('should use FILE_UPLOAD path when local buffer is available', async () => {
    // Mock a local URL (not public internet)
    jest.spyOn(storageService, 'getPublicUrl').mockReturnValue('/uploads/media/user-1/video.mp4');

    mockedAxios.post
      .mockResolvedValueOnce({
        // init → FILE_UPLOAD
        data: { data: { publish_id: 'tiktok_pub_222', upload_url: 'https://upload.tiktok.com/xyz' } },
      });
    mockedAxios.put.mockResolvedValueOnce({ status: 206 });
    mockedAxios.post
      .mockResolvedValueOnce({
        // poll → SUCCESS
        data: { data: { status: 'SUCCESS' } },
      });

    const result = await adapter.publishPost(mockAccount, mockContent, [mockVideoMedia]);
    expect(result.success).toBe(true);
  });

  // ─── Error Handling ──────────────────────────────────────────────────────────
  it('should throw UnauthorizedException when TikTok returns access_token_invalid', async () => {
    mockedAxios.post.mockRejectedValueOnce({
      response: {
        status: 401,
        data: { error: { code: 'access_token_invalid', message: 'The access token is invalid.' } },
      },
      message: 'Request failed with status code 401',
    });

    await expect(adapter.publishPost(mockAccount, mockContent, [mockVideoMedia]))
      .rejects.toThrow(UnauthorizedException);
  });

  it('should throw BadRequestException when TikTok returns generic API error', async () => {
    mockedAxios.post.mockRejectedValueOnce({
      response: {
        status: 400,
        data: { error: { code: 'invalid_params', message: 'Video title too long.' } },
      },
      message: 'Request failed with status code 400',
    });

    await expect(adapter.publishPost(mockAccount, mockContent, [mockVideoMedia]))
      .rejects.toThrow(BadRequestException);
  });

  it('should throw when polling status returns FAILED', async () => {
    mockedAxios.post
      .mockResolvedValueOnce({
        data: { data: { publish_id: 'tiktok_pub_333' } },
      })
      .mockResolvedValueOnce({
        data: { data: { status: 'FAILED', fail_reason: 'Video codec not supported.' } },
      });

    await expect(adapter.publishPost(mockAccount, mockContent, [mockVideoMedia]))
      .rejects.toThrow(/FAILED|failed|Video codec/i);
  });

  it('should return PROCESSING status after max polling attempts without throwing', async () => {
    mockedAxios.post.mockResolvedValue({
      data: { data: { publish_id: 'tiktok_pub_444', status: 'PROCESSING' } },
    });

    const result = await adapter.publishPost(mockAccount, mockContent, [mockVideoMedia]);
    expect(result.success).toBe(true);
    expect(result.metadata?.status).toBe('PROCESSING');
  }, 10000);
});
