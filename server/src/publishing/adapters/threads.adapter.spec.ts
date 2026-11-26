import { Test, TestingModule } from '@nestjs/testing';
import { ConfigService } from '@nestjs/config';
import { BadRequestException, UnauthorizedException } from '@nestjs/common';
import { ThreadsAdapter } from './threads.adapter';
import { EncryptionService } from '../../common/services/encryption.service';
import { StorageService } from '../../storage/storage.service';
import { Platform, Content, SocialAccount, Media } from '@prisma/client';
import axios from 'axios';

jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

const mockAccount: SocialAccount = {
  id: 'acc-threads-1',
  userId: 'user-1',
  platform: Platform.THREADS,
  platformAccountId: '1239847561234',
  accountName: 'my.threads.account',
  avatarUrl: null,
  accessTokenEncrypted: 'encrypted_threads_token',
  refreshTokenEncrypted: null,
  tokenExpiresAt: null,
  scopes: 'threads_basic,threads_content_publish',
  status: 'CONNECTED',
  metadata: null,
  createdAt: new Date(),
  updatedAt: new Date(),
};

const mockTextContent: Content = {
  id: 'content-threads-1',
  userId: 'user-1',
  title: null,
  caption: 'Hôm nay văn phòng drama quá trời ơi!',
  description: null,
  hashtags: '#threads #drama #viral',
  status: 'DRAFT',
  targetPlatforms: ['THREADS'],
  targetAccountIds: null,
  scheduledAt: null,
  createdAt: new Date(),
  updatedAt: new Date(),
};

const mockImageMedia: Media = {
  id: 'media-img-1',
  contentId: 'content-threads-1',
  storageKey: 'media/user-1/drama_proof.jpg',
  originalName: 'drama_proof.jpg',
  mimeType: 'image/jpeg',
  fileSize: 2 * 1024 * 1024,
  duration: null,
  width: 1080,
  height: 1080,
  thumbnailKey: null,
  createdAt: new Date(),
};

const mockVideoMedia: Media = {
  id: 'media-vid-1',
  contentId: 'content-threads-1',
  storageKey: 'media/user-1/drama_clip.mp4',
  originalName: 'drama_clip.mp4',
  mimeType: 'video/mp4',
  fileSize: 100 * 1024 * 1024,
  duration: 45.0,
  width: 1080,
  height: 1920,
  thumbnailKey: null,
  createdAt: new Date(),
};

