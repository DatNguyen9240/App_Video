import { Injectable, Logger } from '@nestjs/common';
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
import * as FormData from 'form-data';

@Injectable()
export class FacebookAdapter implements SocialPlatformAdapter {
  readonly platform = Platform.FACEBOOK;
  private readonly logger = new Logger(FacebookAdapter.name);

  constructor(
    private readonly configService: ConfigService,
    private readonly encryptionService: EncryptionService,
    private readonly storageService: StorageService,
  ) {}

  async validateContent(content: Content, media?: Media[]): Promise<ValidationResult> {
    const errors: string[] = [];
    const textLength = (content.caption || content.title || content.description || '').length;

    if (textLength === 0 && (!media || media.length === 0)) {
      errors.push('Facebook post must contain either text content or at least one media attachment.');
    }

    if (media && media.length > 0) {
      for (const m of media) {
        if (!m.mimeType.startsWith('image/') && !m.mimeType.startsWith('video/')) {
          errors.push(`Unsupported media MIME type for Facebook: ${m.mimeType}`);
        }
        if (m.mimeType.startsWith('video/') && m.fileSize > 1024 * 1024 * 1024) {
          errors.push(`Video exceeds Facebook maximum upload size limit (1 GB).`);
        }
      }
    }

    return { valid: errors.length === 0, errors };
  }

  async publishPost(account: SocialAccount, content: Content, media?: Media[]): Promise<PublishResult> {
    const apiVersion = this.configService.get<string>('META_API_VERSION', 'v21.0');
    const pageId = account.platformAccountId;
    const pageAccessToken = this.encryptionService.decrypt(account.accessTokenEncrypted);

    // Prepare full text message
    let messageText = content.caption || content.title || '';
    if (content.description && content.description !== content.caption) {
      messageText = messageText ? `${messageText}\n\n${content.description}` : content.description;
    }
    if (content.hashtags) {
      messageText = messageText ? `${messageText}\n\n${content.hashtags}` : content.hashtags;
    }

    try {
      const videoMedia = media?.find((m) => m.mimeType.startsWith('video/'));
      const imageMedia = media?.find((m) => m.mimeType.startsWith('image/'));

      // ─── 1. VIDEO POST (Regular Video or Facebook Reel) ────────────────────
      if (videoMedia) {
        const isReelEligible =
          videoMedia.duration &&
          videoMedia.duration <= 90 &&
          videoMedia.width &&
          videoMedia.height &&
          videoMedia.height >= videoMedia.width;

        if (isReelEligible) {
          return await this.publishFacebookReel(apiVersion, pageId, pageAccessToken, messageText, videoMedia);
        } else {
          return await this.publishFacebookRegularVideo(apiVersion, pageId, pageAccessToken, messageText, videoMedia, content.title);
        }
      }

      // ─── 2. PHOTO POST ─────────────────────────────────────────────────────
      if (imageMedia) {
        return await this.publishFacebookPhoto(apiVersion, pageId, pageAccessToken, messageText, imageMedia);
      }

      // ─── 3. TEXT-ONLY POST ─────────────────────────────────────────────────
      return await this.publishFacebookText(apiVersion, pageId, pageAccessToken, messageText);
    } catch (err) {
      const metaErr = err.response?.data?.error;
      const errorCode = metaErr?.code?.toString() || err.code || 'FB_API_ERROR';
      const errorMessage = metaErr?.message || err.message || 'Unknown Meta Graph API error';
      const subcode = metaErr?.error_subcode;

      this.logger.error(`Facebook Publish Error (Code: ${errorCode}, Subcode: ${subcode}): ${errorMessage}`);
      return {
        success: false,
        errorCode,
        errorMessage: `Facebook API: ${errorMessage} (Code: ${errorCode})`,
      };
    }
  }

  private async publishFacebookText(apiVersion: string, pageId: string, pageAccessToken: string, message: string): Promise<PublishResult> {
    const url = `https://graph.facebook.com/${apiVersion}/${pageId}/feed`;
    const res = await axios.post(url, null, {
      params: {
        access_token: pageAccessToken,
        message,
      },
    });

    const postId = res.data.id; // Format: {pageId}_{postId}
    return {
      success: true,
      externalPostId: postId,
      externalPostUrl: `https://www.facebook.com/${postId.replace('_', '/posts/')}`,
      metadata: res.data,
    };
  }

