import { Processor, WorkerHost } from '@nestjs/bullmq';
import { Job } from 'bullmq';
import { Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { PlatformAdapterRegistry } from './adapters/adapter.registry';
import { SocialAccountService } from '../social-account/social-account.service';
import { PublishingService } from './publishing.service';
import { TaskStatus, AccountStatus } from '@prisma/client';
import { QUEUE_NAMES, JOB_NAMES } from '../common/constants';

@Processor(QUEUE_NAMES.PUBLISHING)
export class PublishingProcessor extends WorkerHost {
  private readonly logger = new Logger(PublishingProcessor.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly adapterRegistry: PlatformAdapterRegistry,
    private readonly socialAccountService: SocialAccountService,
    private readonly publishingService: PublishingService,
  ) {
    super();
  }

  async process(job: Job<{ taskId: string }>): Promise<any> {
    const { taskId } = job.data;
    this.logger.log(`Processing BullMQ job #${job.id} for PublishingTask: ${taskId}`);

    const task = await this.prisma.publishingTask.findUnique({
      where: { id: taskId },
      include: {
        content: { include: { mediaItems: true } },
        socialAccount: true,
      },
    });

    if (!task) {
      this.logger.warn(`Task ${taskId} not found in database. Skipping.`);
      return { skipped: true, reason: 'Task not found' };
    }

    // 1. Idempotency Check: Do not publish if already published or cancelled
    if (task.status === TaskStatus.PUBLISHED) {
      this.logger.log(`Task ${taskId} is already PUBLISHED. Idempotency safeguard triggered. Skipping duplicate.`);
      return { success: true, alreadyPublished: true, postId: task.externalPostId };
    }

    if (task.status === TaskStatus.CANCELLED) {
      this.logger.log(`Task ${taskId} was CANCELLED. Skipping execution.`);
      return { cancelled: true };
    }

    // 2. Mark task as PROCESSING atomically
    await this.prisma.publishingTask.update({
      where: { id: taskId },
      data: { status: TaskStatus.PROCESSING },
    });

    try {
      // 3. Verify Account Status & Refresh token if expiring
      const account = task.socialAccount;
      if (account.status === AccountStatus.DISCONNECTED || account.status === AccountStatus.REVOKED) {
        throw new Error(`Social account '${account.accountName}' is ${account.status}. Reconnection required.`);
      }

      // Check adapter availability
      const adapter = this.adapterRegistry.getAdapter(task.platform);

      // 4. Execute Real Publishing via Platform Adapter
      this.logger.log(`Calling ${task.platform} adapter to publish content '${task.content.title || task.content.id}' to account '${account.accountName}'`);
      const result = await adapter.publishPost(account, task.content, task.content.mediaItems);

      if (result.success) {
        // 5. Update Task with Success
        await this.prisma.publishingTask.update({
          where: { id: taskId },
          data: {
            status: TaskStatus.PUBLISHED,
            publishedAt: new Date(),
            externalPostId: result.externalPostId,
            externalPostUrl: result.externalPostUrl,
            platformMetadata: result.metadata || {},
            errorCode: null,
            errorMessage: null,
          },
        });

        this.logger.log(`Task ${taskId} successfully published to ${task.platform}. External Post URL: ${result.externalPostUrl}`);

        // Update overall content status
        await this.publishingService.updateContentAggregatedStatus(task.contentId);
        return result;
      } else {
        // Platform adapter reported an error
        throw new Error(result.errorMessage || `Publishing failed on ${task.platform}`);
      }
    } catch (err) {
      const errorMessage = err.message || 'Unknown publishing execution error';
      this.logger.error(`Publishing failed for task ${taskId} on ${task.platform}: ${errorMessage}`);

      // 6. Update Task with Failure
      await this.prisma.publishingTask.update({
        where: { id: taskId },
        data: {
          status: TaskStatus.FAILED,
          errorCode: err.code || 'PUBLISH_FAILED',
          errorMessage,
          retryCount: { increment: 1 },
        },
      });

      // Update parent content to PARTIALLY_PUBLISHED (never overwrites already published platforms)
      await this.publishingService.updateContentAggregatedStatus(task.contentId);

      // Re-throw so BullMQ applies exponential backoff retry if attempts remain
      throw err;
    }
  }
}
