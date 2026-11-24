import { Injectable, NotFoundException, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { PublishingService } from '../publishing/publishing.service';
import { CreateContentDto, UpdateContentDto } from './dto/content.dto';
import { ContentStatus } from '@prisma/client';

@Injectable()
export class ContentService {
  private readonly logger = new Logger(ContentService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly publishingService: PublishingService,
  ) {}

  async createContent(dto: CreateContentDto, userId: string) {
    const { mediaIds, publishImmediately, scheduledAt, ...rest } = dto;

    const content = await this.prisma.content.create({
      data: {
        ...rest,
        userId,
        status: ContentStatus.DRAFT,
        scheduledAt: scheduledAt ? new Date(scheduledAt) : null,
      },
    });

    // Associate media items if specified
    if (mediaIds && mediaIds.length > 0) {
      await this.prisma.media.updateMany({
        where: { id: { in: mediaIds } },
        data: { contentId: content.id },
      });
    }

    // Auto-trigger publishing or scheduling if requested
    if (publishImmediately) {
      await this.publishingService.publishNow(content.id, userId);
    } else if (scheduledAt && new Date(scheduledAt) > new Date()) {
      await this.publishingService.schedulePublishing(
        content.id,
        new Date(scheduledAt),
        userId,
      );
    }

    return this.getContentById(content.id, userId);
  }

  async getContentList(userId: string, filter?: { status?: ContentStatus; page?: number; limit?: number }) {
    const { status, page = 1, limit = 20 } = filter || {};
    const skip = (Number(page) - 1) * Number(limit);

    const where: any = { userId };
    if (status) where.status = status;

    const [items, total] = await Promise.all([
      this.prisma.content.findMany({
        where,
        include: {
          mediaItems: true,
          publishingTasks: {
            select: {
              id: true,
              platform: true,
              status: true,
              externalPostUrl: true,
              publishedAt: true,
              errorMessage: true,
            },
          },
        },
        orderBy: { updatedAt: 'desc' },
        skip,
        take: Number(limit),
      }),
      this.prisma.content.count({ where }),
    ]);

    return {
      data: items,
      total,
      page: Number(page),
      limit: Number(limit),
      totalPages: Math.ceil(total / Number(limit)),
    };
  }

  async getContentById(id: string, userId: string) {
    const content = await this.prisma.content.findFirst({
      where: { id, userId },
      include: {
        mediaItems: true,
        publishingTasks: {
          include: {
            socialAccount: {
              select: {
                id: true,
                accountName: true,
                platform: true,
                avatarUrl: true,
              },
            },
          },
        },
      },
    });
    if (!content) {
      throw new NotFoundException(`Content with id '${id}' not found.`);
    }
    return content;
  }

  async updateContent(id: string, dto: UpdateContentDto, userId: string) {
    await this.getContentById(id, userId);

    const { mediaIds, scheduledAt, ...rest } = dto;

    await this.prisma.content.update({
      where: { id },
      data: {
        ...rest,
        ...(scheduledAt !== undefined
          ? { scheduledAt: scheduledAt ? new Date(scheduledAt) : null }
          : {}),
      },
    });

    if (mediaIds !== undefined) {
      // Detach existing media
      await this.prisma.media.updateMany({
        where: { contentId: id },
        data: { contentId: null },
      });
      // Attach new media
      if (mediaIds.length > 0) {
        await this.prisma.media.updateMany({
          where: { id: { in: mediaIds } },
          data: { contentId: id },
        });
      }
    }

    return this.getContentById(id, userId);
  }

  async deleteContent(id: string, userId: string) {
    await this.getContentById(id, userId);
    await this.prisma.content.delete({ where: { id } });
    return { success: true, message: `Content ${id} deleted successfully.` };
  }
}
