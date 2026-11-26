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
from typing import Any, Dict, List, Optional

from .normalizer import NON_CRAWL_ROLES, normalize_payload
from .report_normalizer import (
    build_evidence_report_from_items,
    has_muse_report_sections,
    normalize_muse_report,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

        # Additive Data Contract V2 columns
        self._add_column_if_missing("raw_drive_files", "file_role", "TEXT DEFAULT 'crawl_data'")
        self._add_column_if_missing("raw_drive_files", "is_corrected", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("raw_drive_files", "corrects_file", "TEXT")
        self._add_column_if_missing("raw_drive_files", "technical_status", "TEXT")
        self._add_column_if_missing("raw_drive_files", "data_quality_status", "TEXT")
        self._add_column_if_missing("raw_drive_files", "business_usable_records", "INTEGER")
        self._add_column_if_missing("raw_drive_files", "run_meta_json", "TEXT DEFAULT '{}'")

        self._add_column_if_missing("market_items", "data_quality", "TEXT")
        self._add_column_if_missing("market_items", "quality_issues_json", "TEXT NOT NULL DEFAULT '[]'")
        self._add_column_if_missing("market_items", "source_priority", "INTEGER NOT NULL DEFAULT 1")
        self._add_column_if_missing("market_items", "is_corrected", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("market_items", "summary_vi", "TEXT")
        self._add_column_if_missing("market_items", "run_id", "TEXT")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_quality ON market_items(data_quality);")

        self._add_column_if_missing("market_reports", "is_corrected", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing("market_reports", "corrects_report", "TEXT")
        self._add_column_if_missing("market_reports", "request_id", "TEXT")
        self._add_column_if_missing("market_reports", "run_id", "TEXT")
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
            "platform": envelope.get("platform"),
            "run_id": envelope.get("run_id"),
            "request_id": envelope.get("request_id"),
            "source": envelope.get("source"),
            "notes": envelope.get("notes"),
            "run_quality_note": envelope.get("run_quality_note"),
            "engagement_completeness": envelope.get("engagement_completeness"),
            "bucket_counts": envelope.get("bucket_counts"),
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
                 file_role, is_corrected, corrects_file, technical_status, data_quality_status,
                 business_usable_records, run_meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced', NULL, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    technical_status = excluded.technical_status,
                    data_quality_status = excluded.data_quality_status,
                    business_usable_records = excluded.business_usable_records,
                    run_meta_json = excluded.run_meta_json
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
                    envelope.get("technical_status") or envelope.get("status"),
                    envelope.get("data_quality_status"),
                    envelope.get("business_usable_records"),
                    json.dumps(run_meta, ensure_ascii=False),
                ),
            )

            for item in items:
                existing = self._conn.execute(
                    """
                    SELECT id, content_group, purchase_intent, text_content, summary_vi, title, url,
                           likes_count, comments_count, shares_count, views_count, price, sold_count, rating,
                           data_quality, quality_issues_json, source_priority, is_corrected,
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
                            id, dedup_key, platform, external_id, post_id, item_id, shop_id,
                            author_handle, author_name, title, text_content, summary_vi, url,
                            content_group, purchase_intent, language, published_at, crawled_at,
                            likes_count, comments_count, shares_count, views_count, price, sold_count, rating,
                            data_quality, quality_issues_json, source_priority, is_corrected, run_id,
                            metrics_json, extra_meta_json, schema_version, raw_file_id, raw_item_index,
                            raw_payload_json, first_seen_at, last_seen_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
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
                            item["item_id"],
                            item["shop_id"],
                            item["author_handle"],
                            item["author_name"],
                            item["title"],
                            item["text_content"],
                            item["summary_vi"],
                            item["url"],
                            item["content_group"],
                            item["purchase_intent"],
                            item["language"],
                            item["published_at"],
                            item["crawled_at"],
                            item["likes_count"],
                            item["comments_count"],
                            item["shares_count"],
                            item["views_count"],
                            item["price"],
                            item["sold_count"],
                            item["rating"],
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
                    # Original files must NEVER overwrite a corrected_v2 record
                    if incoming_prio < existing_prio:
                        self._conn.execute(
                            "UPDATE market_items SET last_seen_at = ? WHERE id = ?",
                            (now_iso, existing["id"]),
                        )
                        deduped += 1
                        continue

                    if incoming_prio > existing_prio:
                        # Authoritative upgrade from original -> corrected_v2
                        merged_cg = item["content_group"] if item["content_group"] is not None else existing["content_group"]
                        merged_pi = item["purchase_intent"] if item["purchase_intent"] is not None else existing["purchase_intent"]
                        merged_title = item["title"] if item["title"] is not None else existing["title"]
                        merged_text = item["text_content"] if item["text_content"] is not None else existing["text_content"]
                        merged_svi = item["summary_vi"] if item["summary_vi"] is not None else existing["summary_vi"]
                        merged_url = item["url"] if item["url"] is not None else existing["url"]
                        merged_dq = item["data_quality"]
                        merged_qi = quality_issues_json
                        merged_likes = item["likes_count"] if item["likes_count"] is not None else existing["likes_count"]
                        merged_comments = item["comments_count"] if item["comments_count"] is not None else existing["comments_count"]
                        merged_shares = item["shares_count"] if item["shares_count"] is not None else existing["shares_count"]
                        merged_views = item["views_count"] if item["views_count"] is not None else existing["views_count"]
                        merged_price = item["price"] if item["price"] is not None else existing["price"]
                        merged_sold = item["sold_count"] if item["sold_count"] is not None else existing["sold_count"]
                        merged_rating = item["rating"] if item["rating"] is not None else existing["rating"]
                        force_update = True
                    else:
                        merged_cg = item["content_group"] if item["content_group"] is not None else existing["content_group"]
                        merged_pi = item["purchase_intent"] if item["purchase_intent"] is not None else existing["purchase_intent"]
                        merged_title = item["title"] if item["title"] is not None else existing["title"]
                        merged_text = item["text_content"] if item["text_content"] is not None else existing["text_content"]
                        merged_svi = item["summary_vi"] if item["summary_vi"] is not None else existing["summary_vi"]
                        merged_url = item["url"] if item["url"] is not None else existing["url"]
                        merged_dq = item["data_quality"] if item["data_quality"] is not None else existing["data_quality"]
                        merged_qi = quality_issues_json if item["quality_issues"] else (existing["quality_issues_json"] or "[]")
                        merged_likes = item["likes_count"] if item["likes_count"] is not None else existing["likes_count"]
                        merged_comments = item["comments_count"] if item["comments_count"] is not None else existing["comments_count"]
                        merged_shares = item["shares_count"] if item["shares_count"] is not None else existing["shares_count"]
                        merged_views = item["views_count"] if item["views_count"] is not None else existing["views_count"]
                        merged_price = item["price"] if item["price"] is not None else existing["price"]
                        merged_sold = item["sold_count"] if item["sold_count"] is not None else existing["sold_count"]
                        merged_rating = item["rating"] if item["rating"] is not None else existing["rating"]
                        force_update = any(
                            [
                                merged_cg != existing["content_group"],
                                merged_pi != existing["purchase_intent"],
                                merged_title != existing["title"],
                                merged_text != existing["text_content"],
                                merged_svi != existing["summary_vi"],
                                merged_url != existing["url"],
                                merged_dq != existing["data_quality"],
                                merged_qi != (existing["quality_issues_json"] or "[]"),
                                merged_likes != existing["likes_count"],
                                merged_comments != existing["comments_count"],
                                merged_shares != existing["shares_count"],
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
                            SET title = ?, text_content = ?, summary_vi = ?, url = ?,
                                language = COALESCE(?, language), published_at = COALESCE(?, published_at),
                                content_group = ?, purchase_intent = ?,
                                likes_count = ?, comments_count = ?, shares_count = ?, views_count = ?,
                                price = ?, sold_count = ?, rating = ?,
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
                                item["language"],
                                item["published_at"],
                                merged_cg,
                                merged_pi,
                                merged_likes,
                                merged_comments,
                                merged_shares,
                                merged_views,
                                merged_price,
                                merged_sold,
                                merged_rating,
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

        if file_role not in ("data_contract", "crawl_strategy", "quality_audit") and has_muse_report_sections(raw_obj):
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
        search: Optional[str] = None,
        sort_by: str = "recent",
        affiliate_only: bool = False,
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

        if affiliate_only:
            clauses.append("(m.data_quality IS NULL OR m.data_quality != 'needs_verification')")

        if search and search.strip():
            like = f"%{search.strip()}%"
            clauses.append(
                "(m.title LIKE ? OR m.text_content LIKE ? OR m.summary_vi LIKE ? OR m.author_handle LIKE ? "
                "OR m.post_id LIKE ? OR m.item_id LIKE ? OR m.extra_meta_json LIKE ?)"
            )
            params.extend([like, like, like, like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        lim = max(1, min(int(limit), 200))
        off = max(0, int(offset))

        sort_map = {
            "engagement": "COALESCE(m.views_count, 0) + (COALESCE(m.likes_count, 0) * 10) + (COALESCE(m.comments_count, 0) * 20) DESC, m.rowid DESC",
            "views": "COALESCE(m.views_count, -1) DESC, COALESCE(m.likes_count, -1) DESC, m.rowid DESC",
            "intent": (
                "CASE m.purchase_intent WHEN 'cao' THEN 4 WHEN 'trung_binh' THEN 3 WHEN 'thap' THEN 2 WHEN 'khong_ro' THEN 1 ELSE 0 END DESC, "
                "COALESCE(m.likes_count, 0) + COALESCE(m.comments_count, 0) DESC, m.rowid DESC"
            ),
            "recent": "m.is_corrected DESC, COALESCE(m.published_at, m.crawled_at, m.first_seen_at) DESC, m.rowid DESC",
        }
        order_sql = sort_map.get(sort_by or "recent", sort_map["recent"])

        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS c FROM market_items m {where_sql}",
                params,
            ).fetchone()["c"]

            rows = self._conn.execute(
                f"""
                SELECT m.id, m.dedup_key, m.platform, m.external_id, m.post_id, m.item_id, m.shop_id,
                       m.author_handle, m.author_name, m.title, m.text_content, m.summary_vi, m.url,
                       m.content_group, m.purchase_intent, m.language, m.published_at, m.crawled_at,
                       m.likes_count, m.comments_count, m.shares_count, m.views_count, m.price, m.sold_count, m.rating,
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

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total_items = self._conn.execute("SELECT COUNT(*) AS c FROM market_items").fetchone()["c"]
            total_raw_files = self._conn.execute(
                "SELECT COUNT(*) AS c FROM raw_drive_files WHERE status = 'synced'"
            ).fetchone()["c"]

            by_platform = {"threads": 0, "tiktok": 0, "shopee": 0}
            for r in self._conn.execute("SELECT platform, COUNT(*) AS c FROM market_items GROUP BY platform").fetchall():
                by_platform[r["platform"]] = r["c"]

            by_quality = {"usable": 0, "needs_verification": 0, "unlabeled": 0}
            for r in self._conn.execute(
                "SELECT COALESCE(data_quality, 'unlabeled') AS dq, COUNT(*) AS c FROM market_items GROUP BY dq"
            ).fetchall():
                key = r["dq"] if r["dq"] in by_quality else "unlabeled"
                by_quality[key] = r["c"]

            by_platform_quality: Dict[str, Dict[str, int]] = {
                p: {"total": 0, "usable": 0, "needs_verification": 0, "unlabeled": 0}
                for p in ("threads", "tiktok", "shopee")
            }
            for r in self._conn.execute(
                "SELECT platform, COALESCE(data_quality, 'unlabeled') AS dq, COUNT(*) AS c FROM market_items GROUP BY platform, dq"
            ).fetchall():
                plat = r["platform"]
                if plat not in by_platform_quality:
                    by_platform_quality[plat] = {"total": 0, "usable": 0, "needs_verification": 0, "unlabeled": 0}
                dq = r["dq"] if r["dq"] in ("usable", "needs_verification") else "unlabeled"
                by_platform_quality[plat][dq] += r["c"]
                by_platform_quality[plat]["total"] += r["c"]

            # Interaction completeness (never treats null as 0)
            eng_row = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN likes_count IS NOT NULL THEN 1 ELSE 0 END) AS with_likes,
                    SUM(CASE WHEN comments_count IS NOT NULL THEN 1 ELSE 0 END) AS with_comments,
                    SUM(CASE WHEN views_count IS NOT NULL THEN 1 ELSE 0 END) AS with_views,
                    SUM(CASE WHEN shares_count IS NOT NULL THEN 1 ELSE 0 END) AS with_shares,
                    SUM(CASE WHEN price IS NOT NULL THEN 1 ELSE 0 END) AS with_price
                FROM market_items
                """
            ).fetchone()
            denom = max(1, total_items)
            engagement_completeness = {
                "total_items": total_items,
                "with_likes": eng_row["with_likes"] or 0,
                "with_comments": eng_row["with_comments"] or 0,
                "with_views": eng_row["with_views"] or 0,
                "with_shares": eng_row["with_shares"] or 0,
                "with_price": eng_row["with_price"] or 0,
                "likes_pct": round(((eng_row["with_likes"] or 0) * 100.0) / denom, 1) if total_items else None,
                "comments_pct": round(((eng_row["with_comments"] or 0) * 100.0) / denom, 1) if total_items else None,
                "views_pct": round(((eng_row["with_views"] or 0) * 100.0) / denom, 1) if total_items else None,
            }

            by_content_group = {}
            for r in self._conn.execute(
                "SELECT COALESCE(content_group, '(không có)') AS k, COUNT(*) AS c FROM market_items GROUP BY k ORDER BY c DESC"
            ).fetchall():
                by_content_group[r["k"]] = r["c"]

            by_purchase_intent = {}
            for r in self._conn.execute(
                "SELECT COALESCE(purchase_intent, '(không có)') AS k, COUNT(*) AS c FROM market_items GROUP BY k ORDER BY c DESC"
            ).fetchall():
                by_purchase_intent[r["k"]] = r["c"]

            # Upcoming vouchers from latest Shopee file run_meta_json
            upcoming_vouchers: List[Any] = []
            for r in self._conn.execute(
                """
                SELECT file_name, is_corrected, run_meta_json
                FROM raw_drive_files
                WHERE file_role = 'crawl_data' AND lower(file_name) LIKE '%shopee%'
                ORDER BY is_corrected DESC, COALESCE(modified_time, synced_at) DESC
                LIMIT 3
                """
            ).fetchall():
                try:
                    meta = json.loads(r["run_meta_json"] or "{}")
                    if isinstance(meta.get("upcoming_vouchers"), list):
                        upcoming_vouchers = meta["upcoming_vouchers"]
                        break
                except Exception:
                    pass

            non_crawl_files = [
                {
                    "file_name": r["file_name"],
                    "file_role": r["file_role"],
                    "synced_at": r["synced_at"],
                }
                for r in self._conn.execute(
                    "SELECT file_name, file_role, synced_at FROM raw_drive_files WHERE file_role IN ('data_contract', 'crawl_strategy', 'quality_audit') ORDER BY synced_at DESC"
                ).fetchall()
            ]

            latest_run = self.get_latest_sync_run()

        return {
            "total_items": total_items,
            "total_raw_files": total_raw_files,
            "by_platform": by_platform,
            "by_quality": by_quality,
            "by_platform_quality": by_platform_quality,
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
        with self._lock:
            platforms = [
                r["platform"]
                for r in self._conn.execute("SELECT DISTINCT platform FROM market_items ORDER BY platform").fetchall()
                if r["platform"]
            ]
            content_groups = [
                r["content_group"]
                for r in self._conn.execute(
                    "SELECT DISTINCT content_group FROM market_items WHERE content_group IS NOT NULL ORDER BY content_group"
                ).fetchall()
            ]
            purchase_intents = [
                r["purchase_intent"]
                for r in self._conn.execute(
                    "SELECT DISTINCT purchase_intent FROM market_items WHERE purchase_intent IS NOT NULL ORDER BY purchase_intent"
                ).fetchall()
            ]
        return {
            "platforms": platforms,
            "content_groups": content_groups,
            "purchase_intents": purchase_intents,
        }

    def list_raw_files(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM raw_drive_files ORDER BY is_corrected DESC, synced_at DESC LIMIT ?",
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
                 technical_status, data_quality_status, business_usable_records, source_priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def list_reports(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return reports ordered by newest run first, preferring corrected only within the same run/request."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT mr.id, mr.report_key, mr.source_type, mr.title, mr.platform, mr.raw_file_id, mr.report_json,
                       mr.generated_at, mr.updated_at, mr.is_corrected, mr.corrects_report, mr.request_id, mr.run_id,
                       mr.technical_status, mr.data_quality_status, mr.business_usable_records, mr.source_priority,
                       r.modified_time AS raw_modified_time
                FROM market_reports mr
                LEFT JOIN raw_drive_files r ON mr.raw_file_id = r.file_id
                ORDER BY COALESCE(mr.generated_at, r.modified_time, mr.updated_at) DESC, mr.updated_at DESC
                LIMIT 100
                """
            ).fetchall()

        out: List[Dict[str, Any]] = []
        file_name_to_group: Dict[str, str] = {}
        for r in rows:
            d = dict(r)
            d["is_corrected"] = bool(d.get("is_corrected"))
            try:
                d["report"] = json.loads(d.pop("report_json") or "{}")
            except Exception:
                d["report"] = {}
            fname = str((d["report"] or {}).get("file_name") or "").strip().lower()
            grp = str(d.get("request_id") or d.get("run_id") or d.get("report_key"))
            if fname:
                file_name_to_group[fname] = grp
            d["_group_key"] = grp
            d["_item_ts"] = max(
                self._parse_iso_ts(d.get("generated_at")),
                self._parse_iso_ts(d.get("raw_modified_time")),
                self._parse_iso_ts(d.get("updated_at")),
            )
            out.append(d)

        # Resolve corrects_report linkage into the same run group
        for d in out:
            corr_target = str(d.get("corrects_report") or "").strip().lower()
            if corr_target and corr_target in file_name_to_group:
                d["_group_key"] = file_name_to_group[corr_target]

        group_latest_ts: Dict[str, float] = {}
        for d in out:
            gk = d["_group_key"]
            group_latest_ts[gk] = max(group_latest_ts.get(gk, 0.0), d["_item_ts"])

        out.sort(
            key=lambda d: (
                group_latest_ts.get(d["_group_key"], 0.0),
                int(d.get("source_priority") or 1),
                1 if d.get("is_corrected") else 0,
                d["_item_ts"],
            ),
            reverse=True,
        )
        for d in out:
            d.pop("_group_key", None)
            d.pop("_item_ts", None)
            d.pop("raw_modified_time", None)
        return out[: max(1, min(int(limit), 100))]

    def build_evidence_report(self, platform: str = "all") -> Dict[str, Any]:
        """
        Build canonical 5-section report prioritizing the latest corrected Muse report
        (falling back to local evidence only when no valid Muse report matches).
        """
        plat = (platform or "all").lower()
        res = self.query_items(platform=plat, limit=250, offset=0)
        evidence = build_evidence_report_from_items(res["items"], platform=plat)

        stored = self.list_reports(limit=20)
        # Prefer corrected Muse report matching requested scope (or latest corrected overall report)
        chosen = None
        for entry in stored:
            rep = entry.get("report") or {}
            rep_plat = (rep.get("platform") or "").lower()
            has_core = bool(rep.get("telegram_summary") or rep.get("platform_results") or rep.get("trending_highlights"))
            if not has_core:
                continue
            if plat == "all" or rep_plat == plat or rep_plat == "all":
                chosen = rep
                break
        if not chosen and stored:
            chosen = stored[0].get("report")

        if chosen:
            evidence["latest_muse_report"] = chosen
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
            evidence["platform_results"] = chosen.get("platform_results") or {}
            evidence["totals"] = chosen.get("totals") or {}
            evidence["is_corrected"] = bool(chosen.get("is_corrected"))
            if chosen.get("source_label"):
                evidence["source_label"] = f"{chosen['source_label']} + Dữ liệu chuẩn hóa"
            if chosen.get("trending_highlights") and not evidence["trending_highlights"]:
                evidence["trending_highlights"] = chosen["trending_highlights"]
            if chosen.get("consumer_needs_problems") and not evidence["consumer_needs_problems"]:
                evidence["consumer_needs_problems"] = chosen["consumer_needs_problems"]
            if chosen.get("content_suggestions", {}).get("text_posts"):
                evidence["content_suggestions"]["text_posts"] = chosen["content_suggestions"]["text_posts"]
            if chosen.get("content_suggestions", {}).get("short_videos"):
                evidence["content_suggestions"]["short_videos"] = chosen["content_suggestions"]["short_videos"]
        return evidence
