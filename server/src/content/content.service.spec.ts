import { Test, TestingModule } from '@nestjs/testing';
import { NotFoundException } from '@nestjs/common';
import { ContentService } from './content.service';
import { PrismaService } from '../prisma/prisma.service';
import { PublishingService } from '../publishing/publishing.service';
import { ContentStatus } from '@prisma/client';
import { CreateContentDto, UpdateContentDto } from './dto/content.dto';

const mockContent = (overrides: Partial<any> = {}) => ({
  id: 'content-1',
  userId: 'user-demo',
  title: 'Video drama văn phòng',
  caption: 'Hôm nay văn phòng có drama lớn...',
  description: null,
  hashtags: '#viral #drama',
  status: ContentStatus.DRAFT,
  targetPlatforms: ['FACEBOOK', 'YOUTUBE'],
  targetAccountIds: null,
  scheduledAt: null,
  createdAt: new Date(),
  updatedAt: new Date(),
  mediaItems: [],
  publishingTasks: [],
  ...overrides,
});

describe('ContentService', () => {
  let service: ContentService;
  let prisma: { content: jest.Mocked<any>; media: jest.Mocked<any> };
  let publishingService: jest.Mocked<PublishingService>;

  beforeEach(async () => {
    prisma = {
      content: {
        create: jest.fn(),
        findMany: jest.fn(),
        findFirst: jest.fn(),
        update: jest.fn(),
        delete: jest.fn(),
        count: jest.fn(),
      },
      media: {
        updateMany: jest.fn(),
      },
    };

    publishingService = {
      publishNow: jest.fn().mockResolvedValue({ success: true, taskCount: 2 }),
      schedulePublishing: jest.fn().mockResolvedValue({ success: true, taskCount: 2 }),
    } as any;

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        ContentService,
        { provide: PrismaService, useValue: prisma },
        { provide: PublishingService, useValue: publishingService },
      ],
    }).compile();

    service = module.get<ContentService>(ContentService);
    jest.clearAllMocks();
  });

  // ─── createContent ───────────────────────────────────────────────────────────
  describe('createContent()', () => {
    it('should create content with DRAFT status and return with relations', async () => {
      const dto: CreateContentDto = {
        title: 'Drama Video',
        caption: 'Bài viết test',
        targetPlatforms: ['FACEBOOK'],
      };
      const created = mockContent();
      prisma.content.create.mockResolvedValue(created);
      prisma.content.findFirst.mockResolvedValue(created);

      const result = await service.createContent(dto, 'user-demo');

      expect(prisma.content.create).toHaveBeenCalledWith(
        expect.objectContaining({
          data: expect.objectContaining({
            status: ContentStatus.DRAFT,
            userId: 'user-demo',
          }),
        }),
      );
      expect(result.id).toBe('content-1');
    });

    it('should trigger publishNow when publishImmediately=true', async () => {
      const dto: CreateContentDto = {
        caption: 'Publish ngay đi',
        targetPlatforms: ['FACEBOOK'],
        publishImmediately: true,
      };
      const created = mockContent();
      prisma.content.create.mockResolvedValue(created);
      prisma.content.findFirst.mockResolvedValue(created);

      await service.createContent(dto, 'user-demo');

      expect(publishingService.publishNow).toHaveBeenCalledWith('content-1', 'user-demo');
      expect(publishingService.schedulePublishing).not.toHaveBeenCalled();
    });

    it('should trigger schedulePublishing when scheduledAt is in the future', async () => {
      const futureDate = new Date(Date.now() + 3600000).toISOString(); // 1h from now
      const dto: CreateContentDto = {
        caption: 'Post này sẽ được đăng sau 1 tiếng',
        targetPlatforms: ['YOUTUBE'],
        scheduledAt: futureDate,
      };
      const created = mockContent({ scheduledAt: new Date(futureDate) });
      prisma.content.create.mockResolvedValue(created);
      prisma.content.findFirst.mockResolvedValue(created);

      await service.createContent(dto, 'user-demo');

      expect(publishingService.schedulePublishing).toHaveBeenCalledWith(
        'content-1',
        expect.any(Date),
        'user-demo',
      );
      expect(publishingService.publishNow).not.toHaveBeenCalled();
    });

    it('should associate mediaIds when provided', async () => {
      const dto: CreateContentDto = {
        caption: 'Bài đăng có hình ảnh',
        targetPlatforms: ['FACEBOOK'],
        mediaIds: ['media-id-1', 'media-id-2'],
      };
      const created = mockContent();
      prisma.content.create.mockResolvedValue(created);
      prisma.content.findFirst.mockResolvedValue(created);
      prisma.media.updateMany.mockResolvedValue({ count: 2 });

      await service.createContent(dto, 'user-demo');

      expect(prisma.media.updateMany).toHaveBeenCalledWith({
        where: { id: { in: ['media-id-1', 'media-id-2'] } },
        data: { contentId: 'content-1' },
      });
    });
  });

  // ─── getContentList ──────────────────────────────────────────────────────────
  describe('getContentList()', () => {
    it('should return paginated list with correct page and limit', async () => {
      const items = [mockContent(), mockContent({ id: 'content-2' })];
      prisma.content.findMany.mockResolvedValue(items);
      prisma.content.count.mockResolvedValue(25);

      const result = await service.getContentList('user-demo', { page: 2, limit: 10 });

      expect(result.data).toHaveLength(2);
      expect(result.total).toBe(25);
      expect(result.page).toBe(2);
      expect(result.limit).toBe(10);
      expect(result.totalPages).toBe(3);

      expect(prisma.content.findMany).toHaveBeenCalledWith(
        expect.objectContaining({
          skip: 10,
          take: 10,
        }),
      );
    });

    it('should filter by status when provided', async () => {
      prisma.content.findMany.mockResolvedValue([]);
      prisma.content.count.mockResolvedValue(0);

      await service.getContentList('user-demo', { status: ContentStatus.PUBLISHED });

      expect(prisma.content.findMany).toHaveBeenCalledWith(
        expect.objectContaining({
          where: expect.objectContaining({ status: ContentStatus.PUBLISHED }),
        }),
      );
    });

    it('should not filter status when status is ALL or undefined', async () => {
      prisma.content.findMany.mockResolvedValue([]);
      prisma.content.count.mockResolvedValue(0);

      await service.getContentList('user-demo', { status: 'ALL' as any });

      const whereArg = prisma.content.findMany.mock.calls[0][0].where;
      expect(whereArg.status).toBeUndefined();
    });
  });

  // ─── getContentById ──────────────────────────────────────────────────────────
  describe('getContentById()', () => {
    it('should return content when found for the correct user', async () => {
      prisma.content.findFirst.mockResolvedValue(mockContent());

      const result = await service.getContentById('content-1', 'user-demo');
      expect(result.id).toBe('content-1');
    });

    it('should throw NotFoundException when content not found or wrong user', async () => {
      prisma.content.findFirst.mockResolvedValue(null);

      await expect(service.getContentById('nonexistent', 'user-demo'))
        .rejects.toThrow(NotFoundException);
    });
  });

  // ─── updateContent ───────────────────────────────────────────────────────────
  describe('updateContent()', () => {
    it('should update content and re-associate media when mediaIds provided', async () => {
      prisma.content.findFirst.mockResolvedValue(mockContent());
      prisma.content.update.mockResolvedValue({});
      prisma.media.updateMany.mockResolvedValue({ count: 1 });

      const dto: UpdateContentDto = {
        caption: 'Caption mới đã cập nhật',
        mediaIds: ['new-media-1'],
      };

      await service.updateContent('content-1', dto, 'user-demo');

      // Detach old media
      expect(prisma.media.updateMany).toHaveBeenCalledWith({
        where: { contentId: 'content-1' },
        data: { contentId: null },
      });
      // Attach new media
      expect(prisma.media.updateMany).toHaveBeenCalledWith({
        where: { id: { in: ['new-media-1'] } },
        data: { contentId: 'content-1' },
      });
    });

    it('should throw NotFoundException when updating content that does not belong to user', async () => {
      prisma.content.findFirst.mockResolvedValue(null);

      await expect(service.updateContent('wrong-id', { caption: 'x' }, 'user-demo'))
        .rejects.toThrow(NotFoundException);
    });
  });

  // ─── deleteContent ───────────────────────────────────────────────────────────
  describe('deleteContent()', () => {
    it('should delete content and return success message', async () => {
      prisma.content.findFirst.mockResolvedValue(mockContent());
      prisma.content.delete.mockResolvedValue({});

      const result = await service.deleteContent('content-1', 'user-demo');

      expect(result.success).toBe(true);
      expect(prisma.content.delete).toHaveBeenCalledWith({ where: { id: 'content-1' } });
    });

    it('should throw NotFoundException when content not found for deletion', async () => {
      prisma.content.findFirst.mockResolvedValue(null);

      await expect(service.deleteContent('nonexistent', 'user-demo'))
        .rejects.toThrow(NotFoundException);

      expect(prisma.content.delete).not.toHaveBeenCalled();
    });
  });
});
