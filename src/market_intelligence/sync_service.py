"""
Non-blocking Market Intelligence Sync Service & Background Scheduler.

Handles:
  - Auto-sync on App_Video startup (daemon thread, non-blocking)
  - Periodic background sync (default 5 minutes, configurable)
  - Manual Sync Now & Telegram-triggered Sync Now under a single-flight lock
  - Incremental Drive synchronization (skips unchanged files via cursor + md5/mtime)
  - Optional local JSON file ingestion using the identical deduplication pipeline
"""

import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .cloudflare_client import CloudflareBridgeClient
from .normalizer import is_archive_path
from .storage import MarketIntelligenceStore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketSyncService:
    """Thread-safe incremental sync coordinator with background scheduler."""

    def __init__(
        self,
        store: MarketIntelligenceStore,
        client: CloudflareBridgeClient,
        default_interval_sec: int = 300,
        max_list_pages: int = 50,
    ):
        self.store = store
        self.client = client
        self.max_list_pages = max(1, int(max_list_pages))
        if self.store.get_state("sync_interval_seconds") is None:
            self.store.set_sync_interval_seconds(default_interval_sec)

        self._sync_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._is_syncing = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._bg_thread: Optional[threading.Thread] = None

    @property
    def is_syncing(self) -> bool:
        with self._state_lock:
            return self._is_syncing

    def get_interval_seconds(self) -> int:
        return self.store.get_sync_interval_seconds(300)

    def set_interval_seconds(self, seconds: int) -> int:
        updated = self.store.set_sync_interval_seconds(seconds)
        self._wake_event.set()
        return updated

    def start(self, sync_on_startup: bool = True):
        """Start periodic background sync thread (idempotent)."""
        if self._bg_thread and self._bg_thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._bg_thread = threading.Thread(
            target=self._background_loop,
            args=(sync_on_startup,),
            name="MarketSyncScheduler",
            daemon=True,
        )
        self._bg_thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop_event.set()
        self._wake_event.set()
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=timeout)

    def _background_loop(self, sync_on_startup: bool):
        if sync_on_startup and not self._stop_event.is_set():
            try:
                if self.store.get_state("data_contract_v31_backfilled") != "1":
                    self.store.backfill_v31_metadata()
                    self.store.set_state("data_contract_v31_backfilled", "1")
            except Exception:
                pass
            try:
                self.sync_now(trigger_source="startup")
            except Exception:
                pass

        while not self._stop_event.is_set():
            interval = self.get_interval_seconds()
            self._wake_event.wait(timeout=interval)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            try:
                self.sync_now(trigger_source="scheduler")
            except Exception:
                pass

    def trigger_async_sync(self, trigger_source: str = "manual_ui") -> Dict[str, Any]:
        """Fire a non-blocking sync in a background thread so UI requests return immediately."""
        if self.is_syncing:
            return {"started": False, "already_running": True, "message": "Đang đồng bộ rồi."}

        t = threading.Thread(
            target=self.sync_now,
            kwargs={"trigger_source": trigger_source},
            name=f"MarketSync_{trigger_source}",
            daemon=True,
        )
        t.start()
        return {"started": True, "already_running": False}

    def sync_now(
        self,
        trigger_source: str = "manual_ui",
        command_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute one full incremental sync pass under single-flight lock."""
        with self._sync_lock:
            with self._state_lock:
                self._is_syncing = True

            self.client.reload_config()
            run_id = self.store.create_sync_run(trigger_source=trigger_source, command_id=command_id)
            self.store.set_state("last_sync_status", "syncing")

            files_checked = 0
            files_downloaded = 0
            records_parsed = 0
            records_inserted = 0
            records_updated = 0
            records_deduped = 0
            new_count = 0
            duplicate_count = 0
            engagement_updated_count = 0
            missing_views_count = 0
            scanned_count = 0
            rejected_count = 0
            registry_items = []

            try:
                if not self.client.is_configured():
                    err = "Thiếu CLOUDFLARE_APP_TOKEN — chưa thể kết nối Cloudflare Worker."
                    self.store.set_state("last_sync_status", "unconfigured")
                    self.store.set_state("last_error", err)
                    self.store.finish_sync_run(
                        run_id,
                        status="failed",
                        error_message=err,
                    )
                    return {
                        "run_id": run_id,
                        "status": "failed",
                        "unconfigured": True,
                        "files_checked": 0,
                        "files_downloaded": 0,
                        "records_parsed": 0,
                        "records_inserted": 0,
                        "records_updated": 0,
                        "records_deduped": 0,
                        "error_message": err,
                        "finished_at": _utc_now_iso(),
                    }

                cursor = self.store.get_state("drive_cursor")
                all_files = []
                seen_fids = set()
                seen_page_tokens = set()
                page_token = None
                pagination_error: Optional[str] = None
                active_file_ids: Optional[List[str]] = None
                for page_idx in range(self.max_list_pages):
                    listing = self.client.list_market_files(cursor=cursor, limit=100, page_token=page_token)
                    if listing.get("status") == "partial_error" or listing.get("partial_error"):
                        pagination_error = f"Worker pagination partial_error ({listing.get('partial_error') or 'incomplete'})"
                    if not pagination_error and page_token is None and isinstance(listing.get("active_file_ids"), list):
                        active_file_ids = [str(fid) for fid in listing.get("active_file_ids") if fid]
                    page_files = listing.get("files", []) or []
                    for fmeta in page_files:
                        fid = fmeta.get("file_id") or fmeta.get("id")
                        if fid and fid not in seen_fids:
                            seen_fids.add(fid)
                            all_files.append(fmeta)
                    if pagination_error:
                        break
                    has_more = bool(listing.get("has_more"))
                    next_pt = listing.get("next_page_token")
                    if not has_more:
                        break
                    if not next_pt:
                        pagination_error = "Pagination partial_error: has_more=true nhưng thiếu next_page_token"
                        break
                    if next_pt == page_token or next_pt in seen_page_tokens:
                        pagination_error = f"Pagination partial_error: token phân trang bị lặp ({next_pt})"
                        break
                    if page_idx >= self.max_list_pages - 1:
                        pagination_error = f"Pagination partial_error: vượt giới hạn {self.max_list_pages} trang"
                        break
                    seen_page_tokens.add(next_pt)
                    page_token = next_pt

                files = sorted(
                    all_files,
                    key=lambda x: (
                        1 if "_corrected" in str(x.get("name") or "").lower() else 0,
                        str(x.get("modified_time") or x.get("modifiedTime") or ""),
                        str(x.get("file_id") or x.get("id") or ""),
                    ),
                )
                latest_mtime = cursor

                file_errors = []
                explicitly_archived_fids = []
                for fmeta in files:
                    fid = fmeta.get("file_id") or fmeta.get("id")
                    fname = fmeta.get("name") or f"{fid}.json"
                    mtime = fmeta.get("modified_time") or fmeta.get("modifiedTime")
                    md5 = fmeta.get("md5_checksum") or fmeta.get("md5Checksum")
                    folder_path = fmeta.get("folder_path")
                    platform_hint = fmeta.get("platform_hint")

                    if is_archive_path(folder_path) or is_archive_path(fname):
                        if fid:
                            explicitly_archived_fids.append(str(fid))
                        if mtime and (not latest_mtime or mtime > latest_mtime):
                            latest_mtime = mtime
                        continue

                    files_checked += 1
                    if not fid:
                        continue

                    if self.store.is_file_unchanged(fid, modified_time=mtime, md5_checksum=md5):
                        if mtime and (not latest_mtime or mtime > latest_mtime):
                            latest_mtime = mtime
                        continue

                    try:
                        raw_bytes, dl_meta = self.client.download_market_file(fid)
                        ingest_res = self.store.ingest_raw_file(
                            file_id=fid,
                            file_name=fname,
                            raw_bytes=raw_bytes,
                            modified_time=mtime or dl_meta.get("modified_time"),
                            md5_checksum=md5 or dl_meta.get("md5_checksum"),
                            folder_path=folder_path,
                            platform_hint=platform_hint,
                        )
                        files_downloaded += 1
                        records_parsed += ingest_res["parsed"]
                        records_inserted += ingest_res["inserted"]
                        records_updated += ingest_res["updated"]
                        records_deduped += ingest_res["deduped"]
                        new_count += int(ingest_res.get("new_count") if ingest_res.get("new_count") is not None else ingest_res["inserted"])
                        duplicate_count += int(ingest_res.get("duplicate_count") if ingest_res.get("duplicate_count") is not None else ingest_res["deduped"])
                        engagement_updated_count += int(ingest_res.get("engagement_updated_count") or 0)
                        missing_views_count += int(ingest_res.get("missing_views_count") or 0)
                        scanned_count += int(ingest_res.get("scanned_count") or ingest_res["parsed"])
                        rejected_count += int(ingest_res.get("rejected_count") or 0)
                        if isinstance(ingest_res.get("registry_items"), list):
                            registry_items.extend(ingest_res["registry_items"])
                        if mtime and (not latest_mtime or mtime > latest_mtime):
                            latest_mtime = mtime
                    except Exception as file_exc:
                        file_errors.append(f"{fname}: {file_exc}")

                all_errors = list(file_errors)
                if pagination_error:
                    all_errors.append(pagination_error)
                all_ok = len(all_errors) == 0

                if explicitly_archived_fids:
                    self.store.mark_files_archived(explicitly_archived_fids)
                if not pagination_error and not file_errors and active_file_ids is not None:
                    self.store.reconcile_moved_archive_files(active_file_ids)

                # Never commit drive_cursor when pagination or file downloads were incomplete
                if all_ok and latest_mtime:
                    self.store.set_state("drive_cursor", latest_mtime)

                now_iso = _utc_now_iso()
                final_status = "completed" if all_ok else "partial_error"
                err_msg = "; ".join(all_errors) if all_errors else None

                self.store.set_state("last_sync_at", now_iso)
                self.store.set_state("last_sync_status", "ok" if all_ok else "partial_error")
                self.store.set_state("last_error", err_msg or "")

                if registry_items and hasattr(self.client, "record_cross_run_dedup"):
                    try:
                        self.client.record_cross_run_dedup(registry_items[:500], run_id=run_id)
                    except Exception:
                        pass

                self.store.finish_sync_run(
                    run_id,
                    status=final_status,
                    files_checked=files_checked,
                    files_downloaded=files_downloaded,
                    records_parsed=records_parsed,
                    records_inserted=records_inserted,
                    records_updated=records_updated,
                    records_deduped=records_deduped,
                    error_message=err_msg,
                    new_count=new_count,
                    duplicate_count=duplicate_count,
                    updated_count=engagement_updated_count,
                    missing_views_count=missing_views_count,
                    scanned_count=scanned_count,
                    rejected_count=rejected_count,
                )

                stats = self.store.get_stats()
                return {
                    "run_id": run_id,
                    "status": final_status,
                    "partial_error": not all_ok,
                    "pagination_error": pagination_error,
                    "cursor_committed": bool(all_ok and latest_mtime),
                    "files_checked": files_checked,
                    "files_downloaded": files_downloaded,
                    "records_parsed": records_parsed,
                    "records_inserted": records_inserted,
                    "records_updated": records_updated,
                    "records_deduped": records_deduped,
                    "new_count": new_count,
                    "duplicate_count": duplicate_count,
                    "engagement_updated_count": engagement_updated_count,
                    "missing_views_count": missing_views_count,
                    "scanned_count": scanned_count,
                    "rejected_count": rejected_count,
                    "total_items": stats["total_items"],
                    "by_platform": stats["by_platform"],
                    "error_message": err_msg,
                    "finished_at": now_iso,
                }
            except Exception as exc:
                err_msg = str(exc)
                now_iso = _utc_now_iso()
                self.store.set_state("last_sync_at", now_iso)
                self.store.set_state("last_sync_status", "error")
                self.store.set_state("last_error", err_msg)
                self.store.finish_sync_run(
                    run_id,
                    status="failed",
                    files_checked=files_checked,
                    files_downloaded=files_downloaded,
                    records_parsed=records_parsed,
                    records_inserted=records_inserted,
                    records_updated=records_updated,
                    records_deduped=records_deduped,
                    error_message=err_msg,
                )
                return {
                    "run_id": run_id,
                    "status": "failed",
                    "files_checked": files_checked,
                    "files_downloaded": files_downloaded,
                    "records_parsed": records_parsed,
                    "records_inserted": records_inserted,
                    "records_updated": records_updated,
                    "records_deduped": records_deduped,
                    "error_message": err_msg,
                    "finished_at": now_iso,
                }
            finally:
                with self._state_lock:
                    self._is_syncing = False

    def ingest_local_file(self, filepath: str, platform_hint: Optional[str] = None) -> Dict[str, int]:
        """Ingest a local JSON file through the identical raw+normalized+dedup pipeline."""
        abs_path = os.path.abspath(filepath)
        with open(abs_path, "rb") as f:
            raw_bytes = f.read()
        file_name = os.path.basename(abs_path)
        mtime = datetime.fromtimestamp(os.path.getmtime(abs_path), tz=timezone.utc).isoformat()
        file_id = f"local_{file_name}"
        return self.store.ingest_raw_file(
            file_id=file_id,
            file_name=file_name,
            raw_bytes=raw_bytes,
            modified_time=mtime,
            folder_path="local_import",
            platform_hint=platform_hint,
        )
