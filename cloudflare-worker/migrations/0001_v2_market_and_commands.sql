-- Migration 0001: App_Video Market Intelligence, Remote Command Queue, Crawl Center & Reports
-- Target D1 Database: app-video-db (binding: DB)
-- Non-destructive additive-only migration: preserves existing `crawl_notifications` table intact.

-- 1. Remote Command Queue (Telegram -> D1 -> App_Video outbound polling)
CREATE TABLE IF NOT EXISTS app_commands (
  id TEXT PRIMARY KEY,
  command_type TEXT NOT NULL CHECK (command_type IN ('app_status', 'sync_now', 'last_sync_result', 'market_stats')),
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
  requested_by_chat_id TEXT,
  telegram_message_id TEXT,
  dedup_token TEXT UNIQUE,
  claimed_by TEXT,
  claimed_at TEXT,
  lease_expires_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  result_json TEXT,
  error_message TEXT,
  notified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_commands_status_created
  ON app_commands(status, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_app_commands_lease
  ON app_commands(status, lease_expires_at);

-- 2. App_Video Online/Offline Heartbeat, Market Counts & Latest Report Snapshot
CREATE TABLE IF NOT EXISTS app_heartbeats (
  client_id TEXT PRIMARY KEY,
  app_status TEXT NOT NULL DEFAULT 'online',
  sync_status TEXT,
  last_sync_at TEXT,
  last_sync_summary_json TEXT,
  market_counts_json TEXT,
  latest_report_json TEXT,
  sync_interval_seconds INTEGER,
  app_version TEXT,
  last_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 3. Market Sync Audit (records incremental Drive reads by App_Video)
CREATE TABLE IF NOT EXISTS market_sync_audit (
  id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  drive_file_id TEXT,
  file_name TEXT,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_sync_audit_created
  ON market_sync_audit(created_at DESC);

-- 4. Crawl Center Queue (Muse polls D1 via official Cloudflare OAuth Connector)
-- Production polling: every 6 hours (production_6h); limited testing: every 5 minutes (test_5m).
-- Status stays 'queued' until Muse atomically claims it ('running').
CREATE TABLE IF NOT EXISTS crawl_requests (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('threads', 'tiktok', 'shopee', 'all')),
  keywords TEXT NOT NULL DEFAULT '',
  requested_count INTEGER NOT NULL DEFAULT 50,
  options_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
  dedup_token TEXT UNIQUE,
  muse_connector_mode TEXT NOT NULL DEFAULT 'd1_polling',
  poll_profile TEXT NOT NULL DEFAULT 'production_6h' CHECK (poll_profile IN ('production_6h', 'test_5m')),
  expected_pickup_at TEXT,
  claimed_by TEXT,
  claimed_at TEXT,
  lease_expires_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  requested_by_chat_id TEXT,
  requested_by_source TEXT NOT NULL DEFAULT 'telegram',
  result_summary_json TEXT,
  report_file_id TEXT,
  error_message TEXT,
  notified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_crawl_requests_status_created
  ON crawl_requests(status, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_crawl_requests_lease
  ON crawl_requests(status, lease_expires_at);

-- 5. Worker Runtime State (Muse poll schedule tracking, Telegram single primary status msg ID)
CREATE TABLE IF NOT EXISTS worker_runtime_state (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT NOT NULL
);
