import { Injectable, Logger, BadRequestException, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Platform, SocialAccount, Content, Media } from '@prisma/client';
import {
  SocialPlatformAdapter,
  PublishResult,
  ValidationResult,
} from './social-platform.adapter.interface';
import { EncryptionService } from '../../common/services/encryption.service';
import { StorageService } from '../../storage/storage.service';
import axios from 'axios';

@Injectable()
export class TikTokAdapter implements SocialPlatformAdapter {
  readonly platform = Platform.TIKTOK;
  private readonly logger = new Logger(TikTokAdapter.name);

  constructor(
    private readonly configService: ConfigService,
    private readonly encryptionService: EncryptionService,
    private readonly storageService: StorageService,
  ) {}

  async validateContent(content: Content, media?: Media[]): Promise<ValidationResult> {
    const errors: string[] = [];
    const text = [content.caption || content.title || '', content.hashtags || ''].filter(Boolean).join(' ');

    if (!media || media.length === 0) {
      errors.push('TikTok requires at least one video attachment to publish.');
    } else {
      const firstMedia = media[0];
      if (!firstMedia.mimeType.startsWith('video/')) {
        errors.push(`TikTok only supports video publishing. Received: ${firstMedia.mimeType}`);
      }
      if (firstMedia.fileSize > 1024 * 1024 * 1024) {
        errors.push('Video exceeds TikTok maximum file size limit (1 GB).');
      }
    }

    if (text.length > 2200) {
      errors.push(`TikTok caption cannot exceed 2200 characters. Currently: ${text.length} characters.`);
    }

    return { valid: errors.length === 0, errors };
  }

