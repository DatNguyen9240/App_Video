-- Cross-run deduplication & measurement-idempotent engagement snapshot registry in Cloudflare D1 (V3.2)
-- Shared across Muse (Cloudflare Connector), Worker V2, and multiple App_Video clients.

CREATE TABLE IF NOT EXISTS market_post_registry (
    dedup_key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    post_id TEXT,
    post_pk TEXT,
    canonical_url TEXT,
    content_hash TEXT,
    first_run_id TEXT,
    last_run_id TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    snapshot_count INTEGER NOT NULL DEFAULT 1,
    latest_views_display TEXT,
    latest_views_precision TEXT,
    latest_views_source TEXT,
    latest_views_measured_at TEXT,
    latest_views_evidence_url TEXT,
    latest_metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_market_post_registry_plat_post
    ON market_post_registry(platform, post_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_market_post_registry_plat_url_unique
    ON market_post_registry(platform, canonical_url)
    WHERE canonical_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_market_post_registry_last_seen
    ON market_post_registry(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS market_post_snapshots (
    id TEXT PRIMARY KEY,
    snapshot_idempotency_key TEXT NOT NULL UNIQUE,
    dedup_key TEXT NOT NULL,
    platform TEXT NOT NULL,
    post_id TEXT,
    run_id TEXT,
    measured_at TEXT,
    likes_count INTEGER,
    comments_count INTEGER,
    reposts_count INTEGER,
    quotes_count INTEGER,
    shares_count INTEGER,
    views_count INTEGER,
    views_display TEXT,
    views_precision TEXT,
    views_source TEXT,
    views_evidence_url TEXT,
    classification TEXT NOT NULL DEFAULT 'NEW',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_post_snapshots_key
    ON market_post_snapshots(dedup_key, measured_at ASC);
CREATE INDEX IF NOT EXISTS idx_market_post_snapshots_run
    ON market_post_snapshots(run_id);

CREATE TABLE IF NOT EXISTS registry_sync_outbox (
    id TEXT PRIMARY KEY,
    request_id TEXT,
    run_id TEXT,
    crawled_at TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_registry_sync_outbox_status
    ON registry_sync_outbox(status, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_registry_sync_outbox_request
    ON registry_sync_outbox(request_id, status);
