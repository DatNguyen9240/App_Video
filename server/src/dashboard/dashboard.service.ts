import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { TaskStatus, ContentStatus, Platform } from '@prisma/client';

@Injectable()
export class DashboardService {
  constructor(private readonly prisma: PrismaService) {}

  async getOverview(userId: string) {
    const [
      totalAccounts,
      facebookAccounts,
      youtubeAccounts,
      publishedTasks,
      scheduledTasks,
      failedTasks,
      totalContent,
      totalMedia,
    ] = await Promise.all([
      this.prisma.socialAccount.count({ where: { userId, status: 'CONNECTED' } }),
      this.prisma.socialAccount.count({ where: { userId, platform: Platform.FACEBOOK, status: 'CONNECTED' } }),
      this.prisma.socialAccount.count({ where: { userId, platform: Platform.YOUTUBE, status: 'CONNECTED' } }),
      this.prisma.publishingTask.count({ where: { content: { userId }, status: TaskStatus.PUBLISHED } }),
      this.prisma.publishingTask.count({ where: { content: { userId }, status: { in: [TaskStatus.PENDING, TaskStatus.PROCESSING] } } }),
      this.prisma.publishingTask.count({ where: { content: { userId }, status: TaskStatus.FAILED } }),
      this.prisma.content.count({ where: { userId } }),
      this.prisma.media.count({ where: { OR: [{ storageKey: { startsWith: `media/${userId}/` } }, { content: { userId } }] } }),
    ]);

    return {
      connectedAccounts: {
        total: totalAccounts,
        facebook: facebookAccounts,
        youtube: youtubeAccounts,
      },
      publishing: {
        totalPublished: publishedTasks,
        scheduled: scheduledTasks,
        failed: failedTasks,
        totalPosts: totalContent,
      },
      media: {
        totalAssets: totalMedia,
      },
    };
  }

  async getRecentActivity(userId: string, limit: number = 10) {
    const tasks = await this.prisma.publishingTask.findMany({
      where: { content: { userId } },
      include: {
        content: {
          select: {
            id: true,
            title: true,
            caption: true,
          },
        },
        socialAccount: {
          select: {
            id: true,
            accountName: true,
            platform: true,
            avatarUrl: true,
          },
        },
      },
      orderBy: { updatedAt: 'desc' },
      take: Number(limit),
    });

    return tasks;
  }

  async getUpcomingQueue(userId: string, limit: number = 10) {
    const upcoming = await this.prisma.publishingTask.findMany({
      where: {
        content: { userId },
        status: { in: [TaskStatus.PENDING, TaskStatus.PROCESSING] },
      },
      include: {
        content: {
          select: {
            id: true,
            title: true,
            caption: true,
          },
        },
        socialAccount: {
          select: {
            id: true,
            accountName: true,
            platform: true,
            avatarUrl: true,
          },
        },
      },
      orderBy: { scheduledAt: 'asc' },
      take: Number(limit),
    });

    return upcoming;
  }
}
