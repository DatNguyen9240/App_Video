import { Test, TestingModule } from '@nestjs/testing';
import { PublishingProcessor } from './publishing.processor';
import { PrismaService } from '../prisma/prisma.service';
import { PlatformAdapterRegistry } from './adapters/adapter.registry';
import { SocialAccountService } from '../social-account/social-account.service';
import { PublishingService } from './publishing.service';
import { TaskStatus, AccountStatus, Platform } from '@prisma/client';
import { Job } from 'bullmq';

const mockTask = (overrides: Partial<any> = {}) => ({
  id: 'task-1',
  contentId: 'content-1',
  platform: Platform.FACEBOOK,
  status: TaskStatus.PENDING,
  externalPostId: null,
  externalPostUrl: null,
  retryCount: 0,
  errorCode: null,
  errorMessage: null,
  content: {
    id: 'content-1',
    title: 'Test Content',
    userId: 'user-1',
    caption: 'Test caption',
    mediaItems: [],
  },
  socialAccount: {
    id: 'acc-1',
    platform: Platform.FACEBOOK,
    accountName: 'FB Page',
    status: AccountStatus.CONNECTED,
  },
  ...overrides,
});

const makeJob = (taskId: string): Partial<Job<{ taskId: string }>> => ({
  id: 'job-1',
  data: { taskId },
});

