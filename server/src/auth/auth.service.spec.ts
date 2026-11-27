import { Test, TestingModule } from '@nestjs/testing';
import { ConfigService } from '@nestjs/config';
import { ConflictException, NotFoundException, UnauthorizedException } from '@nestjs/common';
import { AuthService } from './auth.service';
import { PrismaService } from '../prisma/prisma.service';
import * as bcrypt from 'bcrypt';

describe('AuthService', () => {
  let service: AuthService;
  let prisma: {
    user: {
      findUnique: jest.Mock;
      create: jest.Mock;
    };
  };

  beforeEach(async () => {
    prisma = {
      user: {
        findUnique: jest.fn(),
        create: jest.fn(),
      },
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AuthService,
        { provide: PrismaService, useValue: prisma },
        {
          provide: ConfigService,
          useValue: {
            get: (_k: string, def?: string) => def || 'test_jwt_secret_123456',
          },
        },
      ],
    }).compile();

    service = module.get<AuthService>(AuthService);
  });

  it('should sign and verify a valid JWT token', () => {
    const token = service.signToken({ id: 'u-1', email: 'a@test.com', name: 'Alice' });
    const payload = service.verifyToken(token);
    expect(payload.sub).toBe('u-1');
    expect(payload.email).toBe('a@test.com');
  });

  it('should reject tampered JWT tokens', () => {
    const token = service.signToken({ id: 'u-1', email: 'a@test.com' });
    const tampered = token.slice(0, -4) + 'xxxx';
    expect(() => service.verifyToken(tampered)).toThrow(UnauthorizedException);
  });

  it('should register a new user and return accessToken', async () => {
    prisma.user.findUnique.mockResolvedValue(null);
    prisma.user.create.mockResolvedValue({
      id: 'u-new',
      email: 'new@test.com',
      name: 'New User',
      createdAt: new Date(),
    });

    const res = await service.register('New@Test.com', 'secret123', 'New User');
    expect(res.user.email).toBe('new@test.com');
    expect(res.accessToken).toBeTruthy();
    expect(prisma.user.create).toHaveBeenCalled();
  });

  it('should throw ConflictException when registering existing email', async () => {
    prisma.user.findUnique.mockResolvedValue({ id: 'existing' });
    await expect(service.register('dup@test.com', 'secret123')).rejects.toThrow(ConflictException);
  });

  it('should login successfully with valid credentials', async () => {
    const hash = await bcrypt.hash('pass1234', 10);
    prisma.user.findUnique.mockResolvedValue({
      id: 'u-1',
      email: 'u1@test.com',
      name: 'U1',
      passwordHash: hash,
      createdAt: new Date(),
    });

    const res = await service.login('u1@test.com', 'pass1234');
    expect(res.user.id).toBe('u-1');
    expect(service.verifyToken(res.accessToken).sub).toBe('u-1');
  });

  it('should throw UnauthorizedException for wrong password', async () => {
    const hash = await bcrypt.hash('pass1234', 10);
    prisma.user.findUnique.mockResolvedValue({
      id: 'u-1',
      email: 'u1@test.com',
      passwordHash: hash,
    });

    await expect(service.login('u1@test.com', 'wrongpass')).rejects.toThrow(UnauthorizedException);
  });

  it('should throw NotFoundException in getProfile for unknown user', async () => {
    prisma.user.findUnique.mockResolvedValue(null);
    await expect(service.getProfile('missing-id')).rejects.toThrow(NotFoundException);
  });
});
