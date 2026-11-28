"""Isolated SQLite storage for Creator Crawler & Phase 1 Batch Reup."""

from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .filters import evaluate_video_filters, resolve_filter_config, sort_and_filter_videos
from .models import RIGHTS_STATUSES, SUPPORTED_PLATFORMS, canonicalize_video_url, normalize_platform, utc_now_iso
from .parser import parse_creator_crawl_payload


class CreatorReupStore:
    """Thread-safe isolated SQLite repository under `data/creator_reup/`."""

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
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def close(self) -> None:
        """Close hook matching store lifecycle (connections are short-lived per call)."""
        return None

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

                CREATE INDEX IF NOT EXISTS idx_cv_platform_filter
                    ON creator_videos (source_platform, filter_status, rights_status, dedup_status);
                CREATE INDEX IF NOT EXISTS idx_cv_creator
                    ON creator_videos (creator_key, request_id);
                CREATE INDEX IF NOT EXISTS idx_cv_canonical_url
                    ON creator_videos (canonical_url);

                CREATE TABLE IF NOT EXISTS creator_reup_jobs (
                    job_id TEXT PRIMARY KEY,
                    video_key TEXT NOT NULL,
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

                CREATE INDEX IF NOT EXISTS idx_crj_video_status
                    ON creator_reup_jobs (video_key, status);
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
            if not row:
                return False
            if row["status"] == "error":
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
                "SELECT * FROM creator_crawl_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            return self._row_to_request(row) if row else None

    def list_crawl_requests(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creator_crawl_requests ORDER BY created_at DESC LIMIT ?",
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
        return d

    def _find_matching_request(
        self,
        conn: sqlite3.Connection,
        *,
        explicit_request_id: Optional[str],
        explicit_crawl_id: Optional[str],
        drive_folder_id: Optional[str],
        creator_profile_urls: List[str],
    ) -> Optional[Dict[str, Any]]:
        if explicit_request_id:
            row = conn.execute(
                "SELECT * FROM creator_crawl_requests WHERE request_id = ?",
                (explicit_request_id,),
            ).fetchone()
            if row:
                return self._row_to_request(row)
        if explicit_crawl_id:
            row = conn.execute(
                "SELECT * FROM creator_crawl_requests WHERE crawl_id = ?",
                (explicit_crawl_id,),
            ).fetchone()
            if row:
                return self._row_to_request(row)
        if drive_folder_id:
            row = conn.execute(
                "SELECT * FROM creator_crawl_requests WHERE drive_folder_id = ? ORDER BY created_at DESC LIMIT 1",
                (drive_folder_id,),
            ).fetchone()
            if row:
                return self._row_to_request(row)
        for url in creator_profile_urls:
            if not url:
                continue
            row = conn.execute(
                """
                SELECT * FROM creator_crawl_requests
                WHERE profile_url = ? AND status = 'awaiting_muse'
                ORDER BY created_at DESC LIMIT 1
                """,
                (url,),
            ).fetchone()
            if row:
                return self._row_to_request(row)
        return None

    def list_profiles(self, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if platform and normalize_platform(platform):
                rows = conn.execute(
                    "SELECT * FROM creator_profiles WHERE platform = ? ORDER BY updated_at DESC",
                    (normalize_platform(platform),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM creator_profiles ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

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
        """Parse, store, pair with crawl requests, evaluate filters, and dedup idempotently."""
        if isinstance(raw_payload, (bytes, bytearray)):
            raw_bytes = bytes(raw_payload)
        elif isinstance(raw_payload, str):
            raw_bytes = raw_payload.encode("utf-8")
        else:
            raw_bytes = json.dumps(raw_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")

        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        effective_file_id = str(file_id or f"manual:{sha256[:24]}").strip()

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

            bundle = parse_creator_crawl_payload(raw_bytes)
            platform = bundle["platform"]
            eff_req_id = request_id or bundle.get("request_id")
            eff_crawl_id = crawl_id or bundle.get("crawl_id")
            eff_folder_id = drive_folder_id or bundle.get("drive_folder_id")
            ref_time = reference_time or bundle.get("reference_time")
            now = utc_now_iso()

            with self._connect() as conn:
                matched_req = self._find_matching_request(
                    conn,
                    explicit_request_id=eff_req_id,
                    explicit_crawl_id=eff_crawl_id,
                    drive_folder_id=eff_folder_id,
                    creator_profile_urls=[c.get("profile_url") or "" for c in bundle["creators"]],
                )
                if matched_req:
                    eff_req_id = eff_req_id or matched_req["request_id"]
                    eff_crawl_id = eff_crawl_id or matched_req["crawl_id"]
                    active_filter_overrides = filter_overrides if filter_overrides is not None else matched_req.get("filters")
                else:
                    active_filter_overrides = filter_overrides

                # Upsert creator profiles
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
                            followers_display = COALESCE(excluded.followers_display, creator_profiles.followers_display),
                            followers = COALESCE(excluded.followers, creator_profiles.followers),
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
                evaluated_items: List[Dict[str, Any]] = []

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
                    ckey = f"{platform}:{evaluated['creator_id']}"
                    canonical = canonicalize_video_url(
                        evaluated["video_url"],
                        platform=platform,
                        video_id=evaluated["source_video_id"],
                    )

                    existing_video = conn.execute(
                        """
                        SELECT video_key, rights_status, rights_note, rights_updated_at,
                               dedup_status, matched_job_id
                        FROM creator_videos
                        WHERE video_key = ? OR canonical_url = ?
                        LIMIT 1
                        """,
                        (vkey, canonical),
                    ).fetchone()

                    job_row = conn.execute(
                        """
                        SELECT job_id, status FROM creator_reup_jobs
                        WHERE (video_key = ? OR canonical_url = ?)
                          AND status IN ('completed', 'running', 'queued')
                        ORDER BY CASE status WHEN 'completed' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,
                                 created_at DESC
                        LIMIT 1
                        """,
                        (vkey, canonical),
                    ).fetchone()

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
                        conn.execute(
                            """
                            UPDATE creator_videos SET
                                creator_name = COALESCE(?, creator_name),
                                request_id = COALESCE(?, request_id),
                                crawl_id = COALESCE(?, crawl_id),
                                raw_file_id = ?,
                                title = COALESCE(?, title),
                                thumbnail = COALESCE(?, thumbnail),
                                views = ?,
                                views_display = COALESCE(?, views_display),
                                effective_views = ?,
                                likes = ?,
                                likes_display = COALESCE(?, likes_display),
                                effective_likes = ?,
                                metrics_precision = ?,
                                comments = COALESCE(?, comments),
                                shares = COALESCE(?, shares),
                                favorites = COALESCE(?, favorites),
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
                                video_key, source_platform, source_video_id, video_id_secondary,
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
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'video', ?, ?,
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
                    evaluated_items.append(evaluated)

                conn.execute(
                    """
                    INSERT INTO creator_raw_files (
                        file_id, file_name, source_kind, drive_folder_id, modified_time,
                        md5_checksum, sha256_checksum, request_id, crawl_id, platform,
                        schema_version, items_parsed, notes_excluded, videos_inserted,
                        videos_deduped, status, error_message, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced', NULL, ?)
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
                        imported_at = excluded.imported_at
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
                    ),
                )

                if matched_req:
                    conn.execute(
                        """
                        UPDATE creator_crawl_requests SET
                            status = 'synced',
                            matched_file_count = matched_file_count + 1,
                            updated_at = ?
                        WHERE request_id = ?
                        """,
                        (now, matched_req["request_id"]),
                    )

            return {
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
        """Query stored creator videos with SQL filters and Python engagement/recency sorting."""
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

    def check_videos_ready_for_production(
        self,
        video_keys: List[str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Validate usage rights AND cross-session dedup before entering Reup pipeline."""
        clean_keys = [str(k).strip() for k in (video_keys or []) if str(k).strip()]
        ready: List[Dict[str, Any]] = []
        blocked: List[Dict[str, Any]] = []
        seen_in_batch = set()

        with self._lock, self._connect() as conn:
            for vk in clean_keys:
                if vk in seen_in_batch:
                    blocked.append({"video_key": vk, "reason": "duplicate_in_batch"})
                    continue
                seen_in_batch.add(vk)

                row = conn.execute(
                    "SELECT * FROM creator_videos WHERE video_key = ?",
                    (vk,),
                ).fetchone()
                if not row:
                    blocked.append({"video_key": vk, "reason": "video_not_found"})
                    continue

                vid = self._row_to_video(row)
                job_row = conn.execute(
                    """
                    SELECT job_id, status FROM creator_reup_jobs
                    WHERE (video_key = ? OR canonical_url = ?)
                      AND status IN ('completed', 'running', 'queued')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (vk, vid["canonical_url"]),
                ).fetchone()
                if job_row or vid.get("dedup_status") in ("duplicate_processed", "duplicate_in_queue", "duplicate_external"):
                    reason = "already_processed"
                    if job_row and job_row["status"] in ("running", "queued"):
                        reason = "already_in_queue"
                    elif vid.get("dedup_status") == "duplicate_in_queue":
                        reason = "already_in_queue"
                    blocked.append(
                        {
                            "video_key": vk,
                            "reason": reason,
                            "matched_job_id": job_row["job_id"] if job_row else vid.get("matched_job_id"),
                        }
                    )
                    continue

                if vid.get("rights_status") != "approved_for_reup":
                    blocked.append(
                        {
                            "video_key": vk,
                            "reason": f"rights_not_approved:{vid.get('rights_status')}",
                        }
                    )
                    continue

                ready.append(vid)

        return ready, blocked

    def create_reup_job(self, video: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now_iso()
        job_id = f"crjob_{uuid.uuid4().hex[:12]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO creator_reup_jobs (
                    job_id, video_key, source_platform, source_video_id, source_url,
                    canonical_url, status, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    video["video_key"],
                    video["source_platform"],
                    video["source_video_id"],
                    video["video_url"],
                    video["canonical_url"],
                    json.dumps(config or {}, ensure_ascii=False),
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
                (job_id, video["video_key"]),
            )
            row = conn.execute("SELECT * FROM creator_reup_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row)

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
        now = utc_now_iso()
        completed_at = now if status == "completed" else None
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
                (status, output_filename, output_path, caption, error_message, now, completed_at, job_id),
            )
            if status == "completed":
                conn.execute(
                    """
                    UPDATE creator_videos SET
                        dedup_status = 'duplicate_processed',
                        matched_job_id = ?
                    WHERE video_key = ?
                    """,
                    (job_id, row["video_key"]),
                )
            elif status == "failed":
                conn.execute(
                    """
                    UPDATE creator_videos SET
                        dedup_status = CASE WHEN matched_job_id = ? AND dedup_status = 'duplicate_in_queue' THEN 'new' ELSE dedup_status END
                    WHERE video_key = ?
                    """,
                    (job_id, row["video_key"]),
                )
            updated = conn.execute("SELECT * FROM creator_reup_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return dict(updated)

    def list_reup_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creator_reup_jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["config"] = json.loads(d.pop("config_json") or "{}")
                except json.JSONDecodeError:
                    d["config"] = {}
                out.append(d)
            return out

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
            "supported_platforms": list(SUPPORTED_PLATFORMS),
        }
