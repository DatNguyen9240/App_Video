import {
  Controller,
  Post,
  Get,
  Delete,
  Param,
  Query,
  UseInterceptors,
  UploadedFile,
  Body,
  Headers,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { ApiTags, ApiOperation, ApiConsumes } from '@nestjs/swagger';
import { MediaService } from './media.service';
import { DEFAULT_USER_ID } from '../common/constants';

@ApiTags('Media')
@Controller('media')
export class MediaController {
  constructor(private readonly mediaService: MediaService) {}

  @Post('upload')
  @ApiOperation({ summary: 'Upload an image or video asset to S3/MinIO' })
  @ApiConsumes('multipart/form-data')
  @UseInterceptors(FileInterceptor('file'))
  async upload(
    @UploadedFile() file: Express.Multer.File,
    @Body('contentId') contentId?: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.mediaService.uploadMedia(file, userId, contentId);
  }

  @Get()
  @ApiOperation({ summary: 'List uploaded media assets' })
  async getList(
    @Query('page') page?: number,
    @Query('limit') limit?: number,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.mediaService.getMediaList(userId, page, limit);
  }

  @Delete(':id')
  @ApiOperation({ summary: 'Delete a media asset' })
  async delete(
    @Param('id') id: string,
    @Headers('x-user-id') userIdHeader?: string,
  ) {
    const userId = userIdHeader || DEFAULT_USER_ID;
    return this.mediaService.deleteMedia(id, userId);
  }
}
