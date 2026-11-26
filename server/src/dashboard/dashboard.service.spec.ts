import { Test, TestingModule } from '@nestjs/testing';
import { DashboardService } from './dashboard.service';
import { PrismaService } from '../prisma/prisma.service';
import { TaskStatus, Platform } from '@prisma/client';

describe('DashboardService', () => {
  let service: DashboardService;
  let prisma: {
    socialAccount: jest.Mocked<any>;
    publishingTask: jest.Mocked<any>;
    content: jest.Mocked<any>;
    media: jest.Mocked<any>;
  };

  beforeEach(async () => {
    prisma = {
      socialAccount: { count: jest.fn() },
      publishingTask: {
        count: jest.fn(),
        findMany: jest.fn(),
      },
      content: { count: jest.fn() },
      media: { count: jest.fn() },
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        DashboardService,
        { provide: PrismaService, useValue: prisma },
      ],
    }).compile();

    service = module.get<DashboardService>(DashboardService);
    jest.clearAllMocks();
  });

  // ─── getOverview ─────────────────────────────────────────────────────────────
  describe('getOverview()', () => {
    it('should aggregate and return correct counts for all metrics', async () => {
      // 8 parallel count queries
      prisma.socialAccount.count
        .mockResolvedValueOnce(3)   // total connected
        .mockResolvedValueOnce(2)   // facebook connected
        .mockResolvedValueOnce(1);  // youtube connected

      prisma.publishingTask.count
        .mockResolvedValueOnce(42)  // published
        .mockResolvedValueOnce(5)   // scheduled/processing
        .mockResolvedValueOnce(3);  // failed

      prisma.content.count.mockResolvedValue(18);
      prisma.media.count.mockResolvedValue(55);

      const result = await service.getOverview('user-demo');

      expect(result.connectedAccounts.total).toBe(3);
      expect(result.connectedAccounts.facebook).toBe(2);
      expect(result.connectedAccounts.youtube).toBe(1);
      expect(result.publishing.totalPublished).toBe(42);
      expect(result.publishing.scheduled).toBe(5);
      expect(result.publishing.failed).toBe(3);
      expect(result.publishing.totalPosts).toBe(18);
      expect(result.media.totalAssets).toBe(55);
    });

    it('should return zeros when no data exists', async () => {
      prisma.socialAccount.count.mockResolvedValue(0);
      prisma.publishingTask.count.mockResolvedValue(0);
      prisma.content.count.mockResolvedValue(0);
      prisma.media.count.mockResolvedValue(0);

      const result = await service.getOverview('user-new');

      expect(result.connectedAccounts.total).toBe(0);
      expect(result.publishing.totalPublished).toBe(0);
      expect(result.media.totalAssets).toBe(0);
    });
  });

  // ─── getRecentActivity ───────────────────────────────────────────────────────
  describe('getRecentActivity()', () => {
    it('should return tasks ordered by updatedAt desc with correct limit', async () => {
      const tasks = Array.from({ length: 5 }, (_, i) => ({
        id: `task-${i}`,
        status: TaskStatus.PUBLISHED,
        content: { id: `content-${i}`, title: `Post ${i}`, caption: `Caption ${i}` },
        socialAccount: {
          id: `acc-${i}`,
          accountName: `FB Page ${i}`,
          platform: Platform.FACEBOOK,
          avatarUrl: null,
        },
        updatedAt: new Date(Date.now() - i * 60000),
      }));
      prisma.publishingTask.findMany.mockResolvedValue(tasks);

      const result = await service.getRecentActivity('user-demo', 5);

      expect(result).toHaveLength(5);
      expect(prisma.publishingTask.findMany).toHaveBeenCalledWith(
        expect.objectContaining({ take: 5 }),
      );
    });

    it('should use default limit of 10 when not specified', async () => {
      prisma.publishingTask.findMany.mockResolvedValue([]);

      await service.getRecentActivity('user-demo');

      expect(prisma.publishingTask.findMany).toHaveBeenCalledWith(
        expect.objectContaining({ take: 10 }),
      );
    });
  });

  // ─── getUpcomingQueue ─────────────────────────────────────────────────────────
  describe('getUpcomingQueue()', () => {
    it('should return only PENDING and PROCESSING tasks ordered by scheduledAt asc', async () => {
      const upcoming = [
        {
          id: 'task-pending-1',
          status: TaskStatus.PENDING,
          scheduledAt: new Date(Date.now() + 3600000),
          content: { id: 'c-1', title: 'Tomorrow Post', caption: null },
          socialAccount: { id: 'acc-1', accountName: 'FB Page', platform: Platform.FACEBOOK, avatarUrl: null },
        },
        {
          id: 'task-processing-1',
          status: TaskStatus.PROCESSING,
          scheduledAt: new Date(Date.now() + 7200000),
          content: { id: 'c-2', title: 'Later Post', caption: null },
          socialAccount: { id: 'acc-1', accountName: 'FB Page', platform: Platform.FACEBOOK, avatarUrl: null },
        },
      ];
      prisma.publishingTask.findMany.mockResolvedValue(upcoming);

      const result = await service.getUpcomingQueue('user-demo');

      expect(result).toHaveLength(2);
      // All items should be PENDING or PROCESSING
      result.forEach((task: any) => {
        expect([TaskStatus.PENDING, TaskStatus.PROCESSING]).toContain(task.status);
      });

      // Verify status filter was applied correctly
      expect(prisma.publishingTask.findMany).toHaveBeenCalledWith(
        expect.objectContaining({
          where: expect.objectContaining({
            status: { in: [TaskStatus.PENDING, TaskStatus.PROCESSING] },
          }),
          orderBy: { scheduledAt: 'asc' },
        }),
      );
    });

    it('should return empty array when no upcoming tasks', async () => {
      prisma.publishingTask.findMany.mockResolvedValue([]);
      const result = await service.getUpcomingQueue('user-demo');
      expect(result).toHaveLength(0);
    });
  });
});
