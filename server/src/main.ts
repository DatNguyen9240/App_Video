import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import { ValidationPipe, Logger } from '@nestjs/common';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { GlobalHttpExceptionFilter } from './common/filters/http-exception.filter';
import { NestExpressApplication } from '@nestjs/platform-express';
import * as path from 'path';

async function bootstrap() {
  const logger = new Logger('Bootstrap');
  const app = await NestFactory.create<NestExpressApplication>(AppModule);

  // Global API Prefix
  app.setGlobalPrefix('api');

  // CORS configuration
  app.enableCors({
    origin: '*',
    methods: 'GET,HEAD,PUT,PATCH,POST,DELETE,OPTIONS',
    credentials: true,
  });

  // Global Exception Filter & Validation Pipe
  app.useGlobalFilters(new GlobalHttpExceptionFilter());
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      transform: true,
      forbidNonWhitelisted: false,
    }),
  );

  // Serve static files from local uploads folder if needed
  const uploadsDir = path.resolve(process.cwd(), 'uploads');
  app.useStaticAssets(uploadsDir, { prefix: '/uploads/' });

  // Swagger OpenAPI Documentation
  const swaggerConfig = new DocumentBuilder()
    .setTitle('Social Media Auto Publishing API')
    .setDescription(
      'Production-ready API for multi-platform social media publishing to Facebook Pages (Meta Graph API) and YouTube Channels (YouTube Data API v3).',
    )
    .setVersion('1.0.0')
    .addTag('Social Accounts', 'Meta & Google OAuth account connection and management')
    .addTag('Content', 'Content drafting, metadata, and multi-platform post creation')
    .addTag('Media', 'Asset upload, S3/MinIO storage, and metadata extraction')
    .addTag('Publishing', 'Immediate publishing, scheduling, retry, and cancellation')
    .addTag('Dashboard', 'Publishing statistics and activity monitoring')
    .build();

  const document = SwaggerModule.createDocument(app, swaggerConfig);
  SwaggerModule.setup('api/docs', app, document);

  const port = process.env.PORT || 3000;
  await app.listen(port);
  logger.log(`🚀 Social Auto Publishing Server running at: http://localhost:${port}/api`);
  logger.log(`📚 Swagger API Documentation available at: http://localhost:${port}/api/docs`);
}

bootstrap();
