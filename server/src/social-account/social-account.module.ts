import { Module } from '@nestjs/common';
import { SocialAccountController } from './social-account.controller';
import { SocialAccountService } from './social-account.service';
import { EncryptionService } from '../common/services/encryption.service';

@Module({
  controllers: [SocialAccountController],
  providers: [SocialAccountService, EncryptionService],
  exports: [SocialAccountService],
})
export class SocialAccountModule {}
