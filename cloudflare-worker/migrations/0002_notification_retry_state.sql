-- Migration 0002: Notification retry metadata for atomic reclaim of definite-unsent transient errors
-- Target D1 Database: app-video-db (binding: DB)
-- Additive-only migration on `crawl_notifications` preserving all existing rows.

ALTER TABLE crawl_notifications ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawl_notifications ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 5;
ALTER TABLE crawl_notifications ADD COLUMN next_retry_at TEXT;
ALTER TABLE crawl_notifications ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawl_notifications ADD COLUMN last_error TEXT;
ALTER TABLE crawl_notifications ADD COLUMN retry_payload_json TEXT;
