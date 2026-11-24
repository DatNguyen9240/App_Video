import {
  Controller,
  Get,
  Post,
  Patch,
  Delete,
  Body,
  Param,
  Query,
  Headers,
} from '@nestjs/common';
import { ApiTags, ApiOperation } from '@nestjs/swagger';
import { ContentService } from './content.service';
import { CreateContentDto, UpdateContentDto } from './dto/content.dto';
import { ContentStatus } from '@prisma/client';
import { DEFAULT_USER_ID } from '../common/constants';

@ApiTags('Content')
@Controller('contents')
export class ContentController {
  constructor(private readonly contentService: ContentService) {}

  @Post()
  @ApiOperation({ summary: 'Create new content draft or publishable post' })
  async create(
    @Body() dto: CreateContentDto,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.contentService.createContent(dto, userId);
  }

  @Get()
  @ApiOperation({ summary: 'List all content posts with optional status filter' })
  async getList(
    @Query('status') status?: ContentStatus,
    @Query('page') page?: number,
    @Query('limit') limit?: number,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.contentService.getContentList(userId, { status, page, limit });
  }

  @Get(':id')
  @ApiOperation({ summary: 'Get details of specific content item' })
  async getById(
    @Param('id') id: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.contentService.getContentById(id, userId);
  }

  @Patch(':id')
  @ApiOperation({ summary: 'Update an existing content item' })
  async update(
    @Param('id') id: string,
    @Body() dto: UpdateContentDto,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.contentService.updateContent(id, dto, userId);
  }

  @Delete(':id')
  @ApiOperation({ summary: 'Delete a content item' })
  async delete(
    @Param('id') id: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.contentService.deleteContent(id, userId);
  }
}
