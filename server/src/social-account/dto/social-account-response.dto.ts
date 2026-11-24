import { Platform, AccountStatus } from '@prisma/client';

export class SocialAccountResponseDto {
  id: string;
  userId: string;
  platform: Platform;
  platformAccountId: string;
  accountName: string;
  avatarUrl?: string;
  tokenExpiresAt?: Date;
  scopes?: string;
  status: AccountStatus;
  metadata?: any;
  createdAt: Date;
  updatedAt: Date;
}