  async publishPost(account: SocialAccount, content: Content, media?: Media[]): Promise<PublishResult> {
    const userAccessToken = this.encryptionService.decrypt(account.accessTokenEncrypted);

    if (!media || media.length === 0) {
      throw new BadRequestException('No video media provided for TikTok publish.');
    }

    const videoMedia = media.find((m) => m.mimeType.startsWith('video/')) || media[0];
    const postTitle = [content.caption || content.title || 'Video by AutoPub', content.hashtags || '']
      .filter(Boolean)
      .join(' ')
      .slice(0, 2200);

    const publicUrl = this.storageService.getPublicUrl(videoMedia.storageKey);
    const isLocalhost = publicUrl.includes('localhost') || publicUrl.includes('127.0.0.1');

    try {
      this.logger.log(`Starting TikTok video publish for account: ${account.accountName} (${account.platformAccountId})`);

      // Determine upload strategy: FILE_UPLOAD (recommended, works anywhere including local dev) vs PULL_FROM_URL
      if (isLocalhost) {
        // Read binary buffer from local/minio storage for direct chunked upload
        const videoBuffer = await this.storageService.getFileBuffer(videoMedia.storageKey);
        const videoSize = videoBuffer.length;

        this.logger.log(`Initiating TikTok FILE_UPLOAD for video size ${videoSize} bytes...`);

        const initRes = await axios.post(
          'https://open.tiktokapis.com/v2/post/publish/video/init/',
          {
            post_info: {
              title: postTitle,
              privacy_level: 'PUBLIC_TO_EVERYONE',
              disable_duet: false,
              disable_comment: false,
              disable_stitch: false,
              video_cover_timestamp_ms: 1000,
            },
            source_info: {
              source: 'FILE_UPLOAD',
              video_size: videoSize,
              chunk_size: videoSize,
              total_chunk_count: 1,
            },
          },
          {
            headers: {
              Authorization: `Bearer ${userAccessToken}`,
              'Content-Type': 'application/json; charset=UTF-8',
            },
          },
        );

        const initData = initRes.data?.data || initRes.data;
        const publishId = initData?.publish_id;
        const uploadUrl = initData?.upload_url;

        if (!publishId || !uploadUrl) {
          throw new Error(`Failed to initialize TikTok video upload: ${JSON.stringify(initRes.data)}`);
        }

        this.logger.log(`Uploading video binary to TikTok upload_url (publish_id: ${publishId})...`);

        await axios.put(uploadUrl, videoBuffer, {
          headers: {
            'Content-Range': `bytes 0-${videoSize - 1}/${videoSize}`,
            'Content-Type': 'video/mp4',
            'Content-Length': videoSize,
          },
          maxBodyLength: Infinity,
          maxContentLength: Infinity,
        });

        this.logger.log(`Video uploaded to TikTok successfully. Polling publish status...`);

        const finalStatus = await this.pollPublishStatus(publishId, userAccessToken);

        return {
          success: true,
          externalPostId: publishId,
          externalPostUrl: `https://www.tiktok.com/@${(account.accountName || '').replace(/^@/, '')}`,
          metadata: {
            publishId,
            status: finalStatus,
            publishedAt: new Date().toISOString(),
          },
        };
      } else {
        // Use PULL_FROM_URL for public internet URLs
        this.logger.log(`Initiating TikTok PULL_FROM_URL with video URL: ${publicUrl}`);

        const initRes = await axios.post(
          'https://open.tiktokapis.com/v2/post/publish/video/init/',
          {
            post_info: {
              title: postTitle,
              privacy_level: 'PUBLIC_TO_EVERYONE',
              disable_duet: false,
              disable_comment: false,
              disable_stitch: false,
              video_cover_timestamp_ms: 1000,
            },
            source_info: {
              source: 'PULL_FROM_URL',
              video_url: publicUrl,
            },
          },
          {
            headers: {
              Authorization: `Bearer ${userAccessToken}`,
              'Content-Type': 'application/json; charset=UTF-8',
            },
          },
        );

        const initData = initRes.data?.data || initRes.data;
        const publishId = initData?.publish_id;

        if (!publishId) {
          throw new Error(`Failed to initialize TikTok PULL_FROM_URL: ${JSON.stringify(initRes.data)}`);
        }

        const finalStatus = await this.pollPublishStatus(publishId, userAccessToken);

        return {
          success: true,
          externalPostId: publishId,
          externalPostUrl: `https://www.tiktok.com/@${(account.accountName || '').replace(/^@/, '')}`,
          metadata: {
            publishId,
            status: finalStatus,
            publishedAt: new Date().toISOString(),
          },
        };
      }
    } catch (err: any) {
      const errResponse = err.response?.data;
      const msg = errResponse?.error?.message || errResponse?.message || err.message;
      const code = errResponse?.error?.code;

      this.logger.error(`TikTok Publish Error (Code: ${code}): ${msg}`);

      if (code === 'access_token_invalid' || err.response?.status === 401) {
        throw new UnauthorizedException(`TikTok access token is invalid or expired. Reconnection required.`);
      }

      throw new BadRequestException(`TikTok API publishing error: ${msg}`);
    }
  }

  private async pollPublishStatus(publishId: string, accessToken: string): Promise<string> {
    let attempts = 0;
    const maxAttempts = 15;

    while (attempts < maxAttempts) {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      attempts++;

      try {
        const res = await axios.post(
          'https://open.tiktokapis.com/v2/post/publish/status/fetch/',
          { publish_id: publishId },
          {
            headers: {
              Authorization: `Bearer ${accessToken}`,
              'Content-Type': 'application/json; charset=UTF-8',
            },
          },
        );

        const statusData = res.data?.data || res.data;
        const status = statusData?.status;
        this.logger.log(`TikTok publish status for ${publishId}: ${status} (attempt ${attempts}/${maxAttempts})`);

        if (status === 'SUCCESS_POST_TO_DRAFT' || status === 'SUCCESS_POST_TO_FEED' || status === 'SUCCESS') {
          return status;
        }

        if (status === 'FAILED') {
          const failReason = statusData?.fail_reason || 'Unknown error during TikTok publishing';
          throw new Error(`TikTok publishing failed: ${failReason}`);
        }
      } catch (err: any) {
        this.logger.warn(`Polling TikTok publish status warning: ${err.message}`);
        if (err.message.includes('TikTok publishing failed')) {
          throw err;
        }
      }
    }

    return 'PROCESSING';
  }
}
