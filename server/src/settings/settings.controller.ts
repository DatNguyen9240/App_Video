import {
  Body,
  Controller,
  Delete,
  Get,
  Headers,
  Param,
  ParseEnumPipe,
  Put,
} from '@nestjs/common';
import { ApiOperation, ApiTags } from '@nestjs/swagger';
import { Platform } from '@prisma/client';
import { IsBoolean, IsOptional, IsString } from 'class-validator';
import { SettingsService } from './settings.service';
import { DEFAULT_USER_ID } from '../common/constants';

export class SavePlatformConfigBodyDto {
  @IsString()
  appId: string;

  @IsOptional()
  @IsString()
  appSecret?: string;

  @IsOptional()
  @IsString()
  redirectUri?: string;

  @IsOptional()
  @IsBoolean()
  isActive?: boolean;
}

@ApiTags('Settings')
@Controller('settings')
export class SettingsController {
  constructor(private readonly settingsService: SettingsService) {}

  @Get('platform-configs')
  @ApiOperation({ summary: 'List OAuth App configurations (DB + .env fallback)' })
  getPlatformConfigs(@Headers('x-user-id') userIdHeader?: string) {
    return this.settingsService.getPlatformConfigs(userIdHeader || DEFAULT_USER_ID);
  }

  @Put('platform-configs/:platform')
  @ApiOperation({ summary: 'Save or update OAuth App credentials in PostgreSQL (encrypted)' })
  upsertPlatformConfig(
    @Param('platform', new ParseEnumPipe(Platform)) platform: Platform,
    @Body() dto: SavePlatformConfigBodyDto,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    return this.settingsService.upsertPlatformConfig(
      userIdHeader || DEFAULT_USER_ID,
      platform,
      dto,
    );
  }

  @Delete('platform-configs/:platform')
  @ApiOperation({ summary: 'Delete DB OAuth App override and revert to .env' })
  deletePlatformConfig(
    @Param('platform', new ParseEnumPipe(Platform)) platform: Platform,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    return this.settingsService.deletePlatformConfig(
      userIdHeader || DEFAULT_USER_ID,
      platform,
    );
  }
}
