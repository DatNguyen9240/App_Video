import {
  Controller,
  Post,
  Get,
  Patch,
  Param,
  Body,
  Query,
  Headers,
} from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { PublishingService } from './publishing.service';
import { DEFAULT_USER_ID } from '../common/constants';

@ApiTags('Publishing')
@Controller('publishing')
export class PublishingController {
  constructor(private readonly publishingService: PublishingService) {}

  @Post('publish-now')
  @ApiOperation({ summary: 'Publish content immediately to target platforms' })
  async publishNow(
    @Body('contentId') contentId: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.publishingService.publishNow(contentId, userId);
  }

  @Post('schedule')
  @ApiOperation({ summary: 'Schedule content for future automated publishing' })
  async schedule(
    @Body() body: { contentId: string; scheduledAt: string },
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.publishingService.schedulePublishing(
      body.contentId,
      new Date(body.scheduledAt),
      userId,
    );
  }

  @Get('tasks')
  @ApiOperation({ summary: 'List publishing history and queued tasks' })
  async getTasks(
    @Query('platform') platform?: string,
    @Query('status') status?: string,
    @Query('page') page?: number,
    @Query('limit') limit?: number,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.publishingService.getTasks(
      { platform, status, page, limit },
      userId,
    );
  }

  @Get('tasks/:id')
  @ApiOperation({ summary: 'Get details of a specific publishing task' })
  async getTaskById(
    @Param('id') id: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.publishingService.getTaskById(id, userId);
  }

  @Post('tasks/:id/retry')
  @ApiOperation({ summary: 'Retry a failed publishing task independently' })
  async retryTask(
    @Param('id') id: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.publishingService.retryTask(id, userId);
  }

  @Post('tasks/:id/cancel')
  @ApiOperation({ summary: 'Cancel a pending or scheduled publishing task' })
  async cancelTask(
    @Param('id') id: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.publishingService.cancelTask(id, userId);
  }

  @Patch('tasks/:id/reschedule')
  @ApiOperation({ summary: 'Reschedule a task for a new future date' })
  async rescheduleTask(
    @Param('id') id: string,
    @Body('scheduledAt') scheduledAt: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.publishingService.rescheduleTask(
      id,
      new Date(scheduledAt),
      userId,
    );
  }
}
