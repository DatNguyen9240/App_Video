import { Global, Module } from '@nestjs/common';
import { SettingsService } from './settings.service';
import { SettingsController } from './settings.controller';
import { EncryptionService } from '../common/services/encryption.service';

@Global()
@Module({
  controllers: [SettingsController],
  providers: [SettingsService, EncryptionService],
  exports: [SettingsService],
})
export class SettingsModule {}
