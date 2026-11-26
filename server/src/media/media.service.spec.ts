import { Test, TestingModule } from '@nestjs/testing';
import { BadRequestException, NotFoundException } from '@nestjs/common';
import { MediaService } from './media.service';
import { PrismaService } from '../prisma/prisma.service';
import { StorageService } from '../storage/storage.service';

const mockMediaRecord = (overrides: Partial<any> = {}) => ({
  id: 'media-1',
  contentId: null,
  storageKey: 'media/user-demo/video.mp4',
  originalName: 'video.mp4',
  mimeType: 'video/mp4',
  fileSize: 25 * 1024 * 1024,
  duration: 58.5,
  width: 1080,
  height: 1920,
  thumbnailKey: null,
  createdAt: new Date(),
  ...overrides,
});

const makeFile = (overrides: Partial<Express.Multer.File> = {}): Express.Multer.File => ({
  fieldname: 'file',
  originalname: 'test_video.mp4',
  encoding: '7bit',
  mimetype: 'video/mp4',
  buffer: Buffer.alloc(10 * 1024 * 1024, 0),
  size: 10 * 1024 * 1024,
  stream: null as any,
  destination: '',
  filename: '',
  path: '',
  ...overrides,
});

describe('MediaService', () => {
  let service: MediaService;
  let prisma: { media: jest.Mocked<any> };
  let storageService: jest.Mocked<StorageService>;

  beforeEach(async () => {
    prisma = {
      media: {
        create: jest.fn(),
        findMany: jest.fn(),
        findFirst: jest.fn(),
        delete: jest.fn(),
        count: jest.fn(),
      },
    };

    storageService = {
      uploadFile: jest.fn().mockResolvedValue({
        storageKey: 'media/user-demo/test_video.mp4',
        publicUrl: 'http://localhost:9000/social-media-assets/media/user-demo/test_video.mp4',
        fileSize: 10 * 1024 * 1024,
        mimeType: 'video/mp4',
      }),
      deleteFile: jest.fn().mockResolvedValue(undefined),
      getPublicUrl: jest.fn().mockReturnValue('http://localhost:9000/assets/key'),
    } as any;

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        MediaService,
        { provide: PrismaService, useValue: prisma },
        { provide: StorageService, useValue: storageService },
      ],
    }).compile();

    service = module.get<MediaService>(MediaService);
    jest.clearAllMocks();
  });

  // ─── uploadMedia ─────────────────────────────────────────────────────────────
  describe('uploadMedia()', () => {
    it('should upload valid video file and create DB record', async () => {
      const file = makeFile();
      const record = mockMediaRecord();
      prisma.media.create.mockResolvedValue(record);

      const result = await service.uploadMedia(file, 'user-demo');

      expect(storageService.uploadFile).toHaveBeenCalled();
      expect(prisma.media.create).toHaveBeenCalled();
      expect(result.id).toBe('media-1');
    });

    it('should upload valid image file (JPEG) and return media record', async () => {
      const file = makeFile({
        originalname: 'photo.jpg',
        mimetype: 'image/jpeg',
        buffer: Buffer.alloc(1 * 1024 * 1024),
        size: 1 * 1024 * 1024,
      });
      prisma.media.create.mockResolvedValue(mockMediaRecord({ mimeType: 'image/jpeg' }));

      const result = await service.uploadMedia(file, 'user-demo');
      expect(result.mimeType).toBe('image/jpeg');
    });

    it('should throw BadRequestException when no file is provided', async () => {
      await expect(service.uploadMedia(null as any, 'user-demo'))
        .rejects.toThrow(BadRequestException);
    });

    it('should throw BadRequestException for unsupported MIME type (PDF)', async () => {
      const file = makeFile({ mimetype: 'application/pdf', originalname: 'doc.pdf' });
      await expect(service.uploadMedia(file, 'user-demo'))
        .rejects.toThrow(BadRequestException);
    });

    it('should throw BadRequestException for unsupported MIME type (EXE)', async () => {
      const file = makeFile({ mimetype: 'application/octet-stream', originalname: 'malware.exe' });
      await expect(service.uploadMedia(file, 'user-demo'))
        .rejects.toThrow(BadRequestException);
    });

    it('should throw BadRequestException when image exceeds 50MB limit', async () => {
      const file = makeFile({
        mimetype: 'image/jpeg',
        size: 51 * 1024 * 1024,
        buffer: Buffer.alloc(51 * 1024 * 1024),
      });
      await expect(service.uploadMedia(file, 'user-demo'))
        .rejects.toThrow(BadRequestException);
    });

    it('should throw BadRequestException when video exceeds 1GB limit', async () => {
      const file = makeFile({
        mimetype: 'video/mp4',
        size: 1025 * 1024 * 1024,
        buffer: Buffer.alloc(100),
      });
      await expect(service.uploadMedia(file, 'user-demo'))
        .rejects.toThrow(BadRequestException);
    });

    it('should allow video under 1GB (500MB) without throwing', async () => {
      const file = makeFile({
        mimetype: 'video/mp4',
        size: 500 * 1024 * 1024,
        buffer: Buffer.alloc(100),
      });
      prisma.media.create.mockResolvedValue(mockMediaRecord({ fileSize: 500 * 1024 * 1024 }));

      await expect(service.uploadMedia(file, 'user-demo')).resolves.not.toThrow();
    });

    it('should associate contentId when provided during upload', async () => {
      const file = makeFile();
      prisma.media.create.mockResolvedValue(mockMediaRecord({ contentId: 'content-1' }));

      await service.uploadMedia(file, 'user-demo', 'content-1');

      expect(prisma.media.create).toHaveBeenCalledWith(
        expect.objectContaining({
          data: expect.objectContaining({ contentId: 'content-1' }),
        }),
      );
    });
  });

  // ─── getMediaList ─────────────────────────────────────────────────────────────
  describe('getMediaList()', () => {
    it('should return paginated media list for user', async () => {
      const items = [mockMediaRecord(), mockMediaRecord({ id: 'media-2' })];
      prisma.media.findMany.mockResolvedValue(items);
      prisma.media.count.mockResolvedValue(15);

      const result = await service.getMediaList('user-demo', { page: 1, limit: 10 });

      expect(result.data).toHaveLength(2);
      expect(result.total).toBe(15);
    });
  });

  // ─── deleteMedia ─────────────────────────────────────────────────────────────
  describe('deleteMedia()', () => {
    it('should delete file from storage and DB record', async () => {
      prisma.media.findFirst.mockResolvedValue(mockMediaRecord());
      prisma.media.delete.mockResolvedValue({});

      const result = await service.deleteMedia('media-1', 'user-demo');

      expect(storageService.deleteFile).toHaveBeenCalledWith('media/user-demo/video.mp4');
      expect(prisma.media.delete).toHaveBeenCalledWith({ where: { id: 'media-1' } });
      expect(result.success).toBe(true);
    });

    it('should throw NotFoundException when media not found', async () => {
      prisma.media.findFirst.mockResolvedValue(null);

      await expect(service.deleteMedia('nonexistent', 'user-demo'))
        .rejects.toThrow(NotFoundException);

      expect(storageService.deleteFile).not.toHaveBeenCalled();
    });
  });
});
