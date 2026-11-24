import { Platform, SocialAccount, Content, Media } from '@prisma/client';

export interface PublishResult {
  success: boolean;
  externalPostId?: string;
  externalPostUrl?: string;
  metadata?: any;
  errorCode?: string;
  errorMessage?: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export interface TokenRefreshResult {
  refreshed: boolean;
  accessToken?: string;
  tokenExpiresAt?: Date;
  errorMessage?: string;
}

export interface SocialPlatformAdapter {
  readonly platform: Platform;

  /**
   * Validates content and attached media against platform-specific constraints.
   */
  validateContent(content: Content, media?: Media[]): Promise<ValidationResult>;

  /**
   * Publishes post/media directly to the platform via its official API.
   */
  publishPost(account: SocialAccount, content: Content, media?: Media[]): Promise<PublishResult>;

  /**
   * Optional token refresh handler.
   */
  refreshToken?(account: SocialAccount): Promise<TokenRefreshResult>;
}