  private async publishFacebookPhoto(
    apiVersion: string,
    pageId: string,
    pageAccessToken: string,
    caption: string,
    media: Media,
  ): Promise<PublishResult> {
    const url = `https://graph.facebook.com/${apiVersion}/${pageId}/photos`;
    const buffer = await this.storageService.getFileBuffer(media.storageKey);

    const form = new FormData();
    form.append('access_token', pageAccessToken);
    form.append('caption', caption);
    form.append('source', buffer, {
      filename: media.originalName || 'image.jpg',
      contentType: media.mimeType,
    });

    const res = await axios.post(url, form, {
      headers: form.getHeaders(),
      maxContentLength: Infinity,
      maxBodyLength: Infinity,
    });

    const photoId = res.data.id;
    const postId = res.data.post_id || photoId;
    return {
      success: true,
      externalPostId: postId,
      externalPostUrl: `https://www.facebook.com/${photoId}`,
      metadata: res.data,
    };
  }

  private async publishFacebookRegularVideo(
    apiVersion: string,
    pageId: string,
    pageAccessToken: string,
    description: string,
    media: Media,
    title?: string,
  ): Promise<PublishResult> {
    const url = `https://graph-video.facebook.com/${apiVersion}/${pageId}/videos`;
    const buffer = await this.storageService.getFileBuffer(media.storageKey);

    const form = new FormData();
    form.append('access_token', pageAccessToken);
    form.append('description', description);
    if (title) {
      form.append('title', title);
    }
    form.append('source', buffer, {
      filename: media.originalName || 'video.mp4',
      contentType: media.mimeType,
    });

    const res = await axios.post(url, form, {
      headers: form.getHeaders(),
      maxContentLength: Infinity,
      maxBodyLength: Infinity,
      timeout: 120000,
    });

    const videoId = res.data.id;
    return {
      success: true,
      externalPostId: videoId,
      externalPostUrl: `https://www.facebook.com/${pageId}/videos/${videoId}`,
      metadata: res.data,
    };
  }

  /**
   * Facebook Reels Publishing API 3-step workflow:
   * 1. Initialize Reel upload session: POST /{page_id}/video_reels
   * 2. Upload video binary chunk: POST {upload_url}
   * 3. Finish / Publish Reel: POST /{page_id}/video_reels with upload_phase=finish
   */
  private async publishFacebookReel(
    apiVersion: string,
    pageId: string,
    pageAccessToken: string,
    description: string,
    media: Media,
  ): Promise<PublishResult> {
    this.logger.log(`Initiating Facebook Reel publishing workflow for page ${pageId}`);

    // Step 1: Initialize upload session
    const initRes = await axios.post(
      `https://graph.facebook.com/${apiVersion}/${pageId}/video_reels`,
      null,
      {
        params: {
          access_token: pageAccessToken,
          upload_phase: 'start',
        },
      },
    );

    const videoId = initRes.data.video_id;
    const uploadUrl = initRes.data.upload_url;

    // Step 2: Upload binary video data to upload_url
    const buffer = await this.storageService.getFileBuffer(media.storageKey);
    await axios.post(uploadUrl, buffer, {
      headers: {
        Authorization: `OAuth ${pageAccessToken}`,
        offset: '0',
        file_size: buffer.length.toString(),
        'Content-Type': 'application/octet-stream',
      },
      maxContentLength: Infinity,
      maxBodyLength: Infinity,
      timeout: 180000,
    });

    // Step 3: Finish and publish
    const publishRes = await axios.post(
      `https://graph.facebook.com/${apiVersion}/${pageId}/video_reels`,
      null,
      {
        params: {
          access_token: pageAccessToken,
          upload_phase: 'finish',
          video_id: videoId,
          video_state: 'PUBLISHED',
          description,
        },
      },
    );

    return {
      success: true,
      externalPostId: videoId,
      externalPostUrl: `https://www.facebook.com/reel/${videoId}`,
      metadata: publishRes.data,
    };
  }
}
