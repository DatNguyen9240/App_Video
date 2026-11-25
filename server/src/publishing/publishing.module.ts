import { Module } from '@nestjs/common';
import { BullModule } from '@nestjs/bullmq';
import { PublishingService } from './publishing.service';
import { PublishingProcessor } from './publishing.processor';
import { PublishingController } from './publishing.controller';
import { PlatformAdapterRegistry } from './adapters/adapter.registry';
import { FacebookAdapter } from './adapters/facebook.adapter';
import { YouTubeAdapter } from './adapters/youtube.adapter';
import { ThreadsAdapter } from './adapters/threads.adapter';
import { TikTokAdapter } from './adapters/tiktok.adapter';
import { SocialAccountModule } from '../social-account/social-account.module';
import { EncryptionService } from '../common/services/encryption.service';
import { QUEUE_NAMES } from '../common/constants';

@Module({
  imports: [
    BullModule.registerQueue({
      name: QUEUE_NAMES.PUBLISHING,
    }),
    SocialAccountModule,
  ],
  controllers: [PublishingController],
  providers: [
    PublishingService,
    PublishingProcessor,
    PlatformAdapterRegistry,
    FacebookAdapter,
    YouTubeAdapter,
    ThreadsAdapter,
    TikTokAdapter,
    EncryptionService,
  ],
  exports: [PublishingService, PlatformAdapterRegistry],
})
export class PublishingModule {}
