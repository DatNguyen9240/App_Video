-- Migration 0005: Data Contract V3.3 (Quality-first Option A) + Target-based Multi-session Crawl
-- Preserves all historical rows and legacy V3.2 request semantics without rebuilding crawl_requests.
-- IMPORTANT: Legacy rows keep NULL contract_version / target_metric and are treated strictly as V3.2.

ALTER TABLE crawl_requests ADD COLUMN contract_version TEXT;
ALTER TABLE crawl_requests ADD COLUMN skill_version TEXT;
ALTER TABLE crawl_requests ADD COLUMN quality_policy_version TEXT;
ALTER TABLE crawl_requests ADD COLUMN target_metric TEXT;

ALTER TABLE crawl_requests ADD COLUMN execution_phase TEXT;
ALTER TABLE crawl_requests ADD COLUMN pause_reason TEXT;
ALTER TABLE crawl_requests ADD COLUMN session_seq INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawl_requests ADD COLUMN error_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawl_requests ADD COLUMN lease_renew_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE crawl_requests ADD COLUMN accumulated_quality_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawl_requests ADD COLUMN accumulated_new_qualified_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawl_requests ADD COLUMN accumulated_watchlist_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crawl_requests ADD COLUMN checkpoint_json TEXT;

CREATE TABLE IF NOT EXISTS crawl_request_sessions (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    session_seq INTEGER NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    claimed_by TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    lease_seconds INTEGER NOT NULL DEFAULT 3600,
    lease_expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    lease_renew_count INTEGER NOT NULL DEFAULT 0,
    pause_reason TEXT,
    stop_reason TEXT,
    run_id TEXT,
    scanned_count INTEGER DEFAULT 0,
    quality_qualified_count INTEGER DEFAULT 0,
    new_qualified_count INTEGER DEFAULT 0,
    watchlist_count INTEGER DEFAULT 0,
    ended_at TEXT,
    checkpoint_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(request_id, session_seq)
);

CREATE INDEX IF NOT EXISTS idx_crawl_sessions_req
    ON crawl_request_sessions(request_id, session_seq ASC);

CREATE TABLE IF NOT EXISTS crawl_request_quality_items (
    request_id TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    session_seq INTEGER,
    run_id TEXT,
    canonical_url TEXT,
    post_id TEXT,
    trend_tier TEXT,
    quality_qualified INTEGER NOT NULL DEFAULT 0,
    quality_exclusion_reason TEXT,
    is_traffic_content INTEGER NOT NULL DEFAULT 0,
    views_count INTEGER,
    likes_count INTEGER,
    comments_count INTEGER,
    shares_count INTEGER,
    reposts_count INTEGER,
    first_seen_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (request_id, dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_req_quality_items_req
    ON crawl_request_quality_items(request_id, quality_qualified);
