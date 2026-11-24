import {
  Controller,
  Get,
  Delete,
  Post,
  Param,
  Query,
  Res,
  Headers,
  HttpStatus,
} from '@nestjs/common';
import { ApiTags, ApiOperation, ApiResponse } from '@nestjs/swagger';
import { Response } from 'express';
import { ConfigService } from '@nestjs/config';
import { SocialAccountService } from './social-account.service';
import { DEFAULT_USER_ID } from '../common/constants';

@ApiTags('Social Accounts')
@Controller('social-accounts')
export class SocialAccountController {
  constructor(
    private readonly socialAccountService: SocialAccountService,
    private readonly configService: ConfigService,
  ) {}

  @Get()
  @ApiOperation({ summary: 'List all connected social accounts' })
  async getAccounts(@Headers('x-user-id') userIdHeader?: string) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.socialAccountService.getAccounts(userId);
  }

  @Get(':id')
  @ApiOperation({ summary: 'Get details of a specific social account' })
  async getAccountById(
    @Param('id') id: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.socialAccountService.getAccountById(userId, id);
  }

  @Get('facebook/connect')
  @ApiOperation({ summary: 'Initiate Facebook OAuth flow' })
  async connectFacebook(
    @Query('redirect') redirectOverride?: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.socialAccountService.generateFacebookAuthUrl(userId, redirectOverride);
  }

  @Get('facebook/callback')
  @ApiOperation({ summary: 'Meta OAuth callback handler' })
  async facebookCallback(
    @Query('code') code: string,
    @Query('state') state: string,
    @Res() res: Response,
  ) {
    const frontendUrl = this.configService.get<string>('FRONTEND_URL', 'http://localhost:5173');
    try {
      await this.socialAccountService.handleFacebookCallback(code, state);
      return res.redirect(`${frontendUrl}/accounts?status=success&platform=facebook`);
    } catch (err) {
      const errMsg = encodeURIComponent(err.message || 'Facebook authorization failed');
      return res.redirect(`${frontendUrl}/accounts?status=error&platform=facebook&message=${errMsg}`);
    }
  }

  @Get('youtube/connect')
  @ApiOperation({ summary: 'Initiate Google YouTube OAuth flow' })
  async connectYouTube(
    @Query('redirect') redirectOverride?: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.socialAccountService.generateYouTubeAuthUrl(userId, redirectOverride);
  }

  @Get('youtube/callback')
  @ApiOperation({ summary: 'Google YouTube OAuth callback handler' })
  async youtubeCallback(
    @Query('code') code: string,
    @Query('state') state: string,
    @Res() res: Response,
  ) {
    const frontendUrl = this.configService.get<string>('FRONTEND_URL', 'http://localhost:5173');
    try {
      await this.socialAccountService.handleYouTubeCallback(code, state);
      return res.redirect(`${frontendUrl}/accounts?status=success&platform=youtube`);
    } catch (err) {
      const errMsg = encodeURIComponent(err.message || 'YouTube authorization failed');
      return res.redirect(`${frontendUrl}/accounts?status=error&platform=youtube&message=${errMsg}`);
    }
  }

  @Post(':id/refresh')
  @ApiOperation({ summary: 'Force refresh account OAuth access token' })
  async refreshToken(@Param('id') id: string) {
    await this.socialAccountService.refreshAccountToken(id);
    return { success: true, message: 'OAuth token refreshed successfully.' };
  }

  @Delete(':id')
  @ApiOperation({ summary: 'Disconnect a social account' })
  async disconnect(
    @Param('id') id: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.socialAccountService.disconnectAccount(userId, id);
  }
}
