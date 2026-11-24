import { Controller, Get, Query, Headers } from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { DashboardService } from './dashboard.service';
import { DEFAULT_USER_ID } from '../common/constants';

@ApiTags('Dashboard')
@Controller('dashboard')
export class DashboardController {
  constructor(private readonly dashboardService: DashboardService) {}

  @Get('overview')
  @ApiOperation({ summary: 'Get SaaS dashboard summary metrics and statistics' })
  async getOverview(@Headers('x-user-id') userIdHeader?: string) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.dashboardService.getOverview(userId);
  }

  @Get('recent-activity')
  @ApiOperation({ summary: 'Get recent publishing activities and execution logs' })
  async getRecentActivity(
    @Query('limit') limit?: number,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.dashboardService.getRecentActivity(userId, limit);
  }

  @Get('upcoming-queue')
  @ApiOperation({ summary: 'Get upcoming scheduled posts queue' })
  async getUpcomingQueue(
    @Query('limit') limit?: number,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.dashboardService.getUpcomingQueue(userId, limit);
  }
}
