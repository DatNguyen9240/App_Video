import {
  Injectable,
  Logger,
  NotFoundException,
  BadRequestException,
} from '@nestjs/common';
import { InjectQueue } from '@nestjs/bullmq';
import { Queue } from 'bullmq';
import { PrismaService } from '../prisma/prisma.service';
import { PlatformAdapterRegistry } from './adapters/adapter.registry';
import {
  ContentStatus,
  TaskStatus,
  Platform,
  PublishingTask,
} from '@prisma/client';
import { QUEUE_NAMES, JOB_NAMES } from '../common/constants';
import * as crypto from 'crypto';

@Injectable()
export class PublishingService {
  private readonly logger = new Logger(PublishingService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly adapterRegistry: PlatformAdapterRegistry,
    @InjectQueue(QUEUE_NAMES.PUBLISHING) private readonly publishingQueue: Queue,
  ) {}

  /**
   * Publishes a content item immediately across its target accounts.
   */
  async publishNow(contentId: string, userId: string) {
    return this.createAndDispatchPublishingTasks(contentId, userId, null);
  }

  /**
   * Schedules publishing for a specified future timestamp.
   */
  async schedulePublishing(contentId: string, scheduledTime: Date, userId: string) {
    const scheduledDate = new Date(scheduledTime);
    if (isNaN(scheduledDate.getTime())) {
      throw new BadRequestException('Invalid scheduled datetime format.');
    }
    if (scheduledDate <= new Date()) {
      throw new BadRequestException('Scheduled time must be in the future.');
    }

    return this.createAndDispatchPublishingTasks(contentId, userId, scheduledDate);
  }

  /**
   * Creates independent PublishingTasks for each target account and enqueues BullMQ jobs.
   */
  private async createAndDispatchPublishingTasks(
    contentId: string,
    userId: string,
    scheduledAt: Date | null,
  ) {
    const content = await this.prisma.content.findFirst({
      where: { id: contentId, userId },
      include: { mediaItems: true },
    });

    if (!content) {
      throw new NotFoundException(`Content with id '${contentId}' not found.`);
    }

    const targetPlatforms = (content.targetPlatforms as string[]) || [];
    if (targetPlatforms.length === 0) {
      throw new BadRequestException('Content has no target platforms specified.');
    }

    // Resolve matching social accounts
    let accounts = await this.prisma.socialAccount.findMany({
      where: {
        userId,
        platform: { in: targetPlatforms as Platform[] },
        status: 'CONNECTED',
      },
    });

    // If specific accounts were targeted in metadata, filter by them
    const targetAccountIds = (content.targetAccountIds as string[]) || [];
    if (targetAccountIds.length > 0) {
      accounts = accounts.filter((acc) => targetAccountIds.includes(acc.id));
    }

    if (accounts.length === 0) {
      throw new BadRequestException(
        `No connected social accounts found for platforms: ${targetPlatforms.join(', ')}. Please connect your accounts first.`,
      );
    }

    // Validate content against all target adapters first
    for (const platformStr of targetPlatforms) {
      const platform = platformStr as Platform;
      if (this.adapterRegistry.hasAdapter(platform)) {
        const adapter = this.adapterRegistry.getAdapter(platform);
        const validation = await adapter.validateContent(content, content.mediaItems);
        if (!validation.valid) {
          throw new BadRequestException(
            `Validation failed for ${platform}: ${validation.errors.join('; ')}`,
          );
        }
      }
    }

    // Update Content status
    const initialStatus = scheduledAt ? ContentStatus.SCHEDULED : ContentStatus.PROCESSING;
    await this.prisma.content.update({
      where: { id: contentId },
      data: {
        status: initialStatus,
        scheduledAt,
      },
    });

    const tasks: PublishingTask[] = [];

    // Dispatch independent BullMQ job per platform account
    for (const account of accounts) {
      // Deterministic idempotency key: SHA256(contentId:accountId:scheduledTimestampOrNow)
      const idempotencyKey = crypto
        .createHash('sha256')
        .update(`${content.id}:${account.id}:${scheduledAt ? scheduledAt.toISOString() : 'now'}`)
        .digest('hex');

      // Check if task already exists
      let task = await this.prisma.publishingTask.findUnique({
        where: { idempotencyKey },
      });

      if (!task) {
        task = await this.prisma.publishingTask.create({
          data: {
            contentId: content.id,
            socialAccountId: account.id,
            platform: account.platform,
            scheduledAt,
            status: scheduledAt ? TaskStatus.PENDING : TaskStatus.PROCESSING,
            idempotencyKey,
          },
        });
      }

      tasks.push(task);

      // Compute job delay
      const delay = scheduledAt ? Math.max(0, scheduledAt.getTime() - Date.now()) : 0;

      await this.publishingQueue.add(
        JOB_NAMES.PUBLISH_TASK,
        { taskId: task.id },
        {
          jobId: `task-${task.id}`, // Ensures BullMQ deduplication
          delay,
          attempts: 5,
          backoff: {
            type: 'exponential',
            delay: 10000, // 10s, 20s, 40s...
          },
          removeOnComplete: false,
          removeOnFail: false,
        },
      );

      this.logger.log(
        `Enqueued publishing job for task ${task.id} (${account.platform} - ${account.accountName}) with delay ${delay}ms`,
      );
    }

    return {
      success: true,
      contentId,
      status: initialStatus,
      scheduledAt,
      taskCount: tasks.length,
      tasks: tasks.map((t) => ({
        id: t.id,
        platform: t.platform,
        status: t.status,
        scheduledAt: t.scheduledAt,
      })),
    };
  }

