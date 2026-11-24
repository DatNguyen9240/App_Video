import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { BullModule } from '@nestjs/bullmq';
import { PrismaModule } from './prisma/prisma.module';
import { StorageModule } from './storage/storage.module';
import { SocialAccountModule } from './social-account/social-account.module';
import { PublishingModule } from './publishing/publishing.module';
import { ContentModule } from './content/content.module';
import { MediaModule } from './media/media.module';
import { DashboardModule } from './dashboard/dashboard.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env', '../.env', '.env.example', '../.env.example'],
    }),
    BullModule.forRootAsync({
      imports: [ConfigModule],
      useFactory: (config: ConfigService) => ({
        connection: {
          host: config.get<string>('REDIS_HOST', 'localhost'),
          port: config.get<number>('REDIS_PORT', 6379),
          password: config.get<string>('REDIS_PASSWORD', 'redis_secure_pass'),
        },
      }),
      inject: [ConfigService],
    }),
    PrismaModule,
    StorageModule,
    SocialAccountModule,
    PublishingModule,
    ContentModule,
    MediaModule,
    DashboardModule,
  ],
})
export class AppModule {}
