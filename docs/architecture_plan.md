# Social Media Auto Publishing Module - Architecture & Implementation Plan

## 1. System Overview

The **Social Media Auto Publishing Module** is an enterprise-grade, production-ready system for multi-platform social media management, content creation, media asset processing, intelligent scheduling, and automated publishing to **Facebook Pages** (via Meta Graph API v21.0) and **YouTube Channels** (via YouTube Data API v3).

```mermaid
flowchart TD
    subgraph Client["Frontend (React + Vite + Tailwind)"]
        UI_Dash["Dashboard & Analytics"]
        UI_Auth["Connected Accounts (OAuth)"]
        UI_Post["Create Post & Preview (FB / YouTube)"]
        UI_Media["Media Library & Uploader"]
        UI_Sched["Publishing Calendar & Queue"]
    end

    subgraph API["Backend (NestJS API Gateway)"]
        AuthModule["OAuth & Account Manager"]
        ContentModule["Content & Validation"]
        MediaModule["Media Service & Storage Engine"]
        PublishModule["Publishing Coordinator & Dispatcher"]
        DashboardModule["Metrics & Activity Aggregator"]
    end

    subgraph Engine["Publishing Engine & Adapters"]
        AdapterRegistry["SocialPlatformAdapter Registry"]
        FBAdapter["FacebookAdapter (Graph API v21.0)"]
        YTAdapter["YouTubeAdapter (Data API v3)"]
    end

    subgraph Queue["Async Jobs & Scheduling"]
        RedisQueue[("Redis + BullMQ")]
        PublishWorker["Publishing Worker (Idempotent, Retry, Backoff)"]
    end

    subgraph Storage["Persistent Storage"]
        Postgres[("PostgreSQL (Prisma ORM)")]
        MinIO[("MinIO / S3 Object Storage")]
    end

    subgraph SocialNetworks["Official External APIs"]
        MetaAPI["Meta Graph API (Pages, Photos, Videos, Reels)"]
        GoogleAPI["Google YouTube API (Resumable Upload, Shorts)"]
    end

    Client -->|REST / JSON| API
    API --> Postgres
    MediaModule --> MinIO
    PublishModule --> RedisQueue
    RedisQueue --> PublishWorker
    PublishWorker --> Engine
    FBAdapter --> MetaAPI
    YTAdapter --> GoogleAPI
```

---

## 2. Technology Stack & Key Libraries

| Component | Technology | Rationale |
|---|---|---|
| **Backend Framework** | NestJS (TypeScript) | Modular, clean architecture, dependency injection, enterprise-standard |
| **ORM & Database** | Prisma ORM + PostgreSQL | Strongly typed database client, safe migrations, JSON support, connection pooling |
| **Job Queue & Scheduling** | BullMQ + Redis | Atomic job locking, delayed job scheduling, exponential backoff, persistent across restarts |
| **Object Storage** | MinIO / AWS S3 SDK v3 | S3-compatible, multipart upload, pre-signed URLs, local Docker development |
| **Video & Media Processing** | fluent-ffmpeg / Sharp | Video duration/dimension detection, thumbnail generation, MIME verification |
| **Frontend Framework** | React 18 + Vite + TypeScript | Blazing fast build, strong typing, clean component hierarchy |
| **UI & Styling** | Tailwind CSS + Lucide Icons | Responsive modern SaaS design system |
| **Data Fetching & State** | TanStack Query v5 + Axios | Server-state caching, background revalidation, optimistic updates |

---

## 3. Database Schema (Prisma Entity Relationship)

