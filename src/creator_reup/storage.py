"""Isolated SQLite storage for Creator Crawler & Phase 1 Batch Reup."""

from contextlib import contextmanager
import hashlib
import json
import sqlite3
import threading
import uuid
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .filters import evaluate_video_filters, sort_and_filter_videos
from .models import (
    CREATOR_BLOCKED_STOP_REASONS,
    CREATOR_SCHEMA_VERSION,
    RIGHTS_STATUSES,
    SUPPORTED_PLATFORMS,
    canonicalize_video_url,
    normalize_platform,
    sanitize_job_config,
    utc_now_iso,
    verify_local_mp4_output,
)
from .parser import CreatorParseError, is_acceptance_sample, normalize_item_metrics, parse_creator_crawl_payload


class CreatorRequestCorrelationError(ValueError):
    """Raised when imported JSON request_id/crawl_id/platform/creator correlation fails."""


def _norm_profile_url(url: Any) -> str:
    return str(url or "").strip().rstrip("/").lower()


class CreatorReupStore:
    """Thread-safe isolated SQLite repository under `data/creator_reup/`.

    NOTE: Multi-laptop dedup is NOT yet active in Phase 1 (requires dedicated Reup D1
    endpoints on Worker in Phase 2). Current dedup is atomic within this local SQLite DB.
    """

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            data_dir = os.path.join(base_dir, "data", "creator_reup")
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "creator_reup.db")
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self, *, manual_tx: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None if manual_tx else "",
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            if not manual_tx:
                conn.commit()
        except Exception:
            if not manual_tx:
                conn.rollback()
            raise
        finally:
            conn.close()

    def close(self) -> None:
        """Lifecycle hook; connections are short-lived and closed after each operation."""
        return None

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS creator_sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creator_crawl_requests (
                    request_id TEXT PRIMARY KEY,
                    crawl_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    profile_url TEXT NOT NULL,
                    creator_id TEXT,
                    requested_count INTEGER NOT NULL DEFAULT 20,
                    filters_json TEXT NOT NULL,
                    drive_folder_id TEXT,
                    prompt_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'awaiting_muse',
                    matched_file_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creator_raw_files (
                    file_id TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    source_kind TEXT NOT NULL DEFAULT 'manual',
                    drive_folder_id TEXT,
                    modified_time TEXT,
                    md5_checksum TEXT,
                    sha256_checksum TEXT NOT NULL,
                    request_id TEXT,
                    crawl_id TEXT,
                    platform TEXT,
                    schema_version TEXT,
                    items_parsed INTEGER NOT NULL DEFAULT 0,
                    notes_excluded INTEGER NOT NULL DEFAULT 0,
                    videos_inserted INTEGER NOT NULL DEFAULT 0,
                    videos_deduped INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'synced',
                    error_message TEXT,
                    imported_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creator_profiles (
                    profile_key TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    creator_handle TEXT,
                    creator_name TEXT,
                    profile_url TEXT NOT NULL,
                    followers_display TEXT,
                    followers INTEGER,
                    request_id TEXT,
                    last_crawled_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creator_videos (
                    video_key TEXT PRIMARY KEY,
                    source_platform TEXT NOT NULL,
                    source_video_id TEXT NOT NULL,
                    video_id_secondary TEXT,
                    secondary_video_key TEXT,
                    creator_key TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    creator_name TEXT,
                    request_id TEXT,
                    crawl_id TEXT,
                    raw_file_id TEXT,
                    video_url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT 'video',
                    title TEXT,
                    thumbnail TEXT,
                    views INTEGER,
                    views_display TEXT,
                    effective_views INTEGER,
                    likes INTEGER,
                    likes_display TEXT,
                    effective_likes INTEGER,
                    metrics_precision TEXT NOT NULL DEFAULT 'exact',
                    comments INTEGER,
                    shares INTEGER,
                    favorites INTEGER,
                    duration_seconds INTEGER,
                    published_at TEXT,
                    published_at_display TEXT,
                    registry_status TEXT NOT NULL DEFAULT 'unknown',
                    filter_status TEXT NOT NULL DEFAULT 'filtered_out',
                    passed_filter INTEGER NOT NULL DEFAULT 0,
                    age_exception_applied INTEGER NOT NULL DEFAULT 0,
                    exclusion_reasons_json TEXT NOT NULL DEFAULT '[]',
                    needs_verification INTEGER NOT NULL DEFAULT 0,
                    verification_reasons_json TEXT NOT NULL DEFAULT '[]',
                    applied_filters_json TEXT NOT NULL DEFAULT '{}',
                    rights_status TEXT NOT NULL DEFAULT 'pending_review',
                    rights_note TEXT,
                    rights_updated_at TEXT,
                    dedup_status TEXT NOT NULL DEFAULT 'new',
                    matched_job_id TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS creator_reup_jobs (
                    job_id TEXT PRIMARY KEY,
                    video_key TEXT NOT NULL,
                    secondary_video_key TEXT,
                    source_platform TEXT NOT NULL,
                    source_video_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    output_filename TEXT,
                    output_path TEXT,
                    caption TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )
            self._ensure_column(conn, "creator_videos", "secondary_video_key", "TEXT")
            self._ensure_column(conn, "creator_reup_jobs", "secondary_video_key", "TEXT")
            self._ensure_column(conn, "creator_raw_files", "result_json", "TEXT")
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_cv_platform_filter
                    ON creator_videos (source_platform, filter_status, rights_status, dedup_status);
                CREATE INDEX IF NOT EXISTS idx_cv_creator
                    ON creator_videos (creator_key, request_id);
                CREATE INDEX IF NOT EXISTS idx_cv_canonical_url
                    ON creator_videos (canonical_url);
                CREATE INDEX IF NOT EXISTS idx_cv_secondary_key
                    ON creator_videos (secondary_video_key);

                CREATE INDEX IF NOT EXISTS idx_crj_video_status
                    ON creator_reup_jobs (video_key, status);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_crj_active_video_key
                    ON creator_reup_jobs (video_key)
                    WHERE status IN ('queued', 'running', 'completed');
                CREATE UNIQUE INDEX IF NOT EXISTS uq_crj_active_canonical_url
                    ON creator_reup_jobs (canonical_url)
                    WHERE status IN ('queued', 'running', 'completed');
                CREATE UNIQUE INDEX IF NOT EXISTS uq_crj_active_secondary_key
                    ON creator_reup_jobs (secondary_video_key)
                    WHERE secondary_video_key IS NOT NULL AND status IN ('queued', 'running', 'completed');
                """
            )

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM creator_sync_state WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: Optional[str]) -> None:
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO creator_sync_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, "" if value is None else str(value), now),
            )

    def is_file_unchanged(
        self,
        file_id: str,
        *,
        modified_time: Optional[str] = None,
        md5_checksum: Optional[str] = None,
        sha256_checksum: Optional[str] = None,
    ) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT modified_time, md5_checksum, sha256_checksum, status FROM creator_raw_files WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            if not row or row["status"] == "error":
                return False
            if sha256_checksum and row["sha256_checksum"] == sha256_checksum:
                return True
            if md5_checksum and row["md5_checksum"] == md5_checksum:
                return True
            if modified_time and row["modified_time"] == modified_time and not sha256_checksum and not md5_checksum:
                return True
            return False

    def mark_ignored_remote_file(
        self,
        *,
        file_id: str,
        file_name: str,
        modified_time: Optional[str],
        md5_checksum: Optional[str],
        sha256_checksum: str,
        folder_path: Optional[str] = None,
    ) -> None:
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO creator_raw_files (
                    file_id, file_name, source_kind, drive_folder_id, modified_time,
                    md5_checksum, sha256_checksum, status, imported_at
                ) VALUES (?, ?, 'drive', ?, ?, ?, ?, 'ignored_non_creator', ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    file_name = excluded.file_name,
                    modified_time = excluded.modified_time,
                    md5_checksum = excluded.md5_checksum,
                    sha256_checksum = excluded.sha256_checksum,
                    status = 'ignored_non_creator',
                    imported_at = excluded.imported_at
                """,
                (file_id, file_name, folder_path, modified_time, md5_checksum, sha256_checksum, now),
            )

    def create_crawl_request(self, request_spec: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now_iso()
        filters_json = json.dumps(request_spec.get("filters") or {}, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO creator_crawl_requests (
                    request_id, crawl_id, platform, profile_url, creator_id,
                    requested_count, filters_json, drive_folder_id, prompt_text,
                    status, matched_file_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_muse', 0, ?, ?)
                """,
                (
                    request_spec["request_id"],
                    request_spec["crawl_id"],
                    request_spec["platform"],
                    request_spec["profile_url"],
                    request_spec.get("creator_id"),
                    int(request_spec.get("requested_count") or 20),
                    filters_json,
                    request_spec.get("drive_folder_id"),
                    request_spec["prompt_text"],
                    now,
                    now,
                ),
            )
        return self.get_crawl_request(request_spec["request_id"]) or request_spec

    def get_crawl_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """SELECT r.*, (
                       SELECT f.result_json FROM creator_raw_files f
                       WHERE f.request_id = r.request_id AND f.result_json IS NOT NULL
                       ORDER BY f.imported_at DESC, f.rowid DESC LIMIT 1
                   ) AS result_json FROM creator_crawl_requests r WHERE r.request_id = ?""",
                (request_id,),
            ).fetchone()
            return self._row_to_request(row) if row else None

    def list_crawl_requests(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT r.*, (
                       SELECT f.result_json FROM creator_raw_files f
                       WHERE f.request_id = r.request_id AND f.result_json IS NOT NULL
                       ORDER BY f.imported_at DESC, f.rowid DESC LIMIT 1
                   ) AS result_json FROM creator_crawl_requests r ORDER BY r.created_at DESC LIMIT ?""",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            return [self._row_to_request(r) for r in rows]

    def has_pending_crawl_requests(self) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM creator_crawl_requests WHERE status = 'awaiting_muse' LIMIT 1"
            ).fetchone()
            return row is not None

    def _row_to_request(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        try:
            d["filters"] = json.loads(d.pop("filters_json") or "{}")
        except json.JSONDecodeError:
            d["filters"] = {}
        result_json = d.pop("result_json", None)
        if result_json:
            d["last_result"] = json.loads(result_json)
        return d

    def _assert_request_matches_bundle(
        self,
        req: Dict[str, Any],
        *,
        platform: str,
        creator_ids: List[str],
        profile_urls: List[str],
        explicit_crawl_id: Optional[str],
        muse_crawl_id: bool = False,
    ) -> None:
        if explicit_crawl_id and not muse_crawl_id and req.get("crawl_id") != explicit_crawl_id:
            raise CreatorRequestCorrelationError(
                f"crawl_id không khớp với request_id {req['request_id']}: nhận {explicit_crawl_id!r}, cần {req.get('crawl_id')!r}."
            )
        if req.get("platform") != platform:
            raise CreatorRequestCorrelationError(
                f"platform ({platform}) không khớp request {req['request_id']} ({req.get('platform')})."
            )
        req_creator = str(req.get("creator_id") or "").strip()
        req_prof = _norm_profile_url(req.get("profile_url"))
        norm_bundle_urls = {_norm_profile_url(u) for u in profile_urls if _norm_profile_url(u)}
        clean_creator_ids = {str(cid).strip() for cid in creator_ids if str(cid).strip()}
        if req_creator and clean_creator_ids and req_creator not in clean_creator_ids:
            raise CreatorRequestCorrelationError(
                f"creator_id trong JSON ({sorted(clean_creator_ids)}) không khớp request {req['request_id']} ({req_creator})."
            )
        same_creator = req_creator and req_creator in clean_creator_ids
        if req_prof and norm_bundle_urls and req_prof not in norm_bundle_urls and not (muse_crawl_id and same_creator):
            raise CreatorRequestCorrelationError(
                f"profile_url trong JSON không khớp request {req['request_id']}."
            )

    def _find_matching_request(
        self,
        conn: sqlite3.Connection,
        *,
        platform: str,
        source_format: str,
        explicit_request_id: Optional[str],
        explicit_crawl_id: Optional[str],
        drive_folder_id: Optional[str],
        creator_ids: List[str],
        creator_profile_urls: List[str],
        muse_crawl_id: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Resolve matching crawl request strictly without falling back across mismatched IDs."""
        if explicit_request_id:
            row = conn.execute(
                "SELECT * FROM creator_crawl_requests WHERE request_id = ?",
                (explicit_request_id,),
            ).fetchone()
            if not row:
                raise CreatorRequestCorrelationError(f"Không tìm thấy request_id={explicit_request_id!r} (không fallback sang request khác).")
            req = self._row_to_request(row)
            self._assert_request_matches_bundle(
                req,
                platform=platform,
                creator_ids=creator_ids,
                profile_urls=creator_profile_urls,
                explicit_crawl_id=explicit_crawl_id,
                muse_crawl_id=muse_crawl_id,
            )
            if muse_crawl_id:
                conflicting = conn.execute(
                    """
                    SELECT 1 FROM creator_raw_files WHERE crawl_id = ? AND request_id != ?
                    UNION ALL
                    SELECT 1 FROM creator_crawl_requests WHERE crawl_id = ? AND request_id != ?
                    LIMIT 1
                    """,
                    (explicit_crawl_id, explicit_request_id, explicit_crawl_id, explicit_request_id),
                ).fetchone()
                if conflicting:
                    raise CreatorRequestCorrelationError("crawl_id đã được gắn với request khác.")
            return req

        if explicit_crawl_id:
            row = conn.execute(
                "SELECT * FROM creator_crawl_requests WHERE crawl_id = ?",
                (explicit_crawl_id,),
            ).fetchone()
            if not row:
                raise CreatorRequestCorrelationError(f"Không tìm thấy crawl_id={explicit_crawl_id!r} (không fallback sang request khác).")
            req = self._row_to_request(row)
            self._assert_request_matches_bundle(
                req,
                platform=platform,
                creator_ids=creator_ids,
                profile_urls=creator_profile_urls,
                explicit_crawl_id=explicit_crawl_id,
            )
            return req

        # Discovery sample payloads must NEVER auto-pair or close production requests.
        if source_format != "muse_v1":
            return None

        if drive_folder_id:
            rows = conn.execute(
                """
                SELECT * FROM creator_crawl_requests
                WHERE drive_folder_id = ? AND platform = ? AND status = 'awaiting_muse'
                ORDER BY created_at DESC
                """,
                (drive_folder_id, platform),
            ).fetchall()
            for r in rows:
                req = self._row_to_request(r)
                try:
                    self._assert_request_matches_bundle(
                        req,
                        platform=platform,
                        creator_ids=creator_ids,
                        profile_urls=creator_profile_urls,
                        explicit_crawl_id=None,
                    )
                    return req
                except CreatorRequestCorrelationError:
                    continue

        for url in creator_profile_urls:
            if not url:
                continue
            rows = conn.execute(
                """
                SELECT * FROM creator_crawl_requests
                WHERE platform = ? AND status = 'awaiting_muse'
                ORDER BY created_at DESC
                """,
                (platform,),
            ).fetchall()
            norm_u = _norm_profile_url(url)
            for r in rows:
                req = self._row_to_request(r)
                if _norm_profile_url(req.get("profile_url")) != norm_u:
                    continue
                try:
                    self._assert_request_matches_bundle(
                        req,
                        platform=platform,
                        creator_ids=creator_ids,
                        profile_urls=creator_profile_urls,
                        explicit_crawl_id=None,
                    )
                    return req
                except CreatorRequestCorrelationError:
                    continue
        return None

    def list_profiles(self, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if platform and normalize_platform(platform):
                rows = conn.execute(
                    "SELECT * FROM creator_profiles WHERE platform = ? ORDER BY updated_at DESC",
                    (normalize_platform(platform),),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM creator_profiles ORDER BY updated_at DESC").fetchall()
            return [dict(r) for r in rows]

    def _alias_keys_for_item(
        self,
        *,
        video_key: str,
        canonical_url: str,
        secondary_video_key: Optional[str],
        platform: str,
    ) -> Tuple[List[str], List[str]]:
        keys = [video_key]
        urls = [canonical_url]
        if secondary_video_key and secondary_video_key not in keys:
            keys.append(secondary_video_key)
            sec_id = secondary_video_key.split(":", 1)[1] if ":" in secondary_video_key else secondary_video_key
            sec_url = canonicalize_video_url("", platform=platform, video_id=sec_id)
            if sec_url and sec_url not in urls:
                urls.append(sec_url)
        return keys, urls

    def _find_existing_video_conn(
        self,
        conn: sqlite3.Connection,
        *,
        keys: List[str],
        urls: List[str],
    ) -> Optional[sqlite3.Row]:
        key_ph = ",".join("?" for _ in keys)
        url_ph = ",".join("?" for _ in urls)
        return conn.execute(
            f"""
            SELECT video_key, secondary_video_key, canonical_url, rights_status, rights_note,
                   rights_updated_at, dedup_status, matched_job_id
            FROM creator_videos
            WHERE video_key IN ({key_ph})
               OR secondary_video_key IN ({key_ph})
               OR canonical_url IN ({url_ph})
            LIMIT 1
            """,
            (*keys, *keys, *urls),
        ).fetchone()

    def _find_competing_job_conn(
        self,
        conn: sqlite3.Connection,
        *,
        keys: List[str],
        urls: List[str],
        exclude_job_id: Optional[str] = None,
    ) -> Optional[sqlite3.Row]:
        key_ph = ",".join("?" for _ in keys)
        url_ph = ",".join("?" for _ in urls)
        excl_sql = "AND job_id != ?" if exclude_job_id else ""
        params: List[Any] = [*keys, *keys, *urls]
        if exclude_job_id:
            params.append(exclude_job_id)
        return conn.execute(
            f"""
            SELECT job_id, status FROM creator_reup_jobs
            WHERE (video_key IN ({key_ph})
                   OR secondary_video_key IN ({key_ph})
                   OR canonical_url IN ({url_ph}))
              AND status IN ('completed', 'running', 'queued')
              {excl_sql}
            ORDER BY CASE status WHEN 'completed' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,
                     created_at DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()

    def ingest_creator_json(
        self,
        *,
        raw_payload: Any,
        file_name: str = "manual_import.json",
        file_id: Optional[str] = None,
        source_kind: str = "manual",
        modified_time: Optional[str] = None,
        md5_checksum: Optional[str] = None,
        drive_folder_id: Optional[str] = None,
        request_id: Optional[str] = None,
        crawl_id: Optional[str] = None,
        filter_overrides: Optional[Dict[str, Any]] = None,
        reference_time: Any = None,
    ) -> Dict[str, Any]:
        """Parse, correlate strictly with request, evaluate filters, and dedup idempotently."""
        if isinstance(raw_payload, (bytes, bytearray)):
            raw_bytes = bytes(raw_payload)
        elif isinstance(raw_payload, str):
            raw_bytes = raw_payload.encode("utf-8")
        else:
            raw_bytes = json.dumps(raw_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")

        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        effective_file_id = str(file_id or f"{source_kind}:{sha256[:24]}").strip()

        bundle = parse_creator_crawl_payload(raw_bytes)
        if source_kind == "drive" and bundle["source_format"] != "muse_v1":
            raise CreatorParseError(
                "JSON discovery sample chỉ dùng để import thủ công, không tự đồng bộ từ Drive."
            )
        result_meta = {
            "schema_version": bundle["schema_version"],
            "crawl_id": bundle.get("crawl_id"),
            "stop_reason": bundle.get("stop_reason"),
            "crawl": bundle.get("crawl", {}),
            "stats": bundle["stats"],
        }
        if is_acceptance_sample(bundle) or bundle["source_format"] == "discovery_sample":
            sample_items = [
                evaluate_video_filters(
                    item,
                    filter_overrides=filter_overrides if filter_overrides is not None else bundle["filters_hint"],
                    reference_time=bundle["reference_time"],
                )
                for item in bundle["items"]
            ]
            return {
                **result_meta,
                "acceptance_only": True,
                "source_format": bundle["source_format"],
                "platform": bundle["platform"],
                "request_id": bundle["request_id"],
                "valid_videos_count": len(sample_items),
                "notes_excluded": bundle["stats"]["notes_excluded"],
                "videos_inserted": 0,
                "videos_deduped": 0,
                "passed_count": sum(item["passed_filter"] for item in sample_items),
                "needs_verification_count": sum(item["needs_verification"] for item in sample_items),
            }

        for key, supplied in (("request_id", request_id), ("crawl_id", crawl_id)):
            if supplied and bundle.get(key) and supplied != bundle[key]:
                raise CreatorRequestCorrelationError(f"{key} nhập tay không khớp JSON.")

        with self._lock:
            if self.is_file_unchanged(
                effective_file_id,
                modified_time=modified_time,
                md5_checksum=md5_checksum,
                sha256_checksum=sha256,
            ):
                with self._connect() as conn:
                    existing_file = conn.execute(
                        "SELECT * FROM creator_raw_files WHERE file_id = ?",
                        (effective_file_id,),
                    ).fetchone()
                    return {
                        **(json.loads(existing_file["result_json"]) if existing_file and existing_file["result_json"] else result_meta),
                        "idempotent_skip": True,
                        "file_id": effective_file_id,
                        "file_name": file_name,
                        "sha256_checksum": sha256,
                        "request_id": existing_file["request_id"] if existing_file else request_id,
                        "crawl_id": existing_file["crawl_id"] if existing_file else crawl_id,
                        "valid_videos_count": existing_file["items_parsed"] if existing_file else 0,
                        "notes_excluded": existing_file["notes_excluded"] if existing_file else 0,
                        "videos_inserted": 0,
                        "videos_deduped": existing_file["videos_deduped"] if existing_file else 0,
                    }

            platform = bundle["platform"]
            eff_req_id = request_id or bundle.get("request_id")
            eff_crawl_id = crawl_id or bundle.get("crawl_id")
            eff_folder_id = drive_folder_id or bundle.get("drive_folder_id")
            ref_time = reference_time or bundle.get("reference_time")
            now = utc_now_iso()

            creator_ids = [c.get("creator_id") or "" for c in bundle["creators"]]
            creator_urls = [c.get("profile_url") or "" for c in bundle["creators"]]
            muse_crawl_id = bundle["schema_version"] == CREATOR_SCHEMA_VERSION

            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                matched_req = self._find_matching_request(
                    conn,
                    platform=platform,
                    source_format=bundle["source_format"],
                    explicit_request_id=eff_req_id,
                    explicit_crawl_id=eff_crawl_id,
                    drive_folder_id=eff_folder_id,
                    creator_ids=creator_ids,
                    creator_profile_urls=creator_urls,
                    muse_crawl_id=muse_crawl_id,
                )
                if matched_req:
                    eff_req_id = eff_req_id or matched_req["request_id"]
                    eff_crawl_id = eff_crawl_id or matched_req["crawl_id"]
                    active_filter_overrides = matched_req["filters"] if muse_crawl_id or filter_overrides is None else filter_overrides
                    if muse_crawl_id and bundle["stats"]["target_count"] != matched_req["requested_count"]:
                        raise CreatorRequestCorrelationError("target_count trong JSON không khớp request.")
                    if muse_crawl_id and source_kind == "drive" and matched_req.get("drive_folder_id") and (
                        matched_req["drive_folder_id"] != drive_folder_id
                    ):
                        raise CreatorRequestCorrelationError("Thư mục Drive của file không khớp request.")
                    if muse_crawl_id:
                        ref_time = bundle["reference_time"]
                        prior = conn.execute(
                            "SELECT request_id, crawl_id FROM creator_raw_files WHERE file_id = ? AND request_id IS NOT NULL",
                            (effective_file_id,),
                        ).fetchone()
                        if prior and (prior["request_id"], prior["crawl_id"]) != (eff_req_id, eff_crawl_id):
                            raise CreatorRequestCorrelationError("File Drive đã liên kết với kết quả crawl khác.")
                else:
                    active_filter_overrides = filter_overrides

                for c in bundle["creators"]:
                    conn.execute(
                        """
                        INSERT INTO creator_profiles (
                            profile_key, platform, creator_id, creator_handle, creator_name,
                            profile_url, followers_display, followers, request_id,
                            last_crawled_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(profile_key) DO UPDATE SET
                            creator_handle = COALESCE(excluded.creator_handle, creator_profiles.creator_handle),
                            creator_name = COALESCE(excluded.creator_name, creator_profiles.creator_name),
                            profile_url = excluded.profile_url,
                            followers_display = excluded.followers_display,
                            followers = excluded.followers,
                            request_id = COALESCE(excluded.request_id, creator_profiles.request_id),
                            last_crawled_at = excluded.last_crawled_at,
                            updated_at = excluded.updated_at
                        """,
                        (
                            c["profile_key"],
                            c["platform"],
                            c["creator_id"],
                            c.get("creator_handle"),
                            c.get("creator_name"),
                            c.get("profile_url") or "",
                            c.get("followers_display"),
                            c.get("followers"),
                            eff_req_id,
                            ref_time or now,
                            now,
                        ),
                    )

                videos_inserted = 0
                videos_deduped = 0
                passed_count = 0
                needs_verification_count = 0
                qualified_keys = set()

                for item in bundle["items"]:
                    evaluated = evaluate_video_filters(
                        item,
                        platform=platform,
                        filter_overrides=active_filter_overrides,
                        reference_time=ref_time,
                    )
                    evaluated["request_id"] = eff_req_id or evaluated.get("request_id")
                    evaluated["crawl_id"] = eff_crawl_id or evaluated.get("crawl_id")
                    if evaluated["passed_filter"]:
                        passed_count += 1
                    if evaluated["needs_verification"]:
                        needs_verification_count += 1

                    vkey = evaluated["video_key"]
                    sec_key = evaluated.get("secondary_video_key")
                    ckey = f"{platform}:{evaluated['creator_id']}"
                    canonical = canonicalize_video_url(
                        evaluated["video_url"],
                        platform=platform,
                        video_id=evaluated["source_video_id"],
                    )
                    alias_keys, alias_urls = self._alias_keys_for_item(
                        video_key=vkey,
                        canonical_url=canonical,
                        secondary_video_key=sec_key,
                        platform=platform,
                    )

                    existing_video = self._find_existing_video_conn(conn, keys=alias_keys, urls=alias_urls)
                    job_row = self._find_competing_job_conn(conn, keys=alias_keys, urls=alias_urls)

                    dedup_status = "new"
                    matched_job_id = None
                    if job_row:
                        matched_job_id = job_row["job_id"]
                        dedup_status = "duplicate_processed" if job_row["status"] == "completed" else "duplicate_in_queue"
                    elif existing_video and existing_video["dedup_status"] in ("duplicate_processed", "duplicate_in_queue", "duplicate_external"):
                        dedup_status = existing_video["dedup_status"]
                        matched_job_id = existing_video["matched_job_id"]

                    raw_item_json = json.dumps(evaluated.get("raw_item") or {}, ensure_ascii=False)
                    excl_json = json.dumps(evaluated["exclusion_reasons"], ensure_ascii=False)
                    verif_json = json.dumps(evaluated["verification_reasons"], ensure_ascii=False)
                    applied_json = json.dumps(evaluated["applied_filters"], ensure_ascii=False)

                    if existing_video:
                        videos_deduped += 1
                        merged_sec_key = sec_key or existing_video["secondary_video_key"]
                        conn.execute(
                            """
                            UPDATE creator_videos SET
                                secondary_video_key = COALESCE(?, secondary_video_key),
                                video_id_secondary = COALESCE(?, video_id_secondary),
                                creator_name = COALESCE(?, creator_name),
                                request_id = COALESCE(?, request_id),
                                crawl_id = COALESCE(?, crawl_id),
                                raw_file_id = ?,
                                title = COALESCE(?, title),
                                thumbnail = COALESCE(?, thumbnail),
                                views = ?,
                                views_display = ?,
                                effective_views = ?,
                                likes = ?,
                                likes_display = ?,
                                effective_likes = ?,
                                metrics_precision = ?,
                                comments = ?,
                                shares = ?,
                                favorites = ?,
                                duration_seconds = COALESCE(?, duration_seconds),
                                published_at = COALESCE(?, published_at),
                                published_at_display = COALESCE(?, published_at_display),
                                registry_status = ?,
                                filter_status = ?,
                                passed_filter = ?,
                                age_exception_applied = ?,
                                exclusion_reasons_json = ?,
                                needs_verification = ?,
                                verification_reasons_json = ?,
                                applied_filters_json = ?,
                                dedup_status = ?,
                                matched_job_id = COALESCE(?, matched_job_id),
                                last_seen_at = ?,
                                raw_json = ?
                            WHERE video_key = ?
                            """,
                            (
                                merged_sec_key,
                                evaluated.get("video_id_secondary"),
                                evaluated.get("creator_name"),
                                evaluated.get("request_id"),
                                evaluated.get("crawl_id"),
                                effective_file_id,
                                evaluated.get("title"),
                                evaluated.get("thumbnail"),
                                evaluated.get("views"),
                                evaluated.get("views_display"),
                                evaluated.get("effective_views"),
                                evaluated.get("likes"),
                                evaluated.get("likes_display"),
                                evaluated.get("effective_likes"),
                                evaluated.get("metrics_precision") or "exact",
                                evaluated.get("comments"),
                                evaluated.get("shares"),
                                evaluated.get("favorites"),
                                evaluated.get("duration_seconds"),
                                evaluated.get("published_at"),
                                evaluated.get("published_at_display"),
                                evaluated.get("registry_status") or "unknown",
                                evaluated["filter_status"],
                                1 if evaluated["passed_filter"] else 0,
                                1 if evaluated["age_exception_applied"] else 0,
                                excl_json,
                                1 if evaluated["needs_verification"] else 0,
                                verif_json,
                                applied_json,
                                dedup_status,
                                matched_job_id,
                                now,
                                raw_item_json,
                                existing_video["video_key"],
                            ),
                        )
                    else:
                        videos_inserted += 1
                        conn.execute(
                            """
                            INSERT INTO creator_videos (
                                video_key, source_platform, source_video_id, video_id_secondary, secondary_video_key,
                                creator_key, creator_id, creator_name, request_id, crawl_id, raw_file_id,
                                video_url, canonical_url, content_type, title, thumbnail,
                                views, views_display, effective_views, likes, likes_display, effective_likes,
                                metrics_precision, comments, shares, favorites, duration_seconds,
                                published_at, published_at_display, registry_status,
                                filter_status, passed_filter, age_exception_applied,
                                exclusion_reasons_json, needs_verification, verification_reasons_json,
                                applied_filters_json, rights_status, rights_note, rights_updated_at,
                                dedup_status, matched_job_id, first_seen_at, last_seen_at, raw_json
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'video', ?, ?,
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?, 'pending_review', '', NULL,
                                ?, ?, ?, ?, ?
                            )
                            """,
                            (
                                vkey,
                                platform,
                                evaluated["source_video_id"],
                                evaluated.get("video_id_secondary"),
                                sec_key,
                                ckey,
                                evaluated["creator_id"],
                                evaluated.get("creator_name"),
                                evaluated.get("request_id"),
                                evaluated.get("crawl_id"),
                                effective_file_id,
                                evaluated["video_url"],
                                canonical,
                                evaluated.get("title"),
                                evaluated.get("thumbnail"),
                                evaluated.get("views"),
                                evaluated.get("views_display"),
                                evaluated.get("effective_views"),
                                evaluated.get("likes"),
                                evaluated.get("likes_display"),
                                evaluated.get("effective_likes"),
                                evaluated.get("metrics_precision") or "exact",
                                evaluated.get("comments"),
                                evaluated.get("shares"),
                                evaluated.get("favorites"),
                                evaluated.get("duration_seconds"),
                                evaluated.get("published_at"),
                                evaluated.get("published_at_display"),
                                evaluated.get("registry_status") or "unknown",
                                evaluated["filter_status"],
                                1 if evaluated["passed_filter"] else 0,
                                1 if evaluated["age_exception_applied"] else 0,
                                excl_json,
                                1 if evaluated["needs_verification"] else 0,
                                verif_json,
                                applied_json,
                                dedup_status,
                                matched_job_id,
                                now,
                                now,
                                raw_item_json,
                            ),
                        )
                    resolved_key = existing_video["video_key"] if existing_video else vkey
                    if evaluated["filter_status"] == "passed":
                        qualified_keys.add(resolved_key)
                    else:
                        qualified_keys.discard(resolved_key)

                result_meta["qualified_local"] = len(qualified_keys)
                result_meta["passed_count"] = passed_count
                result_meta["needs_verification_count"] = needs_verification_count
                stop_reason = bundle.get("stop_reason")
                result_is_terminal = matched_req and stop_reason not in CREATOR_BLOCKED_STOP_REASONS
                if muse_crawl_id and matched_req and stop_reason == "target_reached":
                    result_is_terminal = len(qualified_keys) >= matched_req["requested_count"]
                    if not result_is_terminal:
                        result_meta["completion_note"] = "target_not_met_locally"
                conn.execute(
                    """
                    INSERT INTO creator_raw_files (
                        file_id, file_name, source_kind, drive_folder_id, modified_time,
                        md5_checksum, sha256_checksum, request_id, crawl_id, platform,
                        schema_version, items_parsed, notes_excluded, videos_inserted,
                        videos_deduped, status, error_message, imported_at, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced', NULL, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET
                        file_name = excluded.file_name,
                        source_kind = excluded.source_kind,
                        drive_folder_id = excluded.drive_folder_id,
                        modified_time = excluded.modified_time,
                        md5_checksum = excluded.md5_checksum,
                        sha256_checksum = excluded.sha256_checksum,
                        request_id = excluded.request_id,
                        crawl_id = excluded.crawl_id,
                        platform = excluded.platform,
                        schema_version = excluded.schema_version,
                        items_parsed = excluded.items_parsed,
                        notes_excluded = excluded.notes_excluded,
                        videos_inserted = excluded.videos_inserted,
                        videos_deduped = excluded.videos_deduped,
                        status = 'synced',
                        error_message = NULL,
                        imported_at = excluded.imported_at,
                        result_json = excluded.result_json
                    """,
                    (
                        effective_file_id,
                        file_name,
                        source_kind,
                        eff_folder_id,
                        modified_time,
                        md5_checksum,
                        sha256,
                        eff_req_id,
                        eff_crawl_id,
                        platform,
                        bundle["schema_version"],
                        len(bundle["items"]),
                        bundle["stats"]["notes_excluded"],
                        videos_inserted,
                        videos_deduped,
                        now,
                        json.dumps(result_meta, ensure_ascii=False),
                    ),
                )

                if matched_req and bundle["source_format"] == "muse_v1":
                    conn.execute(
                        """
                        UPDATE creator_crawl_requests SET
                            status = CASE WHEN ? THEN 'synced' ELSE status END,
                            matched_file_count = (SELECT COUNT(DISTINCT sha256_checksum) FROM creator_raw_files WHERE request_id = ? AND status = 'synced'),
                            updated_at = ?
                        WHERE request_id = ?
                        """,
                        (bool(result_is_terminal), matched_req["request_id"], now, matched_req["request_id"]),
                    )

            return {
                **result_meta,
                "idempotent_skip": False,
                "file_id": effective_file_id,
                "file_name": file_name,
                "sha256_checksum": sha256,
                "platform": platform,
                "source_format": bundle["source_format"],
                "schema_version": bundle["schema_version"],
                "request_id": eff_req_id,
                "crawl_id": eff_crawl_id,
                "valid_videos_count": len(bundle["items"]),
                "notes_excluded": bundle["stats"]["notes_excluded"],
                "invalid_excluded": bundle["stats"]["invalid_excluded"],
                "videos_inserted": videos_inserted,
                "videos_deduped": videos_deduped,
                "passed_count": passed_count,
                "needs_verification_count": needs_verification_count,
                "creators": bundle["creators"],
            }

    def reapply_filters(
        self,
        *,
        platform: Optional[str] = None,
        request_id: Optional[str] = None,
        filter_overrides: Optional[Dict[str, Any]] = None,
        reference_time: Any = None,
    ) -> Dict[str, Any]:
        """Re-evaluate filter verdicts on stored videos while preserving rights/dedup states."""
        with self._lock, self._connect() as conn:
            clauses = []
            params: List[Any] = []
            if platform and normalize_platform(platform):
                clauses.append("source_platform = ?")
                params.append(normalize_platform(platform))
            if request_id:
                clauses.append("request_id = ?")
                params.append(request_id)
            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(f"SELECT * FROM creator_videos {where_sql}", tuple(params)).fetchall()

            updated = 0
            passed = 0
            for r in rows:
                item = self._row_to_video(r)
                ev = evaluate_video_filters(
                    item,
                    platform=item["source_platform"],
                    filter_overrides=filter_overrides,
                    reference_time=reference_time,
                )
                if ev["passed_filter"]:
                    passed += 1
                conn.execute(
                    """
                    UPDATE creator_videos SET
                        filter_status = ?,
                        passed_filter = ?,
                        age_exception_applied = ?,
                        exclusion_reasons_json = ?,
                        needs_verification = ?,
                        verification_reasons_json = ?,
                        applied_filters_json = ?
                    WHERE video_key = ?
                    """,
                    (
                        ev["filter_status"],
                        1 if ev["passed_filter"] else 0,
                        1 if ev["age_exception_applied"] else 0,
                        json.dumps(ev["exclusion_reasons"], ensure_ascii=False),
                        1 if ev["needs_verification"] else 0,
                        json.dumps(ev["verification_reasons"], ensure_ascii=False),
                        json.dumps(ev["applied_filters"], ensure_ascii=False),
                        item["video_key"],
                    ),
                )
                updated += 1
            return {"updated": updated, "passed": passed}

    def _row_to_video(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["passed_filter"] = bool(d.get("passed_filter"))
        d["age_exception_applied"] = bool(d.get("age_exception_applied"))
        d["needs_verification"] = bool(d.get("needs_verification"))
        for src_col, dst_key, default_val in (
            ("exclusion_reasons_json", "exclusion_reasons", []),
            ("verification_reasons_json", "verification_reasons", []),
            ("applied_filters_json", "applied_filters", {}),
            ("raw_json", "raw_item", {}),
        ):
            raw_s = d.pop(src_col, None)
            try:
                d[dst_key] = json.loads(raw_s) if raw_s else default_val
            except json.JSONDecodeError:
                d[dst_key] = default_val
        raw_item = d["raw_item"]
        for key, value in normalize_item_metrics(raw_item).items():
            if key not in d:
                d[key] = value
        for key in ("metrics_source", "crawled_at"):
            d[key] = raw_item.get(key)
        return d

    def query_videos(
        self,
        *,
        platform: Optional[str] = None,
        creator_id: Optional[str] = None,
        request_id: Optional[str] = None,
        filter_status: Optional[str] = None,
        rights_status: Optional[str] = None,
        dedup_status: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "likes_desc",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Query stored creator videos with SQL filters and engagement/recency sorting."""
        clauses: List[str] = []
        params: List[Any] = []
        if platform and platform != "all":
            plat = normalize_platform(platform)
            if plat:
                clauses.append("source_platform = ?")
                params.append(plat)
        if creator_id:
            clauses.append("creator_id = ?")
            params.append(str(creator_id).strip())
        if request_id:
            clauses.append("request_id = ?")
            params.append(str(request_id).strip())
        if rights_status and rights_status != "all":
            clauses.append("rights_status = ?")
            params.append(rights_status)
        if dedup_status and dedup_status != "all":
            clauses.append("dedup_status = ?")
            params.append(dedup_status)
        if search:
            q = f"%{str(search).strip().lower()}%"
            clauses.append("(LOWER(COALESCE(title, '')) LIKE ? OR LOWER(COALESCE(creator_name, '')) LIKE ? OR LOWER(source_video_id) LIKE ?)")
            params.extend([q, q, q])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM creator_videos {where_sql}", tuple(params)).fetchall()

        videos = [self._row_to_video(r) for r in rows]
        ordered = sort_and_filter_videos(
            videos,
            sort_by=sort_by,
            filter_status=filter_status if filter_status and filter_status != "all" else None,
        )
        return ordered[: max(1, min(int(limit), 500))]

    def update_video_rights(
        self,
        video_keys: List[str],
        *,
        rights_status: str,
        rights_note: str = "",
    ) -> Dict[str, Any]:
        status = str(rights_status or "").strip()
        if status not in RIGHTS_STATUSES:
            raise ValueError(f"rights_status không hợp lệ: {status!r}")
        clean_keys = [str(k).strip() for k in (video_keys or []) if str(k).strip()]
        if not clean_keys:
            return {"updated": 0, "rights_status": status}

        now = utc_now_iso()
        placeholders = ",".join("?" for _ in clean_keys)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE creator_videos SET
                    rights_status = ?,
                    rights_note = ?,
                    rights_updated_at = ?
                WHERE video_key IN ({placeholders})
                """,
                (status, str(rights_note or "").strip(), now, *clean_keys),
            )
            return {"updated": int(cur.rowcount or 0), "rights_status": status}

    def _validate_single_video_for_production_conn(
        self,
        conn: sqlite3.Connection,
        vk: str,
        *,
        exclude_job_id: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Validate filter=passed, not needs_verification, approved rights, and local dedup."""
        row = conn.execute("SELECT * FROM creator_videos WHERE video_key = ?", (vk,)).fetchone()
        if not row:
            return None, {"video_key": vk, "reason": "video_not_found"}

        vid = self._row_to_video(row)
        alias_keys, alias_urls = self._alias_keys_for_item(
            video_key=vk,
            canonical_url=vid["canonical_url"],
            secondary_video_key=vid.get("secondary_video_key"),
            platform=vid["source_platform"],
        )
        job_row = self._find_competing_job_conn(
            conn,
            keys=alias_keys,
            urls=alias_urls,
            exclude_job_id=exclude_job_id,
        )
        own_queue_only = (
            exclude_job_id is not None
            and vid.get("dedup_status") == "duplicate_in_queue"
            and vid.get("matched_job_id") == exclude_job_id
        )
        if job_row or (
            vid.get("dedup_status") in ("duplicate_processed", "duplicate_in_queue", "duplicate_external")
            and not own_queue_only
        ):
            reason = "already_processed"
            if (job_row and job_row["status"] in ("running", "queued")) or vid.get("dedup_status") == "duplicate_in_queue":
                reason = "already_in_queue"
            return None, {
                "video_key": vk,
                "reason": reason,
                "matched_job_id": job_row["job_id"] if job_row else vid.get("matched_job_id"),
            }

        if vid.get("needs_verification"):
            return None, {
                "video_key": vk,
                "reason": "needs_verification",
            }

        if vid.get("filter_status") != "passed" or not vid.get("passed_filter"):
            return None, {
                "video_key": vk,
                "reason": f"filter_not_passed:{vid.get('filter_status')}",
            }

        if vid.get("rights_status") != "approved_for_reup":
            return None, {
                "video_key": vk,
                "reason": f"rights_not_approved:{vid.get('rights_status')}",
            }

        return vid, None

    def check_videos_ready_for_production(
        self,
        video_keys: List[str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Read-only preflight checking filter=passed, no needs_verification, rights, and dedup."""
        clean_keys = [str(k).strip() for k in (video_keys or []) if str(k).strip()]
        ready: List[Dict[str, Any]] = []
        blocked: List[Dict[str, Any]] = []
        seen_aliases = set()

        with self._lock, self._connect() as conn:
            for vk in clean_keys:
                if vk in seen_aliases:
                    blocked.append({"video_key": vk, "reason": "duplicate_in_batch"})
                    continue
                vid, blk = self._validate_single_video_for_production_conn(conn, vk)
                if blk is not None:
                    blocked.append(blk)
                    continue
                alias_keys, alias_urls = self._alias_keys_for_item(
                    video_key=vk,
                    canonical_url=vid["canonical_url"],
                    secondary_video_key=vid.get("secondary_video_key"),
                    platform=vid["source_platform"],
                )
                if any(a in seen_aliases for a in (*alias_keys, *alias_urls)):
                    blocked.append({"video_key": vk, "reason": "duplicate_in_batch"})
                    continue
                seen_aliases.update(alias_keys)
                seen_aliases.update(alias_urls)
                ready.append(vid)

        return ready, blocked

    def claim_and_create_reup_jobs(
        self,
        video_keys: List[str],
        config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Atomically validate ALL rules and insert Reup jobs inside a single BEGIN IMMEDIATE transaction.

        Never persists raw cookies or credentials in SQLite (`sanitize_job_config`).
        Protected by SQLite partial UNIQUE indexes against concurrent inserts.
        """
        clean_keys = [str(k).strip() for k in (video_keys or []) if str(k).strip()]
        if not clean_keys:
            return [], [{"video_key": "", "reason": "empty_selection"}]

        safe_cfg_json = json.dumps(sanitize_job_config(config), ensure_ascii=False)
        now = utc_now_iso()

        with self._lock, self._connect(manual_tx=True) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                ready_videos: List[Dict[str, Any]] = []
                blocked: List[Dict[str, Any]] = []
                seen_aliases = set()

                for vk in clean_keys:
                    if vk in seen_aliases:
                        blocked.append({"video_key": vk, "reason": "duplicate_in_batch"})
                        continue
                    vid, blk = self._validate_single_video_for_production_conn(conn, vk)
                    if blk is not None:
                        blocked.append(blk)
                        continue
                    alias_keys, alias_urls = self._alias_keys_for_item(
                        video_key=vk,
                        canonical_url=vid["canonical_url"],
                        secondary_video_key=vid.get("secondary_video_key"),
                        platform=vid["source_platform"],
                    )
                    if any(a in seen_aliases for a in (*alias_keys, *alias_urls)):
                        blocked.append({"video_key": vk, "reason": "duplicate_in_batch"})
                        continue
                    seen_aliases.update(alias_keys)
                    seen_aliases.update(alias_urls)
                    ready_videos.append(vid)

                if blocked:
                    conn.execute("ROLLBACK;")
                    return [], blocked

                created_jobs: List[Dict[str, Any]] = []
                for vid in ready_videos:
                    job_id = f"crjob_{uuid.uuid4().hex[:12]}"
                    conn.execute(
                        """
                        INSERT INTO creator_reup_jobs (
                            job_id, video_key, secondary_video_key, source_platform,
                            source_video_id, source_url, canonical_url, status,
                            config_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                        """,
                        (
                            job_id,
                            vid["video_key"],
                            vid.get("secondary_video_key"),
                            vid["source_platform"],
                            vid["source_video_id"],
                            vid["video_url"],
                            vid["canonical_url"],
                            safe_cfg_json,
                            now,
                            now,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE creator_videos SET
                            dedup_status = 'duplicate_in_queue',
                            matched_job_id = ?
                        WHERE video_key = ? AND dedup_status != 'duplicate_processed'
                        """,
                        (job_id, vid["video_key"]),
                    )
                    row = conn.execute("SELECT * FROM creator_reup_jobs WHERE job_id = ?", (job_id,)).fetchone()
                    created_jobs.append(self._row_to_job(row))

                conn.execute("COMMIT;")
                return created_jobs, []
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK;")
                return [], [{"video_key": clean_keys[0], "reason": "already_in_queue"}]
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def create_reup_job(self, video: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compatible wrapper routing single-video job creation through atomic claim."""
        jobs, blocked = self.claim_and_create_reup_jobs([video["video_key"]], config=config)
        if blocked or not jobs:
            reason = blocked[0]["reason"] if blocked else "claim_failed"
            raise ValueError(f"Không thể tạo Reup job ({reason}).")
        return jobs[0]

    def reverify_and_start_job(self, job_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Re-check filter/rights/dedup immediately before processing an individual queued job."""
        now = utc_now_iso()
        with self._lock, self._connect(manual_tx=True) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                job_row = conn.execute(
                    "SELECT * FROM creator_reup_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if not job_row:
                    conn.execute("ROLLBACK;")
                    return False, None, "job_not_found"
                if job_row["status"] != "queued":
                    conn.execute("ROLLBACK;")
                    return False, None, f"invalid_job_status:{job_row['status']}"

                vk = job_row["video_key"]
                vid, blk = self._validate_single_video_for_production_conn(conn, vk, exclude_job_id=job_id)
                if blk is not None:
                    reason = blk["reason"]
                    conn.execute(
                        """
                        UPDATE creator_reup_jobs SET
                            status = 'cancelled',
                            error_message = ?,
                            updated_at = ?
                        WHERE job_id = ?
                        """,
                        (f"Pre-run re-validation blocked: {reason}", now, job_id),
                    )
                    conn.execute(
                        """
                        UPDATE creator_videos SET
                            dedup_status = CASE WHEN matched_job_id = ? AND dedup_status = 'duplicate_in_queue' THEN 'new' ELSE dedup_status END,
                            matched_job_id = CASE WHEN matched_job_id = ? AND dedup_status = 'duplicate_in_queue' THEN NULL ELSE matched_job_id END
                        WHERE video_key = ?
                        """,
                        (job_id, job_id, vk),
                    )
                    conn.execute("COMMIT;")
                    return False, None, reason

                conn.execute(
                    "UPDATE creator_reup_jobs SET status = 'running', updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
                updated_job = conn.execute("SELECT * FROM creator_reup_jobs WHERE job_id = ?", (job_id,)).fetchone()
                conn.execute("COMMIT;")
                return True, self._row_to_job(updated_job), ""
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def update_reup_job_status(
        self,
        job_id: str,
        *,
        status: str,
        output_filename: Optional[str] = None,
        output_path: Optional[str] = None,
        caption: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Transition a Reup job; completion requires a verified local MP4 output file."""
        now = utc_now_iso()
        target_status = str(status or "").strip().lower()
        target_error = error_message

        if target_status == "completed":
            mp4_ok, mp4_err = verify_local_mp4_output(output_path)
            if not mp4_ok:
                target_status = "failed"
                target_error = f"MP4 output verification failed: {mp4_err}"

        completed_at = now if target_status == "completed" else None
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM creator_reup_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE creator_reup_jobs SET
                    status = ?,
                    output_filename = COALESCE(?, output_filename),
                    output_path = COALESCE(?, output_path),
                    caption = COALESCE(?, caption),
                    error_message = ?,
                    updated_at = ?,
                    completed_at = COALESCE(?, completed_at)
                WHERE job_id = ?
                """,
                (target_status, output_filename, output_path, caption, target_error, now, completed_at, job_id),
            )
            if target_status == "completed":
                conn.execute(
                    """
                    UPDATE creator_videos SET
                        dedup_status = 'duplicate_processed',
                        matched_job_id = ?
                    WHERE video_key = ?
                    """,
                    (job_id, row["video_key"]),
                )
            elif target_status in ("failed", "cancelled", "interrupted"):
                conn.execute(
                    """
                    UPDATE creator_videos SET
                        dedup_status = CASE WHEN matched_job_id = ? AND dedup_status = 'duplicate_in_queue' THEN 'new' ELSE dedup_status END,
                        matched_job_id = CASE WHEN matched_job_id = ? AND dedup_status = 'duplicate_in_queue' THEN NULL ELSE matched_job_id END
                    WHERE video_key = ?
                    """,
                    (job_id, job_id, row["video_key"]),
                )
            updated = conn.execute("SELECT * FROM creator_reup_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._row_to_job(updated) if updated else None

    def cancel_unfinished_jobs(
        self,
        job_ids: List[str],
        *,
        status: str = "cancelled",
        reason: str = "Đã hủy hàng loạt.",
    ) -> int:
        """Finalize any queued/running jobs in a batch so none stay wedged in duplicate_in_queue."""
        clean_ids = [str(j).strip() for j in (job_ids or []) if str(j).strip()]
        if not clean_ids:
            return 0
        now = utc_now_iso()
        placeholders = ",".join("?" for _ in clean_ids)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT job_id, video_key FROM creator_reup_jobs
                WHERE job_id IN ({placeholders}) AND status IN ('queued', 'running')
                """,
                tuple(clean_ids),
            ).fetchall()
            if not rows:
                return 0
            conn.execute(
                f"""
                UPDATE creator_reup_jobs SET
                    status = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE job_id IN ({placeholders}) AND status IN ('queued', 'running')
                """,
                (status, reason, now, *clean_ids),
            )
            for r in rows:
                conn.execute(
                    """
                    UPDATE creator_videos SET
                        dedup_status = CASE WHEN matched_job_id = ? AND dedup_status = 'duplicate_in_queue' THEN 'new' ELSE dedup_status END,
                        matched_job_id = CASE WHEN matched_job_id = ? AND dedup_status = 'duplicate_in_queue' THEN NULL ELSE matched_job_id END
                    WHERE video_key = ?
                    """,
                    (r["job_id"], r["job_id"], r["video_key"]),
                )
            return len(rows)

    def recover_interrupted_jobs(
        self,
        *,
        reason: str = "Tiến trình bị gián đoạn khi ứng dụng đóng/khởi động lại.",
    ) -> int:
        """Recover orphaned queued/running jobs left behind after crash or unexpected shutdown."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM creator_reup_jobs WHERE status IN ('queued', 'running')"
            ).fetchall()
            orphan_ids = [r["job_id"] for r in rows]
        if not orphan_ids:
            return 0
        return self.cancel_unfinished_jobs(orphan_ids, status="interrupted", reason=reason)

    def _row_to_job(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        try:
            raw_cfg = json.loads(d.pop("config_json") or "{}")
        except json.JSONDecodeError:
            raw_cfg = {}
        d["config"] = sanitize_job_config(raw_cfg)
        return d

    def list_reup_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creator_reup_jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def list_raw_files(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM creator_raw_files
                WHERE status != 'ignored_non_creator'
                ORDER BY imported_at DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_summary(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_videos,
                    SUM(CASE WHEN filter_status = 'passed' THEN 1 ELSE 0 END) AS passed_count,
                    SUM(CASE WHEN filter_status = 'needs_verification' THEN 1 ELSE 0 END) AS needs_verification_count,
                    SUM(CASE WHEN filter_status = 'filtered_out' THEN 1 ELSE 0 END) AS filtered_out_count,
                    SUM(CASE WHEN rights_status = 'approved_for_reup' THEN 1 ELSE 0 END) AS approved_rights_count,
                    SUM(CASE WHEN dedup_status != 'new' THEN 1 ELSE 0 END) AS duplicate_count
                FROM creator_videos
                """
            ).fetchone()
            req_pending = conn.execute(
                "SELECT COUNT(*) AS cnt FROM creator_crawl_requests WHERE status = 'awaiting_muse'"
            ).fetchone()["cnt"]
            files_synced = conn.execute(
                "SELECT COUNT(*) AS cnt FROM creator_raw_files WHERE status = 'synced'"
            ).fetchone()["cnt"]
        return {
            "total_videos": int((counts["total_videos"] if counts else 0) or 0),
            "passed_count": int((counts["passed_count"] if counts else 0) or 0),
            "needs_verification_count": int((counts["needs_verification_count"] if counts else 0) or 0),
            "filtered_out_count": int((counts["filtered_out_count"] if counts else 0) or 0),
            "approved_rights_count": int((counts["approved_rights_count"] if counts else 0) or 0),
            "duplicate_count": int((counts["duplicate_count"] if counts else 0) or 0),
            "pending_requests": int(req_pending or 0),
            "files_synced": int(files_synced or 0),
            "last_sync_at": self.get_state("last_sync_at"),
            "last_sync_status": self.get_state("last_sync_status", "idle"),
            "last_error": self.get_state("last_error", ""),
            "drive_scope_folder_id": self.get_state("drive_scope_folder_id", ""),
            "supported_platforms": list(SUPPORTED_PLATFORMS),
        }