describe('ThreadsAdapter (Meta Graph Threads API)', () => {
  let adapter: ThreadsAdapter;
  let storageService: StorageService;

  let setTimeoutSpy: jest.SpyInstance;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        ThreadsAdapter,
        {
          provide: ConfigService,
          useValue: {
            get: (key: string, def?: any) => def,
          },
        },
        {
          provide: EncryptionService,
          useValue: {
            decrypt: jest.fn().mockReturnValue('mock_threads_user_access_token'),
          },
        },
        {
          provide: StorageService,
          useValue: {
            getPublicUrl: jest.fn().mockReturnValue('http://localhost:9000/social-media-assets/media/user-1/drama_proof.jpg'),
            getFileBuffer: jest.fn().mockResolvedValue(Buffer.alloc(1024, 0)),
          },
        },
      ],
    }).compile();

    adapter = module.get<ThreadsAdapter>(ThreadsAdapter);
    storageService = module.get<StorageService>(StorageService);
    mockedAxios.post.mockReset();
    mockedAxios.get.mockReset();
    setTimeoutSpy = jest.spyOn(global, 'setTimeout').mockImplementation((cb: any) => {
      if (typeof cb === 'function') cb();
      return 0 as any;
    });
  });

  afterEach(() => {
    setTimeoutSpy?.mockRestore();
  });

  // ─── Platform Identity ──────────────────────────────────────────────────────
  it('should have platform set to THREADS', () => {
    expect(adapter.platform).toBe(Platform.THREADS);
  });

  // ─── Content Validation ─────────────────────────────────────────────────────
  it('should mark empty text without media as invalid', async () => {
    const emptyContent = { ...mockTextContent, caption: '', hashtags: null, title: null };
    const result = await adapter.validateContent(emptyContent as Content, []);
    expect(result.valid).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
  });

  it('should validate text post (< 500 chars) as valid', async () => {
    const result = await adapter.validateContent(mockTextContent, []);
    expect(result.valid).toBe(true);
  });

  it('should reject text exceeding 500 characters', async () => {
    const longCaptionContent = {
      ...mockTextContent,
      caption: 'A'.repeat(501),
    };
    const result = await adapter.validateContent(longCaptionContent as Content, []);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.includes('500'))).toBe(true);
  });

  it('should reject unsupported MIME type (PDF)', async () => {
    const pdfMedia: Media = { ...mockImageMedia, mimeType: 'application/pdf' };
    const result = await adapter.validateContent(mockTextContent, [pdfMedia]);
    expect(result.valid).toBe(false);
  });

  it('should reject video exceeding 500MB', async () => {
    const bigVideo: Media = { ...mockVideoMedia, fileSize: 501 * 1024 * 1024 };
    const result = await adapter.validateContent(mockTextContent, [bigVideo]);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.includes('500 MB') || e.includes('500MB'))).toBe(true);
  });

  // ─── Text-only Post Publish ──────────────────────────────────────────────────
  it('should publish text-only Threads post successfully (2-step: container → publish)', async () => {
    mockedAxios.post
      .mockResolvedValueOnce({ data: { id: 'container_abc123' } })   // Step 1: Create container
      .mockResolvedValueOnce({ data: { id: 'post_xyz789' } });        // Step 2: Publish

    const result = await adapter.publishPost(mockAccount, mockTextContent, []);

    expect(result.success).toBe(true);
    expect(result.externalPostId).toBe('post_xyz789');
    expect(result.externalPostUrl).toContain('threads.net');

    // Step 1: POST to /threads (create container)
    expect(mockedAxios.post).toHaveBeenNthCalledWith(
      1,
      expect.stringContaining(`/${mockAccount.platformAccountId}/threads`),
      null,
      expect.objectContaining({
        params: expect.objectContaining({
          media_type: 'TEXT',
          access_token: 'mock_threads_user_access_token',
        }),
      }),
    );

    // Step 2: POST to /threads_publish
    expect(mockedAxios.post).toHaveBeenNthCalledWith(
      2,
      expect.stringContaining(`/${mockAccount.platformAccountId}/threads_publish`),
      null,
      expect.objectContaining({
        params: expect.objectContaining({
          creation_id: 'container_abc123',
        }),
      }),
    );
  });

  // ─── Image Post Publish ──────────────────────────────────────────────────────
  it('should publish image post with media_type=IMAGE and correct image_url', async () => {
    mockedAxios.post
      .mockResolvedValueOnce({ data: { id: 'img_container_1' } })
      .mockResolvedValueOnce({ data: { id: 'img_post_1' } });

    mockedAxios.get.mockResolvedValueOnce({
      data: { status: 'FINISHED' },
    });

    const result = await adapter.publishPost(mockAccount, mockTextContent, [mockImageMedia]);
    expect(result.success).toBe(true);
  });

  // ─── Video Post with Status Polling ─────────────────────────────────────────
  it('should poll container status until FINISHED before publishing video', async () => {
    mockedAxios.post
      .mockResolvedValueOnce({ data: { id: 'vid_container_1' } }); // Create container

    // Poll: 2x PROCESSING, then FINISHED
    mockedAxios.get
      .mockResolvedValueOnce({ data: { status: 'IN_PROGRESS' } })
      .mockResolvedValueOnce({ data: { status: 'FINISHED' } });

    mockedAxios.post
      .mockResolvedValueOnce({ data: { id: 'vid_post_1' } }); // Publish

    const result = await adapter.publishPost(mockAccount, mockTextContent, [mockVideoMedia]);
    expect(result.success).toBe(true);
    // GET phải được gọi để poll status
    expect(mockedAxios.get).toHaveBeenCalledTimes(2);
  }, 15000);

  it('should handle video container ERROR status during polling and throw if publish fails', async () => {
    mockedAxios.post
      .mockResolvedValueOnce({ data: { id: 'vid_container_err' } })
      .mockRejectedValueOnce(new Error('Container processing failed'));
    mockedAxios.get.mockResolvedValue({
      data: { status: 'ERROR', error_message: 'Video encoding failed on Threads backend.' },
    });

    await expect(adapter.publishPost(mockAccount, mockTextContent, [mockVideoMedia]))
      .rejects.toThrow(BadRequestException);
    expect(mockedAxios.get).toHaveBeenCalledTimes(20);
  });

  it('should stop polling after maxAttempts (20) when container remains IN_PROGRESS', async () => {
    mockedAxios.post
      .mockResolvedValueOnce({ data: { id: 'vid_timeout_container' } })
      .mockResolvedValueOnce({ data: { id: 'vid_post_after_poll' } });
    mockedAxios.get.mockResolvedValue({ data: { status: 'IN_PROGRESS' } });

    const result = await adapter.publishPost(mockAccount, mockTextContent, [mockVideoMedia]);
    expect(mockedAxios.get).toHaveBeenCalledTimes(20);
    expect(result.success).toBe(true);
  });

  // ─── API Error Handling ──────────────────────────────────────────────────────
  it('should throw BadRequestException when Threads API returns error on container creation', async () => {
    mockedAxios.post.mockRejectedValueOnce({
      response: {
        status: 400,
        data: { error: { code: 100, message: 'Invalid media URL.' } },
      },
      message: 'Request failed with status code 400',
    });

    await expect(adapter.publishPost(mockAccount, mockTextContent, [mockImageMedia]))
      .rejects.toThrow(BadRequestException);
  });

  it('should truncate text to 500 chars if caption + hashtags exceeds limit', async () => {
    const longContent = {
      ...mockTextContent,
      caption: 'C'.repeat(400),
      hashtags: 'H'.repeat(200),
    };
    mockedAxios.post
      .mockResolvedValueOnce({ data: { id: 'trunc_container' } })
      .mockResolvedValueOnce({ data: { id: 'trunc_post' } });

    const result = await adapter.publishPost(mockAccount, longContent as Content, []);
    expect(result.success).toBe(true);

    // Confirm the sent text is truncated to max 500 chars
    const calledParams = (mockedAxios.post.mock.calls[0] as any)[2]?.params;
    if (calledParams?.text) {
      expect(calledParams.text.length).toBeLessThanOrEqual(500);
    }
  });
});
