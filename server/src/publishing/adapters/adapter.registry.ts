import { Injectable, Logger } from '@nestjs/common';
import { Platform } from '@prisma/client';
import { SocialPlatformAdapter } from './social-platform.adapter.interface';
import { FacebookAdapter } from './facebook.adapter';
import { YouTubeAdapter } from './youtube.adapter';
import { ThreadsAdapter } from './threads.adapter';
import { TikTokAdapter } from './tiktok.adapter';

@Injectable()
export class PlatformAdapterRegistry {
  private readonly logger = new Logger(PlatformAdapterRegistry.name);
  private readonly adapters = new Map<Platform, SocialPlatformAdapter>();

  constructor(
    private readonly facebookAdapter: FacebookAdapter,
    private readonly youtubeAdapter: YouTubeAdapter,
    private readonly threadsAdapter: ThreadsAdapter,
    private readonly tiktokAdapter: TikTokAdapter,
  ) {
    this.registerAdapter(this.facebookAdapter);
    this.registerAdapter(this.youtubeAdapter);
    this.registerAdapter(this.threadsAdapter);
    this.registerAdapter(this.tiktokAdapter);
  }

  registerAdapter(adapter: SocialPlatformAdapter) {
    this.adapters.set(adapter.platform, adapter);
    this.logger.log(`Registered publishing adapter for platform: ${adapter.platform}`);
  }

  getAdapter(platform: Platform): SocialPlatformAdapter {
    const adapter = this.adapters.get(platform);
    if (!adapter) {
      throw new Error(`No publishing adapter registered for platform: ${platform}`);
    }
    return adapter;
  }

  hasAdapter(platform: Platform): boolean {
    return this.adapters.has(platform);
  }

  getSupportedPlatforms(): Platform[] {
    return Array.from(this.adapters.keys());
  }
}