  /**
   * Retries an individual failed publishing task independently without re-publishing others.
   */
  async retryTask(taskId: string, userId: string) {
    const task = await this.prisma.publishingTask.findFirst({
      where: { id: taskId, content: { userId } },
      include: { content: true, socialAccount: true },
    });

    if (!task) {
      throw new NotFoundException(`Publishing task ${taskId} not found.`);
    }

    if (task.status === TaskStatus.PUBLISHED) {
      throw new BadRequestException('Task is already published. Duplicate publishing prevented.');
    }

    // Reset status to PROCESSING
    await this.prisma.publishingTask.update({
      where: { id: taskId },
      data: {
        status: TaskStatus.PROCESSING,
        errorCode: null,
        errorMessage: null,
        retryCount: task.retryCount + 1,
      },
    });

    // Enqueue immediate job execution
    await this.publishingQueue.add(
      JOB_NAMES.PUBLISH_TASK,
      { taskId: task.id },
      {
        jobId: `retry-task-${task.id}-${Date.now()}`,
        attempts: 3,
        backoff: { type: 'exponential', delay: 5000 },
      },
    );

    this.logger.log(`Enqueued manual retry for task ${taskId} (${task.platform})`);
    return { success: true, message: `Retrying task ${taskId} on ${task.platform}...` };
  }

  /**
   * Cancels a scheduled or pending publishing task and removes it from the queue.
   */
  async cancelTask(taskId: string, userId: string) {
    const task = await this.prisma.publishingTask.findFirst({
      where: { id: taskId, content: { userId } },
    });

    if (!task) {
      throw new NotFoundException(`Publishing task ${taskId} not found.`);
    }

    if (task.status === TaskStatus.PUBLISHED) {
      throw new BadRequestException('Cannot cancel a task that has already been published.');
    }

    // Remove job from BullMQ
    try {
      const job = await this.publishingQueue.getJob(`task-${taskId}`);
      if (job) {
        await job.remove();
        this.logger.log(`Removed BullMQ job for cancelled task ${taskId}`);
      }
    } catch (err) {
      this.logger.warn(`Could not remove BullMQ job: ${err.message}`);
    }

    await this.prisma.publishingTask.update({
      where: { id: taskId },
      data: { status: TaskStatus.CANCELLED },
    });

    // Check parent content status
    await this.updateContentAggregatedStatus(task.contentId);

    return { success: true, message: `Task ${taskId} cancelled successfully.` };
  }

