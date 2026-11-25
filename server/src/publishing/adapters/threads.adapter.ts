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
export class ThreadsAdapter implements SocialPlatformAdapter {
  readonly platform = Platform.THREADS;
  private readonly logger = new Logger(ThreadsAdapter.name);

  constructor(
    private readonly configService: ConfigService,
    private readonly encryptionService: EncryptionService,
    private readonly storageService: StorageService,
  ) {}

  async validateContent(content: Content, media?: Media[]): Promise<ValidationResult> {
    const errors: string[] = [];
    const text = [content.caption || content.title || '', content.hashtags || ''].filter(Boolean).join('\n\n');

    if (!text && (!media || media.length === 0)) {
      errors.push('Threads post must contain either text content or at least one media item.');
    }

    if (text.length > 500) {
      errors.push(`Threads text cannot exceed 500 characters. Currently: ${text.length} characters.`);
    }

    if (media && media.length > 0) {
      for (const m of media) {
        if (!m.mimeType.startsWith('image/') && !m.mimeType.startsWith('video/')) {
          errors.push(`Unsupported media MIME type for Threads: ${m.mimeType}`);
        }
        if (m.mimeType.startsWith('video/') && m.fileSize > 500 * 1024 * 1024) {
          errors.push('Video exceeds Threads maximum size limit (500 MB).');
        }
      }
    }

    return { valid: errors.length === 0, errors };
  }

  async publishPost(account: SocialAccount, content: Content, media?: Media[]): Promise<PublishResult> {
    const threadsUserId = account.platformAccountId;
    const userAccessToken = this.encryptionService.decrypt(account.accessTokenEncrypted);

    // Prepare text message (max 500 chars for Threads)
    let messageText = content.caption || content.title || '';
    if (content.hashtags) {
      messageText = messageText ? `${messageText}\n\n${content.hashtags}` : content.hashtags;
    }
    if (messageText.length > 500) {
      messageText = messageText.substring(0, 497) + '...';
    }

    const firstMedia = media && media.length > 0 ? media[0] : null;

    try {
      // Step 1: Create Media Container on Threads
      const containerParams: Record<string, any> = {
        access_token: userAccessToken,
      };

      if (messageText) {
        containerParams.text = messageText;
      }

      if (firstMedia) {
        const publicUrl = this.storageService.getPublicUrl(firstMedia.storageKey);
        if (firstMedia.mimeType.startsWith('video/')) {
          containerParams.media_type = 'VIDEO';
          containerParams.video_url = publicUrl;
        } else {
          containerParams.media_type = 'IMAGE';
          containerParams.image_url = publicUrl;
        }
      } else {
        containerParams.media_type = 'TEXT';
      }

      this.logger.log(`Creating Threads container for user ${threadsUserId} (Type: ${containerParams.media_type})...`);

      const containerRes = await axios.post(
        `https://graph.threads.net/v1.0/${threadsUserId}/threads`,
        null,
        { params: containerParams },
      );

      const containerId = containerRes.data?.id;
      if (!containerId) {
        throw new Error(`Failed to create Threads container: ${JSON.stringify(containerRes.data)}`);
      }

      this.logger.log(`Threads container created: ${containerId}. Checking readiness...`);

      // Step 2: If media (video), wait until status is FINISHED
      if (firstMedia) {
        let isReady = false;
        let attempts = 0;
        const maxAttempts = 20;

        while (!isReady && attempts < maxAttempts) {
          await new Promise((r) => setTimeout(r, 3000));
          attempts++;

          try {
            const statusRes = await axios.get(`https://graph.threads.net/v1.0/${containerId}`, {
              params: {
                fields: 'status,error_message',
                access_token: userAccessToken,
              },
            });

            const status = statusRes.data?.status;
            this.logger.log(`Threads container ${containerId} status: ${status} (attempt ${attempts}/${maxAttempts})`);

            if (status === 'FINISHED') {
              isReady = true;
            } else if (status === 'ERROR') {
              throw new Error(`Threads media processing error: ${statusRes.data?.error_message || 'Unknown processing error'}`);
            }
          } catch (pollErr) {
            this.logger.warn(`Polling container status warning: ${pollErr.message}`);
          }
        }
      }

      // Step 3: Publish the Container
      this.logger.log(`Publishing Threads container ${containerId}...`);
      const publishRes = await axios.post(
        `https://graph.threads.net/v1.0/${threadsUserId}/threads_publish`,
        null,
        {
          params: {
            creation_id: containerId,
            access_token: userAccessToken,
          },
        },
      );

      const postId = publishRes.data?.id;
      const cleanUsername = (account.accountName || '').replace(/^@/, '');
      const externalPostUrl = `https://www.threads.net/@${cleanUsername}/post/${postId}`;

      this.logger.log(`Threads post published successfully: ${externalPostUrl}`);

      return {
        success: true,
        externalPostId: postId,
        externalPostUrl,
        metadata: {
          containerId,
          postId,
          publishedAt: new Date().toISOString(),
        },
      };
    } catch (err) {
      const errResponse = err.response?.data;
      const msg = errResponse?.error?.message || errResponse?.error_message || err.message;
      const code = errResponse?.error?.code;

      this.logger.error(`Threads Publish Error (Code: ${code}): ${msg}`);

      if (code === 190) {
        throw new UnauthorizedException(`Threads access token expired or revoked. Re-authentication required.`);
      }

      throw new BadRequestException(`Threads API publishing error: ${msg}`);
    }
  }
}
