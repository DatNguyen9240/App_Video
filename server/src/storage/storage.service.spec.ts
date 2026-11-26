import { Test, TestingModule } from '@nestjs/testing';
import { StorageService } from './storage.service';
import { ConfigService } from '@nestjs/config';
import { S3Client, PutObjectCommand, DeleteObjectCommand, GetObjectCommand, HeadBucketCommand } from '@aws-sdk/client-s3';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

jest.mock('@aws-sdk/client-s3');
jest.mock('@aws-sdk/s3-request-presigner');

const MockedS3Client = S3Client as jest.MockedClass<typeof S3Client>;

describe('StorageService', () => {
  let service: StorageService;
  let tempDir: string;
  let mockS3Send: jest.Mock;

  beforeEach(async () => {
    // Create temp dir for local fallback tests
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'storage-test-'));

    mockS3Send = jest.fn();
    MockedS3Client.mockImplementation(() => ({
      send: mockS3Send,
    }) as any);

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        StorageService,
        {
          provide: ConfigService,
          useValue: {
            get: (key: string, def?: any) => {
              const cfg: Record<string, any> = {
                S3_ENDPOINT: 'http://localhost:9000',
                S3_REGION: 'us-east-1',
                S3_ACCESS_KEY: 'minioadmin',
                S3_SECRET_KEY: 'minioadmin_secret',
                S3_BUCKET_NAME: 'test-bucket',
                S3_FORCE_PATH_STYLE: 'true',
                S3_PUBLIC_BASE_URL: 'http://localhost:9000/test-bucket',
              };
              return cfg[key] ?? def;
            },
          },
        },
      ],
    }).compile();

    service = module.get<StorageService>(StorageService);
    jest.clearAllMocks();
  });

  afterEach(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  // ─── onModuleInit (bucket health check) ──────────────────────────────────────
  describe('onModuleInit()', () => {
    it('should set isS3Available=true when bucket exists', async () => {
      mockS3Send.mockResolvedValueOnce({}); // HeadBucketCommand OK
      await service.onModuleInit();
      expect((service as any).isS3Available).toBe(true);
    });

    it('should create bucket when it does not exist and set isS3Available=true', async () => {
      mockS3Send
        .mockRejectedValueOnce(new Error('NoSuchBucket'))  // HeadBucket fails
        .mockResolvedValueOnce({});                         // CreateBucket OK
      await service.onModuleInit();
      expect((service as any).isS3Available).toBe(true);
    });

    it('should set isS3Available=false when S3 is completely unreachable', async () => {
      mockS3Send
        .mockRejectedValueOnce(new Error('ECONNREFUSED'))
        .mockRejectedValueOnce(new Error('ECONNREFUSED'));
      await service.onModuleInit();
      expect((service as any).isS3Available).toBe(false);
    });
  });

  // ─── uploadFile ───────────────────────────────────────────────────────────────
  describe('uploadFile()', () => {
    it('should upload to S3 and return public URL when S3 is available', async () => {
      (service as any).isS3Available = true;
      mockS3Send.mockResolvedValueOnce({}); // PutObjectCommand OK

      const buffer = Buffer.from('fake video data');
      const result = await service.uploadFile(buffer, 'media/user-1/test.mp4', 'video/mp4');

      expect(result.storageKey).toBe('media/user-1/test.mp4');
      expect(result.publicUrl).toContain('test-bucket');
      expect(result.publicUrl).toContain('media/user-1/test.mp4');
      expect(mockS3Send).toHaveBeenCalledWith(expect.any(PutObjectCommand));
    });

    it('should fallback to local filesystem when S3 upload fails', async () => {
      (service as any).isS3Available = true;
      (service as any).localStorageDir = tempDir;
      mockS3Send.mockRejectedValueOnce(new Error('S3 connection reset'));

      const buffer = Buffer.from('fallback data');
      const result = await service.uploadFile(buffer, 'fallback/file.jpg', 'image/jpeg');

      // Should have fallen back to local
      expect(result.publicUrl).toContain('/uploads/');
      const localPath = path.join(tempDir, 'fallback', 'file.jpg');
      expect(fs.existsSync(localPath)).toBe(true);
    });

    it('should use local filesystem when S3 is not available', async () => {
      (service as any).isS3Available = false;
      (service as any).localStorageDir = tempDir;

      const buffer = Buffer.from('local only data');
      const result = await service.uploadFile(buffer, 'local/only.mp4', 'video/mp4');

      expect(result.publicUrl).toContain('/uploads/');
      expect(mockS3Send).not.toHaveBeenCalled();
    });

    it('should return correct fileSize in result', async () => {
      (service as any).isS3Available = true;
      mockS3Send.mockResolvedValueOnce({});
      const buffer = Buffer.alloc(5000, 'x');

      const result = await service.uploadFile(buffer, 'media/size-check.mp4', 'video/mp4');
      expect(result.fileSize).toBe(5000);
    });
  });

  // ─── deleteFile ───────────────────────────────────────────────────────────────
  describe('deleteFile()', () => {
    it('should send DeleteObjectCommand to S3 when available', async () => {
      (service as any).isS3Available = true;
      mockS3Send.mockResolvedValueOnce({});

      await service.deleteFile('media/to-delete.mp4');
      expect(mockS3Send).toHaveBeenCalledWith(expect.any(DeleteObjectCommand));
    });

    it('should delete local file when it exists', async () => {
      (service as any).isS3Available = false;
      (service as any).localStorageDir = tempDir;

      const localFile = path.join(tempDir, 'delete-me.mp4');
      fs.writeFileSync(localFile, 'delete test');
      (service as any).isS3Available = false;

      await service.deleteFile('delete-me.mp4');
      expect(fs.existsSync(localFile)).toBe(false);
    });

    it('should not throw when file does not exist in either storage', async () => {
      (service as any).isS3Available = false;
      (service as any).localStorageDir = tempDir;

      await expect(service.deleteFile('nonexistent-key.mp4')).resolves.not.toThrow();
    });
  });

  // ─── getFileBuffer ────────────────────────────────────────────────────────────
  describe('getFileBuffer()', () => {
    it('should read file from local filesystem when available', async () => {
      (service as any).localStorageDir = tempDir;
      const localFile = path.join(tempDir, 'buffer-test.mp4');
      const testContent = Buffer.from('video buffer content');
      fs.writeFileSync(localFile, testContent);

      const result = await service.getFileBuffer('buffer-test.mp4');
      expect(result.toString()).toBe('video buffer content');
    });

    it('should throw Error when file not found in any storage', async () => {
      (service as any).isS3Available = false;
      (service as any).localStorageDir = tempDir;

      await expect(service.getFileBuffer('totally-nonexistent.mp4'))
        .rejects.toThrow(/not found in storage/i);
    });
  });

  // ─── getPublicUrl ─────────────────────────────────────────────────────────────
  describe('getPublicUrl()', () => {
    it('should return correctly formatted public URL', () => {
      const url = service.getPublicUrl('media/user-1/video.mp4');
      expect(url).toBe('http://localhost:9000/test-bucket/media/user-1/video.mp4');
    });
  });
});
