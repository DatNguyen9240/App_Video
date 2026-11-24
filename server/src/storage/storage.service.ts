import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  S3Client,
  PutObjectCommand,
  DeleteObjectCommand,
  GetObjectCommand,
  HeadBucketCommand,
  CreateBucketCommand,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import * as fs from 'fs';
import * as path from 'path';

export interface UploadResult {
  storageKey: string;
  publicUrl: string;
  fileSize: number;
  mimeType: string;
}

@Injectable()
export class StorageService implements OnModuleInit {
  private readonly logger = new Logger(StorageService.name);
  private s3Client: S3Client;
  private bucket: string;
  private publicBaseUrl: string;
  private isS3Available = false;
  private readonly localStorageDir: string;

  constructor(private readonly configService: ConfigService) {
    this.bucket = this.configService.get<string>('S3_BUCKET_NAME', 'social-media-assets');
    const endpoint = this.configService.get<string>('S3_ENDPOINT', 'http://localhost:9000');
    const region = this.configService.get<string>('S3_REGION', 'us-east-1');
    const accessKeyId = this.configService.get<string>('S3_ACCESS_KEY', 'minioadmin');
    const secretAccessKey = this.configService.get<string>('S3_SECRET_KEY', 'minioadmin_secret');
    const forcePathStyle = this.configService.get<string>('S3_FORCE_PATH_STYLE', 'true') === 'true';

    this.publicBaseUrl = this.configService.get<string>(
      'S3_PUBLIC_BASE_URL',
      `${endpoint}/${this.bucket}`,
    );

    this.s3Client = new S3Client({
      endpoint,
      region,
      credentials: {
        accessKeyId,
        secretAccessKey,
      },
      forcePathStyle,
    });

    this.localStorageDir = path.resolve(process.cwd(), 'uploads');
    if (!fs.existsSync(this.localStorageDir)) {
      fs.mkdirSync(this.localStorageDir, { recursive: true });
    }
  }

  async onModuleInit() {
    try {
      await this.s3Client.send(new HeadBucketCommand({ Bucket: this.bucket }));
      this.isS3Available = true;
      this.logger.log(`S3/MinIO bucket '${this.bucket}' is available.`);
    } catch (err) {
      try {
        await this.s3Client.send(new CreateBucketCommand({ Bucket: this.bucket }));
        this.isS3Available = true;
        this.logger.log(`Created S3/MinIO bucket '${this.bucket}'.`);
      } catch (createErr) {
        this.isS3Available = false;
        this.logger.warn(`S3/MinIO not reachable (${createErr.message}). Using local filesystem storage at ${this.localStorageDir}`);
      }
    }
  }

  /**
   * Uploads file buffer to S3 / MinIO or local fallback.
   */
  async uploadFile(buffer: Buffer, storageKey: string, mimeType: string): Promise<UploadResult> {
    const fileSize = buffer.length;

    if (this.isS3Available) {
      try {
        await this.s3Client.send(
          new PutObjectCommand({
            Bucket: this.bucket,
            Key: storageKey,
            Body: buffer,
            ContentType: mimeType,
          }),
        );
        const publicUrl = `${this.publicBaseUrl}/${storageKey}`;
        return { storageKey, publicUrl, fileSize, mimeType };
      } catch (err) {
        this.logger.error(`S3 upload failed for ${storageKey}: ${err.message}. Falling back to local storage.`);
      }
    }

    // Local filesystem storage fallback
    const localFilePath = path.join(this.localStorageDir, storageKey);
    const localDirPath = path.dirname(localFilePath);
    if (!fs.existsSync(localDirPath)) {
      fs.mkdirSync(localDirPath, { recursive: true });
    }
    fs.writeFileSync(localFilePath, buffer);
    const publicUrl = `/uploads/${storageKey}`;
    return { storageKey, publicUrl, fileSize, mimeType };
  }

  /**
   * Deletes a file from storage.
   */
  async deleteFile(storageKey: string): Promise<void> {
    if (this.isS3Available) {
      try {
        await this.s3Client.send(
          new DeleteObjectCommand({
            Bucket: this.bucket,
            Key: storageKey,
          }),
        );
      } catch (err) {
        this.logger.warn(`Failed to delete ${storageKey} from S3: ${err.message}`);
      }
    }

    const localFilePath = path.join(this.localStorageDir, storageKey);
    if (fs.existsSync(localFilePath)) {
      try {
        fs.unlinkSync(localFilePath);
      } catch (err) {
        this.logger.warn(`Failed to delete local file ${localFilePath}: ${err.message}`);
      }
    }
  }

  /**
   * Generates a pre-signed download URL for private files.
   */
  async getPresignedDownloadUrl(storageKey: string, expiresInSeconds: number = 3600): Promise<string> {
    if (this.isS3Available) {
      try {
        const command = new GetObjectCommand({
          Bucket: this.bucket,
          Key: storageKey,
        });
        return await getSignedUrl(this.s3Client, command, { expiresIn: expiresInSeconds });
      } catch (err) {
        this.logger.error(`Failed to generate presigned URL for ${storageKey}: ${err.message}`);
      }
    }
    return `${this.publicBaseUrl}/${storageKey}`;
  }

  /**
   * Gets absolute local path of a file if available, or downloads buffer.
   */
  async getFileBuffer(storageKey: string): Promise<Buffer> {
    const localFilePath = path.join(this.localStorageDir, storageKey);
    if (fs.existsSync(localFilePath)) {
      return fs.readFileSync(localFilePath);
    }

    if (this.isS3Available) {
      const response = await this.s3Client.send(
        new GetObjectCommand({
          Bucket: this.bucket,
          Key: storageKey,
        }),
      );
      const streamToBuffer = (stream: any): Promise<Buffer> =>
        new Promise((resolve, reject) => {
          const chunks: any[] = [];
          stream.on('data', (chunk: any) => chunks.push(chunk));
          stream.on('error', reject);
          stream.on('end', () => resolve(Buffer.concat(chunks)));
        });
      return streamToBuffer(response.Body);
    }

    throw new Error(`File '${storageKey}' not found in storage.`);
  }

  getPublicUrl(storageKey: string): string {
    return `${this.publicBaseUrl}/${storageKey}`;
  }
}