```mermaid
erDiagram
    User ||--o{ SocialAccount : "owns"
    User ||--o{ Content : "creates"
    Content ||--o{ Media : "attaches"
    Content ||--o{ PublishingTask : "generates"
    SocialAccount ||--o{ PublishingTask : "executes_on"

    User {
        string id PK
        string email UK
        string passwordHash
        datetime createdAt
        datetime updatedAt
    }

    SocialAccount {
        string id PK
        string userId FK
        string platform "FACEBOOK | YOUTUBE"
        string platformAccountId "Page ID or Channel ID"
        string accountName
        string avatarUrl
        string accessTokenEncrypted
        string refreshTokenEncrypted
        datetime tokenExpiresAt
        string scopes
        string status "CONNECTED | EXPIRED | REVOKED | DISCONNECTED"
        json metadata "Page categories, channel subscriber count, etc."
        datetime createdAt
        datetime updatedAt
    }

    Content {
        string id PK
        string userId FK
        string title
        string caption
        string description
        string hashtags
        string status "DRAFT | SCHEDULED | PUBLISHED | PARTIALLY_PUBLISHED | FAILED | CANCELLED"
        json targetPlatforms "['FACEBOOK', 'YOUTUBE']"
        datetime scheduledAt
        datetime createdAt
        datetime updatedAt
    }

    Media {
        string id PK
        string contentId FK
        string storageKey
        string originalName
        string mimeType
        int fileSize
        float duration
        int width
        int height
        string thumbnailKey
        datetime createdAt
    }

    PublishingTask {
        string id PK
        string contentId FK
        string socialAccountId FK
        string platform "FACEBOOK | YOUTUBE"
        string status "PENDING | PROCESSING | PUBLISHED | FAILED | CANCELLED"
        datetime scheduledAt
        datetime publishedAt
        string externalPostId
        string externalPostUrl
        string errorCode
        string errorMessage
        int retryCount
        string idempotencyKey UK
        json platformMetadata
        datetime createdAt
        datetime updatedAt
    }
```

---

## 4. Platform Adapter Specifications

### A. Facebook Adapter (Meta Graph API v21.0)
- **Token Handling**: Long-lived Page Access Token exchanged from User Token via `fb_exchange_token`.
- **Text Post**: `POST /{page_id}/feed` -> body `{ message }`.
- **Photo Post**: `POST /{page_id}/photos` -> multipart upload or S3 public/presigned URL with `caption`.
- **Video Post & Reels**:
  - Regular video: `POST /{page_id}/videos` with `description` and `title`.
  - Reels workflow: `POST /{page_id}/video_reels` (Init session -> Upload binary chunks -> Finish/Publish).
- **Error Mapping**: Decodes Graph API error code & `error_subcode` (e.g. 190 = token expired, 200 = permission missing).

### B. YouTube Adapter (YouTube Data API v3)
- **Token Handling**: Google OAuth2 client with `access_type=offline`, automatic refresh via `refreshToken`.
- **Video Upload**: Resumable upload protocol (`POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`).
- **Metadata**: Title (<= 100 chars), Description (<= 5000 chars), Tags array, Category ID (default 22: People & Blogs), Privacy (`public`, `unlisted`, `private`).
- **Shorts Detection**: Vertical video (aspect ratio 9:16) with duration <= 60s is tagged with `#Shorts` for automatic Shorts shelf placement.
- **Scheduled Publishing**: Supports `status.publishAt` in RFC 3339 format when `status.privacyStatus = 'private'`.

---

## 5. Idempotent Scheduling & Partial Publishing Workflow

```mermaid
sequenceDiagram
    participant User
    participant API as NestJS API
    participant DB as PostgreSQL
    participant Queue as Redis / BullMQ
    participant Worker as Publishing Worker
    participant FB as Meta Graph API
    participant YT as YouTube API

    User->>API: Schedule Post (Target: Facebook + YouTube)
    API->>DB: Create Content (SCHEDULED)
    API->>DB: Create Task 1: Facebook (PENDING, idempotencyKey=hash(cId, accId, 1))
    API->>DB: Create Task 2: YouTube (PENDING, idempotencyKey=hash(cId, accId, 2))
    API->>Queue: Add Job 1 (delay = scheduledTime - now, taskId=Task1)
    API->>Queue: Add Job 2 (delay = scheduledTime - now, taskId=Task2)

    Note over Queue, Worker: At scheduled timestamp
    Queue->>Worker: Process Job 1 (Facebook)
    Worker->>DB: Check idempotency & set status = PROCESSING
    Worker->>FB: POST /{page_id}/videos
    FB-->>Worker: Success (id: 10492817293, url: https://facebook.com/...)
    Worker->>DB: Update Task 1 = PUBLISHED

    Queue->>Worker: Process Job 2 (YouTube)
    Worker->>DB: Check idempotency & set status = PROCESSING
    Worker->>YT: videos.insert
    YT-->>Worker: Error: 403 quotaExceeded
    Worker->>DB: Update Task 2 = FAILED (retryCount=1, error="quotaExceeded")
    Worker->>DB: Update Content = PARTIALLY_PUBLISHED
    Worker->>Queue: Re-queue Job 2 with Exponential Backoff (attempts=5)
    Note over Worker: Facebook is NEVER re-published!
```
