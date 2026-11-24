# Deployment & Production Setup Guide

This guide describes how to run and deploy the **Social Media Auto Publishing Module** in both local development and Dockerized production environments.

---

## 1. Quick Local Development Setup

### Step 1: Start Core Infrastructure Services
Run PostgreSQL, Redis, and MinIO in the background:
```bash
docker-compose up -d
```

Verify container status:
```bash
docker-compose ps
```

You should see:
- `auto_pub_postgres`: port `5432`
- `auto_pub_redis`: port `6379`
- `auto_pub_minio`: ports `9000` (API) & `9001` (Web Console)

### Step 2: Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Update your `META_APP_ID`, `META_APP_SECRET`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET`.

### Step 3: Run Database Migrations
```bash
cd server
npx prisma generate
npx prisma migrate dev --name init
```

### Step 4: Run the Backend API Server
```bash
cd server
npm run start:dev
```
- API Base: `http://localhost:3000/api`
- Swagger OpenAPI Explorer: `http://localhost:3000/api/docs`

### Step 5: Run the Frontend React Application
```bash
cd client
npm run dev
```
- Web Application: `http://localhost:5173`

---

## 2. Production Docker Deployment

For production deployments, containerize the NestJS API and React build.

### Production `Dockerfile` for Backend (`server/Dockerfile`):
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
COPY prisma ./prisma/
RUN npm ci
COPY . .
RUN npx prisma generate
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules/.prisma ./node_modules/.prisma
COPY --from=builder /app/node_modules/@prisma ./node_modules/@prisma
EXPOSE 3000
CMD ["node", "dist/src/main"]
```

### Production `Dockerfile` for Frontend (`client/Dockerfile`):
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

---

## 3. Security Checklist for Production

- [ ] **Encryption Key**: Set `ENCRYPTION_KEY` to a cryptographically secure 64-character hex string (32 bytes). Never reuse the development key.
- [ ] **HTTPS / TLS Termination**: Terminate SSL using Nginx or Caddy reverse proxy. Meta Graph API requires HTTPS for production OAuth redirects.
- [ ] **PostgreSQL Password**: Change default PostgreSQL password and restrict port `5432` to internal container network.
- [ ] **Redis Password**: Change default Redis password and ensure append-only persistence (`--appendonly yes`) is enabled so delayed publishing jobs survive restarts.
- [ ] **MinIO / S3 Credentials**: Generate dedicated IAM access keys for your S3 bucket with least-privilege permissions.
- [ ] **Rate Limiting**: Throttling is configured via `@nestjs/throttler` to prevent abuse.
