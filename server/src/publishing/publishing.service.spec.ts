import { Test, TestingModule } from '@nestjs/testing';
import { PublishingService } from './publishing.service';
import { PrismaService } from '../prisma/prisma.service';
import { PlatformAdapterRegistry } from './adapters/adapter.registry';
import { ContentStatus, TaskStatus, Platform } from '@prisma/client';
import { getQueueToken } from '@nestjs/bullmq';
import { QUEUE_NAMES } from '../common/constants';

describe('PublishingService (Scheduling & Partial Publishing)', () => {
  let service: PublishingService;
  let prisma: PrismaService;
  let mockQueue: any;
  let mockAdapterRegistry: any;

  beforeEach(async () => {
    mockQueue = {
      add: jest.fn().mockResolvedValue({ id: 'job-123' }),
      getJob: jest.fn().mockResolvedValue({ remove: jest.fn().mockResolvedValue(true) }),
    };

    mockAdapterRegistry = {
      hasAdapter: jest.fn().mockReturnValue(true),
      getAdapter: jest.fn().mockReturnValue({
        validateContent: jest.fn().mockResolvedValue({ valid: true, errors: [] }),
      }),
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        PublishingService,
        {
          provide: PrismaService,
          useValue: {
            content: {
              findFirst: jest.fn(),
              update: jest.fn(),
            },
            socialAccount: {
              findMany: jest.fn(),
            },
            publishingTask: {
              findUnique: jest.fn(),
              findFirst: jest.fn(),
              findMany: jest.fn(),
              create: jest.fn(),
              update: jest.fn(),
            },
          },
        },
        {
          provide: PlatformAdapterRegistry,
          useValue: mockAdapterRegistry,
        },
        {
          provide: getQueueToken(QUEUE_NAMES.PUBLISHING),
          useValue: mockQueue,
        },
      ],
    }).compile();

    service = module.get<PublishingService>(PublishingService);
    prisma = module.get<PrismaService>(PrismaService);
    jest.clearAllMocks();
  });

  it('should dispatch independent BullMQ jobs with unique idempotency keys for Facebook and YouTube', async () => {
    const mockContent = {
      id: 'c-100',
      userId: 'user-1',
      title: 'Multi-platform Announcement',
      caption: 'Big launch today!',
      targetPlatforms: ['FACEBOOK', 'YOUTUBE'],
      mediaItems: [],
    };

    const mockAccounts = [
      { id: 'acc-fb-1', platform: Platform.FACEBOOK, accountName: 'FB Page', status: 'CONNECTED' },
      { id: 'acc-yt-1', platform: Platform.YOUTUBE, accountName: 'YT Channel', status: 'CONNECTED' },
    ];

    (prisma.content.findFirst as jest.Mock).mockResolvedValue(mockContent);
    (prisma.socialAccount.findMany as jest.Mock).mockResolvedValue(mockAccounts);
    (prisma.publishingTask.findUnique as jest.Mock).mockResolvedValue(null);
    (prisma.publishingTask.create as jest.Mock).mockImplementation(({ data }) => ({
      id: `task-${data.platform}`,
      ...data,
    }));
    (prisma.content.update as jest.Mock).mockResolvedValue({});

    const result = await service.publishNow('c-100', 'user-1');

    expect(result.success).toBe(true);
    expect(result.taskCount).toBe(2);

    // Two distinct tasks created with distinct idempotency keys
    expect(prisma.publishingTask.create).toHaveBeenCalledTimes(2);

    // Two independent BullMQ jobs enqueued
    expect(mockQueue.add).toHaveBeenCalledTimes(2);
    expect(mockQueue.add).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ taskId: 'task-FACEBOOK' }),
      expect.any(Object),
    );
    expect(mockQueue.add).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ taskId: 'task-YOUTUBE' }),
      expect.any(Object),
    );
  });

  it('should correctly aggregate ContentStatus to PARTIALLY_PUBLISHED when one platform succeeds and another fails', async () => {
    (prisma.publishingTask.findMany as jest.Mock).mockResolvedValue([
      { id: 'task-fb', status: TaskStatus.PUBLISHED },
      { id: 'task-yt', status: TaskStatus.FAILED },
    ]);

    await service.updateContentAggregatedStatus('c-100');

    expect(prisma.content.update).toHaveBeenCalledWith({
      where: { id: 'c-100' },
      data: { status: ContentStatus.PARTIALLY_PUBLISHED },
    });
  });

  it('should retry a failed task independently without touching the already published task', async () => {
    const mockFailedTask = {
      id: 'task-yt',
      contentId: 'c-100',
      platform: Platform.YOUTUBE,
      status: TaskStatus.FAILED,
      retryCount: 1,
      content: { id: 'c-100', userId: 'user-1' },
      socialAccount: { id: 'acc-yt-1', platform: Platform.YOUTUBE },
    };

    (prisma.publishingTask.findFirst as jest.Mock).mockResolvedValue(mockFailedTask);
    (prisma.publishingTask.update as jest.Mock).mockResolvedValue({});

    const res = await service.retryTask('task-yt', 'user-1');

    expect(res.success).toBe(true);
    expect(prisma.publishingTask.update).toHaveBeenCalledWith({
      where: { id: 'task-yt' },
      data: expect.objectContaining({
        status: TaskStatus.PROCESSING,
        retryCount: 2,
      }),
    });

    // Re-enqueues only the YouTube task
    expect(mockQueue.add).toHaveBeenCalledWith(
      expect.any(String),
      { taskId: 'task-yt' },
      expect.any(Object),
    );
  });

  it('should prevent retrying an already published task (idempotency)', async () => {
    const mockPublishedTask = {
      id: 'task-fb',
      status: TaskStatus.PUBLISHED,
      content: { userId: 'user-1' },
    };

    (prisma.publishingTask.findFirst as jest.Mock).mockResolvedValue(mockPublishedTask);

    await expect(service.retryTask('task-fb', 'user-1')).rejects.toThrow(
      'Task is already published. Duplicate publishing prevented.',
    );
  });
});