  /**
   * Reschedules an existing task for a new future date.
   */
  async rescheduleTask(taskId: string, newScheduledTime: Date, userId: string) {
    const newDate = new Date(newScheduledTime);
    if (isNaN(newDate.getTime()) || newDate <= new Date()) {
      throw new BadRequestException('New scheduled date must be in the future.');
    }

    const task = await this.prisma.publishingTask.findFirst({
      where: { id: taskId, content: { userId } },
    });

    if (!task) {
      throw new NotFoundException(`Publishing task ${taskId} not found.`);
    }

    if (task.status === TaskStatus.PUBLISHED) {
      throw new BadRequestException('Cannot reschedule an already published task.');
    }

    // Remove old job
    try {
      const job = await this.publishingQueue.getJob(`task-${taskId}`);
      if (job) {
        await job.remove();
      }
    } catch (err) {
      this.logger.warn(`Error removing old job during reschedule: ${err.message}`);
    }

    const delay = Math.max(0, newDate.getTime() - Date.now());

    await this.prisma.publishingTask.update({
      where: { id: taskId },
      data: {
        scheduledAt: newDate,
        status: TaskStatus.PENDING,
      },
    });

    await this.prisma.content.update({
      where: { id: task.contentId },
      data: {
        scheduledAt: newDate,
        status: ContentStatus.SCHEDULED,
      },
    });

    await this.publishingQueue.add(
      JOB_NAMES.PUBLISH_TASK,
      { taskId: task.id },
      {
        jobId: `task-${task.id}`,
        delay,
        attempts: 5,
        backoff: { type: 'exponential', delay: 10000 },
      },
    );

    return { success: true, taskId, newScheduledAt: newDate };
  }

  /**
   * Lists tasks with filtering and pagination.
   */
  async getTasks(filter: any, userId: string) {
    const { platform, status, page = 1, limit = 20 } = filter;
    const skip = (Number(page) - 1) * Number(limit);

    const where: any = {
      content: { userId },
    };

    if (platform) where.platform = platform;
    if (status) where.status = status;

    const [tasks, total] = await Promise.all([
      this.prisma.publishingTask.findMany({
        where,
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
              avatarUrl: true,
              platform: true,
            },
          },
        },
        orderBy: { createdAt: 'desc' },
        skip,
        take: Number(limit),
      }),
      this.prisma.publishingTask.count({ where }),
    ]);

    return {
      data: tasks,
      total,
      page: Number(page),
      limit: Number(limit),
      totalPages: Math.ceil(total / Number(limit)),
    };
  }

  async getTaskById(taskId: string, userId: string) {
    const task = await this.prisma.publishingTask.findFirst({
      where: { id: taskId, content: { userId } },
      include: {
        content: { include: { mediaItems: true } },
        socialAccount: true,
      },
    });
    if (!task) {
      throw new NotFoundException(`Task ${taskId} not found.`);
    }
    return task;
  }

  /**
   * Helper to update Content status according to the collective state of its tasks.
   */
  async updateContentAggregatedStatus(contentId: string) {
    const allTasks = await this.prisma.publishingTask.findMany({
      where: { contentId },
    });

    if (allTasks.length === 0) return;

    const allPublished = allTasks.every((t) => t.status === TaskStatus.PUBLISHED);
    const anyPublished = allTasks.some((t) => t.status === TaskStatus.PUBLISHED);
    const allFailed = allTasks.every((t) => t.status === TaskStatus.FAILED || t.status === TaskStatus.CANCELLED);

    let newStatus: ContentStatus = ContentStatus.PROCESSING;
    if (allPublished) {
      newStatus = ContentStatus.PUBLISHED;
    } else if (anyPublished) {
      newStatus = ContentStatus.PARTIALLY_PUBLISHED;
    } else if (allFailed) {
      newStatus = ContentStatus.FAILED;
    }

    await this.prisma.content.update({
      where: { id: contentId },
      data: { status: newStatus },
    });
  }
}
