import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Platform, SocialAccount, Content, Media } from '@prisma/client';
import {
  SocialPlatformAdapter,
  PublishResult,
  ValidationResult,
  TokenRefreshResult,
} from './social-platform.adapter.interface';
import { EncryptionService } from '../../common/services/encryption.service';
import { StorageService } from '../../storage/storage.service';
import { google } from 'googleapis';
import { Readable } from 'stream';

@Injectable()
export class YouTubeAdapter implements SocialPlatformAdapter {
  readonly platform = Platform.YOUTUBE;
  private readonly logger = new Logger(YouTubeAdapter.name);

  constructor(
    private readonly configService: ConfigService,
    private readonly encryptionService: EncryptionService,
    private readonly storageService: StorageService,
  ) {}

  async validateContent(content: Content, media?: Media[]): Promise<ValidationResult> {
    const errors: string[] = [];

    // YouTube requires a video file
    const video = media?.find((m) => m.mimeType.startsWith('video/'));
    if (!video) {
      errors.push('YouTube publishing requires at least one video file attachment.');
    }

    const title = content.title || content.caption || '';
    if (!title.trim()) {
      errors.push('YouTube video title is required.');
    } else if (title.length > 100) {
      errors.push(`YouTube video title exceeds maximum allowed length of 100 characters (Current: ${title.length}).`);
    }

    const description = content.description || content.caption || '';
    if (description.length > 5000) {
      errors.push(`YouTube video description exceeds maximum allowed length of 5000 characters (Current: ${description.length}).`);
    }

    return { valid: errors.length === 0, errors };
  }

  async publishPost(account: SocialAccount, content: Content, media?: Media[]): Promise<PublishResult> {
    const video = media?.find((m) => m.mimeType.startsWith('video/'));
    if (!video) {
      return {
        success: false,
        errorCode: 'MISSING_VIDEO',
        errorMessage: 'YouTube requires an attached video file.',
      };
    }

    const clientId = this.configService.get<string>('GOOGLE_CLIENT_ID');
    const clientSecret = this.configService.get<string>('GOOGLE_CLIENT_SECRET');

    const accessToken = this.encryptionService.decrypt(account.accessTokenEncrypted);
    const refreshToken = account.refreshTokenEncrypted
      ? this.encryptionService.decrypt(account.refreshTokenEncrypted)
      : undefined;

    const oauth2Client = new google.auth.OAuth2(clientId, clientSecret);
    oauth2Client.setCredentials({
      access_token: accessToken,
      refresh_token: refreshToken,
    });

    const youtube = google.youtube({ version: 'v3', auth: oauth2Client });

    // 1. Prepare Title, Description & Tags
    let title = (content.title || content.caption || 'New Video').trim();
    if (title.length > 100) {
      title = title.substring(0, 97) + '...';
    }

    let description = content.description || content.caption || '';
    if (content.hashtags) {
      description = description ? `${description}\n\n${content.hashtags}` : content.hashtags;
    }

    // Extract tags from hashtags string
    const tags: string[] = [];
    if (content.hashtags) {
      const extracted = content.hashtags
        .split(/\s+/)
        .filter((t) => t.startsWith('#'))
        .map((t) => t.replace('#', '').trim())
        .filter((t) => t.length > 0);
      tags.push(...extracted);
    }

    // 2. YouTube Shorts Detection & Optimization
    // YouTube Shorts: Vertical video (9:16 or aspect ratio <= 1.0) and duration <= 60 seconds
    const isShorts =
      (video.duration && video.duration <= 60 && video.width && video.height && video.height >= video.width) ||
      title.toLowerCase().includes('#shorts') ||
      description.toLowerCase().includes('#shorts');

    if (isShorts) {
      if (!title.toLowerCase().includes('#shorts') && title.length <= 92) {
        title = `${title} #Shorts`;
      }
      if (!description.toLowerCase().includes('#shorts')) {
        description = `${description}\n\n#Shorts`;
      }
      if (!tags.includes('Shorts')) {
        tags.push('Shorts');
      }
      this.logger.log(`Detected YouTube Shorts video for content '${title}' (duration: ${video.duration}s)`);
    }

    // 3. Privacy Status & Scheduled Publishing (publishAt)
    let privacyStatus = 'public';
    let publishAt: string | undefined = undefined;

    if (content.scheduledAt && new Date(content.scheduledAt) > new Date()) {
      // YouTube requirement: status.publishAt is ONLY allowed when status.privacyStatus is 'private'
      privacyStatus = 'private';
      publishAt = new Date(content.scheduledAt).toISOString();
      this.logger.log(`Scheduling YouTube video for future publication at ${publishAt}`);
    }

    // 4. Stream Video File from Storage
    try {
      const videoBuffer = await this.storageService.getFileBuffer(video.storageKey);
      const mediaStream = Readable.from(videoBuffer);

      this.logger.log(`Starting YouTube video upload: "${title}" (${(videoBuffer.length / 1024 / 1024).toFixed(2)} MB)`);

      const res = await youtube.videos.insert({
        part: ['snippet', 'status'],
        requestBody: {
          snippet: {
            title,
            description,
            tags,
            categoryId: '22', // People & Blogs
          },
          status: {
            privacyStatus,
            ...(publishAt ? { publishAt } : {}),
            selfDeclaredMadeForKids: false,
          },
        },
        media: {
          mimeType: video.mimeType,
          body: mediaStream,
        },
      });

      const videoId = res.data.id;
      if (!videoId) {
        throw new Error('YouTube did not return a valid video ID.');
      }

      const externalPostUrl = isShorts
        ? `https://www.youtube.com/shorts/${videoId}`
        : `https://www.youtube.com/watch?v=${videoId}`;

      this.logger.log(`YouTube video published successfully: ${externalPostUrl}`);

      return {
        success: true,
        externalPostId: videoId,
        externalPostUrl,
        metadata: {
          id: videoId,
          isShorts,
          privacyStatus,
          publishAt,
          etag: res.data.etag,
        },
      };
    } catch (err) {
      const ytError = err.response?.data?.error;
      const errorCode = ytError?.errors?.[0]?.reason || err.code || 'YT_API_ERROR';
      const errorMessage = ytError?.message || err.message || 'Unknown YouTube Data API v3 error';

      this.logger.error(`YouTube Publish Error (Reason: ${errorCode}): ${errorMessage}`);
      return {
        success: false,
        errorCode,
        errorMessage: `YouTube API: ${errorMessage} (${errorCode})`,
      };
    }
  }

  async refreshToken(account: SocialAccount): Promise<TokenRefreshResult> {
    if (!account.refreshTokenEncrypted) {
      return { refreshed: false, errorMessage: 'No refresh token stored.' };
    }

    const clientId = this.configService.get<string>('GOOGLE_CLIENT_ID');
    const clientSecret = this.configService.get<string>('GOOGLE_CLIENT_SECRET');
    const refreshToken = this.encryptionService.decrypt(account.refreshTokenEncrypted);

    const oauth2Client = new google.auth.OAuth2(clientId, clientSecret);
    oauth2Client.setCredentials({ refresh_token: refreshToken });

    try {
      const { credentials } = await oauth2Client.refreshAccessToken();
      return {
        refreshed: true,
        accessToken: credentials.access_token,
        tokenExpiresAt: credentials.expiry_date ? new Date(credentials.expiry_date) : undefined,
      };
    } catch (err) {
      return { refreshed: false, errorMessage: err.message };
    }
  }
}
