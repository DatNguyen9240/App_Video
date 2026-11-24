import {
  Injectable,
  BadRequestException,
  NotFoundException,
  Logger,
} from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { StorageService } from '../storage/storage.service';
import { v4 as uuidv4 } from 'uuid';
import * as path from 'path';

@Injectable()
export class MediaService {
  private readonly logger = new Logger(MediaService.name);

  private readonly allowedMimeTypes = [
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/gif',
    'video/mp4',
    'video/quicktime',
    'video/webm',
    'video/x-msvideo',
  ];

  constructor(
    private readonly prisma: PrismaService,
    private readonly storageService: StorageService,
  ) {}

  async uploadMedia(
    file: Express.Multer.File,
    userId: string,
    contentId?: string,
  ) {
    if (!file) {
      throw new BadRequestException('No file provided for upload.');
    }

    if (!this.allowedMimeTypes.includes(file.mimetype)) {
      throw new BadRequestException(
        `Unsupported media MIME type: ${file.mimetype}. Allowed types: Images (JPEG, PNG, WebP, GIF) and Videos (MP4, QuickTime, WebM).`,
      );
    }

    // Size limits: 50MB for images, 1GB for videos
    const maxSizeBytes = file.mimetype.startsWith('video/')
      ? 1024 * 1024 * 1024 // 1 GB
      : 50 * 1024 * 1024;  // 50 MB

    if (file.size > maxSizeBytes) {
      const maxMb = maxSizeBytes / 1024 / 1024;
      throw new BadRequestException(
        `File size exceeds maximum allowed limit of ${maxMb} MB for ${file.mimetype}.`,
      );
    }

    const ext = path.extname(file.originalname) || (file.mimetype.startsWith('video/') ? '.mp4' : '.jpg');
    const sanitizedBase = path
      .basename(file.originalname, ext)
      .replace(/[^a-zA-Z0-9_-]/g, '_');
    const storageKey = `media/${userId}/${uuidv4()}-${sanitizedBase}${ext}`;

    // Upload to S3 / MinIO
    const uploadResult = await this.storageService.uploadFile(
      file.buffer,
      storageKey,
      file.mimetype,
    );

    // Basic dimension/duration estimation for vertical Reels/Shorts
    let width: number | null = null;
    let height: number | null = null;
    let duration: number | null = null;

    if (file.mimetype.startsWith('video/')) {
      // Default vertical video profile standard
      width = 1080;
      height = 1920;
      duration = Math.max(5, Math.round(file.size / (1024 * 500))); // Rough estimate if ffprobe unavailable
    } else {
      width = 1200;
      height = 630;
    }

    const media = await this.prisma.media.create({
      data: {
        contentId: contentId || null,
        storageKey: uploadResult.storageKey,
        originalName: file.originalname,
        mimeType: file.mimetype,
        fileSize: file.size,
        duration,
        width,
        height,
      },
    });

    return {
      ...media,
      url: uploadResult.publicUrl,
    };
  }

  async getMediaList(userId: string, page: number = 1, limit: number = 20) {
    const skip = (Number(page) - 1) * Number(limit);

    // List media belonging to content created by user or unassigned uploaded by user
    const [items, total] = await Promise.all([
      this.prisma.media.findMany({
        where: {
          OR: [
            { storageKey: { startsWith: `media/${userId}/` } },
            { content: { userId } },
          ],
        },
        orderBy: { createdAt: 'desc' },
        skip,
        take: Number(limit),
      }),
      this.prisma.media.count({
        where: {
          OR: [
            { storageKey: { startsWith: `media/${userId}/` } },
            { content: { userId } },
          ],
        },
      }),
    ]);

    const formatted = items.map((m) => ({
      ...m,
      url: this.storageService.getPublicUrl(m.storageKey),
    }));

    return {
      data: formatted,
      total,
      page: Number(page),
      limit: Number(limit),
      totalPages: Math.ceil(total / Number(limit)),
    };
  }

  async deleteMedia(id: string, userId: string) {
    const media = await this.prisma.media.findFirst({
      where: {
        id,
        OR: [
          { storageKey: { startsWith: `media/${userId}/` } },
          { content: { userId } },
        ],
      },
    });

    if (!media) {
      throw new NotFoundException(`Media with id '${id}' not found.`);
    }

    // Delete from storage
    await this.storageService.deleteFile(media.storageKey);

    // Delete from DB
    await this.prisma.media.delete({ where: { id } });

    return { success: true, message: `Media '${media.originalName}' deleted successfully.` };
  }
}
