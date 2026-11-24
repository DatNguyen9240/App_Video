import { IsString, IsOptional, IsArray, IsDateString } from 'class-validator';
import { ApiProperty } from '@nestjs/swagger';

export class CreateContentDto {
  @ApiProperty({ required: false })
  @IsString()
  @IsOptional()
  title?: string;

  @ApiProperty({ required: false })
  @IsString()
  @IsOptional()
  caption?: string;

  @ApiProperty({ required: false })
  @IsString()
  @IsOptional()
  description?: string;

  @ApiProperty({ required: false })
  @IsString()
  @IsOptional()
  hashtags?: string;

  @ApiProperty({ example: ['FACEBOOK', 'YOUTUBE'] })
  @IsArray()
  targetPlatforms: string[];

  @ApiProperty({ required: false, example: ['acc-id-1'] })
  @IsArray()
  @IsOptional()
  targetAccountIds?: string[];

  @ApiProperty({ required: false, example: ['media-id-1'] })
  @IsArray()
  @IsOptional()
  mediaIds?: string[];

  @ApiProperty({ required: false })
  @IsDateString()
  @IsOptional()
  scheduledAt?: string;

  @ApiProperty({ required: false, default: false })
  @IsOptional()
  publishImmediately?: boolean;
}

export class UpdateContentDto {
  @ApiProperty({ required: false })
  @IsString()
  @IsOptional()
  title?: string;

  @ApiProperty({ required: false })
  @IsString()
  @IsOptional()
  caption?: string;

  @ApiProperty({ required: false })
  @IsString()
  @IsOptional()
  description?: string;

  @ApiProperty({ required: false })
  @IsString()
  @IsOptional()
  hashtags?: string;

  @ApiProperty({ required: false })
  @IsArray()
  @IsOptional()
  targetPlatforms?: string[];

  @ApiProperty({ required: false })
  @IsArray()
  @IsOptional()
  targetAccountIds?: string[];

  @ApiProperty({ required: false })
  @IsArray()
  @IsOptional()
  mediaIds?: string[];

  @ApiProperty({ required: false })
  @IsDateString()
  @IsOptional()
  scheduledAt?: string;
}
