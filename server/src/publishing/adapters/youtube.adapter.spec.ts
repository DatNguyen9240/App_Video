import { Test, TestingModule } from '@nestjs/testing';
import { ConfigService } from '@nestjs/config';
import { YouTubeAdapter } from './youtube.adapter';
import { EncryptionService } from '../../common/services/encryption.service';
import { StorageService } from '../../storage/storage.service';
import { Platform, Content, SocialAccount, Media } from '@prisma/client';
import { google } from 'googleapis';

jest.mock('googleapis');

describe('YouTubeAdapter (YouTube Data API v3)', () => {
  let adapter: YouTubeAdapter;
  let encryptionService: EncryptionService;
  let storageService: StorageService;
  let mockVideosInsert: jest.Mock;

  beforeEach(async () => {
    mockVideosInsert = jest.fn();

    (google.auth.OAuth2 as any) = jest.fn().mockImplementation(() => ({
      setCredentials: jest.fn(),
    }));

    (google.youtube as any) = jest.fn().mockReturnValue({
      videos: {
        insert: mockVideosInsert,
      },
    });

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        YouTubeAdapter,
        {
          provide: ConfigService,
          useValue: {
            get: (key: string, def?: string) => {
              if (key === 'GOOGLE_CLIENT_ID') return 'mock_client_id';
              if (key === 'GOOGLE_CLIENT_SECRET') return 'mock_client_secret';
              return def;
            },
          },
        },
        {
          provide: EncryptionService,
          useValue: {
            decrypt: jest.fn().mockReturnValue('mock_token'),
          },
        },
        {
          provide: StorageService,
          useValue: {
            getFileBuffer: jest.fn().mockResolvedValue(Buffer.from('fake_video_bytes')),
          },
        },
      ],
    }).compile();

    adapter = module.get<YouTubeAdapter>(YouTubeAdapter);
    encryptionService = module.get<EncryptionService>(EncryptionService);
    storageService = module.get<StorageService>(StorageService);
    jest.clearAllMocks();
  });

  it('should reject content validation if no video file is attached', async () => {
    const textContent: Content = {
      id: 'c-1',
      userId: 'u-1',
      title: 'Valid Title',
      caption: 'Some caption',
      description: null,
      hashtags: null,
      status: 'DRAFT',
      targetPlatforms: ['YOUTUBE'],
      targetAccountIds: null,
      scheduledAt: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const res = await adapter.validateContent(textContent, []);
    expect(res.valid).toBe(false);
    expect(res.errors[0]).toContain('YouTube publishing requires at least one video file');
  });

  it('should reject titles longer than 100 characters', async () => {
    const longTitleContent: Content = {
      id: 'c-1',
      userId: 'u-1',
      title: 'A'.repeat(105),
      caption: 'Caption',
      description: null,
      hashtags: null,
      status: 'DRAFT',
      targetPlatforms: ['YOUTUBE'],
      targetAccountIds: null,
      scheduledAt: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const videoMedia: Media = {
      id: 'm-1',
      contentId: 'c-1',
      storageKey: 'media/video.mp4',
      originalName: 'video.mp4',
      mimeType: 'video/mp4',
      fileSize: 1048576,
      duration: 30,
      width: 1080,
      height: 1920,
      thumbnailKey: null,
      createdAt: new Date(),
    };

    const res = await adapter.validateContent(longTitleContent, [videoMedia]);
    expect(res.valid).toBe(false);
    expect(res.errors[0]).toContain('exceeds maximum allowed length of 100 characters');
  });

  it('should upload video and automatically tag Shorts for vertical video under 60 seconds', async () => {
    const account: SocialAccount = {
      id: 'acc-yt-1',
      userId: 'u-1',
      platform: Platform.YOUTUBE,
      platformAccountId: 'UC_x123456789',
      accountName: 'Tech Shorts Channel',
      avatarUrl: null,
      accessTokenEncrypted: 'enc_access',
      refreshTokenEncrypted: 'enc_refresh',
      tokenExpiresAt: null,
      scopes: 'https://www.googleapis.com/auth/youtube.upload',
      status: 'CONNECTED',
      metadata: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const content: Content = {
      id: 'c-shorts',
      userId: 'u-1',
      title: 'Amazing AI Trick',
      caption: 'Watch this quick tip',
      description: 'More details here',
      hashtags: '#aitrick #tech',
      status: 'DRAFT',
      targetPlatforms: ['YOUTUBE'],
      targetAccountIds: null,
      scheduledAt: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const verticalVideo: Media = {
      id: 'm-shorts',
      contentId: 'c-shorts',
      storageKey: 'media/shorts.mp4',
      originalName: 'shorts.mp4',
      mimeType: 'video/mp4',
      fileSize: 5242880,
      duration: 45, // Under 60s
      width: 1080,
      height: 1920, // Vertical 9:16
      thumbnailKey: null,
      createdAt: new Date(),
    };

    mockVideosInsert.mockResolvedValueOnce({
      data: {
        id: 'yt_video_id_999',
        etag: 'mock_etag',
      },
    });

    const result = await adapter.publishPost(account, content, [verticalVideo]);

    expect(result.success).toBe(true);
    expect(result.externalPostId).toEqual('yt_video_id_999');
    expect(result.externalPostUrl).toContain('youtube.com/shorts/yt_video_id_999');
    expect(result.metadata.isShorts).toBe(true);

    // Verify #Shorts was injected into the title & tags
    expect(mockVideosInsert).toHaveBeenCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          snippet: expect.objectContaining({
            title: expect.stringContaining('#Shorts'),
            tags: expect.arrayContaining(['Shorts']),
          }),
        }),
      }),
    );
  });

  it('should enforce private status when scheduled publishAt is set (YouTube API rule)', async () => {
    const account: SocialAccount = {
      id: 'acc-yt-1',
      userId: 'u-1',
      platform: Platform.YOUTUBE,
      platformAccountId: 'UC_x123456789',
      accountName: 'Tech Channel',
      avatarUrl: null,
      accessTokenEncrypted: 'enc_access',
      refreshTokenEncrypted: 'enc_refresh',
      tokenExpiresAt: null,
      scopes: 'https://www.googleapis.com/auth/youtube.upload',
      status: 'CONNECTED',
      metadata: null,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const futureDate = new Date(Date.now() + 86400000); // Tomorrow
    const content: Content = {
      id: 'c-sched',
      userId: 'u-1',
      title: 'Scheduled Episode',
      caption: 'Coming tomorrow',
      description: 'Scheduled post description',
      hashtags: '#premiere',
      status: 'SCHEDULED',
      targetPlatforms: ['YOUTUBE'],
      targetAccountIds: null,
      scheduledAt: futureDate,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    const video: Media = {
      id: 'm-vid',
      contentId: 'c-sched',
      storageKey: 'media/vid.mp4',
      originalName: 'vid.mp4',
      mimeType: 'video/mp4',
      fileSize: 10485760,
      duration: 120,
      width: 1920,
      height: 1080,
      thumbnailKey: null,
      createdAt: new Date(),
    };

    mockVideosInsert.mockResolvedValueOnce({
      data: { id: 'scheduled_yt_id_101' },
    });

    const result = await adapter.publishPost(account, content, [video]);

    expect(result.success).toBe(true);
    expect(mockVideosInsert).toHaveBeenCalledWith(
      expect.objectContaining({
        requestBody: expect.objectContaining({
          status: expect.objectContaining({
            privacyStatus: 'private', // Mandatory when publishAt is provided
            publishAt: futureDate.toISOString(),
          }),
        }),
      }),
    );
  });
});
