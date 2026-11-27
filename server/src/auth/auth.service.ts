import {
  Injectable,
  ConflictException,
  UnauthorizedException,
  NotFoundException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../prisma/prisma.service';
import * as bcrypt from 'bcrypt';
import * as crypto from 'crypto';

export interface JwtPayload {
  sub: string;
  email: string;
  name?: string | null;
  iat: number;
  exp: number;
}

@Injectable()
export class AuthService {
  private readonly jwtSecret: string;

  constructor(
    private readonly prisma: PrismaService,
    private readonly configService: ConfigService,
  ) {
    this.jwtSecret = this.configService.get<string>(
      'JWT_SECRET',
      'autopub_dev_jwt_secret_change_in_production',
    );
  }

  private base64UrlEncode(input: Buffer | string): string {
    const buf = typeof input === 'string' ? Buffer.from(input, 'utf8') : input;
    return buf
      .toString('base64')
      .replace(/=/g, '')
      .replace(/\+/g, '-')
      .replace(/\//g, '_');
  }

  private base64UrlDecode(input: string): string {
    const padded = input.replace(/-/g, '+').replace(/_/g, '/');
    const padLen = (4 - (padded.length % 4)) % 4;
    return Buffer.from(padded + '='.repeat(padLen), 'base64').toString('utf8');
  }

  signToken(user: { id: string; email: string; name?: string | null }): string {
    const now = Math.floor(Date.now() / 1000);
    const header = this.base64UrlEncode(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const payload = this.base64UrlEncode(
      JSON.stringify({
        sub: user.id,
        email: user.email,
        name: user.name ?? null,
        iat: now,
        exp: now + 7 * 24 * 60 * 60, // 7 days
      }),
    );
    const sig = this.base64UrlEncode(
      crypto
        .createHmac('sha256', this.jwtSecret)
        .update(`${header}.${payload}`)
        .digest(),
    );
    return `${header}.${payload}.${sig}`;
  }

  verifyToken(token: string): JwtPayload {
    const parts = token.split('.');
    if (parts.length !== 3) {
      throw new UnauthorizedException('Invalid JWT token format.');
    }
    const [header, payload, signature] = parts;
    const expectedSig = this.base64UrlEncode(
      crypto
        .createHmac('sha256', this.jwtSecret)
        .update(`${header}.${payload}`)
        .digest(),
    );

    const sigBuf = Buffer.from(signature);
    const expBuf = Buffer.from(expectedSig);
    if (sigBuf.length !== expBuf.length || !crypto.timingSafeEqual(sigBuf, expBuf)) {
      throw new UnauthorizedException('Invalid JWT signature.');
    }

    const decoded = JSON.parse(this.base64UrlDecode(payload)) as JwtPayload;
    if (!decoded.sub || (decoded.exp && decoded.exp < Math.floor(Date.now() / 1000))) {
      throw new UnauthorizedException('JWT token has expired.');
    }
    return decoded;
  }

  async register(email: string, password: string, name?: string) {
    const normalizedEmail = email.trim().toLowerCase();
    const existing = await this.prisma.user.findUnique({
      where: { email: normalizedEmail },
    });
    if (existing) {
      throw new ConflictException('Email is already registered.');
    }

    const passwordHash = await bcrypt.hash(password, 10);
    const user = await this.prisma.user.create({
      data: {
        email: normalizedEmail,
        passwordHash,
        name: name?.trim() || normalizedEmail.split('@')[0],
      },
      select: { id: true, email: true, name: true, createdAt: true },
    });

    const accessToken = this.signToken(user);
    return { accessToken, user };
  }

  async login(email: string, password: string) {
    const normalizedEmail = email.trim().toLowerCase();
    const user = await this.prisma.user.findUnique({
      where: { email: normalizedEmail },
    });
    if (!user) {
      throw new UnauthorizedException('Invalid email or password.');
    }

    const isMatch = await bcrypt.compare(password, user.passwordHash);
    if (!isMatch) {
      throw new UnauthorizedException('Invalid email or password.');
    }

    const safeUser = {
      id: user.id,
      email: user.email,
      name: user.name,
      createdAt: user.createdAt,
    };
    const accessToken = this.signToken(safeUser);
    return { accessToken, user: safeUser };
  }

  async getProfile(userId: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { id: true, email: true, name: true, createdAt: true },
    });
    if (!user) {
      throw new NotFoundException('User not found.');
    }
    return user;
  }
}
