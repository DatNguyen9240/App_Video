import {
  Injectable,
  Logger,
  NotFoundException,
  BadRequestException,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../prisma/prisma.service';
import { EncryptionService } from '../common/services/encryption.service';
import { Platform, AccountStatus, SocialAccount } from '@prisma/client';
import axios from 'axios';
import { google } from 'googleapis';
import * as crypto from 'crypto';

@Injectable()
export class SocialAccountService {
  private readonly logger = new Logger(SocialAccountService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly encryptionService: EncryptionService,
    private readonly configService: ConfigService,
  ) {}

  /**
   * Retrieves all connected social accounts for a user.
   * Strips all encrypted tokens before returning to client.
   */
  async getAccounts(userId: string) {
    const accounts = await this.prisma.socialAccount.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      select: {
        id: true,
        userId: true,
        platform: true,
        platformAccountId: true,
        accountName: true,
        avatarUrl: true,
        tokenExpiresAt: true,
        scopes: true,
        status: true,
        metadata: true,
        createdAt: true,
        updatedAt: true,
      },
    });
    return accounts;
  }

  async getAccountById(userId: string, id: string) {
    const account = await this.prisma.socialAccount.findFirst({
      where: { id, userId },
      select: {
        id: true,
        userId: true,
        platform: true,
        platformAccountId: true,
        accountName: true,
        avatarUrl: true,
        tokenExpiresAt: true,
        scopes: true,
        status: true,
        metadata: true,
        createdAt: true,
        updatedAt: true,
      },
    });
    if (!account) {
      throw new NotFoundException(`Social account with id '${id}' not found.`);
    }
    return account;
  }

  /**
   * Generates secure OAuth authorization URL for Meta (Facebook Pages).
   */
  async generateFacebookAuthUrl(userId: string, redirectUriOverride?: string): Promise<{ url: string; state: string }> {
    const appId = this.configService.get<string>('META_APP_ID');
    const apiVersion = this.configService.get<string>('META_API_VERSION', 'v21.0');
    const redirectUri = redirectUriOverride || this.configService.get<string>('META_OAUTH_REDIRECT_URI');

    if (!appId) {
      throw new BadRequestException('META_APP_ID is not configured in server environment.');
    }

    const state = crypto.randomBytes(24).toString('hex');
    await this.prisma.oAuthState.create({
      data: {
        state,
        userId,
        platform: Platform.FACEBOOK,
        redirectUri,
        expiresAt: new Date(Date.now() + 15 * 60 * 1000), // 15 mins expiry
      },
    });

    const configuredScopes = this.configService.get<string>('META_OAUTH_SCOPES');
    const scopes = configuredScopes || [
      'pages_show_list',
      'pages_read_engagement',
      'pages_manage_posts',
      'public_profile',
    ].join(',');

    const url = `https://www.facebook.com/${apiVersion}/dialog/oauth?client_id=${appId}&redirect_uri=${encodeURIComponent(
      redirectUri,
    )}&state=${state}&scope=${encodeURIComponent(scopes)}&response_type=code`;

    return { url, state };
  }

  /**
   * Handles OAuth callback from Meta, exchanges for long-lived page token, and saves Facebook Pages.
   */
  async handleFacebookCallback(code: string, state: string) {
    const oauthState = await this.validateAndConsumeState(state, Platform.FACEBOOK);

    const appId = this.configService.get<string>('META_APP_ID');
    const appSecret = this.configService.get<string>('META_APP_SECRET');
    const apiVersion = this.configService.get<string>('META_API_VERSION', 'v21.0');
    const redirectUri = oauthState.redirectUri || this.configService.get<string>('META_OAUTH_REDIRECT_URI');

    try {
      // 1. Exchange code for short-lived User Access Token
      const tokenRes = await axios.get(`https://graph.facebook.com/${apiVersion}/oauth/access_token`, {
        params: {
          client_id: appId,
          client_secret: appSecret,
          redirect_uri: redirectUri,
          code,
        },
      });
      const shortLivedUserToken = tokenRes.data.access_token;

      // 2. Exchange short-lived token for 60-day Long-Lived User Access Token
      const longLivedRes = await axios.get(`https://graph.facebook.com/${apiVersion}/oauth/access_token`, {
        params: {
          grant_type: 'fb_exchange_token',
          client_id: appId,
          client_secret: appSecret,
          fb_exchange_token: shortLivedUserToken,
        },
      });
      const longLivedUserToken = longLivedRes.data.access_token;

      // 3. Query all Facebook Pages managed by user (with long-lived page access tokens)
      const pagesRes = await axios.get(`https://graph.facebook.com/${apiVersion}/me/accounts`, {
        params: {
          access_token: longLivedUserToken,
          fields: 'id,name,access_token,category,tasks,picture',
        },
      });

      const pages = pagesRes.data.data || [];
      if (pages.length === 0) {
        throw new BadRequestException('No Facebook Pages found for this account. Please ensure you are an admin of at least one Facebook Page.');
      }

      const connectedAccounts = [];

      for (const page of pages) {
        // Page access tokens derived from long-lived user tokens do not expire
        const pageToken = page.access_token;
        const pageId = page.id;
        const pageName = page.name;
        const avatarUrl = page.picture?.data?.url || `https://graph.facebook.com/${apiVersion}/${pageId}/picture?type=large`;

        const accessTokenEncrypted = this.encryptionService.encrypt(pageToken);

        const account = await this.prisma.socialAccount.upsert({
          where: {
            userId_platform_platformAccountId: {
              userId: oauthState.userId,
              platform: Platform.FACEBOOK,
              platformAccountId: pageId,
            },
          },
          create: {
            userId: oauthState.userId,
            platform: Platform.FACEBOOK,
            platformAccountId: pageId,
            accountName: pageName,
            avatarUrl,
            accessTokenEncrypted,
            status: AccountStatus.CONNECTED,
            scopes: 'pages_manage_posts,pages_read_engagement',
            metadata: {
              category: page.category,
              tasks: page.tasks,
            },
          },
          update: {
            accountName: pageName,
            avatarUrl,
            accessTokenEncrypted,
            status: AccountStatus.CONNECTED,
            metadata: {
              category: page.category,
              tasks: page.tasks,
            },
            updatedAt: new Date(),
          },
        });

        connectedAccounts.push({
          id: account.id,
          platform: account.platform,
          platformAccountId: account.platformAccountId,
          accountName: account.accountName,
          avatarUrl: account.avatarUrl,
          status: account.status,
        });
      }

      this.logger.log(`Successfully connected ${connectedAccounts.length} Facebook Page(s) for user ${oauthState.userId}`);
      return { success: true, count: connectedAccounts.length, accounts: connectedAccounts };
    } catch (err) {
      const msg = err.response?.data?.error?.message || err.message;
      this.logger.error(`Facebook OAuth error: ${msg}`);
      throw new BadRequestException(`Meta Graph API authentication failed: ${msg}`);
    }
  }

  /**
   * Generates secure OAuth authorization URL for Google (YouTube Data API v3).
   */
  async generateYouTubeAuthUrl(userId: string, redirectUriOverride?: string): Promise<{ url: string; state: string }> {
    const clientId = this.configService.get<string>('GOOGLE_CLIENT_ID');
    const clientSecret = this.configService.get<string>('GOOGLE_CLIENT_SECRET');
    const redirectUri = redirectUriOverride || this.configService.get<string>('GOOGLE_OAUTH_REDIRECT_URI');

    if (!clientId || !clientSecret) {
      throw new BadRequestException('GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET is missing in server environment.');
    }

    const state = crypto.randomBytes(24).toString('hex');
    await this.prisma.oAuthState.create({
      data: {
        state,
        userId,
        platform: Platform.YOUTUBE,
        redirectUri,
        expiresAt: new Date(Date.now() + 15 * 60 * 1000),
      },
    });

    const oauth2Client = new google.auth.OAuth2(clientId, clientSecret, redirectUri);
    const scopes = [
      'https://www.googleapis.com/auth/youtube.upload',
      'https://www.googleapis.com/auth/youtube.readonly',
      'https://www.googleapis.com/auth/userinfo.profile',
    ];

    const url = oauth2Client.generateAuthUrl({
      access_type: 'offline', // Critical for receiving refresh_token
      prompt: 'consent',     // Guarantees refresh_token is returned
      scope: scopes,
      state,
    });

    return { url, state };
  }

  /**
   * Handles OAuth callback from Google, retrieves YouTube Channel details, and stores encrypted tokens.
   */
  async handleYouTubeCallback(code: string, state: string) {
    const oauthState = await this.validateAndConsumeState(state, Platform.YOUTUBE);

    const clientId = this.configService.get<string>('GOOGLE_CLIENT_ID');
    const clientSecret = this.configService.get<string>('GOOGLE_CLIENT_SECRET');
    const redirectUri = oauthState.redirectUri || this.configService.get<string>('GOOGLE_OAUTH_REDIRECT_URI');

    const oauth2Client = new google.auth.OAuth2(clientId, clientSecret, redirectUri);

    try {
      const { tokens } = await oauth2Client.getToken(code);
      oauth2Client.setCredentials(tokens);

      const youtube = google.youtube({ version: 'v3', auth: oauth2Client });
      const channelRes = await youtube.channels.list({
        part: ['snippet', 'statistics'],
        mine: true,
      });

      const channels = channelRes.data.items || [];
      if (channels.length === 0) {
        throw new BadRequestException('No YouTube Channel found associated with this Google Account.');
      }

      const channel = channels[0];
      const channelId = channel.id;
      const channelTitle = channel.snippet?.title || 'YouTube Channel';
      const avatarUrl = channel.snippet?.thumbnails?.high?.url || channel.snippet?.thumbnails?.default?.url;

      const accessTokenEncrypted = this.encryptionService.encrypt(tokens.access_token);
      const refreshTokenEncrypted = tokens.refresh_token
        ? this.encryptionService.encrypt(tokens.refresh_token)
        : null;

      const tokenExpiresAt = tokens.expiry_date ? new Date(tokens.expiry_date) : null;

      // If re-authorizing without new refresh token, preserve existing refresh token
      let existingRefreshToken: string | null = null;
      if (!refreshTokenEncrypted) {
        const existing = await this.prisma.socialAccount.findUnique({
          where: {
            userId_platform_platformAccountId: {
              userId: oauthState.userId,
              platform: Platform.YOUTUBE,
              platformAccountId: channelId,
            },
          },
        });
        existingRefreshToken = existing?.refreshTokenEncrypted || null;
      }

      const account = await this.prisma.socialAccount.upsert({
        where: {
          userId_platform_platformAccountId: {
            userId: oauthState.userId,
            platform: Platform.YOUTUBE,
            platformAccountId: channelId,
          },
        },
        create: {
          userId: oauthState.userId,
          platform: Platform.YOUTUBE,
          platformAccountId: channelId,
          accountName: channelTitle,
          avatarUrl,
          accessTokenEncrypted,
          refreshTokenEncrypted: refreshTokenEncrypted || existingRefreshToken,
          tokenExpiresAt,
          status: AccountStatus.CONNECTED,
          scopes: tokens.scope || 'https://www.googleapis.com/auth/youtube.upload',
          metadata: {
            subscriberCount: channel.statistics?.subscriberCount,
            videoCount: channel.statistics?.videoCount,
            viewCount: channel.statistics?.viewCount,
            customUrl: channel.snippet?.customUrl,
          },
        },
        update: {
          accountName: channelTitle,
          avatarUrl,
          accessTokenEncrypted,
          ...(refreshTokenEncrypted ? { refreshTokenEncrypted } : {}),
          tokenExpiresAt,
          status: AccountStatus.CONNECTED,
          metadata: {
            subscriberCount: channel.statistics?.subscriberCount,
            videoCount: channel.statistics?.videoCount,
            viewCount: channel.statistics?.viewCount,
            customUrl: channel.snippet?.customUrl,
          },
          updatedAt: new Date(),
        },
      });

      this.logger.log(`Successfully connected YouTube Channel '${channelTitle}' (${channelId}) for user ${oauthState.userId}`);
      return {
        success: true,
        account: {
          id: account.id,
          platform: account.platform,
          platformAccountId: account.platformAccountId,
          accountName: account.accountName,
          avatarUrl: account.avatarUrl,
          status: account.status,
        },
      };
    } catch (err) {
      const msg = err.response?.data?.error?.message || err.message;
      this.logger.error(`YouTube OAuth error: ${msg}`);
      throw new BadRequestException(`Google YouTube API authentication failed: ${msg}`);
    }
  }

  /**
   * Generates secure OAuth authorization URL for Threads (Threads API).
   */
  async generateThreadsAuthUrl(userId: string, redirectUriOverride?: string): Promise<{ url: string; state: string }> {
    const appId = this.configService.get<string>('THREADS_APP_ID') || this.configService.get<string>('META_APP_ID');
    const redirectUri = redirectUriOverride || this.configService.get<string>('THREADS_OAUTH_REDIRECT_URI', 'http://localhost:3000/api/social-accounts/threads/callback');

    if (!appId) {
      throw new BadRequestException('THREADS_APP_ID or META_APP_ID is not configured in server environment.');
    }

    const state = crypto.randomBytes(24).toString('hex');
    await this.prisma.oAuthState.create({
      data: {
        state,
        userId,
        platform: Platform.THREADS,
        redirectUri,
        expiresAt: new Date(Date.now() + 15 * 60 * 1000), // 15 mins expiry
      },
    });

    const scopes = ['threads_basic', 'threads_content_publish'].join(',');
    const url = `https://threads.net/oauth/authorize?client_id=${appId}&redirect_uri=${encodeURIComponent(
      redirectUri,
    )}&scope=${encodeURIComponent(scopes)}&response_type=code&state=${state}`;

    return { url, state };
  }

  /**
   * Handles OAuth callback from Threads, exchanges for long-lived user token, and saves Threads profile.
   */
  async handleThreadsCallback(code: string, state: string) {
    const oauthState = await this.validateAndConsumeState(state, Platform.THREADS);

    const appId = this.configService.get<string>('THREADS_APP_ID') || this.configService.get<string>('META_APP_ID');
    const appSecret = this.configService.get<string>('THREADS_APP_SECRET') || this.configService.get<string>('META_APP_SECRET');
    const redirectUri = oauthState.redirectUri;

    try {
      // 1. Exchange authorization code for short-lived Threads user token
      const tokenForm = new URLSearchParams({
        client_id: appId,
        client_secret: appSecret,
        grant_type: 'authorization_code',
        redirect_uri: redirectUri,
        code,
      });

      const tokenRes = await axios.post('https://graph.threads.net/oauth/access_token', tokenForm.toString(), {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      });

      const shortLivedToken = tokenRes.data.access_token;
      const threadsUserId = tokenRes.data.user_id;

      // 2. Exchange short-lived token for 60-day Long-Lived Token
      let longLivedToken = shortLivedToken;
      let tokenExpiresAt: Date | null = null;

      try {
        const longLivedRes = await axios.get('https://graph.threads.net/access_token', {
          params: {
            grant_type: 'th_exchange_token',
            client_secret: appSecret,
            access_token: shortLivedToken,
          },
        });
        if (longLivedRes.data.access_token) {
          longLivedToken = longLivedRes.data.access_token;
          const expiresIn = longLivedRes.data.expires_in || 5184000;
          tokenExpiresAt = new Date(Date.now() + expiresIn * 1000);
        }
      } catch (err) {
        this.logger.warn(`Could not exchange Threads long-lived token, using short-lived: ${err.message}`);
      }

      // 3. Query Threads User Profile
      let username = `threads_user_${threadsUserId}`;
      let avatarUrl: string | null = null;
      try {
        const profileRes = await axios.get('https://graph.threads.net/v1.0/me', {
          params: {
            fields: 'id,username,name,threads_profile_picture_url,threads_biography',
            access_token: longLivedToken,
          },
        });
        if (profileRes.data) {
          username = profileRes.data.username || profileRes.data.name || username;
          avatarUrl = profileRes.data.threads_profile_picture_url || null;
        }
      } catch (err) {
        this.logger.warn(`Could not fetch Threads profile info: ${err.message}`);
      }

      const accessTokenEncrypted = this.encryptionService.encrypt(longLivedToken);

      const account = await this.prisma.socialAccount.upsert({
        where: {
          userId_platform_platformAccountId: {
            userId: oauthState.userId,
            platform: Platform.THREADS,
            platformAccountId: String(threadsUserId),
          },
        },
        create: {
          userId: oauthState.userId,
          platform: Platform.THREADS,
          platformAccountId: String(threadsUserId),
          accountName: `@${username.replace(/^@/, '')}`,
          avatarUrl,
          accessTokenEncrypted,
          tokenExpiresAt,
          status: AccountStatus.CONNECTED,
          scopes: 'threads_basic,threads_content_publish',
          metadata: {
            threadsUserId: String(threadsUserId),
            username,
          },
        },
        update: {
          accountName: `@${username.replace(/^@/, '')}`,
          avatarUrl,
          accessTokenEncrypted,
          tokenExpiresAt,
          status: AccountStatus.CONNECTED,
          updatedAt: new Date(),
        },
      });

      this.logger.log(`Successfully connected Threads Account '@${username}' (${threadsUserId}) for user ${oauthState.userId}`);
      return {
        success: true,
        account: {
          id: account.id,
          platform: account.platform,
          platformAccountId: account.platformAccountId,
          accountName: account.accountName,
          avatarUrl: account.avatarUrl,
          status: account.status,
        },
      };
    } catch (err) {
      const msg = err.response?.data?.error_message || err.response?.data?.error?.message || err.message;
      this.logger.error(`Threads OAuth error: ${msg}`);
      throw new BadRequestException(`Meta Threads API authentication failed: ${msg}`);
    }
  }

  /**
   * Connects Threads directly using token from Meta User Token Generator (Bypasses HTTPS requirement on localhost)
   */
  async connectThreadsWithToken(userId: string, token: string) {
    const appSecret = this.configService.get<string>('THREADS_APP_SECRET') || this.configService.get<string>('META_APP_SECRET');
    let longLivedToken = token.trim();
    let tokenExpiresAt: Date | null = null;

    try {
      const longLivedRes = await axios.get('https://graph.threads.net/access_token', {
        params: {
          grant_type: 'th_exchange_token',
          client_secret: appSecret,
          access_token: longLivedToken,
        },
      });
      if (longLivedRes.data?.access_token) {
        longLivedToken = longLivedRes.data.access_token;
        const expiresIn = longLivedRes.data.expires_in || 5184000;
        tokenExpiresAt = new Date(Date.now() + expiresIn * 1000);
      }
    } catch (err) {
      this.logger.warn(`Could not exchange Threads token to long-lived: ${err.message}`);
    }

    try {
      const profileRes = await axios.get('https://graph.threads.net/v1.0/me', {
        params: {
          fields: 'id,username,name,threads_profile_picture_url',
          access_token: longLivedToken,
        },
      });

      const profile = profileRes.data;
      const threadsUserId = profile.id;
      const username = profile.username || profile.name || `threads_${threadsUserId}`;
      const avatarUrl = profile.threads_profile_picture_url || null;
      const accessTokenEncrypted = this.encryptionService.encrypt(longLivedToken);

      const account = await this.prisma.socialAccount.upsert({
        where: {
          userId_platform_platformAccountId: {
            userId,
            platform: Platform.THREADS,
            platformAccountId: String(threadsUserId),
          },
        },
        create: {
          userId,
          platform: Platform.THREADS,
          platformAccountId: String(threadsUserId),
          accountName: `@${username.replace(/^@/, '')}`,
          avatarUrl,
          accessTokenEncrypted,
          tokenExpiresAt,
          status: AccountStatus.CONNECTED,
          scopes: 'threads_basic,threads_content_publish',
          metadata: {
            threadsUserId: String(threadsUserId),
            username,
          },
        },
        update: {
          accountName: `@${username.replace(/^@/, '')}`,
          avatarUrl,
          accessTokenEncrypted,
          tokenExpiresAt,
          status: AccountStatus.CONNECTED,
          updatedAt: new Date(),
        },
      });

      this.logger.log(`Successfully connected Threads Account '@${username}' via Token Generator for user ${userId}`);
      return {
        success: true,
        account: {
          id: account.id,
          platform: account.platform,
          platformAccountId: account.platformAccountId,
          accountName: account.accountName,
          avatarUrl: account.avatarUrl,
          status: account.status,
        },
      };
    } catch (err) {
      const msg = err.response?.data?.error?.message || err.message;
      this.logger.error(`Threads Token Error: ${msg}`);
      throw new BadRequestException(`Threads token verification failed: ${msg}`);
    }
  }

  /**
   * Refreshes access token for Google / YouTube accounts.
   */
  async refreshAccountToken(accountId: string): Promise<string> {
    const account = await this.prisma.socialAccount.findUnique({
      where: { id: accountId },
    });
    if (!account) {
      throw new NotFoundException(`Social account ${accountId} not found.`);
    }

    if (account.platform === Platform.YOUTUBE) {
      if (!account.refreshTokenEncrypted) {
        await this.prisma.socialAccount.update({
          where: { id: accountId },
          data: { status: AccountStatus.EXPIRED },
        });
        throw new UnauthorizedException('No refresh token available. Reconnection required.');
      }

      const refreshToken = this.encryptionService.decrypt(account.refreshTokenEncrypted);
      const clientId = this.configService.get<string>('GOOGLE_CLIENT_ID');
      const clientSecret = this.configService.get<string>('GOOGLE_CLIENT_SECRET');

      const oauth2Client = new google.auth.OAuth2(clientId, clientSecret);
      oauth2Client.setCredentials({ refresh_token: refreshToken });

      try {
        const { credentials } = await oauth2Client.refreshAccessToken();
        const newAccessToken = credentials.access_token;
        const newExpiry = credentials.expiry_date ? new Date(credentials.expiry_date) : null;

        await this.prisma.socialAccount.update({
          where: { id: accountId },
          data: {
            accessTokenEncrypted: this.encryptionService.encrypt(newAccessToken),
            tokenExpiresAt: newExpiry,
            status: AccountStatus.CONNECTED,
            updatedAt: new Date(),
          },
        });

        this.logger.log(`Successfully refreshed YouTube access token for account ${account.accountName}`);
        return newAccessToken;
      } catch (err) {
        this.logger.error(`Failed to refresh YouTube token for account ${accountId}: ${err.message}`);
        await this.prisma.socialAccount.update({
          where: { id: accountId },
          data: { status: AccountStatus.REVOKED },
        });
        throw new UnauthorizedException(`YouTube token revoked or expired: ${err.message}`);
      }
    }

    // For Facebook, Page Access tokens from long-lived user tokens do not expire
    return this.encryptionService.decrypt(account.accessTokenEncrypted);
  }

  /**
   * Disconnects / deletes a social account.
   */
  async disconnectAccount(userId: string, id: string) {
    const account = await this.prisma.socialAccount.findFirst({
      where: { id, userId },
    });
    if (!account) {
      throw new NotFoundException(`Social account with id '${id}' not found.`);
    }

    await this.prisma.socialAccount.delete({
      where: { id },
    });

    return { success: true, message: `Account '${account.accountName}' disconnected successfully.` };
  }

  /**
   * Internal helper: returns decrypted tokens for publishing adapter use.
   */
  async getDecryptedTokens(account: SocialAccount): Promise<{ accessToken: string; refreshToken?: string }> {
    // Check if token is expired (for YouTube)
    if (account.platform === Platform.YOUTUBE && account.tokenExpiresAt) {
      const isExpiringSoon = account.tokenExpiresAt.getTime() - Date.now() < 5 * 60 * 1000;
      if (isExpiringSoon) {
        const freshToken = await this.refreshAccountToken(account.id);
        return { accessToken: freshToken };
      }
    }

    const accessToken = this.encryptionService.decrypt(account.accessTokenEncrypted);
    const refreshToken = account.refreshTokenEncrypted
      ? this.encryptionService.decrypt(account.refreshTokenEncrypted)
      : undefined;

    return { accessToken, refreshToken };
  }

  private async validateAndConsumeState(state: string, expectedPlatform: Platform) {
    if (!state) {
      throw new BadRequestException('OAuth state parameter is missing.');
    }
    const record = await this.prisma.oAuthState.findUnique({
      where: { state },
    });
    if (!record || record.platform !== expectedPlatform) {
      throw new BadRequestException('Invalid or expired OAuth state parameter (CSRF protection failed).');
    }
    if (record.expiresAt < new Date()) {
      await this.prisma.oAuthState.delete({ where: { state } });
      throw new BadRequestException('OAuth state parameter has expired. Please initiate connection again.');
    }

    await this.prisma.oAuthState.delete({ where: { state } });
    return record;
  }
}