describe('PublishingProcessor (BullMQ Worker)', () => {
  let processor: PublishingProcessor;
  let prisma: { publishingTask: jest.Mocked<any> };
  let adapterRegistry: jest.Mocked<PlatformAdapterRegistry>;
  let publishingService: jest.Mocked<PublishingService>;
  let mockFacebookAdapter: jest.Mocked<any>;

  beforeEach(async () => {
    mockFacebookAdapter = {
      platform: Platform.FACEBOOK,
      validateContent: jest.fn(),
      publishPost: jest.fn(),
    };

    prisma = {
      publishingTask: {
        findUnique: jest.fn(),
        update: jest.fn(),
      },
    };

    adapterRegistry = {
      getAdapter: jest.fn().mockReturnValue(mockFacebookAdapter),
      hasAdapter: jest.fn().mockReturnValue(true),
      getSupportedPlatforms: jest.fn().mockReturnValue([Platform.FACEBOOK]),
      registerAdapter: jest.fn(),
    } as any;

    publishingService = {
      updateContentAggregatedStatus: jest.fn().mockResolvedValue(undefined),
    } as any;

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        PublishingProcessor,
        { provide: PrismaService, useValue: prisma },
        { provide: PlatformAdapterRegistry, useValue: adapterRegistry },
        { provide: SocialAccountService, useValue: {} },
        { provide: PublishingService, useValue: publishingService },
      ],
    }).compile();

    processor = module.get<PublishingProcessor>(PublishingProcessor);
    jest.clearAllMocks();
  });

  // ─── Task Not Found ──────────────────────────────────────────────────────────
  it('should skip gracefully when task does not exist in DB', async () => {
    prisma.publishingTask.findUnique.mockResolvedValue(null);

    const result = await processor.process(makeJob('nonexistent-task') as Job<{ taskId: string }>);
    expect(result.skipped).toBe(true);
    expect(result.reason).toContain('not found');
    expect(adapterRegistry.getAdapter).not.toHaveBeenCalled();
  });

  // ─── Idempotency Check ───────────────────────────────────────────────────────
  it('should skip and return success when task is already PUBLISHED (idempotency)', async () => {
    prisma.publishingTask.findUnique.mockResolvedValue(
      mockTask({ status: TaskStatus.PUBLISHED, externalPostId: 'fb_post_exists' })
    );

    const result = await processor.process(makeJob('task-1') as Job<{ taskId: string }>);
    expect(result.alreadyPublished).toBe(true);
    expect(result.postId).toBe('fb_post_exists');
    expect(mockFacebookAdapter.publishPost).not.toHaveBeenCalled();
  });

  it('should skip when task is CANCELLED', async () => {
    prisma.publishingTask.findUnique.mockResolvedValue(
      mockTask({ status: TaskStatus.CANCELLED })
    );

    const result = await processor.process(makeJob('task-1') as Job<{ taskId: string }>);
    expect(result.cancelled).toBe(true);
    expect(mockFacebookAdapter.publishPost).not.toHaveBeenCalled();
  });

  // ─── Successful Publish Flow ─────────────────────────────────────────────────
  it('should mark task PROCESSING, call adapter, then mark PUBLISHED on success', async () => {
    const task = mockTask({ status: TaskStatus.PENDING });
    prisma.publishingTask.findUnique.mockResolvedValue(task);
    prisma.publishingTask.update.mockResolvedValue({});

    mockFacebookAdapter.publishPost.mockResolvedValue({
      success: true,
      externalPostId: 'fb_post_new_123',
      externalPostUrl: 'https://facebook.com/permalink/fb_post_new_123',
      metadata: { publishedAt: new Date().toISOString() },
    });

    const result = await processor.process(makeJob('task-1') as Job<{ taskId: string }>);

    expect(result.success).toBe(true);
    expect(result.externalPostId).toBe('fb_post_new_123');

    // First update: PROCESSING
    expect(prisma.publishingTask.update).toHaveBeenNthCalledWith(1, {
      where: { id: 'task-1' },
      data: { status: TaskStatus.PROCESSING },
    });

    // Second update: PUBLISHED
    expect(prisma.publishingTask.update).toHaveBeenNthCalledWith(2,
      expect.objectContaining({
        where: { id: 'task-1' },
        data: expect.objectContaining({
          status: TaskStatus.PUBLISHED,
          externalPostId: 'fb_post_new_123',
          externalPostUrl: 'https://facebook.com/permalink/fb_post_new_123',
        }),
      }),
    );

    // Aggregate content status must be updated
    expect(publishingService.updateContentAggregatedStatus).toHaveBeenCalledWith('content-1');
  });

  // ─── Failure Handling ────────────────────────────────────────────────────────
  it('should mark task FAILED and increment retryCount when adapter throws', async () => {
    const task = mockTask({ status: TaskStatus.PENDING });
    prisma.publishingTask.findUnique.mockResolvedValue(task);
    prisma.publishingTask.update.mockResolvedValue({});

    const publishError = new Error('Meta Graph API error: Page token expired.');
    mockFacebookAdapter.publishPost.mockRejectedValue(publishError);

    await expect(
      processor.process(makeJob('task-1') as Job<{ taskId: string }>)
    ).rejects.toThrow('Meta Graph API error: Page token expired.');

    // Mark FAILED with error details
    expect(prisma.publishingTask.update).toHaveBeenLastCalledWith({
      where: { id: 'task-1' },
      data: expect.objectContaining({
        status: TaskStatus.FAILED,
        errorMessage: 'Meta Graph API error: Page token expired.',
        retryCount: { increment: 1 },
      }),
    });

    // Aggregate status still updated after failure
    expect(publishingService.updateContentAggregatedStatus).toHaveBeenCalledWith('content-1');
  });

  it('should throw and mark FAILED when social account is REVOKED', async () => {
    const task = mockTask({
      status: TaskStatus.PENDING,
      socialAccount: {
        id: 'acc-1',
        platform: Platform.FACEBOOK,
        accountName: 'Revoked Page',
        status: AccountStatus.REVOKED,
      },
    });
    prisma.publishingTask.findUnique.mockResolvedValue(task);
    prisma.publishingTask.update.mockResolvedValue({});

    await expect(
      processor.process(makeJob('task-1') as Job<{ taskId: string }>)
    ).rejects.toThrow(/REVOKED|Reconnection required/i);

    expect(mockFacebookAdapter.publishPost).not.toHaveBeenCalled();
  });

  it('should throw and mark FAILED when social account is DISCONNECTED', async () => {
    const task = mockTask({
      status: TaskStatus.PENDING,
      socialAccount: {
        id: 'acc-1',
        platform: Platform.FACEBOOK,
        accountName: 'Disconnected Page',
        status: AccountStatus.DISCONNECTED,
      },
    });
    prisma.publishingTask.findUnique.mockResolvedValue(task);
    prisma.publishingTask.update.mockResolvedValue({});

    await expect(
      processor.process(makeJob('task-1') as Job<{ taskId: string }>)
    ).rejects.toThrow(/DISCONNECTED|Reconnection required/i);
  });

  it('should use correct adapter based on task platform (YOUTUBE)', async () => {
    const mockYouTubeAdapter = {
      platform: Platform.YOUTUBE,
      publishPost: jest.fn().mockResolvedValue({
        success: true,
        externalPostId: 'yt_video_abc',
        externalPostUrl: 'https://youtube.com/watch?v=yt_video_abc',
        metadata: {},
      }),
    };

    adapterRegistry.getAdapter.mockReturnValue(mockYouTubeAdapter as any);

    const task = mockTask({
      status: TaskStatus.PENDING,
      platform: Platform.YOUTUBE,
      socialAccount: {
        id: 'acc-yt-1',
        platform: Platform.YOUTUBE,
        accountName: 'My YT Channel',
        status: AccountStatus.CONNECTED,
      },
    });
    prisma.publishingTask.findUnique.mockResolvedValue(task);
    prisma.publishingTask.update.mockResolvedValue({});

    await processor.process(makeJob('task-1') as Job<{ taskId: string }>);

    expect(adapterRegistry.getAdapter).toHaveBeenCalledWith(Platform.YOUTUBE);
    expect(mockYouTubeAdapter.publishPost).toHaveBeenCalled();
  });
});
