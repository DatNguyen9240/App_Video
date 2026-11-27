-- Cross-run deduplication & post engagement snapshot registry in Cloudflare D1
-- Shared between Muse crawls, Cloudflare Worker V2, and App_Video SyncService

CREATE TABLE IF NOT EXISTS market_post_registry (
    dedup_key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    post_id TEXT,
    canonical_url TEXT,
    first_run_id TEXT,
    last_run_id TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    snapshot_count INTEGER NOT NULL DEFAULT 1,
    latest_metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_market_post_registry_plat_url
    ON market_post_registry(platform, canonical_url);
CREATE INDEX IF NOT EXISTS idx_market_post_registry_last_seen
    ON market_post_registry(last_seen_at DESC);
