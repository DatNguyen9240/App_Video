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

from .normalizer import NON_CRAWL_ROLES, is_archive_path, normalize_payload
from .report_normalizer import (
    build_evidence_report_from_items,
    has_muse_report_sections,
    normalize_muse_report,
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
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._init_schema()
            self._cleanup_orphaned_running_syncs()

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
            """
        )

        # Additive Data Contract V2 & V3.1 columns
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
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_drive_files_archived ON raw_drive_files(is_archived);")

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

    def ingest_raw_file(
        self,
        file_id: str,
        file_name: str,
        raw_bytes: bytes,
        modified_time: Optional[str] = None,
        md5_checksum: Optional[str] = None,
        folder_path: Optional[str] = None,
        platform_hint: Optional[str] = None,
    ) -> Dict[str, int]:
        """Archive raw JSON file and priority-upsert normalized items (corrected_v2 > original)."""
        if is_archive_path(folder_path) or is_archive_path(file_name):
            return {
                "file_role": "archived",
                "is_corrected": False,
                "parsed": 0,
                "inserted": 0,
                "updated": 0,
                "deduped": 0,
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

        run_meta = {
            "platform": envelope.get("platform") or ("threads" if str(file_name or "").lower().startswith(("threads_trend", "trend_report")) else None),
            "run_id": envelope.get("run_id"),
            "request_id": envelope.get("request_id"),
            "run_status": envelope.get("run_status"),
            "technical_status": envelope.get("technical_status") or envelope.get("status"),
            "data_quality_status": envelope.get("data_quality_status"),
            "crawled_at": envelope.get("crawled_at") or envelope.get("crawl_time"),
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
            "tier_counts": envelope.get("tier_counts"),
            "group_counts": envelope.get("group_counts"),
            "cross_verified_posts": envelope.get("cross_verified_posts"),
            "trends_snapshot": envelope.get("trends_snapshot"),
            "thresholds": envelope.get("thresholds"),
            "upcoming_vouchers": envelope.get("upcoming_vouchers"),
            "trending_context": envelope.get("trending_context"),
        }
        run_meta = {k: v for k, v in run_meta.items() if v is not None}

        inserted = 0
        updated = 0
        deduped = 0

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO raw_drive_files
                (file_id, file_name, folder_path, modified_time, md5_checksum, size_bytes, schema_version,
                 raw_path, record_count, status, error_message, synced_at,
                 file_role, is_corrected, corrects_file, run_status, technical_status, data_quality_status,
                 business_usable_records, run_meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    run_status = excluded.run_status,
                    technical_status = excluded.technical_status,
                    data_quality_status = excluded.data_quality_status,
                    business_usable_records = excluded.business_usable_records,
                    run_meta_json = excluded.run_meta_json,
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
                    envelope.get("corrects_file") or envelope.get("corrects_report"),
                    envelope.get("run_status"),
                    envelope.get("technical_status") or envelope.get("status"),
                    envelope.get("data_quality_status"),
                    envelope.get("business_usable_records"),
                    json.dumps(run_meta, ensure_ascii=False),
                ),
            )

            for item in items:
                existing = self._conn.execute(
                    """
                    SELECT id, content_group, purchase_intent, text_content, summary_vi, title, url, evidence_url,
                           likes_count, comments_count, shares_count, reposts_count, quotes_count, views_count,
                           price, sold_count, rating, trend_tier, trend_group, verification_status, metrics_source, post_pk,
                           data_quality, quality_issues_json, source_priority, is_corrected, run_id, crawled_at, published_at,
                           raw_payload_json, extra_meta_json, metrics_json
                    FROM market_items
                    WHERE dedup_key = ?
                    """,
                    (item["dedup_key"],),
                ).fetchone()

                metrics_json = json.dumps(item["metrics"], ensure_ascii=False)
                extra_meta_json = json.dumps(item["extra_meta"], ensure_ascii=False)
                quality_issues_json = json.dumps(item["quality_issues"], ensure_ascii=False)
                incoming_prio = int(item.get("source_priority") or 1)

                if not existing:
                    self._conn.execute(
                        """
                        INSERT INTO market_items (
                            id, dedup_key, platform, external_id, post_id, post_pk, item_id, shop_id,
                            author_handle, author_name, title, text_content, summary_vi, url, evidence_url,
                            content_group, purchase_intent, language, published_at, crawled_at,
                            likes_count, comments_count, shares_count, reposts_count, quotes_count, views_count,
                            price, sold_count, rating, trend_tier, trend_group, verification_status, metrics_source,
                            data_quality, quality_issues_json, source_priority, is_corrected, run_id,
                            metrics_json, extra_meta_json, schema_version, raw_file_id, raw_item_index,
                            raw_payload_json, first_seen_at, last_seen_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?
                        )
                        """,
                        (
                            str(uuid.uuid4()),
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
                            item.get("run_id"),
                            metrics_json,
                            extra_meta_json,
                            item["schema_version"],
                            file_id,
                            item["raw_item_index"],
                            item["raw_payload_json"],
                            now_iso,
                            now_iso,
                            now_iso,
                        ),
                    )
                    inserted += 1
                else:
                    existing_prio = int(existing["source_priority"] or 1)
                    existing_run = existing["run_id"] or ""
                    incoming_run = item.get("run_id") or ""
                    same_run = bool(existing_run and incoming_run and existing_run == incoming_run)
                    same_or_unknown_run = same_run or not existing_run or not incoming_run
                    existing_ts = self._parse_iso_ts(existing["crawled_at"] or existing["published_at"])
                    incoming_ts = self._parse_iso_ts(item.get("crawled_at") or item.get("published_at") or modified_time)
                    is_newer_distinct_run = bool(
                        existing_run and incoming_run and existing_run != incoming_run and incoming_ts >= existing_ts
                    )

                    # Original files must NEVER overwrite corrected_v2 within the same run or from an older run
                    if (incoming_prio < existing_prio and not is_newer_distinct_run) or (
                        not same_or_unknown_run and existing_ts > 0 and incoming_ts > 0 and incoming_ts < existing_ts
                    ):
                        self._conn.execute(
                            "UPDATE market_items SET last_seen_at = ? WHERE id = ?",
                            (now_iso, existing["id"]),
                        )
                        deduped += 1
                        continue

                    merged_cg = item["content_group"] if item["content_group"] is not None else existing["content_group"]
                    merged_pi = item["purchase_intent"] if item["purchase_intent"] is not None else existing["purchase_intent"]
                    merged_title = item["title"] if item["title"] is not None else existing["title"]
                    merged_text = item["text_content"] if item["text_content"] is not None else existing["text_content"]
                    merged_svi = item["summary_vi"] if item["summary_vi"] is not None else existing["summary_vi"]
                    merged_url = item["url"] if item["url"] is not None else existing["url"]
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
                    merged_price = item["price"] if item["price"] is not None else existing["price"]
                    merged_sold = item["sold_count"] if item["sold_count"] is not None else existing["sold_count"]
                    merged_rating = item["rating"] if item["rating"] is not None else existing["rating"]

                    if incoming_prio > existing_prio or is_newer_distinct_run:
                        merged_dq = item["data_quality"] if item["data_quality"] is not None else existing["data_quality"]
                        merged_qi = quality_issues_json
                        force_update = True
                    else:
                        merged_dq = item["data_quality"] if item["data_quality"] is not None else existing["data_quality"]
                        merged_qi = quality_issues_json if item["quality_issues"] else (existing["quality_issues_json"] or "[]")
                        force_update = any(
                            [
                                merged_cg != existing["content_group"],
                                merged_pi != existing["purchase_intent"],
                                merged_title != existing["title"],
                                merged_text != existing["text_content"],
                                merged_svi != existing["summary_vi"],
                                merged_url != existing["url"],
                                merged_ev_url != existing["evidence_url"],
                                merged_tier != existing["trend_tier"],
                                merged_group != existing["trend_group"],
                                merged_verif != existing["verification_status"],
                                merged_dq != existing["data_quality"],
                                merged_qi != (existing["quality_issues_json"] or "[]"),
                                merged_likes != existing["likes_count"],
                                merged_comments != existing["comments_count"],
                                merged_shares != existing["shares_count"],
                                merged_reposts != existing["reposts_count"],
                                merged_quotes != existing["quotes_count"],
                                merged_views != existing["views_count"],
                                merged_price != existing["price"],
                                merged_sold != existing["sold_count"],
                                merged_rating != existing["rating"],
                                extra_meta_json != (existing["extra_meta_json"] or "{}"),
                                metrics_json != (existing["metrics_json"] or "{}"),
                            ]
                        )

                    if force_update:
                        self._conn.execute(
                            """
                            UPDATE market_items
                            SET title = ?, text_content = ?, summary_vi = ?, url = ?, evidence_url = ?, post_pk = COALESCE(?, post_pk),
                                language = COALESCE(?, language), published_at = COALESCE(?, published_at), crawled_at = COALESCE(?, crawled_at),
                                content_group = ?, purchase_intent = ?,
                                likes_count = ?, comments_count = ?, shares_count = ?, reposts_count = ?, quotes_count = ?, views_count = ?,
                                price = ?, sold_count = ?, rating = ?,
                                trend_tier = ?, trend_group = ?, verification_status = ?, metrics_source = ?,
                                data_quality = ?, quality_issues_json = ?,
                                source_priority = ?, is_corrected = ?, run_id = COALESCE(?, run_id),
                                metrics_json = ?, extra_meta_json = ?, schema_version = ?,
                                raw_file_id = ?, raw_payload_json = ?,
                                last_seen_at = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                merged_title,
                                merged_text,
                                merged_svi,
                                merged_url,
                                merged_ev_url,
                                merged_post_pk,
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
                                item.get("run_id"),
                                metrics_json,
                                extra_meta_json,
                                item["schema_version"],
                                file_id,
                                item["raw_payload_json"],
                                now_iso,
                                now_iso,
                                existing["id"],
                            ),
                        )
                        updated += 1
                    else:
                        self._conn.execute(
                            "UPDATE market_items SET last_seen_at = ? WHERE id = ?",
                            (now_iso, existing["id"]),
                        )
                        deduped += 1

            self._conn.commit()

        if file_role not in ("data_contract", "crawl_strategy", "quality_audit", "research_doc", "capability_test") and has_muse_report_sections(raw_obj):
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
        }

    def reconcile_cached_raw_files(self) -> Dict[str, int]:
        """
        Re-normalize already-archived raw files in original -> corrected order so
        prior duplicate keys or pre-V2 rows converge cleanly to Data Contract V2.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT file_id, file_name, folder_path, modified_time, md5_checksum, raw_path
                FROM raw_drive_files
                WHERE status = 'synced'
                ORDER BY is_corrected ASC, COALESCE(modified_time, synced_at) ASC
                """
            ).fetchall()

            # Sort originals cleanly before corrected files by filename/content timestamp
            sorted_rows = sorted(
                [dict(r) for r in rows],
                key=lambda x: (1 if "_corrected" in (x.get("file_name") or "").lower() else 0, x.get("modified_time") or ""),
            )
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
    ):
        now_iso = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                UPDATE sync_runs
                SET status = ?, files_checked = ?, files_downloaded = ?,
                    records_parsed = ?, records_inserted = ?, records_updated = ?,
                    records_deduped = ?, finished_at = ?, error_message = ?
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
        search: Optional[str] = None,
        sort_by: str = "recent",
        affiliate_only: bool = False,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
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

        if affiliate_only:
            clauses.append(
                "(m.data_quality IS NULL OR m.data_quality != 'needs_verification') "
                "AND COALESCE(m.verification_status, '') != 'unverified' "
                "AND COALESCE(m.trend_tier, '') != 'unverified'"
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
        lim = max(1, min(int(limit), 200))
        off = max(0, int(offset))

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
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM market_items m LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id {where_sql}",
                params,
            ).fetchone()["c"]

            rows = self._conn.execute(
                f"""
                SELECT m.id, m.dedup_key, m.platform, m.external_id, m.post_id, m.post_pk, m.item_id, m.shop_id,
                       m.author_handle, m.author_name, m.title, m.text_content, m.summary_vi, m.url, m.evidence_url,
                       m.content_group, m.purchase_intent, m.language, m.published_at, m.crawled_at,
                       m.likes_count, m.comments_count, m.shares_count, m.reposts_count, m.quotes_count, m.views_count,
                       m.price, m.sold_count, m.rating,
                       m.trend_tier, m.trend_group, m.verification_status, m.metrics_source,
                       m.data_quality, m.quality_issues_json, m.source_priority, m.is_corrected, m.run_id,
                       m.metrics_json, m.extra_meta_json, m.schema_version, m.raw_file_id, m.first_seen_at, m.last_seen_at,
                       r.file_name AS source_file_name
                FROM market_items m
                LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id
                {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """,
                params + [lim, off],
            ).fetchall()

        items = []
        for r in rows:
            d = dict(r)
            d["is_corrected"] = bool(d.get("is_corrected"))
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
            items.append(d)

        return {"total": total, "limit": lim, "offset": off, "items": items}

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
            return d

    def _run_meta_crawl_ts(self, meta: Dict[str, Any], modified_time: Optional[str] = None) -> float:
        for key in ("crawled_at", "crawl_time", "ended_at", "collected_at", "started_at", "generated_at"):
            val = meta.get(key)
            if val:
                parsed = self._parse_iso_ts(val)
                if parsed > 0:
                    return parsed
        return self._parse_iso_ts(modified_time)

    def get_stats(self) -> Dict[str, Any]:
        active_item_join = f"LEFT JOIN raw_drive_files r ON m.raw_file_id = r.file_id WHERE (m.raw_file_id IS NULL OR {_NOT_ARCHIVE_RAW_SQL})"
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
            for r in self._conn.execute(
                f"""
                SELECT m.platform,
                       CASE WHEN m.verification_status = 'unverified' THEN 'unverified' ELSE m.trend_tier END AS eff_tier,
                       COUNT(*) AS c
                FROM market_items m
                {active_item_join}
                GROUP BY m.platform, eff_tier
                """
            ).fetchall():
                tkey = r["eff_tier"] if r["eff_tier"] in by_trend_tier else "legacy"
                by_trend_tier[tkey] += r["c"]
                if r["platform"] == "threads":
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
                        "screening": meta.get("screening"),
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
                    WHERE r.file_role IN ('data_contract', 'crawl_strategy', 'quality_audit', 'research_doc', 'capability_test')
                      AND {_NOT_ARCHIVE_RAW_SQL}
                    ORDER BY r.synced_at DESC
                    """
                ).fetchall()
            ]

            latest_run = self.get_latest_sync_run()

        return {
            "total_items": total_items,
            "total_raw_files": total_raw_files,
            "by_platform": by_platform,
            "by_quality": by_quality,
            "by_platform_quality": by_platform_quality,
            "by_trend_tier": by_trend_tier,
            "threads_by_trend_tier": threads_by_trend_tier,
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

    def build_evidence_report(self, platform: str = "all") -> Dict[str, Any]:
        """
        Build canonical report prioritizing the newest valid Muse report (V3.1 `trend_report_*`
        or V1/V2 `market_report_*`), falling back to local evidence when no valid report matches.
        """
        plat = (platform or "all").lower()
        res = self.query_items(platform=plat, limit=250, offset=0)
        evidence = build_evidence_report_from_items(res["items"], platform=plat)
        stats = self.get_stats()
        if stats.get("trends_snapshot"):
            evidence["trends_snapshot"] = stats["trends_snapshot"]

        stored = self.list_reports(limit=20)
        # Prefer newest Muse report matching requested scope (V3.1 trend_report_* or V1/V2 market_report_*)
        chosen = None
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

        if chosen:
            evidence["latest_muse_report"] = chosen
            evidence["schema_version"] = chosen.get("schema_version") or "v2"
            evidence["run_id"] = chosen.get("run_id")
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
