"""
Local Storage Layer for Market Intelligence (Data Contract V2).

Separates immutable raw Drive JSON archives (`data/market_intelligence/raw/`)
from normalized deduplicated/upserted records stored in SQLite (`market_intel.db`).
Enforces priority upsert (`corrected_v2` overrides original, original never overwrites
corrected), separates technical_status vs data_quality_status, isolates non-crawl files
(data_contract, strategy, audit), and tracks idempotency for Telegram Remote Control.
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .normalizer import (
    NON_CRAWL_ROLES,
    assess_v33_quality,
    extract_canonical_metrics,
    extract_v32_counters,
    is_archive_path,
    is_valid_v32_views_provenance,
    normalize_payload,
)
from .report_normalizer import (
    build_evidence_report_from_items,
    has_muse_report_sections,
    normalize_muse_report,
)
from .crawl_strategy import (
    THREADS_QUALITY_POLICY_VERSION,
    THREADS_SKILL_VERSION,
    get_mode_label as _get_crawl_mode_label,
    normalize_topics_list as _normalize_crawl_topics,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_NOT_ARCHIVE_RAW_SQL = (
    "(COALESCE(r.is_archived, 0) = 0 "
    "AND '/' || lower(replace(COALESCE(r.folder_path, ''), '\\', '/')) || '/' NOT LIKE '%/archive/%' "
    "AND '/' || lower(replace(COALESCE(r.file_name, ''), '\\', '/')) || '/' NOT LIKE '%/archive/%')"
)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name or "data.json").strip("._")
    return cleaned or "data.json"


class MarketIntelligenceStore:
    """Thread-safe local storage managing raw JSON archives and normalized SQLite items."""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            data_dir = os.path.join(base_dir, "data", "market_intelligence")

        self.data_dir = os.path.abspath(data_dir)
        self.raw_dir = os.path.join(self.data_dir, "raw")
        self.db_path = os.path.join(self.data_dir, "market_intel.db")
        os.makedirs(self.raw_dir, exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=10000;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._init_schema()
            self._cleanup_orphaned_running_syncs()
        self._ensure_run_centric_backfill()
        if not self.get_state("metrics_cache_repaired_v1"):
            try:
                rep_summary = self.repair_cached_metrics_from_raw_payloads()
                self.set_state("metrics_cache_repaired_v1", json.dumps(rep_summary))
            except Exception:
                pass

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def _add_column_if_missing(self, table: str, column: str, ddl_type: str):
        cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

    def _init_schema(self):
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_drive_files (
                file_id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                folder_path TEXT,
                modified_time TEXT,
                md5_checksum TEXT,
                size_bytes INTEGER,
                schema_version TEXT,
                raw_path TEXT NOT NULL,
                record_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'synced',
                error_message TEXT,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_items (
                id TEXT PRIMARY KEY,
                dedup_key TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL,
                external_id TEXT,
                post_id TEXT,
                item_id TEXT,
                shop_id TEXT,
                author_handle TEXT,
                author_name TEXT,
                title TEXT,
                text_content TEXT,
                url TEXT,
                content_group TEXT,
                purchase_intent TEXT,
                language TEXT,
                published_at TEXT,
                crawled_at TEXT,
                likes_count INTEGER,
                comments_count INTEGER,
                shares_count INTEGER,
                views_count INTEGER,
                price REAL,
                sold_count INTEGER,
                rating REAL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                extra_meta_json TEXT NOT NULL DEFAULT '{}',
                schema_version TEXT,
                raw_file_id TEXT,
                raw_item_index INTEGER,
                raw_payload_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (raw_file_id) REFERENCES raw_drive_files(file_id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_market_items_platform ON market_items(platform);
            CREATE INDEX IF NOT EXISTS idx_market_items_content_group ON market_items(content_group);
            CREATE INDEX IF NOT EXISTS idx_market_items_purchase_intent ON market_items(purchase_intent);
            CREATE INDEX IF NOT EXISTS idx_market_items_published_at ON market_items(published_at DESC);

            CREATE TABLE IF NOT EXISTS sync_runs (
                run_id TEXT PRIMARY KEY,
                trigger_source TEXT NOT NULL,
                command_id TEXT,
                status TEXT NOT NULL,
                files_checked INTEGER NOT NULL DEFAULT 0,
                files_downloaded INTEGER NOT NULL DEFAULT 0,
                records_parsed INTEGER NOT NULL DEFAULT 0,
                records_inserted INTEGER NOT NULL DEFAULT 0,
                records_updated INTEGER NOT NULL DEFAULT 0,
                records_deduped INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS remote_command_log (
                command_id TEXT PRIMARY KEY,
                command_type TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error_message TEXT,
                pending_ack INTEGER NOT NULL DEFAULT 0,
                claimed_at TEXT NOT NULL,
                completed_at TEXT,
                acked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS market_reports (
                id TEXT PRIMARY KEY,
                report_key TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                platform TEXT NOT NULL,
                raw_file_id TEXT,
                report_json TEXT NOT NULL,
                raw_payload_json TEXT,
                generated_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (raw_file_id) REFERENCES raw_drive_files(file_id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS market_item_snapshots (
                id TEXT PRIMARY KEY,
                snapshot_source_key TEXT NOT NULL UNIQUE,
                item_id TEXT NOT NULL,
                dedup_key TEXT NOT NULL,
                canonical_url TEXT,
                platform TEXT NOT NULL,
                run_id TEXT,
                raw_file_id TEXT,
                snapshot_at TEXT NOT NULL,
                views_count INTEGER,
                likes_count INTEGER,
                comments_count INTEGER,
                shares_count INTEGER,
                reposts_count INTEGER,
                quotes_count INTEGER,
                price REAL,
                sold_count INTEGER,
                views_delta INTEGER,
                likes_delta INTEGER,
                comments_delta INTEGER,
                shares_delta INTEGER,
                reposts_delta INTEGER,
                change_type TEXT NOT NULL DEFAULT 'new',
                measured_at TEXT,
                views_display TEXT,
                views_precision TEXT,
                views_source TEXT,
                views_evidence_url TEXT,
                classification TEXT,
                content_hash TEXT,
                similar_content INTEGER NOT NULL DEFAULT 0,
                similar_to_post_id TEXT,
                engagement_rate_on_views REAL,
                engagement_rate_approx INTEGER,
                first_seen_run_id TEXT,
                last_seen_run_id TEXT,
                snapshot_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES market_items(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_item_id ON market_item_snapshots(item_id, snapshot_at ASC);
            CREATE INDEX IF NOT EXISTS idx_snapshots_dedup_key ON market_item_snapshots(dedup_key, snapshot_at ASC);

            CREATE TABLE IF NOT EXISTS registry_sync_outbox (
                id TEXT PRIMARY KEY,
                run_id TEXT,
                payload_json TEXT NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_registry_outbox_created ON registry_sync_outbox(created_at ASC);
            """
        )

        # Additive Data Contract V2 & V3.1/V3.2 + Cross-Run Dedup columns
        self._add_column_if_missing("raw_drive_files", "file_role", "TEXT DEFAULT 'crawl_data'")
        self._add_column_if_missing("raw_drive_files", "is_corrected", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "corrects_file", "TEXT")
        self._add_column_if_missing("raw_drive_files", "run_status", "TEXT")
        self._add_column_if_missing("raw_drive_files", "technical_status", "TEXT")
        self._add_column_if_missing("raw_drive_files", "data_quality_status", "TEXT")
        self._add_column_if_missing("raw_drive_files", "business_usable_records", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "run_meta_json", "TEXT DEFAULT '{}'")
        self._add_column_if_missing("raw_drive_files", "is_archived", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "archived_at", "TEXT")
        self._add_column_if_missing("raw_drive_files", "new_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "new_qualified_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "duplicate_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "updated_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "missing_views_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "rejected_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "scanned_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "unique_candidate_count", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "unverified_count", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "stop_reason", "TEXT")
        self._add_column_if_missing("raw_drive_files", "target_achieved", "INTEGER")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_drive_files_archived ON raw_drive_files(is_archived);")

        self._add_column_if_missing("sync_runs", "new_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("sync_runs", "new_qualified_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("sync_runs", "duplicate_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("sync_runs", "updated_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("sync_runs", "missing_views_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("sync_runs", "rejected_count", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("sync_runs", "scanned_count", "INTEGER NOT NULL DEFAULT 0")

        self._add_column_if_missing("market_item_snapshots", "measured_at", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "views_display", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "views_precision", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "views_source", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "views_evidence_url", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "classification", "TEXT")

        self._add_column_if_missing("market_items", "canonical_url", "TEXT")
        self._add_column_if_missing("market_items", "views_display", "TEXT")
        self._add_column_if_missing("market_items", "views_precision", "TEXT")
        self._add_column_if_missing("market_items", "views_source", "TEXT")
        self._add_column_if_missing("market_items", "views_measured_at", "TEXT")
        self._add_column_if_missing("market_items", "views_evidence_url", "TEXT")
        self._add_column_if_missing("market_items", "classification", "TEXT")
        self._add_column_if_missing("market_items", "content_hash", "TEXT")
        self._add_column_if_missing("market_items", "similar_content", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("market_items", "similar_to_post_id", "TEXT")
        self._add_column_if_missing("market_items", "engagement_rate_on_views", "REAL")
        self._add_column_if_missing("market_items", "engagement_rate_approx", "INTEGER")
        self._add_column_if_missing("market_items", "last_change_type", "TEXT NOT NULL DEFAULT 'new'")
        self._add_column_if_missing("market_items", "snapshot_count", "INTEGER NOT NULL DEFAULT 1")
        self._add_column_if_missing("market_items", "views_missing", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("market_items", "last_views_delta", "INTEGER")
        self._add_column_if_missing("market_items", "last_likes_delta", "INTEGER")
        self._add_column_if_missing("market_items", "last_comments_delta", "INTEGER")
        self._add_column_if_missing("market_items", "last_shares_delta", "INTEGER")
        self._add_column_if_missing("market_items", "last_reposts_delta", "INTEGER")
        self._add_column_if_missing("market_items", "data_quality", "TEXT")
        self._add_column_if_missing("market_items", "quality_issues_json", "TEXT NOT NULL DEFAULT '[]'")
        self._add_column_if_missing("market_items", "source_priority", "INTEGER NOT NULL DEFAULT 1")
        self._add_column_if_missing("market_items", "is_corrected", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("market_items", "summary_vi", "TEXT")
        self._add_column_if_missing("market_items", "run_id", "TEXT")
        self._add_column_if_missing("market_items", "reposts_count", "INTEGER")
        self._add_column_if_missing("market_items", "quotes_count", "INTEGER")
        self._add_column_if_missing("market_items", "trend_tier", "TEXT")
        self._add_column_if_missing("market_items", "trend_group", "TEXT")
        self._add_column_if_missing("market_items", "verification_status", "TEXT")
        self._add_column_if_missing("market_items", "metrics_source", "TEXT")
        self._add_column_if_missing("market_items", "post_pk", "TEXT")
        self._add_column_if_missing("market_items", "evidence_url", "TEXT")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_canonical_url ON market_items(platform, canonical_url);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_change_type ON market_items(last_change_type, snapshot_count);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_quality ON market_items(data_quality);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_tier ON market_items(trend_tier);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_group ON market_items(trend_group);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_verif ON market_items(verification_status);")

        self._add_column_if_missing("market_reports", "is_corrected", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("market_reports", "corrects_report", "TEXT")
        self._add_column_if_missing("market_reports", "request_id", "TEXT")
        self._add_column_if_missing("market_reports", "run_status", "TEXT")
        self._add_column_if_missing("market_reports", "run_id", "TEXT")
        self._add_column_if_missing("market_reports", "crawl_time", "TEXT")
        self._add_column_if_missing("market_reports", "technical_status", "TEXT")
        self._add_column_if_missing("market_reports", "data_quality_status", "TEXT")
        self._add_column_if_missing("market_reports", "business_usable_records", "INTEGER")
        self._add_column_if_missing("market_reports", "source_priority", "INTEGER NOT NULL DEFAULT 1")

        self._add_column_if_missing("remote_command_log", "attempt", "INTEGER")
        self._add_column_if_missing("remote_command_log", "claimed_by", "TEXT")
        self._add_column_if_missing("remote_command_log", "lease_expires_at", "TEXT")

        # Run-centric raw file metadata & point-in-time snapshot columns (additive)
        self._add_column_if_missing("raw_drive_files", "request_id", "TEXT")
        self._add_column_if_missing("raw_drive_files", "run_id", "TEXT")
        self._add_column_if_missing("raw_drive_files", "platform", "TEXT")
        self._add_column_if_missing("raw_drive_files", "crawl_time", "TEXT")
        self._add_column_if_missing("raw_drive_files", "requested_count", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "duplicate_current_run_count", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "corrects_report", "TEXT")
        self._add_column_if_missing("raw_drive_files", "strategy_version", "TEXT")
        self._add_column_if_missing("raw_drive_files", "contract_version", "TEXT")
        self._add_column_if_missing("raw_drive_files", "special_counts_json", "TEXT")
        self._add_column_if_missing("raw_drive_files", "crawl_mode", "TEXT")
        self._add_column_if_missing("raw_drive_files", "topics_json", "TEXT")
        self._add_column_if_missing("raw_drive_files", "keywords", "TEXT")
        self._add_column_if_missing("raw_drive_files", "lookback_hours", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "session_seq", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "is_checkpoint", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "quality_qualified_count", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "watchlist_count", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "traffic_content_count", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "quality_policy_version", "TEXT")
        self._add_column_if_missing("raw_drive_files", "below_hot_count", "INTEGER")

        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_request_metadata_cache (
                request_id TEXT PRIMARY KEY,
                platform TEXT,
                crawl_mode TEXT,
                crawl_mode_label TEXT,
                topics_json TEXT,
                topic_labels_json TEXT,
                keywords TEXT,
                lookback_hours INTEGER,
                exclusions_json TEXT,
                updated_at TEXT
            )
            """
        )
        self._add_column_if_missing("crawl_request_metadata_cache", "contract_version", "TEXT")
        self._add_column_if_missing("crawl_request_metadata_cache", "skill_version", "TEXT")
        self._add_column_if_missing("crawl_request_metadata_cache", "quality_policy_version", "TEXT")
        self._add_column_if_missing("crawl_request_metadata_cache", "target_metric", "TEXT")
        self._add_column_if_missing("crawl_request_metadata_cache", "execution_phase", "TEXT")
        self._add_column_if_missing("crawl_request_metadata_cache", "pause_reason", "TEXT")
        self._add_column_if_missing("crawl_request_metadata_cache", "session_seq", "INTEGER")
        self._add_column_if_missing("crawl_request_metadata_cache", "lease_renew_count", "INTEGER")
        self._add_column_if_missing("crawl_request_metadata_cache", "accumulated_quality_count", "INTEGER")
        self._add_column_if_missing("crawl_request_metadata_cache", "accumulated_new_qualified_count", "INTEGER")
        self._add_column_if_missing("crawl_request_metadata_cache", "accumulated_watchlist_count", "INTEGER")

        self._add_column_if_missing("market_item_snapshots", "trend_tier", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "trend_group", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "verification_status", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "data_quality", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "summary_vi", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "title", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "extra_meta_json", "TEXT")
        self._add_column_if_missing("market_item_snapshots", "source_priority", "INTEGER DEFAULT 1")
        self._add_column_if_missing("market_item_snapshots", "is_corrected", "INTEGER DEFAULT 0")

        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_crawl_audit_items (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                run_id TEXT,
                request_id TEXT,
                dedup_key TEXT,
                platform TEXT,
                post_id TEXT,
                canonical_url TEXT,
                classification TEXT,
                trend_tier TEXT,
                quality_exclusion_reason TEXT,
                views_count INTEGER,
                likes_count INTEGER,
                comments_count INTEGER,
                is_v33 INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(file_id, dedup_key)
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_items_run ON market_crawl_audit_items(run_id, file_id);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_run_id ON market_item_snapshots(run_id, platform);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_raw_file ON market_item_snapshots(raw_file_id, platform);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_files_run_id ON raw_drive_files(run_id);")

        self._conn.commit()

    def _cleanup_orphaned_running_syncs(self):
        """Mark stale 'running' sync_runs interrupted by prior process exit as failed."""
        now_iso = _utc_now_iso()
        self._conn.execute(
            """
            UPDATE sync_runs
            SET status = 'failed', finished_at = ?,
                error_message = COALESCE(error_message, 'Interrupted when App_Video stopped')
            WHERE status = 'running' AND finished_at IS NULL
            """,
            (now_iso,),
        )
        self._conn.commit()

    def repair_cached_metrics_from_raw_payloads(self, force: bool = False) -> Dict[str, Any]:
        """
        Repair NULL/missing cached metric columns (e.g., likes_count=0 read from metrics.likes)
        strictly from stored raw_payload_json / raw Drive archives.
        NEVER alters raw_payload_json, timestamps, classifications, trend_tier, verification_status, or reports.
        """
        items_scanned = 0
        items_repaired = 0
        likes_repaired = 0
        snapshots_repaired = 0
        file_raw_cache: Dict[str, List[Dict[str, Any]]] = {}

        def _load_raw_file_items(fid: str) -> List[Dict[str, Any]]:
            if fid in file_raw_cache:
                return file_raw_cache[fid]
            row = self._conn.execute(
                "SELECT raw_path, file_name, folder_path FROM raw_drive_files WHERE file_id = ?",
                (fid,),
            ).fetchone()
            items_list: List[Dict[str, Any]] = []
            if row and row["raw_path"] and os.path.exists(row["raw_path"]):
                try:
                    with open(row["raw_path"], "rb") as rf:
                        raw_obj = json.loads(rf.read().decode("utf-8"))
                    norm = normalize_payload(raw_obj, file_name=row["file_name"], folder_path=row["folder_path"])
                    items_list = norm.get("items") or []
                except Exception:
                    items_list = []
            file_raw_cache[fid] = items_list
            return items_list

        metric_cols = (
            ("likes_count", "likes"),
            ("comments_count", "comments"),
            ("shares_count", "shares"),
            ("reposts_count", "reposts"),
            ("quotes_count", "quotes"),
            ("views_count", "views"),
            ("price", "price"),
            ("sold_count", "sold"),
            ("rating", "rating"),
        )
        snap_metric_cols = (
            "likes_count",
            "comments_count",
            "shares_count",
            "reposts_count",
            "quotes_count",
            "views_count",
            "price",
            "sold_count",
        )

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, dedup_key, raw_file_id, raw_payload_json,
                       likes_count, comments_count, shares_count, reposts_count, quotes_count,
                       views_count, price, sold_count, rating, metrics_json
                FROM market_items
                WHERE raw_payload_json IS NOT NULL AND raw_payload_json != ''
                """
            ).fetchall()

            for r in rows:
                items_scanned += 1
                try:
                    raw_obj = json.loads(r["raw_payload_json"])
                except Exception:
                    continue
                if not isinstance(raw_obj, dict):
                    continue
                canon = extract_canonical_metrics(raw_obj)
                if not canon:
                    continue

                updates: Dict[str, Any] = {}
                try:
                    cur_metrics = json.loads(r["metrics_json"] or "{}")
                    if not isinstance(cur_metrics, dict):
                        cur_metrics = {}
                except Exception:
                    cur_metrics = {}
                metrics_dirty = False

                for col_name, compact_key in metric_cols:
                    canon_val = canon.get(col_name)
                    if canon_val is None:
                        continue
                    cur_val = r[col_name]
                    if cur_val is None or (force and cur_val != canon_val):
                        updates[col_name] = canon_val
                        if col_name == "likes_count" and cur_val is None:
                            likes_repaired += 1
                    if compact_key not in cur_metrics or cur_metrics.get(compact_key) is None:
                        cur_metrics[compact_key] = canon_val
                        metrics_dirty = True

                if updates or metrics_dirty:
                    if metrics_dirty:
                        updates["metrics_json"] = json.dumps(cur_metrics, ensure_ascii=False)
                    if updates:
                        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
                        vals = list(updates.values()) + [r["id"]]
                        self._conn.execute(f"UPDATE market_items SET {set_clause} WHERE id = ?", vals)
                        items_repaired += 1

            snap_rows = self._conn.execute(
                """
                SELECT s.id, s.item_id, s.dedup_key, s.raw_file_id,
                       s.likes_count, s.comments_count, s.shares_count, s.reposts_count, s.quotes_count,
                       s.views_count, s.price, s.sold_count,
                       m.raw_file_id AS item_raw_file_id, m.raw_payload_json AS item_raw_payload_json
                FROM market_item_snapshots s
                JOIN market_items m ON m.id = s.item_id
                WHERE s.likes_count IS NULL OR s.views_count IS NULL OR s.comments_count IS NULL
                   OR s.shares_count IS NULL OR s.reposts_count IS NULL OR s.quotes_count IS NULL
                """
            ).fetchall()

            for sr in snap_rows:
                canon_snap: Optional[Dict[str, Any]] = None
                s_fid = sr["raw_file_id"]
                if s_fid and s_fid != sr["item_raw_file_id"]:
                    for cand in _load_raw_file_items(str(s_fid)):
                        if cand.get("dedup_key") == sr["dedup_key"]:
                            canon_snap = cand
                            break
                if canon_snap is None and (not s_fid or s_fid == sr["item_raw_file_id"]) and sr["item_raw_payload_json"]:
                    try:
                        raw_obj = json.loads(sr["item_raw_payload_json"])
                        if isinstance(raw_obj, dict):
                            canon_snap = extract_canonical_metrics(raw_obj)
                    except Exception:
                        canon_snap = None
                if not canon_snap:
                    continue

                s_updates: Dict[str, Any] = {}
                for scol in snap_metric_cols:
                    val = canon_snap.get(scol)
                    if sr[scol] is None and val is not None:
                        s_updates[scol] = val
                if s_updates:
                    set_clause = ", ".join(f"{k} = ?" for k in s_updates.keys())
                    self._conn.execute(
                        f"UPDATE market_item_snapshots SET {set_clause} WHERE id = ?",
                        list(s_updates.values()) + [sr["id"]],
                    )
                    snapshots_repaired += 1

            self._conn.commit()
        return {
            "items_scanned": items_scanned,
            "items_repaired": items_repaired,
            "likes_repaired": likes_repaired,
            "snapshots_repaired": snapshots_repaired,
        }

    # ─── Sync State KV Helpers ─────────────────────────────────────────────────

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: Optional[str]):
        with self._lock:
            self._conn.execute(
                "INSERT INTO sync_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_sync_interval_seconds(self, default: int = 300) -> int:
        raw = self.get_state("sync_interval_seconds")
        try:
            val = int(raw) if raw is not None else default
            return max(30, val)
        except (ValueError, TypeError):
            return default

    def set_sync_interval_seconds(self, seconds: int) -> int:
        clamped = max(30, int(seconds))
        self.set_state("sync_interval_seconds", str(clamped))
        return clamped

    # ─── Incremental File Check & Raw Archive ──────────────────────────────────

    def is_file_unchanged(
        self,
        file_id: str,
        modified_time: Optional[str] = None,
        md5_checksum: Optional[str] = None,
    ) -> bool:
        """Return True when file_id was already synced and neither md5 nor modifiedTime changed."""
        with self._lock:
            row = self._conn.execute(
                "SELECT modified_time, md5_checksum, status, raw_path FROM raw_drive_files WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            if not row or row["status"] != "synced":
                return False
            if not row["raw_path"] or not os.path.exists(row["raw_path"]):
                return False
            if md5_checksum and row["md5_checksum"]:
                return md5_checksum == row["md5_checksum"]
            if modified_time and row["modified_time"]:
                return modified_time == row["modified_time"]
            return False

    def save_raw_bytes(self, file_id: str, file_name: str, raw_bytes: bytes) -> str:
        safe_id = _safe_filename(file_id)
        safe_name = _safe_filename(file_name)
        if not safe_name.lower().endswith(".json"):
            safe_name += ".json"
        dest_path = os.path.join(self.raw_dir, f"{safe_id}__{safe_name}")
        with open(dest_path, "wb") as f:
            f.write(raw_bytes)
        return dest_path

    # ─── Ingestion & Priority Upsert (Data Contract V2) ────────────────────────

    @staticmethod
    def _compute_metric_delta(new_val: Any, old_val: Any) -> Optional[int]:
        if new_val is None or old_val is None:
            return None
        try:
            return int(new_val) - int(old_val)
        except Exception:
            return None

    @staticmethod
    def _snapshot_source_key(
        item_id: str,
        raw_file_id: Optional[str],
        md5_checksum: Optional[str],
        *,
        dedup_key: Optional[str] = None,
        run_id: Optional[str] = None,
        measured_at: Optional[str] = None,
    ) -> str:
        """
        Compute measurement-based snapshot idempotency key independent of which laptop synced it.
        When run_id is known, two laptops syncing the same run/measurement produce the exact same key.
        """
        post_tok = dedup_key or item_id
        if run_id:
            raw_token = f"{post_tok}::run:{run_id}::m:{measured_at or ''}"
        else:
            raw_token = f"{post_tok}::file:{raw_file_id or ''}::md5:{md5_checksum or ''}"
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _is_item_qualified_v32(item: Dict[str, Any]) -> bool:
        """Data Contract V3.2: Only records passing quality/verification WITH full valid V3.2 views provenance count toward new_qualified_count."""
        if not is_valid_v32_views_provenance(item):
            return False
        if str(item.get("verification_status") or "").lower() == "unverified":
            return False
        if str(item.get("data_quality") or "").lower() == "needs_verification":
            return False
        if str(item.get("trend_tier") or "").lower() == "unverified":
            return False
        cls = str(item.get("classification") or "").upper()
        if cls in ("UNVERIFIED", "DUPLICATE_HISTORY", "UPDATED_METRICS", "REJECTED"):
            return False
        return True

    def enqueue_registry_batch(self, items: List[Dict[str, Any]], run_id: Optional[str] = None, chunk_size: int = 100) -> int:
        if not items:
            return 0
        now_iso = _utc_now_iso()
        step = max(1, min(int(chunk_size or 100), 250))
        enqueued = 0
        with self._lock:
            for i in range(0, len(items), step):
                chunk = items[i : i + step]
                bid = f"reg_{uuid.uuid4().hex[:16]}"
                self._conn.execute(
                    "INSERT INTO registry_sync_outbox (id, run_id, payload_json, item_count, attempts, last_error, created_at) VALUES (?, ?, ?, ?, 0, NULL, ?)",
                    (bid, run_id, json.dumps(chunk, ensure_ascii=False), len(chunk), now_iso),
                )
                enqueued += 1
            self._conn.commit()
        return enqueued

    def list_pending_registry_batches(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM registry_sync_outbox ORDER BY created_at ASC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["items"] = json.loads(d.pop("payload_json") or "[]")
                except Exception:
                    d["items"] = []
                out.append(d)
            return out

    def ack_registry_batch(self, batch_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM registry_sync_outbox WHERE id = ?", (batch_id,))
            self._conn.commit()

    def mark_registry_batch_failed(self, batch_id: str, error_msg: str):
        with self._lock:
            self._conn.execute(
                "UPDATE registry_sync_outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                (str(error_msg or "")[:400], batch_id),
            )
            self._conn.commit()

    def _record_audit_item_v33(
        self,
        *,
        file_id: str,
        run_id: Optional[str],
        request_id: Optional[str],
        item: Dict[str, Any],
        q_item: Dict[str, Any],
        now_iso: str,
    ) -> None:
        """Persist minimal audit/dedup metadata only for V3.3 below-Hot/unverified/rejected items (never into market_items)."""
        audit_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO market_crawl_audit_items (
                id, file_id, run_id, request_id, dedup_key, platform, post_id, canonical_url,
                classification, trend_tier, quality_exclusion_reason, views_count, likes_count, comments_count,
                is_v33, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(file_id, dedup_key) DO UPDATE SET
                run_id = COALESCE(excluded.run_id, market_crawl_audit_items.run_id),
                classification = excluded.classification,
                trend_tier = excluded.trend_tier,
                quality_exclusion_reason = excluded.quality_exclusion_reason,
                views_count = excluded.views_count,
                likes_count = excluded.likes_count,
                comments_count = excluded.comments_count
            """,
            (
                audit_id,
                file_id,
                run_id,
                request_id,
                item.get("dedup_key"),
                item.get("platform"),
                item.get("post_id"),
                item.get("canonical_url") or item.get("url"),
                str(item.get("classification") or "").upper() or None,
                q_item.get("quality_tier_basis") or item.get("trend_tier"),
                q_item.get("quality_exclusion_reason"),
                item.get("views_count"),
                item.get("likes_count"),
                item.get("comments_count"),
                now_iso,
            ),
        )

    def get_run_audit_diagnostics(
        self,
        raw_file_ids: Optional[Any] = None,
        run_id: Optional[str] = None,
        run_ids: Optional[List[str]] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return minimal technical audit diagnostics (below-Hot, unverified, rejected) for a run or bundle."""
        single_key = str(raw_file_ids).strip() if isinstance(raw_file_ids, str) else ""
        fids = [] if isinstance(raw_file_ids, str) else [str(f).strip() for f in (raw_file_ids or []) if str(f or "").strip()]
        rids = [str(r).strip() for r in (run_ids or ([run_id] if run_id else [])) if str(r or "").strip()]
        clauses: List[str] = []
        params: List[Any] = []
        if single_key and not rids:
            clauses.append("(file_id = ? OR run_id = ?)")
            params.extend([single_key, single_key])
        elif fids:
            clauses.append(f"file_id IN ({','.join('?' for _ in fids)})")
            params.extend(fids)
        elif rids:
            clauses.append(f"run_id IN ({','.join('?' for _ in rids)})")
            params.extend(rids)
        else:
            return {"total": 0, "below_hot_count": 0, "unverified_count": 0, "rejected_count": 0, "items": []}

        where_sql = f"WHERE {' AND '.join(clauses)}"
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT file_id, run_id, dedup_key, platform, post_id, canonical_url, canonical_url AS url,
                       classification, trend_tier, quality_exclusion_reason,
                       views_count, views_count AS views,
                       likes_count, likes_count AS likes,
                       comments_count, created_at
                FROM market_crawl_audit_items
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, max(1, min(int(limit), 200))),
            ).fetchall()
            cnt_row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN COALESCE(classification, '') = 'REJECTED' THEN 1 ELSE 0 END) AS rejected_cnt,
                       SUM(CASE WHEN COALESCE(classification, '') != 'REJECTED' AND (classification = 'UNVERIFIED' OR trend_tier = 'unverified') THEN 1 ELSE 0 END) AS unverified_cnt,
                       SUM(CASE WHEN COALESCE(classification, '') NOT IN ('REJECTED', 'UNVERIFIED') AND COALESCE(trend_tier, '') != 'unverified' THEN 1 ELSE 0 END) AS below_hot_cnt
                FROM market_crawl_audit_items
                {where_sql}
                """,
                tuple(params),
            ).fetchone()
        return {
            "total": int((cnt_row["total"] or 0) if cnt_row else 0),
            "below_hot_count": int((cnt_row["below_hot_cnt"] or 0) if cnt_row else 0),
            "unverified_count": int((cnt_row["unverified_cnt"] or 0) if cnt_row else 0),
            "rejected_count": int((cnt_row["rejected_cnt"] or 0) if cnt_row else 0),
            "items": [dict(r) for r in rows],
        }

    def ingest_raw_file(
        self,
        file_id: str,
        file_name: str,
        raw_bytes: bytes,
        modified_time: Optional[str] = None,
        md5_checksum: Optional[str] = None,
        folder_path: Optional[str] = None,
        platform_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Archive raw JSON file and priority-upsert normalized items while preserving cross-run engagement snapshots."""
        if is_archive_path(folder_path) or is_archive_path(file_name):
            return {
                "file_role": "archived",
                "is_corrected": False,
                "parsed": 0,
                "inserted": 0,
                "updated": 0,
                "deduped": 0,
                "new_count": 0,
                "duplicate_count": 0,
                "engagement_updated_count": 0,
                "missing_views_count": 0,
                "scanned_count": 0,
                "rejected_count": 0,
            }

        raw_path = self.save_raw_bytes(file_id, file_name, raw_bytes)
        computed_md5 = md5_checksum or hashlib.md5(raw_bytes).hexdigest()
        now_iso = _utc_now_iso()

        try:
            raw_obj = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO raw_drive_files
                    (file_id, file_name, folder_path, modified_time, md5_checksum, size_bytes, schema_version, raw_path, record_count, status, error_message, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0, 'failed', ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        file_name = excluded.file_name,
                        modified_time = excluded.modified_time,
                        md5_checksum = excluded.md5_checksum,
                        size_bytes = excluded.size_bytes,
                        raw_path = excluded.raw_path,
                        status = 'failed',
                        error_message = excluded.error_message,
                        synced_at = excluded.synced_at
                    """,
                    (file_id, file_name, folder_path, modified_time, computed_md5, len(raw_bytes), raw_path, str(exc), now_iso),
                )
                self._conn.commit()
            raise ValueError(f"Invalid JSON in {file_name}: {exc}") from exc

        normalized = normalize_payload(
            raw_obj,
            file_name=file_name,
            platform_hint=platform_hint,
            folder_path=folder_path,
        )
        file_role = normalized["file_role"]
        is_corrected = normalized["is_corrected"]
        schema_version = normalized["schema_version"]
        envelope = normalized["envelope"] or {}
        items = normalized["items"]

        req_ctx = envelope.get("crawl") if isinstance(envelope.get("crawl"), dict) else {}
        src_run_ctx = envelope.get("source_run") if isinstance(envelope.get("source_run"), dict) else {}
        raw_crawl_mode = self._clean_run_str(
            envelope.get("crawl_mode")
            or req_ctx.get("crawl_mode")
            or src_run_ctx.get("crawl_mode")
        )
        raw_topics_raw = (
            envelope.get("topics")
            if envelope.get("topics") is not None
            else (req_ctx.get("topics") if req_ctx.get("topics") is not None else src_run_ctx.get("topics"))
        )
        norm_topic_ids, _ = _normalize_crawl_topics(raw_topics_raw, strict=False)
        raw_keywords_meta = self._clean_run_str(
            envelope.get("keywords")
            or req_ctx.get("keywords")
            or src_run_ctx.get("keywords")
        )
        raw_lookback_val = (
            envelope.get("lookback_hours")
            if envelope.get("lookback_hours") is not None
            else (req_ctx.get("lookback_hours") if req_ctx.get("lookback_hours") is not None else src_run_ctx.get("lookback_hours"))
        )
        try:
            raw_lookback_int = int(raw_lookback_val) if raw_lookback_val is not None and not isinstance(raw_lookback_val, bool) else None
        except (TypeError, ValueError):
            raw_lookback_int = None

        raw_session_seq_val = (
            envelope.get("session_seq")
            if envelope.get("session_seq") is not None
            else (req_ctx.get("session_seq") if req_ctx.get("session_seq") is not None else src_run_ctx.get("session_seq"))
        )
        try:
            raw_session_seq = int(raw_session_seq_val) if raw_session_seq_val is not None and not isinstance(raw_session_seq_val, bool) else None
        except (TypeError, ValueError):
            raw_session_seq = None
        raw_is_checkpoint = 1 if (
            envelope.get("is_checkpoint")
            or req_ctx.get("is_checkpoint")
            or src_run_ctx.get("is_checkpoint")
            or (isinstance(raw_obj, dict) and (raw_obj.get("is_checkpoint") or raw_obj.get("checkpoint")))
        ) else 0
        raw_quality_policy_version = self._clean_run_str(
            envelope.get("quality_policy_version")
            or (raw_obj.get("quality_policy_version") if isinstance(raw_obj, dict) else None)
        )
        raw_skill_version = self._clean_run_str(
            envelope.get("skill_version")
            or (raw_obj.get("skill_version") if isinstance(raw_obj, dict) else None)
        )

        run_meta = {
            "platform": envelope.get("platform") or ("threads" if str(file_name or "").lower().startswith(("threads_trend", "trend_report")) else None),
            "run_id": envelope.get("run_id"),
            "request_id": envelope.get("request_id"),
            "strategy_version": envelope.get("strategy_version"),
            "contract_version": envelope.get("contract_version"),
            "skill_version": raw_skill_version,
            "quality_policy_version": raw_quality_policy_version,
            "session_seq": raw_session_seq,
            "is_checkpoint": bool(raw_is_checkpoint),
            "target_metric": envelope.get("target_metric") or (raw_obj.get("target_metric") if isinstance(raw_obj, dict) else None),
            "crawl_mode": raw_crawl_mode,
            "topics": norm_topic_ids if raw_topics_raw is not None else None,
            "keywords": raw_keywords_meta,
            "lookback_hours": raw_lookback_int,
            "special_counts": envelope.get("special_counts"),
            "run_status": envelope.get("run_status"),
            "technical_status": envelope.get("technical_status") or envelope.get("status"),
            "data_quality_status": envelope.get("data_quality_status"),
            "crawled_at": envelope.get("crawled_at") or envelope.get("crawl_time"),
            "crawl_time": envelope.get("crawl_time") or envelope.get("crawled_at") or envelope.get("ended_at") or envelope.get("generated_at"),
            "started_at": envelope.get("started_at"),
            "ended_at": envelope.get("ended_at"),
            "collected_at": envelope.get("collected_at"),
            "generated_at": envelope.get("generated_at"),
            "source": envelope.get("source"),
            "crawl": envelope.get("crawl"),
            "notes": envelope.get("notes"),
            "run_quality_note": envelope.get("run_quality_note"),
            "engagement_completeness": envelope.get("engagement_completeness"),
            "bucket_counts": envelope.get("bucket_counts"),
            "screening": envelope.get("screening"),
            "counters": envelope.get("counters"),
            "tier_counts": envelope.get("tier_counts"),
            "group_counts": envelope.get("group_counts"),
            "cross_verified_posts": envelope.get("cross_verified_posts"),
            "trends_snapshot": envelope.get("trends_snapshot"),
            "thresholds": envelope.get("thresholds"),
            "upcoming_vouchers": envelope.get("upcoming_vouchers"),
            "trending_context": envelope.get("trending_context"),
            "source_files": raw_obj.get("source_files") if isinstance(raw_obj, dict) else envelope.get("source_files"),
        }
        run_meta = {k: v for k, v in run_meta.items() if v is not None}

        v32_c = extract_v32_counters(raw_obj, legacy_fallback_qualified=False)
        screening_meta = envelope.get("screening") if isinstance(envelope.get("screening"), dict) else {}
        requested_count_val = v32_c.get("requested_count")
        if requested_count_val is None and isinstance(envelope.get("counters"), dict):
            requested_count_val = envelope["counters"].get("requested_count")
        if requested_count_val is None and isinstance(screening_meta, dict):
            requested_count_val = screening_meta.get("requested_count")
        dup_current_run_val = v32_c.get("duplicate_current_run_count")
        if dup_current_run_val is None and isinstance(envelope.get("counters"), dict):
            dup_current_run_val = envelope["counters"].get("duplicate_current_run_count")
        raw_plat = run_meta.get("platform") or platform_hint
        if not raw_plat and items:
            plats = {str(it.get("platform") or "").lower() for it in items if it.get("platform") and str(it.get("platform")).lower() != "unknown"}
            raw_plat = "all" if len(plats) > 1 else (next(iter(plats), None))
        raw_crawl_time = (
            envelope.get("crawl_time")
            or envelope.get("crawled_at")
            or envelope.get("ended_at")
            or envelope.get("generated_at")
            or envelope.get("collected_at")
            or envelope.get("started_at")
        )
        scanned_count = int(
            v32_c.get("scanned_count")
            if v32_c.get("scanned_count") is not None
            else (
                screening_meta.get("scanned_count")
                or screening_meta.get("scanned_posts")
                or envelope.get("scanned_count")
                or len(items)
            )
        )
        rejected_count = int(
            v32_c.get("rejected_count")
            if v32_c.get("rejected_count") is not None
            else (
                screening_meta.get("rejected_count")
                or screening_meta.get("rejected_posts")
                or envelope.get("rejected_count")
                or 0
            )
        )

        raw_is_v33 = bool(
            str(self._clean_run_str(envelope.get("contract_version")) or "").startswith("3.3")
            or str(raw_quality_policy_version or "").startswith("3.3")
        )
        below_hot_count = 0
        unverified_audit_count = 0
        rejected_audit_count = 0
        inserted = 0
        updated = 0
        deduped = 0
        new_count = 0
        new_qualified_count = 0
        quality_qualified_count = 0
        watchlist_count = 0
        traffic_content_count = 0
        duplicate_count = 0
        engagement_updated_count = 0
        missing_views_count = 0
        batch_registry_items: List[Dict[str, Any]] = []

        with self._lock:
            if not self._conn.in_transaction:
                self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO raw_drive_files
                    (file_id, file_name, folder_path, modified_time, md5_checksum, size_bytes, schema_version,
                     raw_path, record_count, status, error_message, synced_at,
                     file_role, is_corrected, corrects_file, corrects_report, run_status, technical_status, data_quality_status,
                     business_usable_records, run_meta_json, request_id, run_id, platform, crawl_time,
                     requested_count, duplicate_current_run_count, strategy_version, contract_version, special_counts_json,
                     crawl_mode, topics_json, keywords, lookback_hours, session_seq, is_checkpoint, quality_policy_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        file_name = excluded.file_name,
                        folder_path = excluded.folder_path,
                        modified_time = excluded.modified_time,
                        md5_checksum = excluded.md5_checksum,
                        size_bytes = excluded.size_bytes,
                        schema_version = excluded.schema_version,
                        raw_path = excluded.raw_path,
                        record_count = excluded.record_count,
                        status = 'synced',
                        error_message = NULL,
                        synced_at = excluded.synced_at,
                        file_role = excluded.file_role,
                        is_corrected = excluded.is_corrected,
                        corrects_file = excluded.corrects_file,
                        corrects_report = excluded.corrects_report,
                        run_status = excluded.run_status,
                        technical_status = excluded.technical_status,
                        data_quality_status = excluded.data_quality_status,
                        business_usable_records = excluded.business_usable_records,
                        run_meta_json = excluded.run_meta_json,
                        request_id = COALESCE(excluded.request_id, raw_drive_files.request_id),
                        run_id = COALESCE(excluded.run_id, raw_drive_files.run_id),
                        platform = COALESCE(excluded.platform, raw_drive_files.platform),
                        crawl_time = COALESCE(excluded.crawl_time, raw_drive_files.crawl_time),
                        requested_count = COALESCE(excluded.requested_count, raw_drive_files.requested_count),
                        duplicate_current_run_count = COALESCE(excluded.duplicate_current_run_count, raw_drive_files.duplicate_current_run_count),
                        strategy_version = COALESCE(excluded.strategy_version, raw_drive_files.strategy_version),
                        contract_version = COALESCE(excluded.contract_version, raw_drive_files.contract_version),
                        special_counts_json = COALESCE(excluded.special_counts_json, raw_drive_files.special_counts_json),
                        crawl_mode = COALESCE(excluded.crawl_mode, raw_drive_files.crawl_mode),
                        topics_json = COALESCE(excluded.topics_json, raw_drive_files.topics_json),
                        keywords = COALESCE(excluded.keywords, raw_drive_files.keywords),
                        lookback_hours = COALESCE(excluded.lookback_hours, raw_drive_files.lookback_hours),
                        session_seq = COALESCE(excluded.session_seq, raw_drive_files.session_seq),
                        is_checkpoint = MAX(COALESCE(excluded.is_checkpoint, 0), COALESCE(raw_drive_files.is_checkpoint, 0)),
                        quality_policy_version = COALESCE(excluded.quality_policy_version, raw_drive_files.quality_policy_version),
                        is_archived = 0,
                        archived_at = NULL
                    """,
                    (
                        file_id,
                        file_name,
                        folder_path,
                        modified_time,
                        computed_md5,
                        len(raw_bytes),
                        schema_version,
                        raw_path,
                        len(items),
                        now_iso,
                        file_role,
                        1 if is_corrected else 0,
                        envelope.get("corrects_file"),
                        envelope.get("corrects_report"),
                        envelope.get("run_status"),
                        envelope.get("technical_status") or envelope.get("status"),
                        envelope.get("data_quality_status"),
                        envelope.get("business_usable_records"),
                        json.dumps(run_meta, ensure_ascii=False),
                        envelope.get("request_id"),
                        envelope.get("run_id"),
                        raw_plat,
                        raw_crawl_time,
                        int(requested_count_val) if requested_count_val is not None else None,
                        int(dup_current_run_val) if dup_current_run_val is not None else None,
                        self._clean_run_str(envelope.get("strategy_version")),
                        self._clean_run_str(envelope.get("contract_version")),
                        json.dumps(envelope.get("special_counts"), ensure_ascii=False) if isinstance(envelope.get("special_counts"), dict) else None,
                        raw_crawl_mode,
                        json.dumps(norm_topic_ids, ensure_ascii=False) if raw_topics_raw is not None else None,
                        raw_keywords_meta,
                        raw_lookback_int,
                        raw_session_seq,
                        raw_is_checkpoint,
                        raw_quality_policy_version,
                    ),
                )

                for item in items:
                    if item.get("views_count") is None:
                        missing_views_count += 1

                    c_url = item.get("canonical_url")
                    existing = self._conn.execute(
                        """
                        SELECT id, dedup_key, canonical_url, post_id, post_pk, content_group, purchase_intent, text_content, summary_vi, title, url, evidence_url,
                               likes_count, comments_count, shares_count, reposts_count, quotes_count, views_count,
                               views_display, views_precision, views_source, views_measured_at, views_evidence_url,
                               classification, content_hash, similar_content, similar_to_post_id, engagement_rate_on_views, engagement_rate_approx,
                               price, sold_count, rating, trend_tier, trend_group, verification_status, metrics_source,
                               data_quality, quality_issues_json, source_priority, is_corrected, run_id, crawled_at, published_at,
                               raw_file_id, first_seen_at, last_seen_at, snapshot_count, last_change_type,
                               last_views_delta, last_likes_delta, last_comments_delta, last_shares_delta, last_reposts_delta,
                               raw_payload_json, extra_meta_json, metrics_json
                        FROM market_items
                        WHERE dedup_key = ?
                        """,
                        (item["dedup_key"],),
                    ).fetchone()
                    if not existing and item.get("post_id"):
                        existing = self._conn.execute(
                            """
                            SELECT id, dedup_key, canonical_url, post_id, post_pk, content_group, purchase_intent, text_content, summary_vi, title, url, evidence_url,
                                   likes_count, comments_count, shares_count, reposts_count, quotes_count, views_count,
                                   views_display, views_precision, views_source, views_measured_at, views_evidence_url,
                                   classification, content_hash, similar_content, similar_to_post_id, engagement_rate_on_views, engagement_rate_approx,
                                   price, sold_count, rating, trend_tier, trend_group, verification_status, metrics_source,
                                   data_quality, quality_issues_json, source_priority, is_corrected, run_id, crawled_at, published_at,
                                   raw_file_id, first_seen_at, last_seen_at, snapshot_count, last_change_type,
                                   last_views_delta, last_likes_delta, last_comments_delta, last_shares_delta, last_reposts_delta,
                                   raw_payload_json, extra_meta_json, metrics_json
                            FROM market_items
                            WHERE platform = ? AND post_id = ?
                            LIMIT 1
                            """,
                            (item["platform"], item["post_id"]),
                        ).fetchone()
                    if not existing and c_url:
                        existing = self._conn.execute(
                            """
                            SELECT id, dedup_key, canonical_url, post_id, post_pk, content_group, purchase_intent, text_content, summary_vi, title, url, evidence_url,
                                   likes_count, comments_count, shares_count, reposts_count, quotes_count, views_count,
                                   views_display, views_precision, views_source, views_measured_at, views_evidence_url,
                                   classification, content_hash, similar_content, similar_to_post_id, engagement_rate_on_views, engagement_rate_approx,
                                   price, sold_count, rating, trend_tier, trend_group, verification_status, metrics_source,
                                   data_quality, quality_issues_json, source_priority, is_corrected, run_id, crawled_at, published_at,
                                   raw_file_id, first_seen_at, last_seen_at, snapshot_count, last_change_type,
                                   last_views_delta, last_likes_delta, last_comments_delta, last_shares_delta, last_reposts_delta,
                                   raw_payload_json, extra_meta_json, metrics_json
                            FROM market_items
                            WHERE platform = ? AND canonical_url = ?
                            LIMIT 1
                            """,
                            (item["platform"], c_url),
                        ).fetchone()

                    metrics_json = json.dumps(item["metrics"], ensure_ascii=False)
                    extra_meta_json = json.dumps(item["extra_meta"], ensure_ascii=False)
                    quality_issues_json = json.dumps(item["quality_issues"], ensure_ascii=False)
                    incoming_prio = int(item.get("source_priority") or 1)
                    # Real measurement time ONLY — NEVER fall back to laptop sync timestamp (`now_iso`)
                    measured_at = (
                        item.get("views_measured_at")
                        or item.get("measured_at")
                        or item.get("crawled_at")
                        or envelope.get("crawled_at")
                        or envelope.get("ended_at")
                        or envelope.get("collected_at")
                        or envelope.get("started_at")
                        or item.get("published_at")
                        or item.get("record_last_seen_at")
                        or modified_time
                    )
                    snap_at = measured_at or ""
                    effective_run_id = item.get("run_id") or envelope.get("run_id")

                    if raw_is_v33:
                        q_v33 = assess_v33_quality(item, envelope=envelope)
                        cls_v33 = str(item.get("classification") or "").upper()
                        existing_tier = str(existing["trend_tier"] or "").lower() if existing else ""
                        allow_research_corpus = (
                            (cls_v33 in ("", "NEW") and q_v33["quality_qualified"])
                            or (
                                existing is not None
                                and cls_v33 in ("UPDATED_METRICS", "DUPLICATE_HISTORY")
                                and (q_v33.get("quality_tier_basis") in ("very_hot", "hot") or existing_tier in ("very_hot", "hot"))
                            )
                        )
                        if not allow_research_corpus:
                            self._record_audit_item_v33(
                                file_id=file_id,
                                run_id=effective_run_id,
                                request_id=envelope.get("request_id"),
                                item=item,
                                q_item=q_v33,
                                now_iso=now_iso,
                            )
                            if cls_v33 == "REJECTED":
                                rejected_audit_count += 1
                            elif q_v33.get("quality_tier_basis") == "unverified" or cls_v33 == "UNVERIFIED":
                                unverified_audit_count += 1
                            else:
                                below_hot_count += 1
                            deduped += 1
                            continue
                        if q_v33["quality_qualified"]:
                            item["trend_tier"] = q_v33["quality_tier_basis"]

                    if not existing:
                        item_uuid = str(uuid.uuid4())
                        first_seen_val = item.get("record_first_seen_at") or measured_at or now_iso
                        last_seen_val = item.get("record_last_seen_at") or measured_at or now_iso
                        self._conn.execute(
                            """
                            INSERT INTO market_items (
                                id, dedup_key, platform, external_id, post_id, post_pk, item_id, shop_id,
                                author_handle, author_name, title, text_content, summary_vi, url, canonical_url, evidence_url,
                                content_group, purchase_intent, language, published_at, crawled_at,
                                likes_count, comments_count, shares_count, reposts_count, quotes_count, views_count,
                                views_display, views_precision, views_source, views_measured_at, views_evidence_url,
                                classification, content_hash, similar_content, similar_to_post_id, engagement_rate_on_views, engagement_rate_approx,
                                price, sold_count, rating, trend_tier, trend_group, verification_status, metrics_source,
                                data_quality, quality_issues_json, source_priority, is_corrected, run_id,
                                last_change_type, snapshot_count, views_missing,
                                metrics_json, extra_meta_json, schema_version, raw_file_id, raw_item_index,
                                raw_payload_json, first_seen_at, last_seen_at, updated_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?,
                                'new', ?, ?,
                                ?, ?, ?, ?, ?,
                                ?, ?, ?, ?
                            )
                            """,
                            (
                                item_uuid,
                                item["dedup_key"],
                                item["platform"],
                                item["external_id"],
                                item["post_id"],
                                item.get("post_pk"),
                                item["item_id"],
                                item["shop_id"],
                                item["author_handle"],
                                item["author_name"],
                                item["title"],
                                item["text_content"],
                                item["summary_vi"],
                                item["url"],
                                c_url,
                                item.get("evidence_url"),
                                item["content_group"],
                                item["purchase_intent"],
                                item["language"],
                                item["published_at"],
                                item["crawled_at"],
                                item["likes_count"],
                                item["comments_count"],
                                item["shares_count"],
                                item.get("reposts_count"),
                                item.get("quotes_count"),
                                item["views_count"],
                                item.get("views_display"),
                                item.get("views_precision"),
                                item.get("views_source"),
                                item.get("views_measured_at"),
                                item.get("views_evidence_url"),
                                item.get("classification"),
                                item.get("content_hash"),
                                1 if item.get("similar_content") else 0,
                                item.get("similar_to_post_id"),
                                item.get("engagement_rate_on_views"),
                                1 if item.get("engagement_rate_approx") else (0 if item.get("engagement_rate_approx") is False else None),
                                item["price"],
                                item["sold_count"],
                                item["rating"],
                                item.get("trend_tier"),
                                item.get("trend_group"),
                                item.get("verification_status"),
                                item.get("metrics_source"),
                                item["data_quality"],
                                quality_issues_json,
                                incoming_prio,
                                1 if item.get("is_corrected") else 0,
                                effective_run_id,
                                max(1, int(item.get("record_observation_count") or 1)),
                                1 if item.get("views_count") is None else 0,
                                metrics_json,
                                extra_meta_json,
                                item["schema_version"],
                                file_id,
                                item["raw_item_index"],
                                item["raw_payload_json"],
                                first_seen_val,
                                last_seen_val,
                                now_iso,
                            ),
                        )
                        snap_src_key = self._snapshot_source_key(
                            item_uuid,
                            file_id,
                            computed_md5,
                            dedup_key=item["dedup_key"],
                            run_id=effective_run_id,
                            measured_at=measured_at,
                        )
                        self._insert_snapshot_row(
                            snapshot_id=str(uuid.uuid4()),
                            snapshot_source_key=snap_src_key,
                            item_db_id=item_uuid,
                            dedup_key=item["dedup_key"],
                            canonical_url=c_url,
                            platform=item["platform"],
                            run_id=effective_run_id,
                            raw_file_id=file_id,
                            snapshot_at=snap_at,
                            item={**item, "classification": item.get("classification") or "NEW"},
                            change_type="new",
                            created_at=now_iso,
                            source_priority=incoming_prio,
                            is_corrected=bool(item.get("is_corrected")),
                        )
                        inserted += 1
                        new_count += 1
                        if self._is_item_qualified_v32(item):
                            new_qualified_count += 1
                        q_item = assess_v33_quality(item)
                        if q_item["quality_qualified"]:
                            quality_qualified_count += 1
                        if q_item["is_watchlist"]:
                            watchlist_count += 1
                        if q_item["traffic_content"]:
                            traffic_content_count += 1
                        batch_registry_items.append({
                            "dedup_key": item["dedup_key"],
                            "platform": item["platform"],
                            "post_id": item.get("post_id"),
                            "post_pk": item.get("post_pk"),
                            "canonical_url": c_url,
                            "content_hash": item.get("content_hash"),
                            "run_id": effective_run_id,
                            "measured_at": measured_at,
                            "snapshot_idempotency_key": snap_src_key,
                            "classification": item.get("classification") or "NEW",
                            "views_display": item.get("views_display"),
                            "views_precision": item.get("views_precision"),
                            "views_source": item.get("views_source"),
                            "views_measured_at": item.get("views_measured_at"),
                            "views_evidence_url": item.get("views_evidence_url"),
                            "metrics": item["metrics"],
                        })
                    else:
                        # Synchronize post_id & upgrade url-fallback key -> platform:post:post_id when ID appears after URL
                        eff_dedup_key = existing["dedup_key"]
                        eff_post_id = existing["post_id"] or item.get("post_id")
                        if item.get("post_id") and not existing["post_id"]:
                            candidate_key = f"{item['platform']}:post:{item['post_id']}"
                            if eff_dedup_key.startswith(f"{item['platform']}:url:"):
                                conflict_key = self._conn.execute(
                                    "SELECT id FROM market_items WHERE dedup_key = ? AND id != ?",
                                    (candidate_key, existing["id"]),
                                ).fetchone()
                                if not conflict_key:
                                    self._conn.execute(
                                        "UPDATE market_items SET dedup_key = ?, post_id = ?, post_pk = COALESCE(post_pk, ?) WHERE id = ?",
                                        (candidate_key, item["post_id"], item.get("post_pk"), existing["id"]),
                                    )
                                    self._conn.execute(
                                        "UPDATE market_item_snapshots SET dedup_key = ? WHERE item_id = ?",
                                        (candidate_key, existing["id"]),
                                    )
                                    eff_dedup_key = candidate_key
                                else:
                                    self._conn.execute(
                                        "UPDATE market_items SET post_id = COALESCE(post_id, ?), post_pk = COALESCE(post_pk, ?) WHERE id = ?",
                                        (item["post_id"], item.get("post_pk"), existing["id"]),
                                    )
                            else:
                                self._conn.execute(
                                    "UPDATE market_items SET post_id = COALESCE(post_id, ?), post_pk = COALESCE(post_pk, ?) WHERE id = ?",
                                    (item["post_id"], item.get("post_pk"), existing["id"]),
                                )

                        existing_prio = int(existing["source_priority"] or 1)
                        existing_run = existing["run_id"] or ""
                        incoming_run = effective_run_id or ""
                        same_run = bool(existing_run and incoming_run and existing_run == incoming_run)
                        same_or_unknown_run = same_run or not existing_run or not incoming_run
                        existing_ts = self._parse_iso_ts(
                            existing["views_measured_at"] or existing["crawled_at"] or existing["published_at"]
                        )
                        incoming_ts = self._parse_iso_ts(
                            measured_at or item.get("crawled_at") or item.get("published_at") or modified_time
                        )
                        is_newer_distinct_run = bool(
                            existing_run and incoming_run and existing_run != incoming_run and incoming_ts >= existing_ts
                        )

                        is_older_late_snapshot = bool(
                            not same_or_unknown_run and existing_ts > 0 and incoming_ts > 0 and incoming_ts < existing_ts
                        )

                        # Original files must NEVER overwrite corrected_v2 within the same run; older late runs never overwrite newer metrics or last_seen_at
                        if is_older_late_snapshot:
                            hist_snap_key = self._snapshot_source_key(
                                existing["id"],
                                file_id,
                                computed_md5,
                                dedup_key=eff_dedup_key,
                                run_id=effective_run_id,
                                measured_at=measured_at,
                            )
                            hist_row = self._conn.execute(
                                """
                                SELECT snapshot_source_key, COALESCE(source_priority, 1) AS sp, COALESCE(is_corrected, 0) AS ic
                                FROM market_item_snapshots
                                WHERE snapshot_source_key = ?
                                   OR (item_id = ? AND ? != '' AND run_id = ? AND COALESCE(measured_at, '') = COALESCE(?, ''))
                                LIMIT 1
                                """,
                                (hist_snap_key, existing["id"], effective_run_id or "", effective_run_id, measured_at),
                            ).fetchone()
                            hist_can_write = (
                                hist_row is None
                                or (bool(item.get("is_corrected")) and not bool(hist_row["ic"]))
                                or incoming_prio > int(hist_row["sp"] or 1)
                            )
                            if hist_can_write:
                                self._insert_snapshot_row(
                                    snapshot_id=str(uuid.uuid4()),
                                    snapshot_source_key=hist_row["snapshot_source_key"] if hist_row is not None else hist_snap_key,
                                    item_db_id=existing["id"],
                                    dedup_key=eff_dedup_key,
                                    canonical_url=c_url or existing["canonical_url"],
                                    platform=item["platform"],
                                    run_id=effective_run_id,
                                    raw_file_id=file_id,
                                    snapshot_at=snap_at,
                                    item={**item, "classification": item.get("classification") or "DUPLICATE_HISTORY"},
                                    change_type="historical_backfill",
                                    created_at=now_iso,
                                    source_priority=incoming_prio,
                                    is_corrected=bool(item.get("is_corrected")),
                                )
                            self._conn.execute(
                                """
                                UPDATE market_items
                                SET canonical_url = COALESCE(canonical_url, ?),
                                    post_id = COALESCE(post_id, ?),
                                    post_pk = COALESCE(post_pk, ?),
                                    first_seen_at = CASE
                                        WHEN ? IS NOT NULL AND (first_seen_at IS NULL OR ? < first_seen_at) THEN ?
                                        ELSE first_seen_at
                                    END,
                                    snapshot_count = COALESCE((SELECT COUNT(*) FROM market_item_snapshots WHERE item_id = ?), snapshot_count)
                                WHERE id = ?
                                """,
                                (c_url, eff_post_id, item.get("post_pk"), measured_at, measured_at, measured_at, existing["id"], existing["id"]),
                            )
                            batch_registry_items.append({
                                "dedup_key": eff_dedup_key,
                                "platform": item["platform"],
                                "post_id": eff_post_id,
                                "post_pk": item.get("post_pk") or existing["post_pk"],
                                "canonical_url": c_url or existing["canonical_url"],
                                "content_hash": item.get("content_hash") or existing["content_hash"],
                                "run_id": effective_run_id,
                                "measured_at": measured_at,
                                "snapshot_idempotency_key": hist_snap_key,
                                "classification": item.get("classification") or "DUPLICATE_HISTORY",
                                "views_display": item.get("views_display"),
                                "views_precision": item.get("views_precision"),
                                "views_source": item.get("views_source"),
                                "views_measured_at": item.get("views_measured_at"),
                                "views_evidence_url": item.get("views_evidence_url"),
                                "metrics": item["metrics"],
                            })
                            deduped += 1
                            duplicate_count += 1
                            continue

                        if incoming_prio < existing_prio and not is_newer_distinct_run:
                            deduped += 1
                            duplicate_count += 1
                            continue

                        # Preserve legacy pre-snapshot metrics before updating in-place
                        existing_snap_total = self._conn.execute(
                            "SELECT COUNT(*) AS c FROM market_item_snapshots WHERE item_id = ?",
                            (existing["id"],),
                        ).fetchone()["c"]
                        if existing_snap_total == 0:
                            base_at = existing["crawled_at"] or existing["published_at"] or existing["first_seen_at"]
                            base_snap_key = self._snapshot_source_key(
                                existing["id"],
                                existing["raw_file_id"] or "legacy_baseline",
                                "baseline",
                                dedup_key=eff_dedup_key,
                                run_id=existing["run_id"],
                                measured_at=base_at,
                            )
                            try:
                                base_extra = json.loads(existing["extra_meta_json"] or "{}")
                            except Exception:
                                base_extra = {}
                            self._insert_snapshot_row(
                                snapshot_id=str(uuid.uuid4()),
                                snapshot_source_key=base_snap_key,
                                item_db_id=existing["id"],
                                dedup_key=eff_dedup_key,
                                canonical_url=existing["canonical_url"],
                                platform=item["platform"],
                                run_id=existing["run_id"],
                                raw_file_id=existing["raw_file_id"],
                                snapshot_at=base_at or "",
                                item={
                                    "views_count": existing["views_count"],
                                    "likes_count": existing["likes_count"],
                                    "comments_count": existing["comments_count"],
                                    "shares_count": existing["shares_count"],
                                    "reposts_count": existing["reposts_count"],
                                    "quotes_count": existing["quotes_count"],
                                    "price": existing["price"],
                                    "sold_count": existing["sold_count"],
                                    "measured_at": base_at,
                                    "views_display": existing["views_display"],
                                    "views_precision": existing["views_precision"],
                                    "views_source": existing["views_source"],
                                    "views_evidence_url": existing["views_evidence_url"],
                                    "classification": existing["classification"] or "NEW",
                                    "trend_tier": existing["trend_tier"],
                                    "trend_group": existing["trend_group"],
                                    "verification_status": existing["verification_status"],
                                    "data_quality": existing["data_quality"],
                                    "summary_vi": existing["summary_vi"],
                                    "title": existing["title"],
                                    "extra_meta": base_extra,
                                },
                                change_type="new",
                                created_at=existing["first_seen_at"] or now_iso,
                                source_priority=int(existing["source_priority"] or 1),
                                is_corrected=bool(existing["is_corrected"]),
                            )

                        d_views = self._compute_metric_delta(item["views_count"], existing["views_count"])
                        d_likes = self._compute_metric_delta(item["likes_count"], existing["likes_count"])
                        d_comments = self._compute_metric_delta(item["comments_count"], existing["comments_count"])
                        d_shares = self._compute_metric_delta(item["shares_count"], existing["shares_count"])
                        d_reposts = self._compute_metric_delta(item.get("reposts_count"), existing["reposts_count"])

                        metric_pairs = (
                            (item["views_count"], existing["views_count"]),
                            (item["likes_count"], existing["likes_count"]),
                            (item["comments_count"], existing["comments_count"]),
                            (item["shares_count"], existing["shares_count"]),
                            (item.get("reposts_count"), existing["reposts_count"]),
                            (item.get("quotes_count"), existing["quotes_count"]),
                            (item["price"], existing["price"]),
                            (item["sold_count"], existing["sold_count"]),
                        )
                        metrics_changed = any((nv is not None and nv != ov) for nv, ov in metric_pairs)
                        snap_change_type = "updated" if metrics_changed else "duplicate"

                        merged_cg = item["content_group"] if item["content_group"] is not None else existing["content_group"]
                        merged_pi = item["purchase_intent"] if item["purchase_intent"] is not None else existing["purchase_intent"]
                        merged_title = item["title"] if item["title"] is not None else existing["title"]
                        merged_text = item["text_content"] if item["text_content"] is not None else existing["text_content"]
                        merged_svi = item["summary_vi"] if item["summary_vi"] is not None else existing["summary_vi"]
                        merged_url = item["url"] if item["url"] is not None else existing["url"]
                        merged_c_url = c_url or existing["canonical_url"]
                        merged_ev_url = item.get("evidence_url") if item.get("evidence_url") is not None else existing["evidence_url"]
                        merged_post_pk = item.get("post_pk") if item.get("post_pk") is not None else existing["post_pk"]
                        merged_tier = item.get("trend_tier") if item.get("trend_tier") is not None else existing["trend_tier"]
                        merged_group = item.get("trend_group") if item.get("trend_group") is not None else existing["trend_group"]
                        merged_verif = item.get("verification_status") if item.get("verification_status") is not None else existing["verification_status"]
                        merged_msrc = item.get("metrics_source") if item.get("metrics_source") is not None else existing["metrics_source"]
                        merged_likes = item["likes_count"] if item["likes_count"] is not None else existing["likes_count"]
                        merged_comments = item["comments_count"] if item["comments_count"] is not None else existing["comments_count"]
                        merged_shares = item["shares_count"] if item["shares_count"] is not None else existing["shares_count"]
                        merged_reposts = item.get("reposts_count") if item.get("reposts_count") is not None else existing["reposts_count"]
                        merged_quotes = item.get("quotes_count") if item.get("quotes_count") is not None else existing["quotes_count"]
                        merged_views = item["views_count"] if item["views_count"] is not None else existing["views_count"]
                        merged_views_disp = item.get("views_display") if item.get("views_display") is not None else existing["views_display"]
                        merged_views_prec = item.get("views_precision") if item.get("views_precision") is not None else existing["views_precision"]
                        merged_views_src = item.get("views_source") if item.get("views_source") is not None else existing["views_source"]
                        merged_views_mat = item.get("views_measured_at") if item.get("views_measured_at") is not None else existing["views_measured_at"]
                        merged_views_ev = item.get("views_evidence_url") if item.get("views_evidence_url") is not None else existing["views_evidence_url"]
                        merged_chash = item.get("content_hash") if item.get("content_hash") is not None else existing["content_hash"]
                        merged_cls = item.get("classification") if item.get("classification") is not None else existing["classification"]
                        merged_er = item.get("engagement_rate_on_views") if item.get("engagement_rate_on_views") is not None else existing["engagement_rate_on_views"]
                        merged_er_appx = (
                            1 if item.get("engagement_rate_approx") else (0 if item.get("engagement_rate_approx") is False else existing["engagement_rate_approx"])
                        )
                        merged_price = item["price"] if item["price"] is not None else existing["price"]
                        merged_sold = item["sold_count"] if item["sold_count"] is not None else existing["sold_count"]
                        merged_rating = item["rating"] if item["rating"] is not None else existing["rating"]

                        incoming_is_rejected = str(item.get("classification") or "").upper() == "REJECTED"
                        existing_is_rejected = str(existing["classification"] or "").upper() == "REJECTED"
                        if incoming_is_rejected and not existing_is_rejected and (existing["views_count"] is not None or existing["trend_tier"] is not None):
                            # Preserve previously qualified/rich record on market_items while logging the REJECTED snapshot for this run
                            force_update = False
                        elif incoming_prio > existing_prio or is_newer_distinct_run:
                            merged_dq = item["data_quality"] if item["data_quality"] is not None else existing["data_quality"]
                            merged_qi = quality_issues_json
                            force_update = True
                        else:
                            merged_dq = item["data_quality"] if item["data_quality"] is not None else existing["data_quality"]
                            merged_qi = quality_issues_json if item["quality_issues"] else (existing["quality_issues_json"] or "[]")
                            force_update = metrics_changed or any(
                                [
                                    merged_cg != existing["content_group"],
                                    merged_pi != existing["purchase_intent"],
                                    merged_title != existing["title"],
                                    merged_text != existing["text_content"],
                                    merged_svi != existing["summary_vi"],
                                    merged_url != existing["url"],
                                    merged_ev_url != existing["evidence_url"],
                                    merged_views_disp != existing["views_display"],
                                    merged_tier != existing["trend_tier"],
                                    merged_group != existing["trend_group"],
                                    merged_verif != existing["verification_status"],
                                    merged_dq != existing["data_quality"],
                                    merged_qi != (existing["quality_issues_json"] or "[]"),
                                    extra_meta_json != (existing["extra_meta_json"] or "{}"),
                                    metrics_json != (existing["metrics_json"] or "{}"),
                                ]
                            )

                        if same_run and incoming_prio > existing_prio and not metrics_changed and existing["last_change_type"] == "new":
                            snap_change_type = "new"
                            default_snap_cls = existing["classification"] or "NEW"
                        else:
                            default_snap_cls = "UPDATED_METRICS" if metrics_changed else "DUPLICATE_HISTORY"

                        snap_src_key = self._snapshot_source_key(
                            existing["id"],
                            file_id,
                            computed_md5,
                            dedup_key=eff_dedup_key,
                            run_id=effective_run_id,
                            measured_at=measured_at,
                        )
                        snap_cls = item.get("classification") or (default_snap_cls if item.get("classification") is not None or same_run else default_snap_cls)
                        if item.get("classification") is None and snap_change_type == "new":
                            snap_cls = existing["classification"]
                        snap_row = self._conn.execute(
                            """
                            SELECT snapshot_source_key, COALESCE(source_priority, 1) AS sp, COALESCE(is_corrected, 0) AS ic
                            FROM market_item_snapshots
                            WHERE snapshot_source_key = ?
                               OR (item_id = ? AND ? != '' AND run_id = ? AND COALESCE(measured_at, '') = COALESCE(?, ''))
                            LIMIT 1
                            """,
                            (snap_src_key, existing["id"], effective_run_id or "", effective_run_id, measured_at),
                        ).fetchone()
                        snap_can_write = (
                            snap_row is None
                            or (bool(item.get("is_corrected")) and not bool(snap_row["ic"]))
                            or incoming_prio > int(snap_row["sp"] or 1)
                        )
                        if snap_can_write:
                            self._insert_snapshot_row(
                                snapshot_id=str(uuid.uuid4()),
                                snapshot_source_key=snap_row["snapshot_source_key"] if snap_row is not None else snap_src_key,
                                item_db_id=existing["id"],
                                dedup_key=eff_dedup_key,
                                canonical_url=merged_c_url,
                                platform=item["platform"],
                                run_id=effective_run_id,
                                raw_file_id=file_id,
                                snapshot_at=snap_at,
                                item={**item, "classification": snap_cls},
                                views_delta=d_views,
                                likes_delta=d_likes,
                                comments_delta=d_comments,
                                shares_delta=d_shares,
                                reposts_delta=d_reposts,
                                change_type=snap_change_type,
                                created_at=now_iso,
                                source_priority=incoming_prio,
                                is_corrected=bool(item.get("is_corrected")),
                            )

                        total_snaps = self._conn.execute(
                            "SELECT COUNT(*) AS c FROM market_item_snapshots WHERE item_id = ?",
                            (existing["id"],),
                        ).fetchone()["c"]
                        snap_cnt = max(1, int(total_snaps or existing["snapshot_count"] or 1))

                        if metrics_changed:
                            engagement_updated_count += 1
                        else:
                            duplicate_count += 1

                        if force_update:
                            self._conn.execute(
                                """
                                UPDATE market_items
                                SET title = ?, text_content = ?, summary_vi = ?, url = ?, canonical_url = COALESCE(?, canonical_url),
                                    evidence_url = ?, post_pk = COALESCE(?, post_pk), post_id = COALESCE(?, post_id),
                                    language = COALESCE(?, language), published_at = COALESCE(?, published_at), crawled_at = COALESCE(?, crawled_at),
                                    content_group = ?, purchase_intent = ?,
                                    likes_count = ?, comments_count = ?, shares_count = ?, reposts_count = ?, quotes_count = ?, views_count = ?,
                                    views_display = ?, views_precision = ?, views_source = ?, views_measured_at = ?, views_evidence_url = ?,
                                    classification = COALESCE(?, classification), content_hash = COALESCE(?, content_hash),
                                    similar_content = CASE WHEN ? IS NOT NULL THEN ? ELSE similar_content END,
                                    similar_to_post_id = COALESCE(?, similar_to_post_id),
                                    engagement_rate_on_views = COALESCE(?, engagement_rate_on_views),
                                    engagement_rate_approx = COALESCE(?, engagement_rate_approx),
                                    price = ?, sold_count = ?, rating = ?,
                                    trend_tier = ?, trend_group = ?, verification_status = ?, metrics_source = ?,
                                    data_quality = ?, quality_issues_json = ?,
                                    source_priority = ?, is_corrected = ?, run_id = COALESCE(?, run_id),
                                    last_change_type = ?, snapshot_count = ?, views_missing = ?,
                                    last_views_delta = COALESCE(?, last_views_delta),
                                    last_likes_delta = COALESCE(?, last_likes_delta),
                                    last_comments_delta = COALESCE(?, last_comments_delta),
                                    last_shares_delta = COALESCE(?, last_shares_delta),
                                    last_reposts_delta = COALESCE(?, last_reposts_delta),
                                    metrics_json = ?, extra_meta_json = ?, schema_version = ?,
                                    raw_file_id = ?, raw_payload_json = ?,
                                    last_seen_at = CASE WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN ? ELSE last_seen_at END,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                                (
                                    merged_title,
                                    merged_text,
                                    merged_svi,
                                    merged_url,
                                    merged_c_url,
                                    merged_ev_url,
                                    merged_post_pk,
                                    eff_post_id,
                                    item["language"],
                                    item["published_at"],
                                    item["crawled_at"],
                                    merged_cg,
                                    merged_pi,
                                    merged_likes,
                                    merged_comments,
                                    merged_shares,
                                    merged_reposts,
                                    merged_quotes,
                                    merged_views,
                                    merged_views_disp,
                                    merged_views_prec,
                                    merged_views_src,
                                    merged_views_mat,
                                    merged_views_ev,
                                    merged_cls,
                                    merged_chash,
                                    (1 if item.get("similar_content") else 0) if item.get("similar_content") is not None else None,
                                    (1 if item.get("similar_content") else 0) if item.get("similar_content") is not None else 0,
                                    item.get("similar_to_post_id"),
                                    merged_er,
                                    merged_er_appx,
                                    merged_price,
                                    merged_sold,
                                    merged_rating,
                                    merged_tier,
                                    merged_group,
                                    merged_verif,
                                    merged_msrc,
                                    merged_dq,
                                    merged_qi,
                                    incoming_prio,
                                    1 if item.get("is_corrected") else 0,
                                    effective_run_id,
                                    snap_change_type,
                                    snap_cnt,
                                    1 if merged_views is None else 0,
                                    d_views if metrics_changed else None,
                                    d_likes if metrics_changed else None,
                                    d_comments if metrics_changed else None,
                                    d_shares if metrics_changed else None,
                                    d_reposts if metrics_changed else None,
                                    metrics_json,
                                    extra_meta_json,
                                    item["schema_version"],
                                    file_id,
                                    item["raw_payload_json"],
                                    measured_at,
                                    measured_at,
                                    measured_at,
                                    now_iso,
                                    existing["id"],
                                ),
                            )
                            updated += 1
                        else:
                            self._conn.execute(
                                """
                                UPDATE market_items
                                SET last_seen_at = CASE WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN ? ELSE last_seen_at END,
                                    snapshot_count = ?,
                                    last_change_type = ?,
                                    canonical_url = COALESCE(?, canonical_url)
                                WHERE id = ?
                                """,
                                (measured_at, measured_at, measured_at, snap_cnt, snap_change_type, merged_c_url, existing["id"]),
                            )
                            deduped += 1

                        batch_registry_items.append({
                            "dedup_key": eff_dedup_key,
                            "platform": item["platform"],
                            "post_id": eff_post_id,
                            "post_pk": merged_post_pk,
                            "canonical_url": merged_c_url,
                            "content_hash": merged_chash,
                            "run_id": effective_run_id,
                            "measured_at": measured_at,
                            "snapshot_idempotency_key": snap_src_key,
                            "classification": item.get("classification") or ("UPDATED_METRICS" if metrics_changed else "DUPLICATE_HISTORY"),
                            "views_display": item.get("views_display"),
                            "views_precision": item.get("views_precision"),
                            "views_source": item.get("views_source"),
                            "views_measured_at": item.get("views_measured_at"),
                            "views_evidence_url": item.get("views_evidence_url"),
                            "metrics": item["metrics"],
                        })

                self._conn.execute(
                    """
                    UPDATE raw_drive_files
                    SET new_count = ?, new_qualified_count = ?, duplicate_count = ?, updated_count = ?,
                        missing_views_count = ?, scanned_count = ?, rejected_count = ?,
                        unique_candidate_count = ?, unverified_count = ?, stop_reason = ?, target_achieved = ?,
                        quality_qualified_count = ?, watchlist_count = ?, traffic_content_count = ?, below_hot_count = ?
                    WHERE file_id = ?
                    """,
                    (
                        v32_c.get("new_count") if v32_c.get("new_count") is not None else new_count,
                        v32_c.get("new_qualified_count") if v32_c.get("new_qualified_count") is not None else new_qualified_count,
                        v32_c.get("duplicate_history_count") if v32_c.get("duplicate_history_count") is not None else duplicate_count,
                        v32_c.get("updated_metrics_count") if v32_c.get("updated_metrics_count") is not None else engagement_updated_count,
                        v32_c.get("missing_views_count") if v32_c.get("missing_views_count") is not None else missing_views_count,
                        scanned_count,
                        rejected_count or rejected_audit_count,
                        v32_c.get("unique_candidate_count"),
                        v32_c.get("unverified_count") if v32_c.get("unverified_count") is not None else (unverified_audit_count if raw_is_v33 else None),
                        v32_c.get("stop_reason"),
                        (1 if v32_c.get("target_achieved") else 0) if v32_c.get("target_achieved") is not None else None,
                        v32_c.get("quality_qualified_count") if v32_c.get("quality_qualified_count") is not None else quality_qualified_count,
                        v32_c.get("watchlist_count") if v32_c.get("watchlist_count") is not None else watchlist_count,
                        v32_c.get("traffic_content_count") if v32_c.get("traffic_content_count") is not None else traffic_content_count,
                        below_hot_count,
                        file_id,
                    ),
                )
                self._conn.commit()
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                raise

        if file_role not in (NON_CRAWL_ROLES - {"market_report"}) and has_muse_report_sections(raw_obj):
            try:
                norm_report = normalize_muse_report(raw_obj, file_name=file_name, modified_time=modified_time)
                self.save_report(norm_report, raw_file_id=file_id, raw_payload=raw_obj)
            except Exception:
                pass

        return {
            "file_role": file_role,
            "is_corrected": is_corrected,
            "parsed": len(items),
            "inserted": inserted,
            "updated": updated,
            "deduped": deduped,
            "new_count": new_count,
            "new_qualified_count": new_qualified_count,
            "quality_qualified_count": quality_qualified_count,
            "watchlist_count": watchlist_count,
            "traffic_content_count": traffic_content_count,
            "below_hot_count": below_hot_count,
            "duplicate_count": duplicate_count,
            "engagement_updated_count": engagement_updated_count,
            "missing_views_count": missing_views_count,
            "scanned_count": scanned_count,
            "rejected_count": rejected_count or rejected_audit_count,
            "v32_counters": v32_c,
            "registry_items": batch_registry_items,
        }

    def reconcile_cached_raw_files(self) -> Dict[str, Any]:
        """
        Re-normalize already-archived raw files in original -> corrected order so
        prior duplicate keys or pre-V2 rows converge cleanly to Data Contract V2.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT file_id, file_name, folder_path, modified_time, md5_checksum, raw_path,
                       is_corrected, crawl_time, synced_at
                FROM raw_drive_files
                WHERE status = 'synced' AND COALESCE(is_archived, 0) = 0
                ORDER BY COALESCE(crawl_time, modified_time, synced_at) ASC, is_corrected ASC
                """
            ).fetchall()

            # Sort chronologically first, with original before corrected revisions within the same run/time
            sorted_rows = sorted(
                [dict(r) for r in rows],
                key=lambda x: (
                    str(x.get("crawl_time") or x.get("modified_time") or x.get("synced_at") or ""),
                    1 if (x.get("is_corrected") or "_corrected" in (x.get("file_name") or "").lower()) else 0,
                    str(x.get("file_name") or ""),
                ),
            )
            self._conn.execute("DELETE FROM market_item_snapshots")
            self._conn.execute("DELETE FROM market_items")
            self._conn.execute("DELETE FROM market_reports")
            self._conn.commit()

        reprocessed = 0
        for row in sorted_rows:
            if is_archive_path(row.get("folder_path")) or is_archive_path(row.get("file_name")):
                continue
            rp = row.get("raw_path")
            if not rp or not os.path.exists(rp):
                continue
            with open(rp, "rb") as f:
                raw_bytes = f.read()
            self.ingest_raw_file(
                file_id=row["file_id"],
                file_name=row["file_name"],
                raw_bytes=raw_bytes,
                modified_time=row.get("modified_time"),
                md5_checksum=row.get("md5_checksum"),
                folder_path=row.get("folder_path"),
            )
            reprocessed += 1
        return {"files_reprocessed": reprocessed}

    def backfill_v31_metadata(self) -> Dict[str, int]:
        """
        Non-destructively upgrade already-cached V3.1 files (`threads_trend_*` and `trend_report_*`)
        from their local raw JSON archives without deleting any historical V1/V2 rows.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT file_id, file_name, folder_path, modified_time, md5_checksum, raw_path
                FROM raw_drive_files
                WHERE status = 'synced'
                  AND (lower(file_name) LIKE 'threads_trend%' OR lower(file_name) LIKE 'trend_report%')
                ORDER BY COALESCE(modified_time, synced_at) ASC
                """
            ).fetchall()

        updated_files = 0
        for r in rows:
            row = dict(r)
            if is_archive_path(row.get("folder_path")) or is_archive_path(row.get("file_name")):
                continue
            rp = row.get("raw_path")
            if not rp or not os.path.exists(rp):
                continue
            with open(rp, "rb") as f:
                raw_bytes = f.read()
            self.ingest_raw_file(
                file_id=row["file_id"],
                file_name=row["file_name"],
                raw_bytes=raw_bytes,
                modified_time=row.get("modified_time"),
                md5_checksum=row.get("md5_checksum"),
                folder_path=row.get("folder_path"),
            )
            updated_files += 1
        return {"files_backfilled": updated_files}

    # ─── Sync Runs ─────────────────────────────────────────────────────────────

    def create_sync_run(self, trigger_source: str, command_id: Optional[str] = None) -> str:
        run_id = f"sync_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        now_iso = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sync_runs (run_id, trigger_source, command_id, status, started_at)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (run_id, trigger_source, command_id, now_iso),
            )
            self._conn.commit()
        return run_id

    def finish_sync_run(
        self,
        run_id: str,
        status: str,
        files_checked: int = 0,
        files_downloaded: int = 0,
        records_parsed: int = 0,
        records_inserted: int = 0,
        records_updated: int = 0,
        records_deduped: int = 0,
        error_message: Optional[str] = None,
        *,
        new_count: Optional[int] = None,
        duplicate_count: Optional[int] = None,
        updated_count: Optional[int] = None,
        missing_views_count: Optional[int] = None,
        scanned_count: Optional[int] = None,
        rejected_count: Optional[int] = None,
    ):
        now_iso = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE sync_runs
                SET status = ?, files_checked = ?, files_downloaded = ?,
                    records_parsed = ?, records_inserted = ?, records_updated = ?,
                    records_deduped = ?,
                    new_count = ?, duplicate_count = ?, updated_count = ?,
                    missing_views_count = ?, scanned_count = ?, rejected_count = ?,
                    finished_at = ?, error_message = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    files_checked,
                    files_downloaded,
                    records_parsed,
                    records_inserted,
                    records_updated,
                    records_deduped,
                    int(new_count if new_count is not None else records_inserted),
                    int(duplicate_count if duplicate_count is not None else records_deduped),
                    int(updated_count if updated_count is not None else records_updated),
                    int(missing_views_count or 0),
                    int(scanned_count if scanned_count is not None else records_parsed),
                    int(rejected_count or 0),
                    now_iso,
                    error_message,
                    run_id,
                ),
            )
            self._conn.commit()

    def get_latest_sync_run(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def list_sync_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Query, Filter, Search & Statistics ────────────────────────────────────

    def query_items(
        self,
        platform: Optional[str] = None,
        content_group: Optional[str] = None,
        purchase_intent: Optional[str] = None,
        data_quality: Optional[str] = None,
        trend_tier: Optional[str] = None,
        trend_group: Optional[str] = None,
        verification_status: Optional[str] = None,
        activity_filter: Optional[str] = None,
        special_filter: Optional[str] = None,
        quality_filter: Optional[str] = None,
        run_key: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "recent",
        affiliate_only: bool = False,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        lim = max(1, min(int(limit), 250))
        off = max(0, int(offset))

        if run_key and str(run_key).strip() not in ("", "all"):
            grp = self._resolve_run_scope(run_key, include_archived=include_archived)
            if not grp:
                return {"total": 0, "limit": lim, "offset": off, "items": [], "run_key": run_key}

            plat_filter = (platform or "all").strip().lower()
            candidates = self._query_effective_run_snapshots(
                run_id=grp["run_id"],
                run_ids=grp.get("run_ids"),
                raw_file_ids=[str(f["file_id"]) for f in grp["data_files"]],
                platform=plat_filter,
                prefer_qualified=bool(grp.get("is_request_bundle")),
            )

            filtered: List[Dict[str, Any]] = []
            search_q = search.strip().lower() if (search and search.strip()) else ""
            tier_norm = trend_tier.lower().strip() if (trend_tier and trend_tier != "all") else None
            tg_norm = trend_group.lower().strip() if (trend_group and trend_group != "all") else None
            ver_norm = verification_status.lower().strip() if (verification_status and verification_status != "all") else None
            act_norm = activity_filter.lower().strip() if (activity_filter and activity_filter != "all") else None

            for it in candidates:
                if content_group and content_group != "all":
                    if content_group == "__none__":
                        if it.get("content_group") is not None:
                            continue
                    elif it.get("content_group") != content_group:
                        continue

                if purchase_intent and purchase_intent != "all":
                    if purchase_intent == "__none__":
                        if it.get("purchase_intent") is not None:
                            continue
                    elif it.get("purchase_intent") != purchase_intent:
                        continue

                if data_quality and data_quality != "all":
                    if data_quality == "__none__":
                        if it.get("data_quality") is not None:
                            continue
                    elif it.get("data_quality") != data_quality:
                        continue

                if tier_norm:
                    it_tier = str(it.get("trend_tier") or "").lower()
                    it_ver = str(it.get("verification_status") or "").lower()
                    if tier_norm == "legacy":
                        if it.get("trend_tier") is not None:
                            continue
                    elif tier_norm == "unverified":
                        if not (it_tier == "unverified" or it_ver == "unverified" or str(it.get("classification") or "").upper() == "UNVERIFIED"):
                            continue
                    else:
                        if it_tier != tier_norm or it_ver == "unverified":
                            continue

                if tg_norm:
                    if tg_norm == "__none__":
                        if it.get("trend_group") is not None:
                            continue
                    elif str(it.get("trend_group") or "").lower() != tg_norm:
                        continue

                if ver_norm:
                    if ver_norm == "__none__":
                        if it.get("verification_status") is not None:
                            continue
                    elif str(it.get("verification_status") or "").lower() != ver_norm:
                        continue

                if act_norm:
                    cls = str(it.get("classification") or "").upper()
                    ctype = str(it.get("last_change_type") or "new").lower()
                    it_tier = str(it.get("trend_tier") or "").lower()
                    it_ver = str(it.get("verification_status") or "").lower()
                    it_dq = str(it.get("data_quality") or "").lower()
                    if act_norm in ("new", "new_qualified"):
                        is_new_match = (cls == "NEW") or (not cls and ctype == "new")
                        if not is_new_match:
                            continue
                    elif act_norm in ("growth", "updated", "updated_metrics"):
                        has_pos_delta = any(
                            int(it.get(k) or 0) > 0
                            for k in ("last_views_delta", "last_likes_delta", "last_comments_delta", "last_shares_delta", "last_reposts_delta")
                        )
                        is_growth_match = (cls == "UPDATED_METRICS") or (
                            cls not in ("REJECTED", "DUPLICATE_HISTORY", "UNVERIFIED", "NEW")
                            and (ctype in ("updated", "metrics_updated") or has_pos_delta)
                        )
                        if not is_growth_match:
                            continue
                    elif act_norm in ("duplicate", "duplicate_history"):
                        if not (cls == "DUPLICATE_HISTORY" or ctype == "duplicate_history" or (not cls and int(it.get("snapshot_count") or 1) > 1)):
                            continue
                    elif act_norm == "unverified":
                        if cls == "REJECTED" or not (cls == "UNVERIFIED" or it_ver == "unverified" or it_dq == "needs_verification" or it_tier == "unverified"):
                            continue
                    elif act_norm == "rejected":
                        if cls != "REJECTED":
                            continue
                    elif act_norm == "missing_views":
                        if it.get("views_count") is not None or (cls and cls not in ("NEW", "UNVERIFIED")):
                            continue

                if special_filter and special_filter != "all":
                    sf_norm = special_filter.lower().strip()
                    if sf_norm == "natural_viral" and it.get("natural_viral") is not True:
                        continue
                    if sf_norm == "traffic_content" and it.get("traffic_content") is not True:
                        continue
                    if sf_norm == "buyer_intent":
                        bi_v = it.get("buyer_intent")
                        if not (bi_v is True or (isinstance(bi_v, str) and bi_v.lower() in ("true", "cao", "trung_binh"))):
                            continue

                if quality_filter and quality_filter != "all":
                    qf_norm = quality_filter.lower().strip()
                    if qf_norm == "quality_only" and not it.get("quality_qualified"):
                        continue
                    if qf_norm == "watchlist_only" and not it.get("is_watchlist"):
                        continue
                    if qf_norm == "traffic_content" and not it.get("traffic_content"):
                        continue

                if affiliate_only:
                    if (
                        str(it.get("data_quality") or "").lower() == "needs_verification"
                        or str(it.get("verification_status") or "").lower() == "unverified"
                        or str(it.get("trend_tier") or "").lower() == "unverified"
                        or str(it.get("classification") or "").upper() == "REJECTED"
                    ):
                        continue

                if search_q:
                    hay = " ".join(
                        str(v or "")
                        for v in (
                            it.get("title"),
                            it.get("text_content"),
                            it.get("summary_vi"),
                            it.get("author_handle"),
                            it.get("post_id"),
                            it.get("item_id"),
                            it.get("trend_tier"),
                            it.get("trend_group"),
                            json.dumps(it.get("extra_meta") or {}, ensure_ascii=False),
                        )
                    ).lower()
                    if search_q not in hay:
                        continue

                filtered.append(it)

            tier_rank_map = {"very_hot": 5, "hot": 4, "emerging": 3, "watchlist": 2, "unverified": 1}
            intent_rank_map = {"cao": 4, "trung_binh": 3, "thap": 2, "khong_ro": 1}
            sb = sort_by or "recent"
            if sb == "engagement":
                filtered.sort(
                    key=lambda x: (
                        int(x.get("views_count") or 0)
                        + int(x.get("likes_count") or 0) * 10
                        + int(x.get("comments_count") or 0) * 20
                        + int(x.get("reposts_count") or 0) * 15
                        + int(x.get("quotes_count") or 0) * 10
                    ),
                    reverse=True,
                )
            elif sb == "views":
                filtered.sort(
                    key=lambda x: (
                        x.get("views_count") if x.get("views_count") is not None else -1,
                        x.get("likes_count") if x.get("likes_count") is not None else -1,
                    ),
                    reverse=True,
                )
            elif sb == "tier":
                filtered.sort(
                    key=lambda x: (
                        tier_rank_map.get(str(x.get("trend_tier") or "").lower(), 0),
                        int(x.get("likes_count") or 0) + int(x.get("comments_count") or 0),
                    ),
                    reverse=True,
                )
            elif sb == "intent":
                filtered.sort(
                    key=lambda x: (
                        intent_rank_map.get(str(x.get("purchase_intent") or "").lower(), 0),
                        int(x.get("likes_count") or 0) + int(x.get("comments_count") or 0),
                    ),
                    reverse=True,
                )
            else:
                filtered.sort(
                    key=lambda x: (
                        1 if x.get("is_corrected") else 0,
                        str(x.get("published_at") or x.get("crawled_at") or x.get("first_seen_at") or ""),
                    ),
                    reverse=True,
                )

            return {
                "total": len(filtered),
                "limit": lim,
                "offset": off,
                "items": filtered[off : off + lim],
                "run_key": grp["run_key"],
                "run_id": grp["run_id"],
                "is_request_bundle": bool(grp.get("is_request_bundle")),
            }

        clauses: List[str] = []
        params: List[Any] = []

        if platform and platform != "all":
            clauses.append("m.platform = ?")
            params.append(platform.lower())

        if content_group and content_group != "all":
            if content_group == "__none__":
                clauses.append("m.content_group IS NULL")
            else:
                clauses.append("m.content_group = ?")
                params.append(content_group)

        if purchase_intent and purchase_intent != "all":
            if purchase_intent == "__none__":
                clauses.append("m.purchase_intent IS NULL")
            else:
                clauses.append("m.purchase_intent = ?")
                params.append(purchase_intent)

        if data_quality and data_quality != "all":
            if data_quality == "__none__":
                clauses.append("m.data_quality IS NULL")
            else:
                clauses.append("m.data_quality = ?")
                params.append(data_quality)

        if trend_tier and trend_tier != "all":
            tier_norm = trend_tier.lower().strip()
            if tier_norm == "legacy":
                clauses.append("m.trend_tier IS NULL")
            elif tier_norm == "unverified":
                clauses.append("(m.trend_tier = 'unverified' OR m.verification_status = 'unverified')")
            else:
                clauses.append("m.trend_tier = ? AND COALESCE(m.verification_status, '') != 'unverified'")
                params.append(tier_norm)

        if trend_group and trend_group != "all":
            if trend_group == "__none__":
                clauses.append("m.trend_group IS NULL")
            else:
                clauses.append("m.trend_group = ?")
                params.append(trend_group.lower().strip())

        if verification_status and verification_status != "all":
            if verification_status == "__none__":
                clauses.append("m.verification_status IS NULL")
            else:
                clauses.append("m.verification_status = ?")
                params.append(verification_status.lower().strip())

        if activity_filter and activity_filter != "all":
            act_norm = activity_filter.lower().strip()
            if act_norm in ("new", "new_qualified"):
                clauses.append("COALESCE(m.snapshot_count, 1) = 1 AND COALESCE(m.last_change_type, 'new') = 'new'")
            elif act_norm in ("growth", "updated", "updated_metrics"):
                clauses.append(
                    "(COALESCE(m.last_views_delta, 0) > 0 OR COALESCE(m.last_likes_delta, 0) > 0 "
                    "OR COALESCE(m.last_comments_delta, 0) > 0 OR COALESCE(m.last_shares_delta, 0) > 0 "
                    "OR COALESCE(m.last_reposts_delta, 0) > 0 OR m.last_change_type = 'updated' "
                    "OR EXISTS (SELECT 1 FROM market_item_snapshots s WHERE s.item_id = m.id AND s.change_type = 'updated'))"
                )
            elif act_norm in ("duplicate", "duplicate_history"):
                clauses.append("(COALESCE(m.snapshot_count, 1) > 1 OR m.classification = 'DUPLICATE_HISTORY')")
            elif act_norm == "unverified":
                clauses.append(
                    "(COALESCE(m.classification, '') != 'REJECTED' AND "
                    "(m.classification = 'UNVERIFIED' OR m.verification_status = 'unverified' OR m.data_quality = 'needs_verification' OR m.trend_tier = 'unverified'))"
                )
            elif act_norm == "rejected":
                clauses.append("m.classification = 'REJECTED'")
            elif act_norm == "missing_views":
                clauses.append("(m.views_count IS NULL AND (m.classification IS NULL OR m.classification IN ('', 'NEW', 'UNVERIFIED')))")

        if special_filter and special_filter != "all":
            sf_norm = special_filter.lower().strip()
            if sf_norm == "natural_viral":
                clauses.append("json_extract(m.extra_meta_json, '$.natural_viral') = 1")
            elif sf_norm == "traffic_content":
                clauses.append("json_extract(m.extra_meta_json, '$.traffic_content') = 1")
            elif sf_norm == "buyer_intent":
                clauses.append("(json_extract(m.extra_meta_json, '$.buyer_intent') = 1 OR m.purchase_intent IN ('cao', 'trung_binh'))")

        qf_norm = quality_filter.lower().strip() if (quality_filter and quality_filter != "all") else ""
        if qf_norm == "traffic_content":
            clauses.append("json_extract(m.extra_meta_json, '$.traffic_content') = 1")
        elif qf_norm == "watchlist_only":
            clauses.append("LOWER(COALESCE(m.trend_tier, '')) = 'watchlist' AND COALESCE(m.classification, '') NOT IN ('REJECTED', 'DUPLICATE_HISTORY', 'UPDATED_METRICS', 'UNVERIFIED')")

        if affiliate_only:
            clauses.append(
                "(m.data_quality IS NULL OR m.data_quality != 'needs_verification') "
                "AND COALESCE(m.verification_status, '') != 'unverified' "
                "AND COALESCE(m.trend_tier, '') != 'unverified' "
                "AND COALESCE(m.classification, '') != 'REJECTED'"
            )

        if search and search.strip():
            like = f"%{search.strip()}%"
            clauses.append(
                "(m.title LIKE ? OR m.text_content LIKE ? OR m.summary_vi LIKE ? OR m.author_handle LIKE ? "
                "OR m.post_id LIKE ? OR m.item_id LIKE ? OR m.trend_tier LIKE ? OR m.trend_group LIKE ? OR m.extra_meta_json LIKE ?)"
            )
            params.extend([like, like, like, like, like, like, like, like, like])

        if not include_archived:
            clauses.insert(0, f"(m.raw_file_id IS NULL OR {_NOT_ARCHIVE_RAW_SQL})")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        sort_map = {
            "engagement": (
                "COALESCE(m.views_count, 0) + (COALESCE(m.likes_count, 0) * 10) "
                "+ (COALESCE(m.comments_count, 0) * 20) + (COALESCE(m.reposts_count, 0) * 15) "
                "+ (COALESCE(m.quotes_count, 0) * 10) DESC, m.rowid DESC"
            ),
            "views": "COALESCE(m.views_count, -1) DESC, COALESCE(m.likes_count, -1) DESC, m.rowid DESC",
            "tier": (
                "CASE m.trend_tier WHEN 'very_hot' THEN 5 WHEN 'hot' THEN 4 WHEN 'emerging' THEN 3 "
                "WHEN 'watchlist' THEN 2 WHEN 'unverified' THEN 1 ELSE 0 END DESC, "
                "COALESCE(m.likes_count, 0) + COALESCE(m.comments_count, 0) DESC, m.rowid DESC"
            ),
            "intent": (
                "CASE m.purchase_intent WHEN 'cao' THEN 4 WHEN 'trung_binh' THEN 3 WHEN 'thap' THEN 2 WHEN 'khong_ro' THEN 1 ELSE 0 END DESC, "
                "COALESCE(m.likes_count, 0) + COALESCE(m.comments_count, 0) DESC, m.rowid DESC"
            ),
            "recent": "m.is_corrected DESC, COALESCE(m.published_at, m.crawled_at, m.first_seen_at) DESC, m.rowid DESC",
        }
        order_sql = sort_map.get(sort_by or "recent", sort_map["recent"])

        with self._lock:
            base_select = f"""
                SELECT m.id, m.dedup_key, m.platform, m.external_id, m.post_id, m.post_pk, m.item_id, m.shop_id,
                       m.author_handle, m.author_name, m.title, m.text_content, m.summary_vi, m.url, m.canonical_url, m.evidence_url,
                       m.content_group, m.purchase_intent, m.language, m.published_at, m.crawled_at,
                       m.likes_count, m.comments_count, m.shares_count, m.reposts_count, m.quotes_count, m.views_count,
                       m.views_display, m.views_precision, m.views_source, m.views_measured_at, m.views_evidence_url,
                       m.classification, m.content_hash, m.similar_content, m.similar_to_post_id,
                       m.engagement_rate_on_views, m.engagement_rate_approx,
                       m.price, m.sold_count, m.rating,
                       m.trend_tier, m.trend_group, m.verification_status, m.metrics_source,
                       m.data_quality, m.quality_issues_json, m.source_priority, m.is_corrected, m.run_id,
                       m.last_change_type, m.snapshot_count, m.views_missing,
                       m.last_views_delta, m.last_likes_delta, m.last_comments_delta, m.last_shares_delta, m.last_reposts_delta,
                       m.metrics_json, m.extra_meta_json, m.schema_version, m.raw_file_id, m.first_seen_at, m.last_seen_at,
                       r.file_name AS source_file_name
                FROM market_items m
                LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id
                {where_sql}
                ORDER BY {order_sql}
            """
            if qf_norm == "quality_only":
                rows = self._conn.execute(base_select, params).fetchall()
                total = None
            else:
                total = self._conn.execute(
                    f"SELECT COUNT(*) AS c FROM market_items m LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id {where_sql}",
                    params,
                ).fetchone()["c"]
                rows = self._conn.execute(
                    f"{base_select} LIMIT ? OFFSET ?",
                    params + [lim, off],
                ).fetchall()

        items = []
        for r in rows:
            d = dict(r)
            d["is_corrected"] = bool(d.get("is_corrected"))
            d["drive_url"] = self._build_drive_view_url(d.get("raw_file_id"))
            try:
                d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
            except Exception:
                d["metrics"] = {}
            try:
                d["extra_meta"] = json.loads(d.pop("extra_meta_json") or "{}")
            except Exception:
                d["extra_meta"] = {}
            try:
                d["quality_issues"] = json.loads(d.pop("quality_issues_json") or "[]")
            except Exception:
                d["quality_issues"] = []
            enriched = self._attach_v33_fields(d)
            if qf_norm == "quality_only" and not enriched.get("quality_qualified"):
                continue
            items.append(enriched)

        if qf_norm == "quality_only":
            total = len(items)
            items = items[off : off + lim]

        return {"total": int(total or 0), "limit": lim, "offset": off, "items": items}

    def query_item_snapshots(self, item_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.*, r.file_name AS source_file_name
                FROM market_item_snapshots s
                LEFT JOIN raw_drive_files r ON s.raw_file_id = r.file_id
                WHERE s.item_id = ?
                ORDER BY s.snapshot_at ASC, s.rowid ASC
                """,
                (item_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_item_detail(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT m.*, r.file_name AS source_file_name, r.raw_path AS source_raw_path
                FROM market_items m
                LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id
                WHERE m.id = ?
                """,
                (item_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            for src_key, dst_key in (
                ("metrics_json", "metrics"),
                ("extra_meta_json", "extra_meta"),
                ("raw_payload_json", "raw_payload"),
                ("quality_issues_json", "quality_issues"),
            ):
                raw_txt = d.pop(src_key, "{}")
                try:
                    d[dst_key] = json.loads(raw_txt or "{}")
                except Exception:
                    d[dst_key] = raw_txt
            snaps = self._conn.execute(
                """
                SELECT s.*, r.file_name AS source_file_name
                FROM market_item_snapshots s
                LEFT JOIN raw_drive_files r ON s.raw_file_id = r.file_id
                WHERE s.item_id = ?
                ORDER BY s.snapshot_at ASC, s.rowid ASC
                """,
                (item_id,),
            ).fetchall()
            d["snapshots"] = [dict(s) for s in snaps]
            return d

    def _run_meta_crawl_ts(self, meta: Dict[str, Any], modified_time: Optional[str] = None) -> float:
        for key in ("crawled_at", "crawl_time", "ended_at", "collected_at", "started_at", "generated_at"):
            val = meta.get(key)
            if val:
                parsed = self._parse_iso_ts(val)
                if parsed > 0:
                    return parsed
        return self._parse_iso_ts(modified_time)

    def get_stats(self, run_key: Optional[str] = None, platform: Optional[str] = None) -> Dict[str, Any]:
        active_item_join = f"LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id WHERE (m.raw_file_id IS NULL OR {_NOT_ARCHIVE_RAW_SQL})"
        plat_scope = (platform or "all").strip().lower()

        if run_key and str(run_key).strip() not in ("", "all"):
            grp = self._resolve_run_scope(run_key)
            if not grp:
                empty_act = {"total": 0, "new": 0, "growth": 0, "multi_run": 0, "missing_views": 0, "unverified": 0, "rejected": 0}
                return {
                    "total_items": 0,
                    "total_snapshots": 0,
                    "total_raw_files": 0,
                    "by_activity": dict(empty_act),
                    "by_platform": {"threads": 0, "tiktok": 0, "shopee": 0},
                    "by_platform_activity": {p: dict(empty_act) for p in ("all", "threads", "tiktok", "shopee")},
                    "by_quality": {"usable": 0, "needs_verification": 0, "unlabeled": 0},
                    "by_platform_quality": {p: {"total": 0, "usable": 0, "needs_verification": 0, "unlabeled": 0} for p in ("threads", "tiktok", "shopee")},
                    "by_trend_tier": {"very_hot": 0, "hot": 0, "emerging": 0, "watchlist": 0, "unverified": 0, "legacy": 0},
                    "threads_by_trend_tier": {"very_hot": 0, "hot": 0, "emerging": 0, "watchlist": 0, "unverified": 0, "legacy": 0},
                    "by_platform_trend_tier": {},
                    "by_trend_group": {},
                    "by_verification_status": {},
                    "trends_snapshot": None,
                    "latest_v3_screening": None,
                    "engagement_completeness": {"total_items": 0, "with_likes": 0, "with_comments": 0, "with_views": 0, "with_shares": 0, "with_reposts": 0, "with_quotes": 0, "with_price": 0, "likes_pct": None, "comments_pct": None, "views_pct": None},
                    "by_content_group": {},
                    "by_purchase_intent": {},
                    "upcoming_vouchers": [],
                    "non_crawl_files": [],
                    "last_sync_at": self.get_state("last_sync_at"),
                    "last_sync_status": self.get_state("last_sync_status", "idle"),
                    "last_error": self.get_state("last_error"),
                    "cursor": self.get_state("drive_cursor"),
                    "sync_interval_seconds": self.get_sync_interval_seconds(),
                    "latest_run": self.get_latest_sync_run(),
                    "run_key": run_key,
                    "run_summary": None,
                }

            all_snaps = self._query_effective_run_snapshots(
                run_id=grp["run_id"],
                run_ids=grp.get("run_ids"),
                raw_file_ids=[str(f["file_id"]) for f in grp["data_files"]],
                platform="all",
                prefer_qualified=bool(grp.get("is_request_bundle")),
            )
            scoped_snaps = all_snaps if plat_scope == "all" else [it for it in all_snaps if str(it.get("platform") or "").lower() == plat_scope]

            def _init_act() -> Dict[str, int]:
                return {"total": 0, "new": 0, "growth": 0, "multi_run": 0, "missing_views": 0, "unverified": 0, "rejected": 0}

            def _init_tiers() -> Dict[str, int]:
                return {"very_hot": 0, "hot": 0, "emerging": 0, "watchlist": 0, "unverified": 0, "legacy": 0}

            by_platform = {"threads": 0, "tiktok": 0, "shopee": 0}
            by_platform_activity: Dict[str, Dict[str, int]] = {"all": _init_act(), "threads": _init_act(), "tiktok": _init_act(), "shopee": _init_act()}
            by_platform_quality: Dict[str, Dict[str, int]] = {p: {"total": 0, "usable": 0, "needs_verification": 0, "unlabeled": 0} for p in ("threads", "tiktok", "shopee")}
            by_platform_trend_tier: Dict[str, Dict[str, int]] = {"all": _init_tiers(), "threads": _init_tiers(), "tiktok": _init_tiers(), "shopee": _init_tiers()}

            for it in all_snaps:
                ip = str(it.get("platform") or "unknown").lower()
                if ip not in by_platform:
                    by_platform[ip] = 0
                by_platform[ip] += 1
                for k_map, init_fn in ((by_platform_activity, _init_act), (by_platform_trend_tier, _init_tiers)):
                    if ip not in k_map:
                        k_map[ip] = init_fn()
                if ip not in by_platform_quality:
                    by_platform_quality[ip] = {"total": 0, "usable": 0, "needs_verification": 0, "unlabeled": 0}

                dq_raw = str(it.get("data_quality") or "unlabeled").lower()
                dq = dq_raw if dq_raw in ("usable", "needs_verification") else "unlabeled"
                by_platform_quality[ip][dq] += 1
                by_platform_quality[ip]["total"] += 1

                cls = str(it.get("classification") or "").upper()
                ctype = str(it.get("last_change_type") or "new").lower()
                it_tier = str(it.get("trend_tier") or "").lower()
                it_ver = str(it.get("verification_status") or "").lower()
                has_growth = any(int(it.get(k) or 0) > 0 for k in ("last_views_delta", "last_likes_delta", "last_comments_delta", "last_shares_delta", "last_reposts_delta"))

                for bucket_key in ("all", ip):
                    act_b = by_platform_activity[bucket_key]
                    act_b["total"] += 1
                    if it.get("views_count") is None and (not cls or cls in ("NEW", "UNVERIFIED")):
                        act_b["missing_views"] += 1
                    if cls == "REJECTED":
                        act_b["rejected"] += 1
                    elif cls == "DUPLICATE_HISTORY" or ctype == "duplicate_history" or (not cls and int(it.get("snapshot_count") or 1) > 1):
                        act_b["multi_run"] += 1
                    elif cls == "UPDATED_METRICS" or ctype in ("updated", "metrics_updated") or has_growth:
                        act_b["growth"] += 1
                    elif cls == "UNVERIFIED" or it_ver == "unverified" or dq == "needs_verification" or it_tier == "unverified":
                        act_b["unverified"] += 1
                    else:
                        act_b["new"] += 1

                    tier_b = by_platform_trend_tier[bucket_key]
                    if cls not in ("REJECTED", "UPDATED_METRICS", "DUPLICATE_HISTORY"):
                        if it_ver == "unverified" or it_tier == "unverified" or cls == "UNVERIFIED":
                            tier_b["unverified"] += 1
                        elif it_tier in ("very_hot", "hot", "emerging", "watchlist"):
                            tier_b[it_tier] += 1
                        elif not cls:
                            tier_b["legacy"] += 1

            total_items = len(scoped_snaps)
            by_quality = {"usable": 0, "needs_verification": 0, "unlabeled": 0}
            by_trend_group = {"trend_toan_thi_truong": 0, "trend_affiliate": 0, "trend_moi_noi": 0}
            by_verification_status = {"cross_verified_web": 0, "connector_verified": 0, "unverified": 0}
            by_content_group: Dict[str, int] = {}
            by_purchase_intent: Dict[str, int] = {}
            wl = wc = wv = ws = wr = wq = wp = 0
            for it in scoped_snaps:
                dq = it.get("data_quality")
                by_quality[dq if dq in ("usable", "needs_verification") else "unlabeled"] += 1
                tg = it.get("trend_group")
                if tg:
                    by_trend_group[tg] = by_trend_group.get(tg, 0) + 1
                vs = it.get("verification_status")
                if vs:
                    by_verification_status[vs] = by_verification_status.get(vs, 0) + 1
                cg = it.get("content_group") or "(không có)"
                by_content_group[cg] = by_content_group.get(cg, 0) + 1
                pi = it.get("purchase_intent") or "(không có)"
                by_purchase_intent[pi] = by_purchase_intent.get(pi, 0) + 1
                if it.get("likes_count") is not None:
                    wl += 1
                if it.get("comments_count") is not None:
                    wc += 1
                if it.get("views_count") is not None:
                    wv += 1
                if it.get("shares_count") is not None:
                    ws += 1
                if it.get("reposts_count") is not None:
                    wr += 1
                if it.get("quotes_count") is not None:
                    wq += 1
                if it.get("price") is not None:
                    wp += 1
            denom = max(1, total_items)
            engagement_completeness = {
                "total_items": total_items,
                "with_likes": wl,
                "with_comments": wc,
                "with_views": wv,
                "with_shares": ws,
                "with_reposts": wr,
                "with_quotes": wq,
                "with_price": wp,
                "likes_pct": round((wl * 100.0) / denom, 1) if total_items else None,
                "comments_pct": round((wc * 100.0) / denom, 1) if total_items else None,
                "views_pct": round((wv * 100.0) / denom, 1) if total_items else None,
            }

            run_summary = self._summarize_run_group(grp, platform=plat_scope)
            # Run-scoped screening / trends_snapshot / vouchers from THIS run's files or paired report
            trends_snapshot = None
            latest_v3_screening = None
            upcoming_vouchers: List[Any] = []
            for df in sorted(grp["data_files"], key=lambda x: (1 if x.get("is_corrected") else 0, str(x.get("modified_time") or "")), reverse=True):
                meta = df.get("_meta") or {}
                if not upcoming_vouchers and isinstance(meta.get("upcoming_vouchers"), list):
                    upcoming_vouchers = meta["upcoming_vouchers"]
                if not trends_snapshot and isinstance(meta.get("trends_snapshot"), dict):
                    trends_snapshot = meta["trends_snapshot"]
                if not latest_v3_screening and (meta.get("screening") or meta.get("counters") or meta.get("tier_counts") or meta.get("run_status")):
                    latest_v3_screening = {
                        "file_name": df["file_name"],
                        "run_id": grp["run_id"],
                        "run_status": meta.get("run_status"),
                        "technical_status": meta.get("technical_status"),
                        "data_quality_status": meta.get("data_quality_status"),
                        "source": meta.get("source"),
                        "screening": meta.get("screening") or meta.get("counters"),
                        "counters": meta.get("counters"),
                        "tier_counts": meta.get("tier_counts") or run_summary["tier_counts"],
                        "group_counts": meta.get("group_counts"),
                        "cross_verified_posts": meta.get("cross_verified_posts"),
                        "thresholds": meta.get("thresholds"),
                        "notes": meta.get("notes"),
                    }
            if grp["reports"]:
                try:
                    rep_obj = json.loads(grp["reports"][0].get("report_json") or "{}")
                except Exception:
                    rep_obj = {}
                if not trends_snapshot and isinstance(rep_obj.get("trends_snapshot"), dict):
                    trends_snapshot = rep_obj["trends_snapshot"]
                if not trends_snapshot and isinstance(rep_obj.get("trending_highlights"), list) and rep_obj["trending_highlights"]:
                    trends_snapshot = {
                        "summary": f"Chủ đề nổi bật từ {len(rep_obj['trending_highlights'])} nhóm xu hướng trong lượt crawl",
                        "topics": [
                            {
                                "topic": str(t.get("title") or t.get("topic") or t.get("name") or "").strip(),
                                "posts_label": t.get("basis") or (f"{len(t['evidence_ids'])} bài" if isinstance(t.get("evidence_ids"), list) and t["evidence_ids"] else None),
                            }
                            for t in rep_obj["trending_highlights"]
                            if str(t.get("title") or t.get("topic") or t.get("name") or "").strip()
                        ],
                    }

            scoped_act = dict(by_platform_activity.get(plat_scope) or _init_act())
            audit_diag = self.get_run_audit_diagnostics(
                raw_file_ids=[str(f["file_id"]) for f in grp["data_files"]],
                run_id=grp["run_id"],
                run_ids=grp.get("run_ids"),
                limit=50,
            )
            return {
                "total_items": total_items,
                "total_snapshots": len(all_snaps),
                "total_raw_files": len(grp["data_files"]) + len(grp["report_files"]),
                "by_activity": scoped_act,
                "by_platform": by_platform,
                "by_platform_activity": by_platform_activity,
                "by_quality": by_quality,
                "by_platform_quality": by_platform_quality,
                "by_trend_tier": by_platform_trend_tier.get(plat_scope) or _init_tiers(),
                "threads_by_trend_tier": by_platform_trend_tier["threads"],
                "by_platform_trend_tier": by_platform_trend_tier,
                "by_trend_group": by_trend_group,
                "by_verification_status": by_verification_status,
                "trends_snapshot": trends_snapshot,
                "latest_v3_screening": latest_v3_screening,
                "engagement_completeness": engagement_completeness,
                "by_content_group": dict(sorted(by_content_group.items(), key=lambda kv: kv[1], reverse=True)),
                "by_purchase_intent": dict(sorted(by_purchase_intent.items(), key=lambda kv: kv[1], reverse=True)),
                "upcoming_vouchers": upcoming_vouchers,
                "non_crawl_files": [],
                "last_sync_at": self.get_state("last_sync_at"),
                "last_sync_status": self.get_state("last_sync_status", "idle"),
                "last_error": self.get_state("last_error"),
                "cursor": self.get_state("drive_cursor"),
                "sync_interval_seconds": self.get_sync_interval_seconds(),
                "latest_run": self.get_latest_sync_run(),
                "run_key": grp["run_key"],
                "run_id": grp["run_id"],
                "run_summary": run_summary,
                "technical_diagnostics": audit_diag,
            }

        with self._lock:
            total_items = self._conn.execute(f"SELECT COUNT(*) AS c FROM market_items m {active_item_join}").fetchone()["c"]
            total_raw_files = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM raw_drive_files r WHERE r.status = 'synced' AND {_NOT_ARCHIVE_RAW_SQL}"
            ).fetchone()["c"]

            by_platform = {"threads": 0, "tiktok": 0, "shopee": 0}
            for r in self._conn.execute(
                f"SELECT m.platform, COUNT(*) AS c FROM market_items m {active_item_join} GROUP BY m.platform"
            ).fetchall():
                by_platform[r["platform"]] = r["c"]

            by_quality = {"usable": 0, "needs_verification": 0, "unlabeled": 0}
            for r in self._conn.execute(
                f"SELECT COALESCE(m.data_quality, 'unlabeled') AS dq, COUNT(*) AS c FROM market_items m {active_item_join} GROUP BY dq"
            ).fetchall():
                key = r["dq"] if r["dq"] in by_quality else "unlabeled"
                by_quality[key] = r["c"]

            by_platform_quality: Dict[str, Dict[str, int]] = {
                p: {"total": 0, "usable": 0, "needs_verification": 0, "unlabeled": 0}
                for p in ("threads", "tiktok", "shopee")
            }
            for r in self._conn.execute(
                f"SELECT m.platform, COALESCE(m.data_quality, 'unlabeled') AS dq, COUNT(*) AS c FROM market_items m {active_item_join} GROUP BY m.platform, dq"
            ).fetchall():
                plat = r["platform"]
                if plat not in by_platform_quality:
                    by_platform_quality[plat] = {"total": 0, "usable": 0, "needs_verification": 0, "unlabeled": 0}
                dq = r["dq"] if r["dq"] in ("usable", "needs_verification") else "unlabeled"
                by_platform_quality[plat][dq] += r["c"]
                by_platform_quality[plat]["total"] += r["c"]

            # Interaction completeness (never treats null as 0)
            eng_row = self._conn.execute(
                f"""
                SELECT
                    SUM(CASE WHEN m.likes_count IS NOT NULL THEN 1 ELSE 0 END) AS with_likes,
                    SUM(CASE WHEN m.comments_count IS NOT NULL THEN 1 ELSE 0 END) AS with_comments,
                    SUM(CASE WHEN m.views_count IS NOT NULL THEN 1 ELSE 0 END) AS with_views,
                    SUM(CASE WHEN m.shares_count IS NOT NULL THEN 1 ELSE 0 END) AS with_shares,
                    SUM(CASE WHEN m.reposts_count IS NOT NULL THEN 1 ELSE 0 END) AS with_reposts,
                    SUM(CASE WHEN m.quotes_count IS NOT NULL THEN 1 ELSE 0 END) AS with_quotes,
                    SUM(CASE WHEN m.price IS NOT NULL THEN 1 ELSE 0 END) AS with_price
                FROM market_items m
                {active_item_join}
                """
            ).fetchone()
            denom = max(1, total_items)
            engagement_completeness = {
                "total_items": total_items,
                "with_likes": eng_row["with_likes"] or 0,
                "with_comments": eng_row["with_comments"] or 0,
                "with_views": eng_row["with_views"] or 0,
                "with_shares": eng_row["with_shares"] or 0,
                "with_reposts": eng_row["with_reposts"] or 0,
                "with_quotes": eng_row["with_quotes"] or 0,
                "with_price": eng_row["with_price"] or 0,
                "likes_pct": round(((eng_row["with_likes"] or 0) * 100.0) / denom, 1) if total_items else None,
                "comments_pct": round(((eng_row["with_comments"] or 0) * 100.0) / denom, 1) if total_items else None,
                "views_pct": round(((eng_row["with_views"] or 0) * 100.0) / denom, 1) if total_items else None,
            }

            by_trend_tier = {"very_hot": 0, "hot": 0, "emerging": 0, "watchlist": 0, "unverified": 0, "legacy": 0}
            threads_by_trend_tier = {"very_hot": 0, "hot": 0, "emerging": 0, "watchlist": 0, "unverified": 0, "legacy": 0}
            by_platform_trend_tier: Dict[str, Dict[str, int]] = {
                p: {"very_hot": 0, "hot": 0, "emerging": 0, "watchlist": 0, "unverified": 0, "legacy": 0}
                for p in ("all", "threads", "tiktok", "shopee")
            }
            for r in self._conn.execute(
                f"""
                SELECT m.platform,
                       CASE WHEN COALESCE(m.classification, '') = 'REJECTED' THEN '__rejected__'
                            WHEN m.verification_status = 'unverified' OR m.classification = 'UNVERIFIED' THEN 'unverified'
                            ELSE m.trend_tier END AS eff_tier,
                       COUNT(*) AS c
                FROM market_items m
                {active_item_join}
                GROUP BY m.platform, eff_tier
                """
            ).fetchall():
                if r["eff_tier"] == "__rejected__":
                    continue
                tkey = r["eff_tier"] if r["eff_tier"] in by_trend_tier else "legacy"
                by_trend_tier[tkey] += r["c"]
                by_platform_trend_tier["all"][tkey] += r["c"]
                p_low = str(r["platform"] or "").lower()
                if p_low not in by_platform_trend_tier:
                    by_platform_trend_tier[p_low] = {"very_hot": 0, "hot": 0, "emerging": 0, "watchlist": 0, "unverified": 0, "legacy": 0}
                by_platform_trend_tier[p_low][tkey] += r["c"]
                if p_low == "threads":
                    threads_by_trend_tier[tkey] += r["c"]

            by_trend_group = {"trend_toan_thi_truong": 0, "trend_affiliate": 0, "trend_moi_noi": 0}
            for r in self._conn.execute(
                f"SELECT m.trend_group, COUNT(*) AS c FROM market_items m {active_item_join} AND m.trend_group IS NOT NULL GROUP BY m.trend_group"
            ).fetchall():
                by_trend_group[r["trend_group"]] = r["c"]

            by_verification_status = {"cross_verified_web": 0, "connector_verified": 0, "unverified": 0}
            for r in self._conn.execute(
                f"SELECT m.verification_status, COUNT(*) AS c FROM market_items m {active_item_join} AND m.verification_status IS NOT NULL GROUP BY m.verification_status"
            ).fetchall():
                by_verification_status[r["verification_status"]] = r["c"]

            by_content_group = {}
            for r in self._conn.execute(
                f"SELECT COALESCE(m.content_group, '(không có)') AS k, COUNT(*) AS c FROM market_items m {active_item_join} GROUP BY k ORDER BY c DESC"
            ).fetchall():
                by_content_group[r["k"]] = r["c"]

            by_purchase_intent = {}
            for r in self._conn.execute(
                f"SELECT COALESCE(m.purchase_intent, '(không có)') AS k, COUNT(*) AS c FROM market_items m {active_item_join} GROUP BY k ORDER BY c DESC"
            ).fetchall():
                by_purchase_intent[r["k"]] = r["c"]

            # Upcoming vouchers from latest Shopee file run_meta_json (ordered by crawl time, Archive excluded)
            upcoming_vouchers: List[Any] = []
            shopee_rows = []
            for r in self._conn.execute(
                f"""
                SELECT r.file_name, r.is_corrected, r.modified_time, r.run_meta_json
                FROM raw_drive_files r
                WHERE r.file_role = 'crawl_data' AND lower(r.file_name) LIKE '%shopee%'
                  AND {_NOT_ARCHIVE_RAW_SQL}
                LIMIT 20
                """
            ).fetchall():
                try:
                    meta = json.loads(r["run_meta_json"] or "{}")
                    shopee_rows.append((self._run_meta_crawl_ts(meta, r["modified_time"]), int(r["is_corrected"] or 0), meta))
                except Exception:
                    pass
            shopee_rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
            for _ts, _corr, meta in shopee_rows:
                if isinstance(meta.get("upcoming_vouchers"), list):
                    upcoming_vouchers = meta["upcoming_vouchers"]
                    break

            # Latest V3.1 Trends Snapshot & Screening from latest threads_trend_* ordered by crawl time
            trends_snapshot = None
            latest_v3_screening = None
            v3_rows = []
            for r in self._conn.execute(
                f"""
                SELECT r.file_name, r.is_corrected, r.modified_time, r.run_meta_json
                FROM raw_drive_files r
                WHERE r.file_role = 'crawl_data' AND lower(r.file_name) LIKE 'threads_trend%'
                  AND {_NOT_ARCHIVE_RAW_SQL}
                LIMIT 20
                """
            ).fetchall():
                try:
                    meta = json.loads(r["run_meta_json"] or "{}")
                    v3_rows.append((
                        self._run_meta_crawl_ts(meta, r["modified_time"]),
                        int(r["is_corrected"] or 0),
                        self._parse_iso_ts(r["modified_time"]),
                        r["file_name"],
                        meta,
                    ))
                except Exception:
                    pass
            v3_rows.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
            for _ts, _corr, _mts, fname, meta in v3_rows:
                if not trends_snapshot and isinstance(meta.get("trends_snapshot"), dict):
                    trends_snapshot = meta["trends_snapshot"]
                if not latest_v3_screening and (meta.get("screening") or meta.get("tier_counts") or meta.get("run_status")):
                    latest_v3_screening = {
                        "file_name": fname,
                        "run_id": meta.get("run_id"),
                        "run_status": meta.get("run_status"),
                        "technical_status": meta.get("technical_status"),
                        "data_quality_status": meta.get("data_quality_status"),
                        "source": meta.get("source"),
                        "screening": meta.get("screening") or meta.get("counters"),
                        "counters": meta.get("counters"),
                        "tier_counts": meta.get("tier_counts"),
                        "group_counts": meta.get("group_counts"),
                        "cross_verified_posts": meta.get("cross_verified_posts"),
                        "thresholds": meta.get("thresholds"),
                        "notes": meta.get("notes"),
                    }
                if trends_snapshot and latest_v3_screening:
                    break

            non_crawl_files = [
                {
                    "file_name": r["file_name"],
                    "file_role": r["file_role"],
                    "synced_at": r["synced_at"],
                }
                for r in self._conn.execute(
                    f"""
                    SELECT r.file_name, r.file_role, r.synced_at
                    FROM raw_drive_files r
                    WHERE r.file_role IN ('data_contract', 'crawl_strategy', 'quality_audit', 'research_doc', 'capability_test', 'identity_index')
                      AND {_NOT_ARCHIVE_RAW_SQL}
                    ORDER BY r.synced_at DESC
                    """
                ).fetchall()
            ]

            latest_run = self.get_latest_sync_run()
            total_snapshots = self._conn.execute(
                f"""
                SELECT COUNT(*) AS c
                FROM market_item_snapshots s
                JOIN market_items m ON s.item_id = m.id
                LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id
                WHERE (m.raw_file_id IS NULL OR {_NOT_ARCHIVE_RAW_SQL})
                """
            ).fetchone()["c"]

            plat_act_rows = self._conn.execute(
                f"""
                SELECT m.platform,
                    COUNT(*) AS total_items,
                    SUM(CASE WHEN COALESCE(m.snapshot_count, 1) = 1 AND COALESCE(m.last_change_type, 'new') = 'new' THEN 1 ELSE 0 END) AS new_items,
                    SUM(CASE WHEN COALESCE(m.last_views_delta, 0) > 0 OR COALESCE(m.last_likes_delta, 0) > 0 OR COALESCE(m.last_comments_delta, 0) > 0 OR COALESCE(m.last_shares_delta, 0) > 0 OR COALESCE(m.last_reposts_delta, 0) > 0 OR m.last_change_type = 'updated' OR EXISTS (SELECT 1 FROM market_item_snapshots s WHERE s.item_id = m.id AND s.change_type = 'updated') THEN 1 ELSE 0 END) AS growth_items,
                    SUM(CASE WHEN COALESCE(m.snapshot_count, 1) > 1 OR m.classification = 'DUPLICATE_HISTORY' THEN 1 ELSE 0 END) AS multi_run_items,
                    SUM(CASE WHEN m.views_count IS NULL AND (m.classification IS NULL OR m.classification IN ('', 'NEW', 'UNVERIFIED')) THEN 1 ELSE 0 END) AS missing_views_items,
                    SUM(CASE WHEN m.views_count IS NULL THEN 1 ELSE 0 END) AS missing_views_all,
                    SUM(CASE WHEN COALESCE(m.classification, '') != 'REJECTED' AND (m.classification = 'UNVERIFIED' OR m.verification_status = 'unverified' OR m.data_quality = 'needs_verification' OR m.trend_tier = 'unverified') THEN 1 ELSE 0 END) AS unverified_items,
                    SUM(CASE WHEN m.classification = 'REJECTED' THEN 1 ELSE 0 END) AS rejected_items
                FROM market_items m
                {active_item_join}
                GROUP BY m.platform
                """
            ).fetchall()

            def _empty_pa() -> Dict[str, int]:
                return {"total": 0, "new": 0, "growth": 0, "multi_run": 0, "missing_views": 0, "unverified": 0, "rejected": 0}

            by_platform_activity: Dict[str, Dict[str, int]] = {"all": _empty_pa(), "threads": _empty_pa(), "tiktok": _empty_pa(), "shopee": _empty_pa()}
            global_missing_views_raw = 0
            for ar in plat_act_rows:
                p_low = str(ar["platform"] or "unknown").lower()
                entry = {
                    "total": int(ar["total_items"] or 0),
                    "new": int(ar["new_items"] or 0),
                    "growth": int(ar["growth_items"] or 0),
                    "multi_run": int(ar["multi_run_items"] or 0),
                    "missing_views": int(ar["missing_views_items"] or 0),
                    "unverified": int(ar["unverified_items"] or 0),
                    "rejected": int(ar["rejected_items"] or 0),
                }
                by_platform_activity[p_low] = entry
                for k, v in entry.items():
                    by_platform_activity["all"][k] += v
                global_missing_views_raw += int(ar["missing_views_all"] or 0)

            by_activity = {
                "total": by_platform_activity["all"]["total"],
                "new": by_platform_activity["all"]["new"],
                "growth": by_platform_activity["all"]["growth"],
                "multi_run": by_platform_activity["all"]["multi_run"],
                "missing_views": global_missing_views_raw,
                "unverified": by_platform_activity["all"]["unverified"],
                "rejected": by_platform_activity["all"]["rejected"],
            }

        return {
            "total_items": total_items,
            "total_snapshots": total_snapshots,
            "total_raw_files": total_raw_files,
            "by_activity": by_activity,
            "by_platform": by_platform,
            "by_platform_activity": by_platform_activity,
            "by_quality": by_quality,
            "by_platform_quality": by_platform_quality,
            "by_trend_tier": by_trend_tier,
            "threads_by_trend_tier": threads_by_trend_tier,
            "by_platform_trend_tier": by_platform_trend_tier,
            "by_trend_group": by_trend_group,
            "by_verification_status": by_verification_status,
            "trends_snapshot": trends_snapshot,
            "latest_v3_screening": latest_v3_screening,
            "engagement_completeness": engagement_completeness,
            "by_content_group": by_content_group,
            "by_purchase_intent": by_purchase_intent,
            "upcoming_vouchers": upcoming_vouchers,
            "non_crawl_files": non_crawl_files,
            "last_sync_at": self.get_state("last_sync_at"),
            "last_sync_status": self.get_state("last_sync_status", "idle"),
            "last_error": self.get_state("last_error"),
            "cursor": self.get_state("drive_cursor"),
            "sync_interval_seconds": self.get_sync_interval_seconds(),
            "latest_run": latest_run,
        }

    def get_filter_options(self) -> Dict[str, List[str]]:
        active_join = f"LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id WHERE (m.raw_file_id IS NULL OR {_NOT_ARCHIVE_RAW_SQL})"
        with self._lock:
            platforms = [
                r["platform"]
                for r in self._conn.execute(
                    f"SELECT DISTINCT m.platform FROM market_items m {active_join} ORDER BY m.platform"
                ).fetchall()
                if r["platform"]
            ]
            content_groups = [
                r["content_group"]
                for r in self._conn.execute(
                    f"SELECT DISTINCT m.content_group FROM market_items m {active_join} AND m.content_group IS NOT NULL ORDER BY m.content_group"
                ).fetchall()
            ]
            purchase_intents = [
                r["purchase_intent"]
                for r in self._conn.execute(
                    f"SELECT DISTINCT m.purchase_intent FROM market_items m {active_join} AND m.purchase_intent IS NOT NULL ORDER BY m.purchase_intent"
                ).fetchall()
            ]
            trend_tiers = [
                r["trend_tier"]
                for r in self._conn.execute(
                    f"SELECT DISTINCT m.trend_tier FROM market_items m {active_join} AND m.trend_tier IS NOT NULL ORDER BY m.trend_tier"
                ).fetchall()
            ]
            trend_groups = [
                r["trend_group"]
                for r in self._conn.execute(
                    f"SELECT DISTINCT m.trend_group FROM market_items m {active_join} AND m.trend_group IS NOT NULL ORDER BY m.trend_group"
                ).fetchall()
            ]
            verification_statuses = [
                r["verification_status"]
                for r in self._conn.execute(
                    f"SELECT DISTINCT m.verification_status FROM market_items m {active_join} AND m.verification_status IS NOT NULL ORDER BY m.verification_status"
                ).fetchall()
            ]
        return {
            "platforms": platforms,
            "content_groups": content_groups,
            "purchase_intents": purchase_intents,
            "trend_tiers": trend_tiers,
            "trend_groups": trend_groups,
            "verification_statuses": verification_statuses,
        }

    def mark_files_archived(self, file_ids: Iterable[str]) -> int:
        """Mark explicitly listed Drive files as moved to Archive/ without deleting historical rows."""
        ids = [str(fid).strip() for fid in (file_ids or []) if str(fid or "").strip()]
        if not ids:
            return 0
        now_iso = _utc_now_iso()
        updated = 0
        with self._lock:
            for fid in ids:
                cur = self._conn.execute(
                    """
                    UPDATE raw_drive_files
                    SET is_archived = 1, archived_at = COALESCE(archived_at, ?)
                    WHERE file_id = ? AND COALESCE(is_archived, 0) = 0
                    """,
                    (now_iso, fid),
                )
                updated += int(cur.rowcount or 0)
            if updated:
                self._conn.commit()
        return updated

    def reconcile_moved_archive_files(self, active_file_ids: Iterable[str]) -> Dict[str, int]:
        """
        Reconcile previously synced Drive files after files are moved into (or restored from) Archive/.
        Excludes archived files from current reporting via `is_archived=1` while preserving all
        historical SQLite rows (`raw_drive_files`, `market_items`, `market_reports`) and disk files.
        """
        active_ids = {str(fid).strip() for fid in (active_file_ids or []) if str(fid or "").strip()}
        now_iso = _utc_now_iso()
        archived_count = 0
        restored_count = 0
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT file_id, COALESCE(is_archived, 0) AS is_archived
                FROM raw_drive_files
                WHERE file_id NOT LIKE 'local_%'
                  AND COALESCE(folder_path, '') NOT IN ('local_import', 'local_upload')
                """
            ).fetchall()
            for r in rows:
                fid = r["file_id"]
                was_archived = int(r["is_archived"] or 0) == 1
                if fid not in active_ids and not was_archived:
                    self._conn.execute(
                        "UPDATE raw_drive_files SET is_archived = 1, archived_at = COALESCE(archived_at, ?) WHERE file_id = ?",
                        (now_iso, fid),
                    )
                    archived_count += 1
                elif fid in active_ids and was_archived:
                    self._conn.execute(
                        "UPDATE raw_drive_files SET is_archived = 0, archived_at = NULL WHERE file_id = ?",
                        (fid,),
                    )
                    restored_count += 1
            if archived_count or restored_count:
                self._conn.commit()
        return {"archived": archived_count, "restored": restored_count}

    def list_raw_files(self, limit: int = 30, include_archived: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            where_sql = "" if include_archived else f"WHERE {_NOT_ARCHIVE_RAW_SQL}"
            rows = self._conn.execute(
                f"SELECT * FROM raw_drive_files r {where_sql} ORDER BY r.is_corrected DESC, r.synced_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Run-Centric Backfill, Snapshots & Pairing Helpers ───────────────────

    def _ensure_run_centric_backfill(self):
        """One-time + self-healing backfill of point-in-time snapshots and raw file metadata for cached Drive JSONs."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM raw_drive_files WHERE status = 'synced' AND raw_path IS NOT NULL"
                ).fetchone()
                cached_count = int(row["c"] or 0) if row else 0
                needs_heal = False
                if cached_count > 0:
                    if self.get_state("run_centric_backfill_v4") != "done":
                        needs_heal = True
                    else:
                        null_meta_snap = self._conn.execute(
                            "SELECT 1 FROM market_item_snapshots WHERE extra_meta_json IS NULL LIMIT 1"
                        ).fetchone()
                        unpop_raw = self._conn.execute(
                            """
                            SELECT 1 FROM raw_drive_files
                            WHERE status = 'synced' AND COALESCE(is_archived, 0) = 0
                              AND file_role IN ('crawl_data', 'market_report')
                              AND (
                                  (run_id IS NULL AND json_extract(run_meta_json, '$.run_id') IS NOT NULL)
                                  OR (strategy_version IS NULL AND json_extract(run_meta_json, '$.strategy_version') IS NOT NULL)
                              )
                            LIMIT 1
                            """
                        ).fetchone()
                        needs_heal = bool(null_meta_snap or unpop_raw)
            if needs_heal and cached_count > 0:
                self.reconcile_cached_raw_files()
            else:
                with self._lock:
                    self._conn.execute(
                        """
                        UPDATE raw_drive_files
                        SET crawl_mode = COALESCE(crawl_mode, json_extract(run_meta_json, '$.crawl_mode')),
                            topics_json = COALESCE(topics_json, json_extract(run_meta_json, '$.topics')),
                            keywords = COALESCE(keywords, json_extract(run_meta_json, '$.keywords')),
                            lookback_hours = COALESCE(lookback_hours, json_extract(run_meta_json, '$.lookback_hours'))
                        WHERE status = 'synced'
                          AND COALESCE(is_archived, 0) = 0
                          AND (
                              (crawl_mode IS NULL AND json_extract(run_meta_json, '$.crawl_mode') IS NOT NULL)
                              OR (topics_json IS NULL AND json_extract(run_meta_json, '$.topics') IS NOT NULL)
                              OR (keywords IS NULL AND json_extract(run_meta_json, '$.keywords') IS NOT NULL)
                              OR (lookback_hours IS NULL AND json_extract(run_meta_json, '$.lookback_hours') IS NOT NULL)
                          )
                        """
                    )
                    self._conn.commit()
            self.set_state("run_centric_backfill_v4", "done")
        except Exception:
            # Never block startup if legacy cache has a bad raw file; subsequent syncs still write new columns.
            pass

    def upsert_crawl_request_metadata_cache(self, requests_list: Optional[Iterable[Dict[str, Any]]]) -> int:
        """Persist real request strategy metadata from Cloudflare Worker keyed by request_id (never guesses legacy runs)."""
        if not requests_list:
            return 0
        now_iso = _utc_now_iso()
        saved = 0

        def _opt_int(val: Any) -> Optional[int]:
            if val is None or isinstance(val, bool):
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return None

        with self._lock:
            for req in requests_list:
                if not isinstance(req, dict):
                    continue
                req_id = self._clean_run_str(req.get("id") or req.get("request_id"))
                if not req_id:
                    continue
                opts: Dict[str, Any] = {}
                if isinstance(req.get("options"), dict):
                    opts = req["options"]
                elif isinstance(req.get("options_json"), str) and req["options_json"].strip():
                    try:
                        parsed_o = json.loads(req["options_json"])
                        if isinstance(parsed_o, dict):
                            opts = parsed_o
                    except Exception:
                        opts = {}
                crawl_mode = self._clean_run_str(req.get("crawl_mode") or opts.get("crawl_mode"))
                topics_raw = req.get("topics") if req.get("topics") is not None else opts.get("topics")
                topic_ids, topic_labels = _normalize_crawl_topics(topics_raw, strict=False)
                lookback_raw = req.get("lookback_hours") if req.get("lookback_hours") is not None else opts.get("lookback_hours")
                lookback_int = _opt_int(lookback_raw)
                kw = self._clean_run_str(req.get("keywords"))
                excl_raw = req.get("exclusions") if isinstance(req.get("exclusions"), dict) else opts.get("exclusions")
                contract_ver = self._clean_run_str(req.get("contract_version") or opts.get("contract_version"))
                skill_ver = self._clean_run_str(req.get("skill_version") or opts.get("skill_version"))
                qp_ver = self._clean_run_str(req.get("quality_policy_version") or opts.get("quality_policy_version"))
                target_metric = self._clean_run_str(req.get("target_metric") or opts.get("target_metric"))
                exec_phase = self._clean_run_str(req.get("execution_phase") or req.get("display_status"))
                pause_reason = self._clean_run_str(req.get("pause_reason"))
                session_seq = _opt_int(req.get("session_seq"))
                lease_renew_count = _opt_int(req.get("lease_renew_count"))
                acc_quality = _opt_int(req.get("accumulated_quality_count") if req.get("accumulated_quality_count") is not None else req.get("actual_quality_count"))
                acc_new_q = _opt_int(req.get("accumulated_new_qualified_count") if req.get("accumulated_new_qualified_count") is not None else req.get("actual_new_qualified_count"))
                acc_watch = _opt_int(req.get("accumulated_watchlist_count"))

                if not (crawl_mode or topic_ids or kw or lookback_int is not None or contract_ver or target_metric or exec_phase or acc_quality is not None):
                    continue
                self._conn.execute(
                    """
                    INSERT INTO crawl_request_metadata_cache
                    (request_id, platform, crawl_mode, crawl_mode_label, topics_json, topic_labels_json,
                     keywords, lookback_hours, exclusions_json,
                     contract_version, skill_version, quality_policy_version, target_metric,
                     execution_phase, pause_reason, session_seq, lease_renew_count,
                     accumulated_quality_count, accumulated_new_qualified_count, accumulated_watchlist_count,
                     updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(request_id) DO UPDATE SET
                        platform = COALESCE(excluded.platform, crawl_request_metadata_cache.platform),
                        crawl_mode = COALESCE(excluded.crawl_mode, crawl_request_metadata_cache.crawl_mode),
                        crawl_mode_label = COALESCE(excluded.crawl_mode_label, crawl_request_metadata_cache.crawl_mode_label),
                        topics_json = COALESCE(excluded.topics_json, crawl_request_metadata_cache.topics_json),
                        topic_labels_json = COALESCE(excluded.topic_labels_json, crawl_request_metadata_cache.topic_labels_json),
                        keywords = COALESCE(excluded.keywords, crawl_request_metadata_cache.keywords),
                        lookback_hours = COALESCE(excluded.lookback_hours, crawl_request_metadata_cache.lookback_hours),
                        exclusions_json = COALESCE(excluded.exclusions_json, crawl_request_metadata_cache.exclusions_json),
                        contract_version = COALESCE(excluded.contract_version, crawl_request_metadata_cache.contract_version),
                        skill_version = COALESCE(excluded.skill_version, crawl_request_metadata_cache.skill_version),
                        quality_policy_version = COALESCE(excluded.quality_policy_version, crawl_request_metadata_cache.quality_policy_version),
                        target_metric = COALESCE(excluded.target_metric, crawl_request_metadata_cache.target_metric),
                        execution_phase = COALESCE(excluded.execution_phase, crawl_request_metadata_cache.execution_phase),
                        pause_reason = excluded.pause_reason,
                        session_seq = COALESCE(excluded.session_seq, crawl_request_metadata_cache.session_seq),
                        lease_renew_count = COALESCE(excluded.lease_renew_count, crawl_request_metadata_cache.lease_renew_count),
                        accumulated_quality_count = COALESCE(excluded.accumulated_quality_count, crawl_request_metadata_cache.accumulated_quality_count),
                        accumulated_new_qualified_count = COALESCE(excluded.accumulated_new_qualified_count, crawl_request_metadata_cache.accumulated_new_qualified_count),
                        accumulated_watchlist_count = COALESCE(excluded.accumulated_watchlist_count, crawl_request_metadata_cache.accumulated_watchlist_count),
                        updated_at = excluded.updated_at
                    """,
                    (
                        req_id,
                        self._clean_run_str(req.get("platform")),
                        crawl_mode,
                        self._clean_run_str(req.get("crawl_mode_label")) or _get_crawl_mode_label(crawl_mode),
                        json.dumps(topic_ids, ensure_ascii=False) if topics_raw is not None else None,
                        json.dumps(topic_labels, ensure_ascii=False) if topics_raw is not None else None,
                        kw,
                        lookback_int,
                        json.dumps(excl_raw, ensure_ascii=False) if isinstance(excl_raw, dict) else None,
                        contract_ver,
                        skill_ver,
                        qp_ver,
                        target_metric,
                        exec_phase,
                        pause_reason,
                        session_seq,
                        lease_renew_count,
                        acc_quality,
                        acc_new_q,
                        acc_watch,
                        now_iso,
                    ),
                )
                saved += 1
            if saved:
                self._conn.commit()
        return saved

    @staticmethod
    def _attach_v33_fields(d: Dict[str, Any]) -> Dict[str, Any]:
        """Promote Strategy V3.3 / Contract V3.3 metadata from extra_meta and compute authoritative V3.3 quality flags."""
        em = d.get("extra_meta") if isinstance(d.get("extra_meta"), dict) else {}
        v33_obs = em.get("v33_observation") if isinstance(em.get("v33_observation"), dict) else None
        followers = em.get("author_followers")
        if followers is None:
            followers = em.get("followers")
        if followers is None and v33_obs is not None:
            followers = v33_obs.get("author_followers")
        ratio = em.get("views_to_followers_ratio")
        if ratio is None and v33_obs is not None and "views_to_followers_ratio" in v33_obs:
            ratio = v33_obs.get("views_to_followers_ratio")

        nat_viral = em.get("natural_viral") if "natural_viral" in em else None
        buyer_intent = em.get("buyer_intent") if "buyer_intent" in em else None
        if buyer_intent is None and d.get("purchase_intent") not in (None, "", "True", "False"):
            buyer_intent = d.get("purchase_intent")
        traffic_content = bool(em.get("traffic_content")) if em.get("traffic_content") is not None else False
        traffic_analysis = em.get("traffic_analysis")

        d["strategy_version"] = em.get("strategy_version")
        d["contract_version"] = em.get("contract_version")
        d["author_followers"] = followers
        d["followers_measured_at"] = em.get("followers_measured_at")
        d["views_to_followers_ratio"] = ratio
        d["natural_viral"] = nat_viral
        d["buyer_intent"] = buyer_intent
        d["traffic_content"] = traffic_content
        d["traffic_analysis"] = traffic_analysis
        d["v33_observation"] = v33_obs

        q_eval = assess_v33_quality(d)
        d["quality_qualified"] = bool(q_eval["quality_qualified"])
        d["quality_tier_basis"] = q_eval.get("quality_tier_basis")
        d["quality_exclusion_reason"] = q_eval.get("quality_exclusion_reason")
        d["is_watchlist"] = bool(q_eval.get("is_watchlist"))
        d["traffic_content"] = bool(q_eval.get("traffic_content"))
        d["quality_policy_version"] = q_eval.get("quality_policy_version") or em.get("quality_policy_version")
        d["skill_version"] = em.get("skill_version")
        return d

    @staticmethod
    def _clean_run_str(val: Any) -> Optional[str]:
        if val is None:
            return None
        s = str(val).strip()
        return s or None

    @staticmethod
    def _norm_source_file_name(val: Any) -> str:
        s = str(val or "").strip()
        if not s:
            return ""
        s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
        return os.path.basename(s).lower()

    @staticmethod
    def _build_drive_view_url(file_id: Optional[str]) -> Optional[str]:
        fid = str(file_id or "").strip()
        if not fid or fid.startswith("local_"):
            return None
        return f"https://drive.google.com/file/d/{fid}/view"

    def _insert_snapshot_row(
        self,
        *,
        snapshot_id: str,
        snapshot_source_key: str,
        item_db_id: str,
        dedup_key: str,
        canonical_url: Optional[str],
        platform: str,
        run_id: Optional[str],
        raw_file_id: Optional[str],
        snapshot_at: str,
        item: Dict[str, Any],
        views_delta: Optional[int] = None,
        likes_delta: Optional[int] = None,
        comments_delta: Optional[int] = None,
        shares_delta: Optional[int] = None,
        reposts_delta: Optional[int] = None,
        change_type: str = "new",
        created_at: str,
        source_priority: int = 1,
        is_corrected: bool = False,
    ):
        """Insert or enrich a point-in-time snapshot row without duplicating across re-ingests."""
        extra_meta_raw = item.get("extra_meta")
        extra_meta_json = json.dumps(extra_meta_raw, ensure_ascii=False) if isinstance(extra_meta_raw, dict) else "{}"
        self._conn.execute(
            """
            INSERT INTO market_item_snapshots (
                id, snapshot_source_key, item_id, dedup_key, canonical_url, platform,
                run_id, raw_file_id, snapshot_at,
                views_count, likes_count, comments_count, shares_count, reposts_count, quotes_count,
                price, sold_count,
                views_delta, likes_delta, comments_delta, shares_delta, reposts_delta,
                change_type, created_at, measured_at,
                views_display, views_precision, views_source, views_evidence_url, classification,
                trend_tier, trend_group, verification_status, data_quality, summary_vi, title,
                extra_meta_json, source_priority, is_corrected
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_source_key) DO UPDATE SET
                raw_file_id = CASE
                    WHEN excluded.source_priority >= COALESCE(market_item_snapshots.source_priority, 1)
                         AND (excluded.is_corrected >= COALESCE(market_item_snapshots.is_corrected, 0) OR excluded.source_priority > COALESCE(market_item_snapshots.source_priority, 1))
                    THEN COALESCE(excluded.raw_file_id, market_item_snapshots.raw_file_id)
                    ELSE market_item_snapshots.raw_file_id
                END,
                views_count = CASE
                    WHEN excluded.is_corrected > COALESCE(market_item_snapshots.is_corrected, 0) OR excluded.source_priority > COALESCE(market_item_snapshots.source_priority, 1)
                    THEN COALESCE(excluded.views_count, market_item_snapshots.views_count)
                    ELSE COALESCE(market_item_snapshots.views_count, excluded.views_count)
                END,
                likes_count = CASE
                    WHEN excluded.is_corrected > COALESCE(market_item_snapshots.is_corrected, 0) OR excluded.source_priority > COALESCE(market_item_snapshots.source_priority, 1)
                    THEN COALESCE(excluded.likes_count, market_item_snapshots.likes_count)
                    ELSE COALESCE(market_item_snapshots.likes_count, excluded.likes_count)
                END,
                comments_count = CASE
                    WHEN excluded.is_corrected > COALESCE(market_item_snapshots.is_corrected, 0) OR excluded.source_priority > COALESCE(market_item_snapshots.source_priority, 1)
                    THEN COALESCE(excluded.comments_count, market_item_snapshots.comments_count)
                    ELSE COALESCE(market_item_snapshots.comments_count, excluded.comments_count)
                END,
                shares_count = COALESCE(excluded.shares_count, market_item_snapshots.shares_count),
                reposts_count = COALESCE(excluded.reposts_count, market_item_snapshots.reposts_count),
                quotes_count = COALESCE(excluded.quotes_count, market_item_snapshots.quotes_count),
                trend_tier = COALESCE(excluded.trend_tier, market_item_snapshots.trend_tier),
                trend_group = COALESCE(excluded.trend_group, market_item_snapshots.trend_group),
                verification_status = COALESCE(excluded.verification_status, market_item_snapshots.verification_status),
                data_quality = COALESCE(excluded.data_quality, market_item_snapshots.data_quality),
                summary_vi = COALESCE(excluded.summary_vi, market_item_snapshots.summary_vi),
                title = COALESCE(excluded.title, market_item_snapshots.title),
                extra_meta_json = COALESCE(excluded.extra_meta_json, market_item_snapshots.extra_meta_json),
                source_priority = CASE
                    WHEN excluded.source_priority > COALESCE(market_item_snapshots.source_priority, 1) THEN excluded.source_priority
                    ELSE COALESCE(market_item_snapshots.source_priority, excluded.source_priority)
                END,
                is_corrected = CASE
                    WHEN excluded.is_corrected = 1 THEN 1
                    ELSE COALESCE(market_item_snapshots.is_corrected, 0)
                END,
                classification = COALESCE(excluded.classification, market_item_snapshots.classification),
                views_display = COALESCE(excluded.views_display, market_item_snapshots.views_display),
                views_precision = COALESCE(excluded.views_precision, market_item_snapshots.views_precision),
                views_source = COALESCE(excluded.views_source, market_item_snapshots.views_source),
                views_evidence_url = COALESCE(excluded.views_evidence_url, market_item_snapshots.views_evidence_url)
            """,
            (
                snapshot_id,
                snapshot_source_key,
                item_db_id,
                dedup_key,
                canonical_url,
                platform,
                run_id,
                raw_file_id,
                snapshot_at,
                item.get("views_count"),
                item.get("likes_count"),
                item.get("comments_count"),
                item.get("shares_count"),
                item.get("reposts_count"),
                item.get("quotes_count"),
                item.get("price"),
                item.get("sold_count"),
                views_delta,
                likes_delta,
                comments_delta,
                shares_delta,
                reposts_delta,
                change_type,
                created_at,
                item.get("measured_at") or item.get("views_measured_at") or item.get("crawled_at"),
                item.get("views_display"),
                item.get("views_precision"),
                item.get("views_source"),
                item.get("views_evidence_url"),
                item.get("classification"),
                item.get("trend_tier"),
                item.get("trend_group"),
                item.get("verification_status"),
                item.get("data_quality"),
                item.get("summary_vi"),
                item.get("title"),
                extra_meta_json,
                int(source_priority or 1),
                1 if is_corrected else 0,
            ),
        )

    @staticmethod
    def _is_snapshot_qualified(row: Dict[str, Any]) -> bool:
        """Qualified check on snapshot row (supports both V3.2 provenance and V1/V2 historical records)."""
        cls = str(row.get("classification") or "").upper()
        if cls in ("REJECTED", "UNVERIFIED", "DUPLICATE_HISTORY", "UPDATED_METRICS"):
            return False
        if str(row.get("verification_status") or "").lower() == "unverified":
            return False
        if str(row.get("data_quality") or "").lower() == "needs_verification":
            return False
        if str(row.get("trend_tier") or "").lower() == "unverified":
            return False
        if cls == "NEW":
            return is_valid_v32_views_provenance(row)
        return True

    def _snapshot_row_to_item_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert joined snapshot + market_items + raw_drive_files row into point-in-time item API dict."""
        d = dict(row)
        same_primary_file = bool(d.get("snap_raw_file_id") and d.get("item_raw_file_id") == d.get("snap_raw_file_id"))

        # Preserve point-in-time metrics from market_item_snapshots strictly
        views_val = d.get("snap_views_count")
        likes_val = d.get("snap_likes_count")
        comments_val = d.get("snap_comments_count")
        shares_val = d.get("snap_shares_count")
        reposts_val = d.get("snap_reposts_count")
        quotes_val = d.get("snap_quotes_count")
        price_val = d.get("snap_price")
        sold_val = d.get("snap_sold_count")

        trend_tier = d.get("snap_trend_tier") if d.get("snap_trend_tier") is not None else (d.get("trend_tier") if same_primary_file else None)
        trend_group = d.get("snap_trend_group") if d.get("snap_trend_group") is not None else (d.get("trend_group") if same_primary_file else None)
        verif_status = d.get("snap_verification_status") if d.get("snap_verification_status") is not None else (d.get("verification_status") if same_primary_file else None)
        data_quality = d.get("snap_data_quality") if d.get("snap_data_quality") is not None else (d.get("data_quality") if same_primary_file else None)
        classification = d.get("snap_classification") if d.get("snap_classification") is not None else (d.get("classification") if same_primary_file else None)
        summary_vi = d.get("snap_summary_vi") or d.get("summary_vi")
        title = d.get("snap_title") or d.get("title")
        measured_at = d.get("snap_measured_at") or d.get("views_measured_at") or d.get("snap_snapshot_at")

        raw_extra = d.get("snap_extra_meta_json") or d.get("extra_meta_json") or "{}"
        try:
            extra_meta = json.loads(raw_extra)
        except Exception:
            extra_meta = {}
        try:
            quality_issues = json.loads(d.get("quality_issues_json") or "[]")
        except Exception:
            quality_issues = []

        metrics = {
            k: v
            for k, v in {
                "likes": likes_val,
                "comments": comments_val,
                "reposts": reposts_val,
                "quotes": quotes_val,
                "shares": shares_val,
                "views": views_val,
                "price": price_val,
                "sold": sold_val,
                "rating": d.get("rating"),
            }.items()
            if v is not None
        }
        change_type = d.get("snap_change_type") or d.get("last_change_type") or "new"
        snap_views_display = d.get("snap_views_display") or (d.get("views_display") if same_primary_file else None)
        snap_views_precision = d.get("snap_views_precision") or (d.get("views_precision") if same_primary_file else None)
        snap_views_source = d.get("snap_views_source") or (d.get("views_source") if same_primary_file else None)
        snap_views_ev = d.get("snap_views_evidence_url") or (d.get("views_evidence_url") if same_primary_file else None)

        return self._attach_v33_fields({
            "id": d["id"],
            "dedup_key": d["dedup_key"],
            "platform": d["platform"],
            "external_id": d["external_id"],
            "post_id": d["post_id"],
            "item_id": d["item_id"],
            "shop_id": d["shop_id"],
            "author_handle": d["author_handle"],
            "author_name": d["author_name"],
            "title": title,
            "text_content": d["text_content"],
            "summary_vi": summary_vi,
            "url": d["url"],
            "canonical_url": d.get("canonical_url"),
            "evidence_url": d.get("evidence_url"),
            "content_group": d["content_group"],
            "purchase_intent": d["purchase_intent"],
            "language": d["language"],
            "published_at": d["published_at"],
            "crawled_at": d.get("snap_snapshot_at") or d.get("crawled_at"),
            "run_id": d.get("snap_run_id") or d.get("run_id"),
            "likes_count": likes_val,
            "comments_count": comments_val,
            "shares_count": shares_val,
            "reposts_count": reposts_val,
            "quotes_count": quotes_val,
            "views_count": views_val,
            "views_missing": 1 if views_val is None else 0,
            "views_display": snap_views_display,
            "views_precision": snap_views_precision,
            "views_source": snap_views_source,
            "views_measured_at": measured_at,
            "views_evidence_url": snap_views_ev,
            "classification": classification,
            "content_hash": d.get("content_hash"),
            "similar_content": bool(d.get("similar_content")),
            "similar_to_post_id": d.get("similar_to_post_id"),
            "engagement_rate_on_views": d.get("engagement_rate_on_views"),
            "engagement_rate_approx": bool(d.get("engagement_rate_approx")) if d.get("engagement_rate_approx") is not None else None,
            "price": price_val,
            "sold_count": sold_val,
            "rating": d["rating"],
            "trend_tier": trend_tier,
            "trend_group": trend_group,
            "verification_status": verif_status,
            "metrics_source": d.get("metrics_source"),
            "post_pk": d.get("post_pk"),
            "data_quality": data_quality,
            "quality_issues": quality_issues,
            "is_corrected": bool(d.get("snap_is_corrected")),
            "source_priority": int(d.get("snap_source_priority") or 1),
            "last_change_type": change_type,
            "snapshot_count": int(d.get("snapshot_count") or 1),
            "last_views_delta": d.get("snap_views_delta"),
            "last_likes_delta": d.get("snap_likes_delta"),
            "last_comments_delta": d.get("snap_comments_delta"),
            "last_shares_delta": d.get("snap_shares_delta"),
            "last_reposts_delta": d.get("snap_reposts_delta"),
            "first_seen_at": d.get("first_seen_at"),
            "last_seen_at": d.get("last_seen_at"),
            "metrics": metrics,
            "extra_meta": extra_meta,
            "schema_version": d["schema_version"],
            "raw_file_id": d.get("snap_raw_file_id"),
            "raw_file_name": d.get("raw_file_name"),
            "drive_url": self._build_drive_view_url(d.get("snap_raw_file_id")),
            "updated_at": d.get("snap_snapshot_at") or d["updated_at"],
        })

    def _query_effective_run_snapshots(
        self,
        *,
        run_id: Optional[str] = None,
        run_ids: Optional[List[str]] = None,
        raw_file_ids: Optional[List[str]] = None,
        platform: str = "all",
        prefer_qualified: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Return deduplicated point-in-time snapshots belonging to one crawl run or source-file group,
        choosing higher source_priority / corrected revisions over superseded base revisions.
        """
        plat = (platform or "all").strip().lower()
        where_parts: List[str] = []
        params: List[Any] = []
        rid_list = list(
            dict.fromkeys(
                ([str(run_id).strip()] if run_id and str(run_id).strip() else [])
                + [str(x).strip() for x in (run_ids or []) if str(x or "").strip()]
            )
        )

        if raw_file_ids:
            placeholders = ",".join("?" for _ in raw_file_ids)
            if rid_list:
                r_placeholders = ",".join("?" for _ in rid_list)
                where_parts.append(f"(s.raw_file_id IN ({placeholders}) OR s.run_id IN ({r_placeholders}))")
                params.extend(raw_file_ids)
                params.extend(rid_list)
            else:
                where_parts.append(f"s.raw_file_id IN ({placeholders})")
                params.extend(raw_file_ids)
        elif rid_list:
            r_placeholders = ",".join("?" for _ in rid_list)
            where_parts.append(f"s.run_id IN ({r_placeholders})")
            params.extend(rid_list)
        else:
            return []

        where_parts.append("(s.raw_file_id IS NULL OR rf.file_id IS NULL OR COALESCE(rf.is_archived, 0) = 0)")
        if plat and plat != "all":
            where_parts.append("LOWER(s.platform) = ?")
            params.append(plat)

        prefer_cls_sql = (
            "(CASE WHEN COALESCE(s.classification, '') = 'NEW' THEN 2 WHEN COALESCE(s.classification, '') = 'UPDATED_METRICS' THEN 1 ELSE 0 END) DESC,"
            if prefer_qualified
            else ""
        )

        sql = f"""
            SELECT
                m.id, m.dedup_key, m.platform, m.external_id, m.post_id, m.item_id, m.shop_id,
                m.author_handle, m.author_name, m.text_content, m.summary_vi, m.title, m.url,
                m.canonical_url, m.evidence_url, m.content_group, m.purchase_intent, m.language,
                m.published_at, m.crawled_at, m.run_id, m.rating,
                m.trend_tier, m.trend_group, m.verification_status, m.metrics_source, m.post_pk,
                m.data_quality, m.quality_issues_json, m.extra_meta_json, m.classification,
                m.content_hash, m.similar_content, m.similar_to_post_id,
                m.engagement_rate_on_views, m.engagement_rate_approx,
                m.views_display, m.views_precision, m.views_source, m.views_measured_at, m.views_evidence_url,
                m.raw_file_id AS item_raw_file_id, m.first_seen_at, m.last_seen_at, m.last_change_type,
                m.snapshot_count, m.schema_version, m.updated_at,
                s.id AS snap_row_id, s.run_id AS snap_run_id, s.raw_file_id AS snap_raw_file_id,
                s.snapshot_at AS snap_snapshot_at, s.measured_at AS snap_measured_at,
                s.views_count AS snap_views_count, s.likes_count AS snap_likes_count,
                s.comments_count AS snap_comments_count, s.shares_count AS snap_shares_count,
                s.reposts_count AS snap_reposts_count, s.quotes_count AS snap_quotes_count,
                s.price AS snap_price, s.sold_count AS snap_sold_count,
                s.views_delta AS snap_views_delta, s.likes_delta AS snap_likes_delta,
                s.comments_delta AS snap_comments_delta, s.shares_delta AS snap_shares_delta,
                s.reposts_delta AS snap_reposts_delta, s.change_type AS snap_change_type,
                s.views_display AS snap_views_display, s.views_precision AS snap_views_precision,
                s.views_source AS snap_views_source, s.views_evidence_url AS snap_views_evidence_url,
                s.classification AS snap_classification,
                s.trend_tier AS snap_trend_tier, s.trend_group AS snap_trend_group,
                s.verification_status AS snap_verification_status, s.data_quality AS snap_data_quality,
                s.summary_vi AS snap_summary_vi, s.title AS snap_title,
                s.extra_meta_json AS snap_extra_meta_json,
                COALESCE(s.source_priority, m.source_priority, 1) AS snap_source_priority,
                COALESCE(s.is_corrected, rf.is_corrected, 0) AS snap_is_corrected,
                rf.file_name AS raw_file_name
            FROM market_item_snapshots s
            JOIN market_items m ON m.id = s.item_id
            LEFT JOIN raw_drive_files rf ON s.raw_file_id = rf.file_id
            WHERE {' AND '.join(where_parts)}
            ORDER BY
                COALESCE(s.is_corrected, rf.is_corrected, 0) DESC,
                COALESCE(s.source_priority, m.source_priority, 1) DESC,
                {prefer_cls_sql}
                s.snapshot_at DESC,
                s.id DESC
        """
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()

        deduped: List[Dict[str, Any]] = []
        seen_keys: set = set()
        for r in rows:
            item_dict = self._snapshot_row_to_item_dict(r)
            ukey = item_dict.get("dedup_key") or item_dict.get("id")
            if ukey in seen_keys:
                continue
            seen_keys.add(ukey)
            deduped.append(item_dict)
        return deduped

    def _build_run_groups_index(
        self,
        *,
        include_archived: bool = False,
        crawl_requests: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build canonical crawl run groups pairing Data + Report Drive files by run_id,
        request_id, corrects_file/corrects_report, and source_files metadata without inventing missing run_id.
        """
        archive_filter = "" if include_archived else f"WHERE {_NOT_ARCHIVE_RAW_SQL}"
        with self._lock:
            raw_rows = [
                dict(r)
                for r in self._conn.execute(
                    f"SELECT * FROM raw_drive_files r {archive_filter} ORDER BY COALESCE(r.modified_time, r.synced_at) ASC"
                ).fetchall()
            ]
            rep_rows = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT * FROM market_reports ORDER BY COALESCE(is_corrected, 0) DESC, COALESCE(crawl_time, generated_at, updated_at) DESC"
                ).fetchall()
            ]
            snap_counts = {
                r["raw_file_id"]: int(r["c"] or 0)
                for r in self._conn.execute(
                    "SELECT raw_file_id, COUNT(*) AS c FROM market_item_snapshots WHERE raw_file_id IS NOT NULL GROUP BY raw_file_id"
                ).fetchall()
            }
            snap_plats_rows = self._conn.execute(
                "SELECT raw_file_id, LOWER(platform) AS plat FROM market_item_snapshots WHERE raw_file_id IS NOT NULL GROUP BY raw_file_id, LOWER(platform)"
            ).fetchall()
            cached_req_rows = [
                dict(r)
                for r in self._conn.execute("SELECT * FROM crawl_request_metadata_cache").fetchall()
            ]

        snap_plats_by_file: Dict[str, set] = {}
        for sr in snap_plats_rows:
            fid = sr["raw_file_id"]
            if sr["plat"]:
                snap_plats_by_file.setdefault(fid, set()).add(sr["plat"])

        reports_by_raw_fid: Dict[str, Dict[str, Any]] = {}
        for rep in rep_rows:
            rfid = rep.get("raw_file_id")
            if rfid:
                reports_by_raw_fid[str(rfid)] = rep

        req_lookup: Dict[str, Dict[str, Any]] = {}
        for cr in cached_req_rows:
            rid = str(cr.get("request_id") or "").strip()
            if not rid:
                continue
            item_cache = dict(cr)
            item_cache["id"] = rid
            if cr.get("topics_json"):
                try:
                    item_cache["topics"] = json.loads(cr["topics_json"])
                except Exception:
                    pass
            if cr.get("topic_labels_json"):
                try:
                    item_cache["topic_labels"] = json.loads(cr["topic_labels_json"])
                except Exception:
                    pass
            req_lookup[rid] = item_cache
        for req in crawl_requests or []:
            if isinstance(req, dict) and req.get("id"):
                rid = str(req["id"]).strip()
                merged_req = dict(req_lookup.get(rid) or {})
                merged_req.update(req)
                req_lookup[rid] = merged_req

        # Filter to actual crawl data / report artifacts
        relevant_files: List[Dict[str, Any]] = []
        for rf in raw_rows:
            role = str(rf.get("file_role") or "crawl_data").lower()
            if role not in ("crawl_data", "market_report"):
                continue
            try:
                meta = json.loads(rf.get("run_meta_json") or "{}")
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            fid = str(rf["file_id"])
            rep = reports_by_raw_fid.get(fid)
            run_id = self._clean_run_str(rf.get("run_id") or meta.get("run_id") or (rep or {}).get("run_id"))
            request_id = self._clean_run_str(rf.get("request_id") or meta.get("request_id") or (rep or {}).get("request_id"))
            if (
                role == "crawl_data"
                and not run_id
                and not request_id
                and int(rf.get("record_count") or 0) == 0
                and snap_counts.get(fid, 0) == 0
                and rep is None
            ):
                continue
            rf["_meta"] = meta
            rf["_rep"] = rep
            rf["_run_id"] = run_id
            rf["_request_id"] = request_id
            relevant_files.append(rf)

        groups: Dict[str, Dict[str, Any]] = {}
        file_to_group: Dict[str, str] = {}
        fname_to_files: Dict[str, List[Dict[str, Any]]] = {}
        req_to_group: Dict[str, str] = {}

        def _ensure_group(gkey: str, *, run_id: Optional[str], grouped_by: str) -> Dict[str, Any]:
            if gkey not in groups:
                groups[gkey] = {
                    "run_key": gkey,
                    "run_id": run_id,
                    "run_ids": set(),
                    "grouped_by": grouped_by,
                    "has_run_id": bool(run_id),
                    "request_ids": set(),
                    "data_files": [],
                    "report_files": [],
                    "reports": [],
                    "platforms": set(),
                    "source_files_refs": [],
                }
            if run_id:
                groups[gkey]["run_ids"].add(run_id)
            return groups[gkey]

        def _attach_file(gkey: str, rf: Dict[str, Any]):
            grp = groups[gkey]
            fid = str(rf["file_id"])
            file_to_group[fid] = gkey
            if rf.get("_run_id"):
                grp["run_ids"].add(rf["_run_id"])
            fname_low = str(rf.get("file_name") or "").strip().lower()
            if fname_low:
                fname_to_files.setdefault(fname_low, []).append(rf)
            req_id = rf.get("_request_id")
            if req_id:
                grp["request_ids"].add(req_id)
                req_to_group.setdefault(req_id, gkey)
            role = str(rf.get("file_role") or "crawl_data").lower()
            if role == "market_report":
                grp["report_files"].append(rf)
                if rf.get("_rep"):
                    grp["reports"].append(rf["_rep"])
            else:
                grp["data_files"].append(rf)

            plat = self._clean_run_str(rf.get("platform") or rf["_meta"].get("platform") or (rf.get("_rep") or {}).get("platform"))
            if plat and plat.lower() not in ("all", "unknown"):
                grp["platforms"].add(plat.lower())
            for sp in snap_plats_by_file.get(fid, set()):
                if sp not in ("all", "unknown"):
                    grp["platforms"].add(sp)
            # Infer platform from filename when historical file metadata lacked explicit platform
            if not grp["platforms"] and role == "crawl_data":
                low = fname_low
                for cand in ("threads", "tiktok", "shopee"):
                    if cand in low:
                        grp["platforms"].add(cand)
                        break

        # Pass 1: Explicit run_id files
        unmatched_pass1: List[Dict[str, Any]] = []
        for rf in relevant_files:
            run_id = rf.get("_run_id")
            if run_id:
                _ensure_group(run_id, run_id=run_id, grouped_by="run_id")
                _attach_file(run_id, rf)
            else:
                unmatched_pass1.append(rf)

        # Pass 2: Match by request_id or corrects_file/corrects_report
        unmatched_pass2: List[Dict[str, Any]] = []
        for rf in unmatched_pass1:
            req_id = rf.get("_request_id")
            corr_target = self._norm_source_file_name(rf.get("corrects_file") or rf.get("corrects_report") or rf["_meta"].get("corrects_file"))
            target_gkey = req_to_group.get(req_id) if req_id else None
            if not target_gkey and corr_target and corr_target in fname_to_files:
                for candidate_f in fname_to_files[corr_target]:
                    if str(candidate_f["file_id"]) in file_to_group:
                        target_gkey = file_to_group[str(candidate_f["file_id"])]
                        break
            if target_gkey:
                _attach_file(target_gkey, rf)
            else:
                unmatched_pass2.append(rf)

        # Pass 3: Remaining crawl_data files lacking run_id group strictly by request_id or source file BEFORE report pairing
        unmatched_reports: List[Dict[str, Any]] = []
        for rf in unmatched_pass2:
            if str(rf.get("file_role") or "").lower() == "market_report":
                unmatched_reports.append(rf)
                continue
            req_id = rf.get("_request_id")
            if req_id:
                gkey = req_to_group.get(req_id) or f"req:{req_id}"
                _ensure_group(gkey, run_id=None, grouped_by="request_id")
                _attach_file(gkey, rf)
            else:
                gkey = f"file:{rf['file_id']}"
                _ensure_group(gkey, run_id=None, grouped_by="source_file")
                _attach_file(gkey, rf)

        # Pass 4: Pair reports without run_id using request_id, corrects_report, or metadata source_files
        for rf in unmatched_reports:
            req_id = rf.get("_request_id")
            target_gk = req_to_group.get(req_id) if req_id else None
            if not target_gk:
                src_refs = rf["_meta"].get("source_files") or []
                if not src_refs and rf.get("_rep"):
                    try:
                        rj = json.loads(rf["_rep"].get("report_json") or "{}")
                        src_refs = rj.get("source_files") or []
                    except Exception:
                        src_refs = []
                matched_group_keys: List[str] = []
                for sref in src_refs if isinstance(src_refs, list) else []:
                    norm_name = self._norm_source_file_name(sref)
                    for cand_f in fname_to_files.get(norm_name, []):
                        gk = file_to_group.get(str(cand_f["file_id"]))
                        if gk and gk not in matched_group_keys:
                            matched_group_keys.append(gk)
                if matched_group_keys:
                    without_rep = [gk for gk in matched_group_keys if not groups[gk]["report_files"]]
                    target_gk = without_rep[-1] if without_rep else matched_group_keys[-1]
                    groups[target_gk]["source_files_refs"] = [str(s) for s in src_refs]
            if target_gk:
                _attach_file(target_gk, rf)
            else:
                gkey = f"req:{req_id}" if req_id else f"file:{rf['file_id']}"
                _ensure_group(gkey, run_id=None, grouped_by="request_id" if req_id else "source_file")
                _attach_file(gkey, rf)

        # Pass 5: Standalone DB reports without raw_file_id (e.g., local synthetic tests)
        for rep in rep_rows:
            if rep.get("raw_file_id"):
                continue
            r_run_id = self._clean_run_str(rep.get("run_id"))
            if r_run_id and r_run_id in groups:
                groups[r_run_id]["reports"].append(rep)

        result_list: List[Dict[str, Any]] = []
        for gkey, grp in groups.items():
            # Prefer real crawl timestamps from data files first so re-ingesting legacy reports never pushes old runs to top
            data_ts_candidates: List[Tuple[float, str]] = []
            for f in grp["data_files"]:
                meta = f["_meta"]
                for raw_ts in (
                    f.get("crawl_time"),
                    meta.get("crawl_time"),
                    meta.get("ended_at"),
                    meta.get("crawled_at"),
                    meta.get("started_at"),
                    f.get("modified_time"),
                    f.get("synced_at"),
                ):
                    pt = self._parse_iso_ts(raw_ts)
                    if pt > 0:
                        data_ts_candidates.append((pt, str(raw_ts)))
                        break

            rep_ts_candidates: List[Tuple[float, str]] = []
            if not data_ts_candidates:
                for f in grp["report_files"]:
                    meta = f["_meta"]
                    for raw_ts in (
                        f.get("crawl_time"),
                        meta.get("crawl_time"),
                        meta.get("generated_at"),
                        f.get("modified_time"),
                        f.get("synced_at"),
                    ):
                        pt = self._parse_iso_ts(raw_ts)
                        if pt > 0:
                            rep_ts_candidates.append((pt, str(raw_ts)))
                            break
                if not rep_ts_candidates:
                    for rep in grp["reports"]:
                        for raw_ts in (rep.get("crawl_time"), rep.get("generated_at"), rep.get("updated_at")):
                            pt = self._parse_iso_ts(raw_ts)
                            if pt > 0:
                                rep_ts_candidates.append((pt, str(raw_ts)))
                                break

            ts_candidates = data_ts_candidates or rep_ts_candidates
            ts_candidates.sort(key=lambda x: x[0], reverse=True)
            sort_ts = ts_candidates[0][0] if ts_candidates else 0.0
            crawl_time_iso = ts_candidates[0][1] if ts_candidates else ""

            req_id = next(iter(sorted(grp["request_ids"])), None)
            linked_req = req_lookup.get(req_id) if req_id else None
            grp["sort_ts"] = sort_ts
            grp["crawl_time"] = crawl_time_iso
            grp["request_id"] = req_id
            grp["linked_request"] = linked_req
            result_list.append(grp)

        result_list.sort(key=lambda g: (g["sort_ts"], str(g["run_key"])), reverse=True)
        return result_list

    def _summarize_run_group(self, grp: Dict[str, Any], *, platform: str = "all") -> Dict[str, Any]:
        """Compute clean per-run summary counters, paired Drive links, and status for UI/API."""
        plat = (platform or "all").strip().lower()
        data_files = grp["data_files"]
        report_files = grp["report_files"]
        reports = grp["reports"]
        raw_fids = [str(f["file_id"]) for f in data_files if f.get("file_id")]

        effective_snaps = self._query_effective_run_snapshots(
            run_id=grp["run_id"],
            run_ids=grp.get("run_ids"),
            raw_file_ids=raw_fids,
            platform=plat,
            prefer_qualified=bool(grp.get("is_request_bundle")),
        )
        all_platform_snaps = effective_snaps if plat == "all" else self._query_effective_run_snapshots(
            run_id=grp["run_id"],
            run_ids=grp.get("run_ids"),
            raw_file_ids=raw_fids,
            platform="all",
            prefer_qualified=bool(grp.get("is_request_bundle")),
        )

        # Tier & classification counts directly from point-in-time effective snapshots
        tier_counts: Dict[str, int] = {"very_hot": 0, "hot": 0, "emerging": 0, "watchlist": 0, "unverified": 0}
        new_qualified_snap = 0
        quality_qualified_snap = 0
        watchlist_snap = 0
        traffic_content_snap = 0
        updated_snap = 0
        dup_history_snap = 0
        unverified_snap = 0
        rejected_snap = 0
        missing_views_non_rejected_snap = 0
        missing_views_all_snap = 0

        for item in effective_snaps:
            cls = str(item.get("classification") or "").upper()
            ctype = str(item.get("last_change_type") or "new").lower()
            tier = str(item.get("trend_tier") or "").lower()
            verif = str(item.get("verification_status") or "").lower()
            dq = str(item.get("data_quality") or "").lower()
            is_missing_v = item.get("views_count") is None

            if is_missing_v:
                missing_views_all_snap += 1
                if not cls or cls in ("NEW", "UNVERIFIED"):
                    missing_views_non_rejected_snap += 1

            if cls == "REJECTED":
                rejected_snap += 1
            elif cls == "DUPLICATE_HISTORY" or ctype == "duplicate_history":
                dup_history_snap += 1
            elif cls == "UPDATED_METRICS" or ctype == "metrics_updated":
                updated_snap += 1
            elif cls == "UNVERIFIED" or verif == "unverified" or dq == "needs_verification" or tier == "unverified":
                unverified_snap += 1
                tier_counts["unverified"] += 1
            else:
                if self._is_snapshot_qualified(item):
                    new_qualified_snap += 1
                if item.get("quality_qualified"):
                    quality_qualified_snap += 1
                if item.get("is_watchlist"):
                    watchlist_snap += 1
                if item.get("traffic_content"):
                    traffic_content_snap += 1
                if tier in ("very_hot", "hot", "emerging", "watchlist"):
                    tier_counts[tier] += 1

        # Extract authoritative envelope/report counters when scope aligns
        grp_plats = sorted(grp["platforms"])
        is_single_plat_run = len(grp_plats) <= 1
        matching_data_files = [
            f for f in data_files
            if plat == "all" or str(f.get("platform") or f["_meta"].get("platform") or "").lower() == plat or is_single_plat_run
        ]
        # Prefer corrected > highest modified_time data file for metadata counters
        matching_data_files.sort(
            key=lambda f: (1 if f.get("is_corrected") else 0, str(f.get("modified_time") or f.get("synced_at") or "")),
            reverse=True,
        )
        rep_parsed: Dict[str, Any] = {}
        if reports:
            rep_sorted = sorted(
                reports,
                key=lambda r: (1 if r.get("is_corrected") else 0, str(r.get("crawl_time") or r.get("generated_at") or "")),
                reverse=True,
            )
            try:
                rep_parsed = json.loads(rep_sorted[0].get("report_json") or "{}")
            except Exception:
                rep_parsed = {}

        primary_df = matching_data_files[0] if matching_data_files else (data_files[0] if data_files else None)
        df_counters: Dict[str, Any] = {}
        for df in matching_data_files:
            c = df["_meta"].get("counters")
            if isinstance(c, dict) and c:
                df_counters = c
                break
        rep_counters = rep_parsed.get("counters") if isinstance(rep_parsed.get("counters"), dict) else {}
        if not df_counters and rep_counters:
            df_counters = rep_counters

        # When multi-platform run is filtered to a single sub-platform, prefer snapshot-derived counts for that platform
        use_envelope_counters = bool(df_counters) and (plat == "all" or is_single_plat_run)
        linked_req = grp.get("linked_request") or {}

        requested_count = (
            (primary_df.get("requested_count") if primary_df else None)
            or df_counters.get("requested_count")
            or rep_counters.get("requested_count")
            or linked_req.get("requested_count")
        )
        dup_current_run = int(
            (df_counters.get("duplicate_current_run_count") if use_envelope_counters else None)
            or (primary_df.get("duplicate_current_run_count") if use_envelope_counters and primary_df else None)
            or 0
        )
        unique_candidates = len(effective_snaps)
        scanned_count = (
            int(df_counters["scanned_count"])
            if use_envelope_counters and df_counters.get("scanned_count") is not None
            else (unique_candidates + dup_current_run)
        )
        missing_views_count = (
            int(df_counters["missing_views_count"])
            if use_envelope_counters and df_counters.get("missing_views_count") is not None
            else missing_views_non_rejected_snap
        )

        # If snapshots weren't in sync with raw counters (e.g., empty partial upload), fall back to counters safely
        is_bundle = bool(grp.get("is_request_bundle"))
        audit_diag_grp = self.get_run_audit_diagnostics(
            raw_file_ids=raw_fids,
            run_id=grp["run_id"],
            run_ids=grp.get("run_ids"),
            limit=50,
        )
        below_hot_sum_files = sum(int(f.get("below_hot_count") or 0) for f in matching_data_files)
        unverified_sum_files = sum(int(f.get("unverified_count") or 0) for f in matching_data_files)
        rejected_sum_files = sum(int(f.get("rejected_count") or 0) for f in matching_data_files)
        below_hot_count = max(int(audit_diag_grp.get("below_hot_count") or 0), below_hot_sum_files)
        new_qualified_count = (
            int(df_counters["new_qualified_count"])
            if not is_bundle and use_envelope_counters and df_counters.get("new_qualified_count") is not None and len(effective_snaps) == 0
            else new_qualified_snap
        )
        quality_qualified_count = (
            int(df_counters["quality_qualified_count"])
            if not is_bundle and use_envelope_counters and df_counters.get("quality_qualified_count") is not None and len(effective_snaps) == 0
            else quality_qualified_snap
        )
        watchlist_count = (
            int(df_counters["watchlist_count"])
            if not is_bundle and use_envelope_counters and df_counters.get("watchlist_count") is not None and len(effective_snaps) == 0
            else watchlist_snap
        )
        traffic_content_count = (
            int(df_counters["traffic_content_count"])
            if not is_bundle and use_envelope_counters and df_counters.get("traffic_content_count") is not None and len(effective_snaps) == 0
            else traffic_content_snap
        )
        updated_metrics_count = (
            int(df_counters["updated_metrics_count"])
            if not is_bundle and use_envelope_counters and df_counters.get("updated_metrics_count") is not None and len(effective_snaps) == 0
            else updated_snap
        )
        duplicate_history_count = (
            int(df_counters["duplicate_history_count"])
            if not is_bundle and use_envelope_counters and df_counters.get("duplicate_history_count") is not None and len(effective_snaps) == 0
            else dup_history_snap
        )
        unverified_count = (
            int(df_counters["unverified_count"])
            if not is_bundle and use_envelope_counters and df_counters.get("unverified_count") is not None and len(effective_snaps) == 0
            else max(unverified_snap, int(audit_diag_grp.get("unverified_count") or 0), unverified_sum_files if is_bundle or int(audit_diag_grp.get("total") or 0) > 0 else 0)
        )
        rejected_count = (
            int(df_counters["rejected_count"])
            if not is_bundle and use_envelope_counters and df_counters.get("rejected_count") is not None and len(effective_snaps) == 0
            else max(rejected_snap, int(audit_diag_grp.get("rejected_count") or 0), rejected_sum_files if is_bundle or int(audit_diag_grp.get("total") or 0) > 0 else 0)
        )

        # Effective counts match snapshots on all items cleanly
        qualified_total = new_qualified_count
        stop_reason = (
            df_counters.get("stop_reason")
            or (primary_df.get("stop_reason") if primary_df else None)
            or (rep_parsed.get("screening") or {}).get("stop_reason")
            or linked_req.get("pause_reason")
        )
        target_achieved_raw = (
            df_counters.get("target_achieved")
            if df_counters.get("target_achieved") is not None and not is_bundle
            else (primary_df.get("target_achieved") if primary_df and not is_bundle else None)
        )
        target_achieved = bool(target_achieved_raw) if target_achieved_raw is not None else None
        if is_bundle and requested_count is not None:
            is_v33_bundle = str(linked_req.get("contract_version") or (primary_df.get("contract_version") if primary_df else "") or "").startswith("3.3")
            target_achieved = (quality_qualified_count >= int(requested_count)) if is_v33_bundle else (new_qualified_count >= int(requested_count))

        run_status = (
            (linked_req.get("display_status") if is_bundle else None)
            or (primary_df.get("run_status") if primary_df else None)
            or (rep_parsed.get("run_status") if rep_parsed else None)
            or linked_req.get("display_status")
            or linked_req.get("status")
            or "completed"
        )

        def _fmt_file(f: Dict[str, Any]) -> Dict[str, Any]:
            fid = str(f["file_id"])
            return {
                "file_id": fid,
                "file_name": f["file_name"],
                "file_role": f.get("file_role"),
                "schema_version": f.get("schema_version"),
                "platform": f.get("platform") or f["_meta"].get("platform"),
                "record_count": int(f.get("record_count") or 0),
                "is_corrected": bool(f.get("is_corrected")),
                "corrects_file": f.get("corrects_file"),
                "modified_time": f.get("modified_time") or f.get("synced_at"),
                "drive_url": self._build_drive_view_url(fid),
            }

        formatted_data_files = [_fmt_file(f) for f in sorted(data_files, key=lambda x: (1 if x.get("is_corrected") else 0, str(x.get("file_name") or "")), reverse=True)]
        formatted_report_files = [_fmt_file(f) for f in sorted(report_files, key=lambda x: (1 if x.get("is_corrected") else 0, str(x.get("file_name") or "")), reverse=True)]

        primary_data_file = formatted_data_files[0] if formatted_data_files else None
        primary_report_file = formatted_report_files[0] if formatted_report_files else None

        schema_versions = sorted({str(f.get("schema_version") or "") for f in data_files + report_files if f.get("schema_version")})
        display_platform = "all" if len(grp_plats) > 1 else (grp_plats[0] if grp_plats else "unknown")

        strategy_version = None
        contract_version = None
        crawl_mode = None
        topics_raw = None
        keywords_meta = None
        lookback_hours = None
        special_counts: Dict[str, Any] = {}
        for f in sorted(data_files + report_files, key=lambda x: (1 if x.get("is_corrected") else 0, str(x.get("modified_time") or "")), reverse=True):
            f_meta = f.get("_meta") or {}
            if not strategy_version:
                strategy_version = self._clean_run_str(f.get("strategy_version") or f_meta.get("strategy_version"))
            if not contract_version:
                contract_version = self._clean_run_str(f.get("contract_version") or f_meta.get("contract_version"))
            if not crawl_mode:
                crawl_mode = self._clean_run_str(f.get("crawl_mode") or f_meta.get("crawl_mode"))
            if topics_raw is None:
                if f.get("topics_json") is not None:
                    topics_raw = f.get("topics_json")
                elif f_meta.get("topics") is not None:
                    topics_raw = f_meta.get("topics")
            if not keywords_meta:
                keywords_meta = self._clean_run_str(f.get("keywords") or f_meta.get("keywords"))
            if lookback_hours is None:
                lb_cand = f.get("lookback_hours") if f.get("lookback_hours") is not None else f_meta.get("lookback_hours")
                if lb_cand is not None and not isinstance(lb_cand, bool):
                    try:
                        lookback_hours = int(lb_cand)
                    except (TypeError, ValueError):
                        pass
            if not special_counts:
                sc_raw = f_meta.get("special_counts")
                if not isinstance(sc_raw, dict) and f.get("special_counts_json"):
                    try:
                        sc_raw = json.loads(f["special_counts_json"])
                    except Exception:
                        sc_raw = None
                if isinstance(sc_raw, dict) and sc_raw:
                    special_counts = dict(sc_raw)
        if not crawl_mode and rep_parsed:
            crawl_mode = self._clean_run_str(rep_parsed.get("crawl_mode"))
        if topics_raw is None and rep_parsed and rep_parsed.get("topics") is not None:
            topics_raw = rep_parsed.get("topics")
        if not keywords_meta and rep_parsed:
            keywords_meta = self._clean_run_str(rep_parsed.get("keywords"))
        if lookback_hours is None and rep_parsed and rep_parsed.get("lookback_hours") is not None:
            try:
                lookback_hours = int(rep_parsed.get("lookback_hours"))
            except (TypeError, ValueError):
                pass

        linked_opts: Dict[str, Any] = {}
        if isinstance(linked_req.get("options"), dict):
            linked_opts = linked_req["options"]
        elif isinstance(linked_req.get("options_json"), str) and linked_req["options_json"].strip():
            try:
                parsed_lo = json.loads(linked_req["options_json"])
                if isinstance(parsed_lo, dict):
                    linked_opts = parsed_lo
            except Exception:
                linked_opts = {}

        if not crawl_mode:
            crawl_mode = self._clean_run_str(linked_req.get("crawl_mode") or linked_opts.get("crawl_mode"))
        if topics_raw is None:
            if linked_req.get("topics") is not None:
                topics_raw = linked_req.get("topics")
            elif linked_opts.get("topics") is not None:
                topics_raw = linked_opts.get("topics")
        if not keywords_meta:
            keywords_meta = self._clean_run_str(linked_req.get("keywords"))
        if lookback_hours is None:
            lb_linked = linked_req.get("lookback_hours") if linked_req.get("lookback_hours") is not None else linked_opts.get("lookback_hours")
            if lb_linked is not None and not isinstance(lb_linked, bool):
                try:
                    lookback_hours = int(lb_linked)
                except (TypeError, ValueError):
                    pass

        topic_ids, topic_labels = _normalize_crawl_topics(topics_raw, strict=False) if topics_raw is not None else ([], [])
        crawl_mode_label = (
            self._clean_run_str(linked_req.get("crawl_mode_label")) or _get_crawl_mode_label(crawl_mode)
            if crawl_mode
            else None
        )
        has_strategy_metadata = bool(crawl_mode or topic_ids or keywords_meta or lookback_hours is not None)

        if not special_counts:
            nv_snap = sum(1 for it in effective_snaps if it.get("natural_viral") is True)
            bi_snap = sum(1 for it in effective_snaps if it.get("buyer_intent") is True)
            if nv_snap or bi_snap or traffic_content_snap:
                special_counts = {"natural_viral": nv_snap, "buyer_intent": bi_snap, "traffic_content": traffic_content_snap}

        if not contract_version:
            contract_version = self._clean_run_str(linked_req.get("contract_version") or linked_opts.get("contract_version"))
        skill_version = self._clean_run_str(
            (primary_df.get("_meta") or {}).get("skill_version") if primary_df else None
        ) or self._clean_run_str(rep_parsed.get("skill_version") or linked_req.get("skill_version") or linked_opts.get("skill_version"))
        quality_policy_version = self._clean_run_str(
            (primary_df.get("quality_policy_version") if primary_df else None)
            or ((primary_df.get("_meta") or {}).get("quality_policy_version") if primary_df else None)
            or rep_parsed.get("quality_policy_version")
            or linked_req.get("quality_policy_version")
        )
        session_seq_val = (
            (primary_df.get("session_seq") if primary_df else None)
            or ((primary_df.get("_meta") or {}).get("session_seq") if primary_df else None)
            or rep_parsed.get("session_seq")
            or linked_req.get("session_seq")
        )
        is_checkpoint_val = bool(
            (primary_df.get("is_checkpoint") if primary_df else False)
            or ((primary_df.get("_meta") or {}).get("is_checkpoint") if primary_df else False)
            or rep_parsed.get("is_checkpoint")
        )
        target_metric_val = self._clean_run_str(
            df_counters.get("target_metric")
            or ((primary_df.get("_meta") or {}).get("target_metric") if primary_df else None)
            or linked_req.get("target_metric")
            or ("quality_qualified_count" if str(contract_version or "").startswith("3.3") else "new_qualified_count")
        )
        session_run_ids = sorted(str(x) for x in (grp.get("run_ids") or []) if x)

        return {
            "run_key": grp["run_key"],
            "run_id": grp["run_id"],
            "grouped_by": grp["grouped_by"],
            "has_run_id": grp["has_run_id"],
            "is_request_bundle": is_bundle,
            "session_count": len(grp.get("session_run_keys") or session_run_ids) or 1,
            "session_run_ids": session_run_ids,
            "session_seq": int(session_seq_val) if session_seq_val is not None else None,
            "is_checkpoint": is_checkpoint_val,
            "request_id": grp["request_id"],
            "crawl_time": grp["crawl_time"],
            "sort_ts": grp["sort_ts"],
            "platform": display_platform,
            "platforms": grp_plats or ([display_platform] if display_platform != "unknown" else []),
            "schema_version": schema_versions[-1] if schema_versions else None,
            "schema_versions": schema_versions,
            "strategy_version": strategy_version,
            "contract_version": contract_version,
            "is_v33": bool(str(contract_version or "").startswith("3.3") or str(quality_policy_version or "").startswith("3.3")),
            "skill_version": skill_version,
            "quality_policy_version": quality_policy_version,
            "target_metric": target_metric_val,
            "crawl_mode": crawl_mode,
            "crawl_mode_label": crawl_mode_label,
            "topics": topic_ids,
            "topic_labels": topic_labels,
            "lookback_hours": lookback_hours,
            "has_strategy_metadata": has_strategy_metadata,
            "special_counts": special_counts or None,
            "run_status": run_status,
            "display_status": linked_req.get("display_status"),
            "display_status_label": linked_req.get("display_status_label"),
            "keywords": keywords_meta,
            "stop_reason": stop_reason,
            "target_achieved": target_achieved,
            "requested_count": int(requested_count) if requested_count is not None else None,
            "scanned_count": scanned_count,
            "unique_candidate_count": unique_candidates,
            "duplicate_current_run_count": dup_current_run,
            "new_qualified_count": new_qualified_count,
            "qualified_count": qualified_total,
            "quality_qualified_count": quality_qualified_count,
            "watchlist_count": watchlist_count,
            "below_hot_count": below_hot_count,
            "traffic_content_count": traffic_content_count,
            "updated_metrics_count": updated_metrics_count,
            "duplicate_history_count": duplicate_history_count,
            "unverified_count": unverified_count,
            "rejected_count": rejected_count,
            "missing_views_count": missing_views_count,
            "tier_counts": tier_counts,
            "total_all_platforms": len(all_platform_snaps),
            "data_file": primary_data_file,
            "report_file": primary_report_file,
            "data_files": formatted_data_files,
            "report_files": formatted_report_files,
            "source_files_refs": grp.get("source_files_refs") or [],
            "has_report": bool(formatted_report_files or reports),
        }

    def _build_request_bundle_groups(self, base_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Aggregate individual session run groups sharing the same request_id into Request Bundles."""
        by_req: Dict[str, List[Dict[str, Any]]] = {}
        for grp in base_groups:
            rid = self._clean_run_str(grp.get("request_id"))
            if rid:
                by_req.setdefault(rid, []).append(grp)

        bundles: List[Dict[str, Any]] = []
        for req_id, sub_groups in by_req.items():
            sub_sorted = sorted(sub_groups, key=lambda g: (float(g.get("sort_ts") or 0.0), str(g.get("run_key") or "")), reverse=True)
            seen_df: set = set()
            seen_rf: set = set()
            seen_rep: set = set()
            merged_df: List[Dict[str, Any]] = []
            merged_rf: List[Dict[str, Any]] = []
            merged_rep: List[Dict[str, Any]] = []
            merged_plats: set = set()
            merged_run_ids: set = set()
            merged_src_refs: List[str] = []
            session_keys: List[str] = []
            linked_req = None
            for sg in sub_sorted:
                session_keys.append(str(sg["run_key"]))
                if sg.get("run_id"):
                    merged_run_ids.add(str(sg["run_id"]))
                for r in sg.get("run_ids") or []:
                    if r:
                        merged_run_ids.add(str(r))
                merged_plats.update(sg.get("platforms") or [])
                for f in sg.get("data_files") or []:
                    fid = str(f["file_id"])
                    if fid not in seen_df:
                        seen_df.add(fid)
                        merged_df.append(f)
                for f in sg.get("report_files") or []:
                    fid = str(f["file_id"])
                    if fid not in seen_rf:
                        seen_rf.add(fid)
                        merged_rf.append(f)
                for rep in sg.get("reports") or []:
                    rkey = str(rep.get("report_key") or rep.get("id") or "")
                    if rkey and rkey in seen_rep:
                        continue
                    if rkey:
                        seen_rep.add(rkey)
                    merged_rep.append(rep)
                for sr in sg.get("source_files_refs") or []:
                    if sr not in merged_src_refs:
                        merged_src_refs.append(sr)
                if not linked_req and sg.get("linked_request"):
                    linked_req = sg.get("linked_request")

            top_sg = sub_sorted[0]
            bundles.append({
                "run_key": f"req_bundle:{req_id}",
                "run_id": req_id,
                "run_ids": merged_run_ids,
                "grouped_by": "request_bundle",
                "is_request_bundle": True,
                "session_run_keys": session_keys,
                "has_run_id": True,
                "request_id": req_id,
                "request_ids": {req_id},
                "data_files": merged_df,
                "report_files": merged_rf,
                "reports": merged_rep,
                "platforms": merged_plats,
                "source_files_refs": merged_src_refs,
                "sort_ts": top_sg.get("sort_ts") or 0.0,
                "crawl_time": top_sg.get("crawl_time") or "",
                "linked_request": linked_req,
            })

        bundles.sort(key=lambda g: (float(g.get("sort_ts") or 0.0), str(g["run_key"])), reverse=True)
        return bundles

    def _resolve_run_scope(
        self,
        run_key: Optional[str],
        *,
        include_archived: bool = False,
        crawl_requests: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        key = str(run_key or "").strip()
        if not key:
            return None
        groups = self._build_run_groups_index(include_archived=include_archived, crawl_requests=crawl_requests)
        if key.startswith("req_bundle:"):
            target_rid = key[len("req_bundle:"):].strip()
            for bg in self._build_request_bundle_groups(groups):
                if bg["request_id"] == target_rid or bg["run_key"] == key:
                    return bg
            return None
        for grp in groups:
            if grp["run_key"] == key or (grp["run_id"] and grp["run_id"] == key):
                return grp
            # Also match explicit file_id if caller passed raw file_id or file:<id>
            fids = {f["file_id"] for f in grp["data_files"] + grp["report_files"]}
            if key in fids or (key.startswith("file:") and key[5:] in fids):
                return grp
        return None

    def list_request_bundles(
        self,
        platform: str = "all",
        limit: int = 50,
        include_archived: bool = False,
        crawl_requests: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """List request-level aggregated bundles across multi-session runs (newest-first)."""
        plat = (platform or "all").strip().lower()
        groups = self._build_run_groups_index(include_archived=include_archived, crawl_requests=crawl_requests)
        bundles = self._build_request_bundle_groups(groups)
        out: List[Dict[str, Any]] = []
        for bg in bundles:
            if plat and plat != "all":
                if plat not in bg["platforms"] and not any(
                    plat in str(f.get("file_name") or "").lower()
                    for f in bg["data_files"] + bg["report_files"]
                ):
                    continue
            out.append(self._summarize_run_group(bg, platform=plat))
            if len(out) >= max(1, min(int(limit), 200)):
                break
        return out

    def list_crawl_runs(
        self,
        platform: str = "all",
        limit: int = 50,
        include_archived: bool = False,
        crawl_requests: Optional[List[Dict[str, Any]]] = None,
        crawl_mode: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List crawl runs newest-first with Data+Report Drive links, per-run counters, optional metadata filters, and status."""
        plat = (platform or "all").strip().lower()
        mode_filter = (crawl_mode or "").strip().lower()
        topic_filter_raw = (topic or "").strip()
        topic_filter_ids, topic_filter_labels = _normalize_crawl_topics(topic_filter_raw, strict=False) if topic_filter_raw and topic_filter_raw.lower() != "all" else ([], [])
        topic_match_set = {t.lower() for t in (topic_filter_ids + topic_filter_labels)}

        groups = self._build_run_groups_index(include_archived=include_archived, crawl_requests=crawl_requests)
        out: List[Dict[str, Any]] = []
        for grp in groups:
            if plat and plat != "all":
                if plat not in grp["platforms"] and not any(
                    plat in str(f.get("file_name") or "").lower()
                    for f in grp["data_files"] + grp["report_files"]
                ):
                    continue
            summary = self._summarize_run_group(grp, platform=plat)
            if mode_filter and mode_filter != "all":
                if mode_filter == "__none__":
                    if summary.get("crawl_mode"):
                        continue
                elif str(summary.get("crawl_mode") or "").lower() != mode_filter:
                    continue
            if topic_match_set:
                run_topics = {
                    str(x).lower()
                    for x in ((summary.get("topics") or []) + (summary.get("topic_labels") or []))
                    if x
                }
                if not (run_topics & topic_match_set):
                    continue
            out.append(summary)
            if len(out) >= max(1, min(int(limit), 200)):
                break
        return out

    def compare_crawl_runs(
        self,
        base_run_key: str,
        target_run_key: str,
        platform: str = "all",
    ) -> Dict[str, Any]:
        """Compare metrics and post sets between two historical crawl runs (Mode 3)."""
        plat = (platform or "all").strip().lower()
        base_grp = self._resolve_run_scope(base_run_key)
        target_grp = self._resolve_run_scope(target_run_key)
        if not base_grp or not target_grp:
            missing = base_run_key if not base_grp else target_run_key
            return {"ok": False, "error": f"Không tìm thấy lượt crawl: {missing}"}

        base_summary = self._summarize_run_group(base_grp, platform=plat)
        target_summary = self._summarize_run_group(target_grp, platform=plat)

        base_items = self._query_effective_run_snapshots(
            run_id=base_grp["run_id"],
            raw_file_ids=[str(f["file_id"]) for f in base_grp["data_files"]],
            platform=plat,
        )
        target_items = self._query_effective_run_snapshots(
            run_id=target_grp["run_id"],
            raw_file_ids=[str(f["file_id"]) for f in target_grp["data_files"]],
            platform=plat,
        )

        base_map = {str(it.get("dedup_key") or it["id"]): it for it in base_items}
        target_map = {str(it.get("dedup_key") or it["id"]): it for it in target_items}

        common_items: List[Dict[str, Any]] = []
        new_in_target: List[Dict[str, Any]] = []
        dropped_from_base: List[Dict[str, Any]] = []
        total_views_delta = 0
        total_likes_delta = 0
        total_comments_delta = 0

        for key, t_it in target_map.items():
            b_it = base_map.get(key)
            if not b_it:
                new_in_target.append(t_it)
                continue
            vd = self._compute_metric_delta(t_it.get("views_count"), b_it.get("views_count"))
            ld = self._compute_metric_delta(t_it.get("likes_count"), b_it.get("likes_count"))
            cd = self._compute_metric_delta(t_it.get("comments_count"), b_it.get("comments_count"))
            sd = self._compute_metric_delta(t_it.get("shares_count"), b_it.get("shares_count"))
            rd = self._compute_metric_delta(t_it.get("reposts_count"), b_it.get("reposts_count"))
            if vd is not None:
                total_views_delta += vd
            if ld is not None:
                total_likes_delta += ld
            if cd is not None:
                total_comments_delta += cd
            common_items.append({
                "id": t_it["id"],
                "dedup_key": key,
                "platform": t_it["platform"],
                "post_id": t_it.get("post_id") or t_it.get("external_id"),
                "author_handle": t_it.get("author_handle"),
                "url": t_it.get("url"),
                "text_content": t_it.get("summary_vi") or t_it.get("text_content"),
                "base_views": b_it.get("views_count"),
                "target_views": t_it.get("views_count"),
                "views_delta": vd,
                "base_likes": b_it.get("likes_count"),
                "target_likes": t_it.get("likes_count"),
                "likes_delta": ld,
                "base_comments": b_it.get("comments_count"),
                "target_comments": t_it.get("comments_count"),
                "comments_delta": cd,
                "shares_delta": sd,
                "reposts_delta": rd,
                "base_tier": b_it.get("trend_tier"),
                "target_tier": t_it.get("trend_tier"),
                "tier_changed": (b_it.get("trend_tier") or "") != (t_it.get("trend_tier") or ""),
                "base_classification": b_it.get("classification") or b_it.get("last_change_type"),
                "target_classification": t_it.get("classification") or t_it.get("last_change_type"),
            })

        for key, b_it in base_map.items():
            if key not in target_map:
                dropped_from_base.append(b_it)

        common_items.sort(
            key=lambda x: abs(x.get("views_delta") or 0) * 10 + abs(x.get("likes_delta") or 0),
            reverse=True,
        )

        return {
            "ok": True,
            "platform": plat,
            "base_run": base_summary,
            "target_run": target_summary,
            "summary": {
                "base_count": len(base_items),
                "target_count": len(target_items),
                "overlap_count": len(common_items),
                "new_in_target_count": len(new_in_target),
                "dropped_from_base_count": len(dropped_from_base),
                "qualified_base": base_summary["new_qualified_count"],
                "qualified_target": target_summary["new_qualified_count"],
                "total_views_delta": total_views_delta,
                "total_likes_delta": total_likes_delta,
                "total_comments_delta": total_comments_delta,
            },
            "common_items": common_items[:100],
            "new_in_target": new_in_target[:100],
            "dropped_from_base": dropped_from_base[:100],
        }

    # ─── Remote Command Idempotency Ledger ─────────────────────────────────────

    def get_local_command(self, command_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM remote_command_log WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_command_running(
        self,
        command_id: str,
        command_type: str,
        *,
        attempt: Optional[int] = None,
        claimed_by: Optional[str] = None,
        lease_expires_at: Optional[str] = None,
    ):
        now_iso = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO remote_command_log (command_id, command_type, status, claimed_at, pending_ack, attempt, claimed_by, lease_expires_at)
                VALUES (?, ?, 'running', ?, 0, ?, ?, ?)
                ON CONFLICT(command_id) DO UPDATE SET
                    status = 'running',
                    attempt = COALESCE(excluded.attempt, remote_command_log.attempt),
                    claimed_by = COALESCE(excluded.claimed_by, remote_command_log.claimed_by),
                    lease_expires_at = COALESCE(excluded.lease_expires_at, remote_command_log.lease_expires_at)
                """,
                (command_id, command_type, now_iso, attempt, claimed_by, lease_expires_at),
            )
            self._conn.commit()

    def save_command_result(
        self,
        command_id: str,
        command_type: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        acked: bool = False,
        *,
        attempt: Optional[int] = None,
        claimed_by: Optional[str] = None,
        lease_expires_at: Optional[str] = None,
    ):
        now_iso = _utc_now_iso()
        res_json = json.dumps(result or {}, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO remote_command_log
                (command_id, command_type, status, result_json, error_message, pending_ack, claimed_at, completed_at, acked_at, attempt, claimed_by, lease_expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(command_id) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    error_message = excluded.error_message,
                    pending_ack = excluded.pending_ack,
                    completed_at = excluded.completed_at,
                    acked_at = excluded.acked_at,
                    attempt = COALESCE(excluded.attempt, remote_command_log.attempt),
                    claimed_by = COALESCE(excluded.claimed_by, remote_command_log.claimed_by),
                    lease_expires_at = COALESCE(excluded.lease_expires_at, remote_command_log.lease_expires_at)
                """,
                (
                    command_id,
                    command_type,
                    status,
                    res_json,
                    error_message,
                    0 if acked else 1,
                    now_iso,
                    now_iso,
                    now_iso if acked else None,
                    attempt,
                    claimed_by,
                    lease_expires_at,
                ),
            )
            self._conn.commit()

    def mark_command_acked(self, command_id: str):
        now_iso = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE remote_command_log SET pending_ack = 0, acked_at = ? WHERE command_id = ?",
                (now_iso, command_id),
            )
            self._conn.commit()

    def mark_command_superseded(self, command_id: str, reason: Optional[str] = None):
        now_iso = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE remote_command_log
                SET pending_ack = 0, status = 'superseded', acked_at = ?,
                    error_message = COALESCE(?, error_message)
                WHERE command_id = ?
                """,
                (now_iso, reason, command_id),
            )
            self._conn.commit()

    def list_pending_ack_commands(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM remote_command_log WHERE pending_ack = 1 ORDER BY completed_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def list_recent_remote_commands(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM remote_command_log ORDER BY claimed_at DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["result"] = json.loads(d.pop("result_json") or "{}")
                except Exception:
                    d["result"] = {}
                out.append(d)
            return out

    # ─── Standardized Analysis Reports ────────────────────────────────────────

    def save_report(
        self,
        report: Dict[str, Any],
        raw_file_id: Optional[str] = None,
        raw_payload: Optional[Any] = None,
    ) -> str:
        now_iso = _utc_now_iso()
        report_key = str(report.get("report_key") or uuid.uuid4().hex)
        rid = str(uuid.uuid4())
        is_corr = 1 if report.get("is_corrected") else 0
        prio = int(report.get("source_priority") or (2 if is_corr else 1))
        with self._lock:
            existing = self._conn.execute(
                "SELECT source_priority FROM market_reports WHERE report_key = ?",
                (report_key,),
            ).fetchone()
            if existing and prio < int(existing["source_priority"] or 1):
                return report_key

            self._conn.execute(
                """
                INSERT INTO market_reports
                (id, report_key, source_type, title, platform, raw_file_id, report_json, raw_payload_json,
                 generated_at, created_at, updated_at, is_corrected, corrects_report, request_id, run_id,
                 crawl_time, run_status, technical_status, data_quality_status, business_usable_records, source_priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_key) DO UPDATE SET
                    source_type = excluded.source_type,
                    title = excluded.title,
                    platform = excluded.platform,
                    raw_file_id = COALESCE(excluded.raw_file_id, market_reports.raw_file_id),
                    report_json = excluded.report_json,
                    raw_payload_json = COALESCE(excluded.raw_payload_json, market_reports.raw_payload_json),
                    generated_at = excluded.generated_at,
                    updated_at = excluded.updated_at,
                    is_corrected = excluded.is_corrected,
                    corrects_report = excluded.corrects_report,
                    request_id = excluded.request_id,
                    run_id = excluded.run_id,
                    crawl_time = COALESCE(excluded.crawl_time, market_reports.crawl_time),
                    run_status = excluded.run_status,
                    technical_status = excluded.technical_status,
                    data_quality_status = excluded.data_quality_status,
                    business_usable_records = excluded.business_usable_records,
                    source_priority = excluded.source_priority
                """,
                (
                    rid,
                    report_key,
                    str(report.get("source_type") or "muse_report_json"),
                    str(report.get("title") or "Market Analysis Report"),
                    str(report.get("platform") or "threads"),
                    raw_file_id,
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None,
                    report.get("generated_at") or now_iso,
                    now_iso,
                    now_iso,
                    is_corr,
                    report.get("corrects_report"),
                    report.get("request_id"),
                    report.get("run_id"),
                    report.get("crawl_time"),
                    report.get("run_status"),
                    report.get("technical_status"),
                    report.get("data_quality_status"),
                    report.get("business_usable_records"),
                    prio,
                ),
            )
            self._conn.commit()
        return report_key

    @staticmethod
    def _parse_iso_ts(val: Optional[str]) -> float:
        if not val:
            return 0.0
        try:
            s = str(val).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0

    def _extract_report_crawl_ts(self, row: Dict[str, Any]) -> Tuple[float, bool]:
        rep = row.get("report") if isinstance(row.get("report"), dict) else {}
        explicit_flag = bool(row.get("crawl_time") or rep.get("crawl_time_is_explicit"))
        for candidate in (
            row.get("crawl_time"),
            rep.get("crawl_time"),
            rep.get("ended_at"),
            rep.get("collected_at"),
            rep.get("started_at"),
        ):
            ts = self._parse_iso_ts(candidate)
            if ts > 0:
                return ts, True
        gen_ts = self._parse_iso_ts(rep.get("generated_at") or row.get("generated_at"))
        if gen_ts > 0 and explicit_flag:
            return gen_ts, True
        if gen_ts > 0 and self._parse_iso_ts(row.get("raw_modified_time")) == 0 and self._parse_iso_ts(row.get("updated_at")) == 0:
            return gen_ts, False
        # Fallback to file/updated timestamps ONLY when payload itself has no crawl execution timestamp
        for candidate in (row.get("generated_at"), row.get("raw_modified_time"), row.get("updated_at")):
            ts = self._parse_iso_ts(candidate)
            if ts > 0:
                return ts, False
        return 0.0, False

    def list_reports(self, limit: int = 20, include_archived: bool = False) -> List[Dict[str, Any]]:
        """Return reports (non-archived by default) ordered between runs strictly by crawl execution time; revision time breaks ties within the same run."""
        where_sql = "" if include_archived else f"WHERE (mr.raw_file_id IS NULL OR {_NOT_ARCHIVE_RAW_SQL})"
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT mr.id, mr.report_key, mr.source_type, mr.title, mr.platform, mr.raw_file_id, mr.report_json,
                       mr.generated_at, mr.updated_at, mr.is_corrected, mr.corrects_report, mr.request_id, mr.run_id,
                       mr.crawl_time, mr.run_status, mr.technical_status, mr.data_quality_status, mr.business_usable_records, mr.source_priority,
                       r.modified_time AS raw_modified_time, r.folder_path AS raw_folder_path
                FROM market_reports mr
                LEFT JOIN raw_drive_files r ON mr.raw_file_id = r.file_id
                {where_sql}
                ORDER BY COALESCE(mr.crawl_time, mr.generated_at, mr.updated_at) DESC
                LIMIT 500
                """
            ).fetchall()

        out: List[Dict[str, Any]] = []
        file_name_to_group: Dict[str, str] = {}
        for r in rows:
            d = dict(r)
            if not include_archived and is_archive_path(d.pop("raw_folder_path", None)):
                continue
            d.pop("raw_folder_path", None)
            d["is_corrected"] = bool(d.get("is_corrected"))
            try:
                d["report"] = json.loads(d.pop("report_json") or "{}")
            except Exception:
                d["report"] = {}
            fname = str((d["report"] or {}).get("file_name") or "").strip().lower()
            if not include_archived and is_archive_path(fname):
                continue
            grp = str(d.get("request_id") or d.get("run_id") or d.get("report_key"))
            if fname:
                file_name_to_group[fname] = grp
            d["_group_key"] = grp
            crawl_ts, from_payload = self._extract_report_crawl_ts(d)
            d["_crawl_ts"] = crawl_ts
            d["_crawl_from_payload"] = from_payload
            d["_revision_ts"] = max(
                self._parse_iso_ts(d.get("raw_modified_time")),
                self._parse_iso_ts(d.get("updated_at")),
            )
            out.append(d)

        # Resolve corrects_report linkage into the same run group
        for d in out:
            corr_target = str(d.get("corrects_report") or "").strip().lower()
            if corr_target and corr_target in file_name_to_group:
                d["_group_key"] = file_name_to_group[corr_target]

        # Inter-run order uses base crawl execution time (preferring original/payload timestamp, NEVER MAX with modifiedTime/updated_at)
        group_crawl_meta: Dict[str, Tuple[float, bool, bool]] = {}
        for d in out:
            gk = d["_group_key"]
            ts = d["_crawl_ts"]
            is_orig = not d["is_corrected"]
            from_p = bool(d["_crawl_from_payload"])
            cur = group_crawl_meta.get(gk)
            if cur is None:
                group_crawl_meta[gk] = (ts, is_orig, from_p)
                continue
            cur_ts, cur_orig, cur_p = cur
            if from_p and not cur_p:
                group_crawl_meta[gk] = (ts, is_orig, True)
            elif is_orig and (not cur_orig or (ts > 0 and (cur_ts == 0 or ts < cur_ts))):
                group_crawl_meta[gk] = (ts, True, cur_p or from_p)
            elif cur_orig == is_orig and ts > 0 and (cur_ts == 0 or ts < cur_ts):
                group_crawl_meta[gk] = (ts, cur_orig, cur_p or from_p)

        out.sort(
            key=lambda d: (
                group_crawl_meta.get(d["_group_key"], (0.0, False, False))[0],
                int(d.get("source_priority") or 1),
                1 if d.get("is_corrected") else 0,
                d["_revision_ts"],
                d["_crawl_ts"],
            ),
            reverse=True,
        )
        for d in out:
            d.pop("_group_key", None)
            d.pop("_crawl_ts", None)
            d.pop("_crawl_from_payload", None)
            d.pop("_revision_ts", None)
            d.pop("raw_modified_time", None)
        return out[: max(1, min(int(limit), 100))]

    def build_evidence_report(self, platform: str = "all", run_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Build canonical report prioritizing the newest valid Muse report (V3.1 `trend_report_*`
        or V1/V2 `market_report_*`) for the requested run_key (or latest overall when run_key omitted),
        falling back to local evidence when no valid report matches.
        """
        plat = (platform or "all").lower()
        res = self.query_items(platform=plat, run_key=run_key, limit=250, offset=0)
        evidence = build_evidence_report_from_items(res["items"], platform=plat)
        stats = self.get_stats(run_key=run_key, platform=plat)
        if stats.get("trends_snapshot"):
            evidence["trends_snapshot"] = stats["trends_snapshot"]

        chosen = None
        run_summary = None
        if run_key and str(run_key).strip() not in ("", "all"):
            grp = self._resolve_run_scope(run_key)
            if grp:
                run_summary = self._summarize_run_group(grp, platform=plat)
                rep_entries = []
                for rep_row in grp["reports"]:
                    try:
                        parsed_rep = json.loads(rep_row.get("report_json") or "{}")
                    except Exception:
                        parsed_rep = {}
                    if parsed_rep:
                        parsed_rep["_raw_file_id"] = rep_row.get("raw_file_id")
                        rep_entries.append((1 if rep_row.get("is_corrected") else 0, parsed_rep))
                rep_entries.sort(key=lambda x: x[0], reverse=True)
                for _corr, rep in rep_entries:
                    rep_plat = (rep.get("platform") or "").lower()
                    if plat == "all" or rep_plat == plat or rep_plat == "all":
                        chosen = rep
                        break
                if not chosen and rep_entries:
                    chosen = rep_entries[0][1]
        else:
            stored = self.list_reports(limit=20)
            for entry in stored:
                rep = entry.get("report") or {}
                rep_plat = (rep.get("platform") or "").lower()
                has_core = bool(
                    rep.get("telegram_summary")
                    or rep.get("platform_results")
                    or rep.get("trending_highlights")
                    or rep.get("screening")
                    or rep.get("run_status")
                )
                if not has_core:
                    continue
                if plat == "all" or rep_plat == plat or rep_plat == "all":
                    chosen = rep
                    break
            if not chosen and stored:
                chosen = stored[0].get("report")

        if run_summary:
            evidence["run_summary"] = run_summary
            evidence["data_file"] = run_summary.get("data_file")
            evidence["report_file"] = run_summary.get("report_file")
            evidence["data_files"] = run_summary.get("data_files") or []
            evidence["report_files"] = run_summary.get("report_files") or []
            evidence["run_id"] = run_summary.get("run_id")
            evidence["run_key"] = run_summary.get("run_key")
            evidence["grouped_by"] = run_summary.get("grouped_by")
            evidence["is_request_bundle"] = bool(run_summary.get("is_request_bundle"))
            evidence["session_count"] = run_summary.get("session_count")
            evidence["session_seq"] = run_summary.get("session_seq")
            evidence["is_checkpoint"] = bool(run_summary.get("is_checkpoint"))
            evidence["contract_version"] = run_summary.get("contract_version")
            evidence["skill_version"] = run_summary.get("skill_version")
            evidence["quality_policy_version"] = run_summary.get("quality_policy_version")
            evidence["target_metric"] = run_summary.get("target_metric")
            evidence["crawl_mode"] = run_summary.get("crawl_mode")
            evidence["crawl_mode_label"] = run_summary.get("crawl_mode_label")
            evidence["topics"] = run_summary.get("topics") or []
            evidence["topic_labels"] = run_summary.get("topic_labels") or []
            evidence["keywords"] = run_summary.get("keywords")
            evidence["lookback_hours"] = run_summary.get("lookback_hours")
        evidence["has_muse_report"] = bool(chosen)

        if chosen:
            evidence["latest_muse_report"] = chosen
            evidence["schema_version"] = chosen.get("schema_version") or "v2"
            evidence["strategy_version"] = chosen.get("strategy_version") or (run_summary.get("strategy_version") if run_summary else None)
            evidence["contract_version"] = chosen.get("contract_version") or (run_summary.get("contract_version") if run_summary else None)
            evidence["crawl_mode"] = chosen.get("crawl_mode") or evidence.get("crawl_mode")
            if chosen.get("crawl_mode") and not evidence.get("crawl_mode_label"):
                evidence["crawl_mode_label"] = _get_crawl_mode_label(chosen.get("crawl_mode"))
            if isinstance(chosen.get("topics"), list) and chosen.get("topics"):
                c_tids, c_tlabs = _normalize_crawl_topics(chosen.get("topics"), strict=False)
                evidence["topics"] = c_tids
                evidence["topic_labels"] = c_tlabs
            evidence["keywords"] = chosen.get("keywords") or evidence.get("keywords")
            evidence["lookback_hours"] = chosen.get("lookback_hours") if chosen.get("lookback_hours") is not None else evidence.get("lookback_hours")
            evidence["special_counts"] = chosen.get("special_counts") or (run_summary.get("special_counts") if run_summary else None)
            evidence["growth_tracking"] = chosen.get("growth_tracking") or []
            evidence["run_id"] = chosen.get("run_id") or evidence.get("run_id")
            evidence["run_status"] = chosen.get("run_status")
            evidence["run_status_label_vi"] = chosen.get("run_status_label_vi")
            evidence["is_source_error"] = bool(chosen.get("is_source_error"))
            evidence["is_empty_market_valid"] = bool(chosen.get("is_empty_market_valid"))
            evidence["muse_signals"] = chosen.get("trending_highlights") or []
            evidence["muse_affiliate_opportunities"] = chosen.get("affiliate_opportunities") or []
            evidence["telegram_summary"] = chosen.get("telegram_summary")
            evidence["technical_status"] = chosen.get("technical_status")
            evidence["data_quality_status"] = chosen.get("data_quality_status")
            if isinstance(evidence.get("data_quality"), dict):
                if chosen.get("technical_status"):
                    evidence["data_quality"]["technical_status"] = chosen.get("technical_status")
                if chosen.get("data_quality_status"):
                    evidence["data_quality"]["data_quality_status"] = chosen.get("data_quality_status")
                if chosen.get("run_status"):
                    evidence["data_quality"]["run_status"] = chosen.get("run_status")
            evidence["platform_results"] = chosen.get("platform_results") or {}
            evidence["totals"] = chosen.get("totals") or {}
            evidence["screening"] = chosen.get("screening")
            evidence["tier_distribution"] = chosen.get("tier_distribution")
            evidence["top_viral"] = chosen.get("top_viral") or []
            evidence["emerging_and_signals"] = chosen.get("emerging_and_signals")
            evidence["priority_actions"] = chosen.get("priority_actions") or []
            evidence["verification_detail"] = chosen.get("verification_detail")
            evidence["is_corrected"] = bool(chosen.get("is_corrected"))
            evidence["source_files"] = chosen.get("source_files") or (run_summary.get("source_files_refs") if run_summary else [])
            if chosen.get("source_label"):
                evidence["source_label"] = f"{chosen['source_label']} + Dữ liệu chuẩn hóa"
            if chosen.get("trending_highlights"):
                evidence["trending_highlights"] = chosen["trending_highlights"]
            if chosen.get("consumer_needs_problems"):
                evidence["consumer_needs_problems"] = chosen["consumer_needs_problems"]
            if chosen.get("affiliate_opportunities") or chosen.get("schema_version") == "v3.1":
                evidence["affiliate_opportunities"] = chosen.get("affiliate_opportunities") or []
            if chosen.get("content_suggestions", {}).get("text_posts"):
                evidence["content_suggestions"]["text_posts"] = chosen["content_suggestions"]["text_posts"]
            if chosen.get("content_suggestions", {}).get("short_videos"):
                evidence["content_suggestions"]["short_videos"] = chosen["content_suggestions"]["short_videos"]
            if chosen.get("source_links"):
                evidence["source_links"] = chosen["source_links"]
        return evidence
