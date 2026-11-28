"""Google Drive sync & manual JSON import service isolated for Creator Crawler."""

import hashlib
import json
import threading
from typing import Any, Dict, List, Optional

from market_intelligence.cloudflare_client import CloudflareBridgeClient

from .models import CREATOR_SCHEMA_VERSION, is_creator_crawler_file_payload, is_market_filename, utc_now_iso
from .storage import CreatorReupStore

CREATOR_DRIVE_SYNC_BLOCKER = (
    "Endpoint Worker hiện tại (/api/app/market/files) chỉ được phép đọc thư mục 01_Market_Data; "
    "mọi thư mục Creator Crawler riêng nằm ngoài 01_Market_Data sẽ bị Worker từ chối (HTTP 403). "
    "Phase 1 không mở rộng quyền API Market sang Reup — cần endpoint /api/reup/v1 riêng ở Phase 2. "
    "Vui lòng dùng Import JSON thủ công nếu file Muse không nằm trong 01_Market_Data."
)


class CreatorDriveSyncService:
    """Syncs Creator Crawler JSON outputs from Google Drive (read-only) and manual JSON imports.

    This service reuses CloudflareBridgeClient transport with its own cursor and SQLite
    store (`CreatorReupStore`), strictly skipping Market Intelligence files so datasets stay isolated.
    """

    def __init__(
        self,
        store: CreatorReupStore,
        client: Optional[CloudflareBridgeClient] = None,
        *,
        default_interval_sec: float = 30.0,
        max_list_pages: int = 20,
    ):
        self.store = store
        self.client = client or CloudflareBridgeClient()
        self.default_interval_sec = max(5.0, float(default_interval_sec))
        self.max_list_pages = max(1, int(max_list_pages))
        self._sync_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._is_syncing = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_syncing(self) -> bool:
        with self._state_lock:
            return self._is_syncing

    def start(self, sync_on_startup: bool = False) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(sync_on_startup,),
            name="CreatorReupDriveSyncLoop",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run_loop(self, sync_on_startup: bool) -> None:
        if sync_on_startup and self.store.has_pending_crawl_requests():
            try:
                self.sync_now(trigger_source="startup")
            except Exception:
                pass
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=self.default_interval_sec)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            if self.store.has_pending_crawl_requests():
                try:
                    self.sync_now(trigger_source="auto_poll")
                except Exception:
                    pass

    def trigger_async_sync(self, trigger_source: str = "manual_ui") -> Dict[str, Any]:
        if self.is_syncing:
            return {"started": False, "already_running": True, "message": "Đang đồng bộ Creator JSON rồi."}
        t = threading.Thread(
            target=self.sync_now,
            kwargs={"trigger_source": trigger_source},
            name=f"CreatorSync_{trigger_source}",
            daemon=True,
        )
        t.start()
        return {"started": True, "already_running": False}

    def import_json_text(
        self,
        raw_text: str,
        *,
        file_name: str = "manual_import.json",
        request_id: Optional[str] = None,
        crawl_id: Optional[str] = None,
        filter_overrides: Optional[Dict[str, Any]] = None,
        reference_time: Any = None,
    ) -> Dict[str, Any]:
        """Validate and ingest a manually uploaded or pasted Creator JSON string."""
        text = str(raw_text or "").strip()
        if not text:
            raise ValueError("Nội dung JSON trống.")
        return self.store.ingest_creator_json(
            raw_payload=text,
            file_name=file_name,
            source_kind="manual",
            request_id=request_id,
            crawl_id=crawl_id,
            filter_overrides=filter_overrides,
            reference_time=reference_time,
        )

    def sync_now(self, trigger_source: str = "manual_ui") -> Dict[str, Any]:
        """List Worker-scoped files incrementally and ingest ONLY strict Muse v1 Creator JSONs."""
        with self._sync_lock:
            with self._state_lock:
                self._is_syncing = True

            self.client.reload_config()
            self.store.set_state("last_sync_status", "syncing")
            files_checked = 0
            files_downloaded = 0
            files_ingested = 0
            files_skipped_market = 0
            videos_inserted = 0
            videos_deduped = 0
            file_errors: List[str] = []
            scope_folder_id: Optional[str] = None
            scoped_mismatch_requests: List[str] = []

            try:
                if not self.client.is_configured():
                    err = "Thiếu CLOUDFLARE_APP_TOKEN — chưa kết nối Cloudflare Worker để đọc Drive."
                    self.store.set_state("last_sync_status", "unconfigured")
                    self.store.set_state("last_error", err)
                    return {
                        "status": "failed",
                        "unconfigured": True,
                        "error_message": err,
                        "blocker": CREATOR_DRIVE_SYNC_BLOCKER,
                        "files_checked": 0,
                        "files_ingested": 0,
                        "files_skipped_market": 0,
                    }

                cursor = self.store.get_state("creator_drive_cursor")
                all_files: List[Dict[str, Any]] = []
                seen_fids = set()
                seen_page_tokens = set()
                page_token = None
                pagination_error: Optional[str] = None

                for page_idx in range(self.max_list_pages):
                    listing = self.client.list_market_files(cursor=cursor, limit=100, page_token=page_token)
                    if page_idx == 0 and listing.get("scope_folder_id"):
                        scope_folder_id = str(listing.get("scope_folder_id") or "").strip() or None
                    if listing.get("status") == "partial_error" or listing.get("partial_error"):
                        pagination_error = f"Pagination partial_error ({listing.get('partial_error') or 'incomplete'})"
                        break
                    for fmeta in listing.get("files", []) or []:
                        fid = str(fmeta.get("file_id") or fmeta.get("id") or "").strip()
                        if fid and fid not in seen_fids:
                            seen_fids.add(fid)
                            all_files.append(fmeta)
                    if not bool(listing.get("has_more")):
                        break
                    next_pt = listing.get("next_page_token")
                    if not next_pt or next_pt == page_token or next_pt in seen_page_tokens:
                        pagination_error = "Pagination partial_error: invalid next_page_token"
                        break
                    if page_idx >= self.max_list_pages - 1:
                        pagination_error = f"Pagination partial_error: exceeded {self.max_list_pages} pages"
                        break
                    seen_page_tokens.add(next_pt)
                    page_token = next_pt

                if scope_folder_id:
                    self.store.set_state("drive_scope_folder_id", scope_folder_id)
                    for req in self.store.list_crawl_requests(limit=50):
                        req_folder = str(req.get("drive_folder_id") or "").strip()
                        if req.get("status") == "awaiting_muse" and req_folder and req_folder != scope_folder_id:
                            scoped_mismatch_requests.append(f"{req['request_id']} ({req_folder})")

                all_files.sort(
                    key=lambda x: (
                        str(x.get("modified_time") or x.get("modifiedTime") or ""),
                        str(x.get("file_id") or x.get("id") or ""),
                    )
                )
                latest_mtime = cursor

                for fmeta in all_files:
                    fid = str(fmeta.get("file_id") or fmeta.get("id") or "").strip()
                    fname = str(fmeta.get("name") or f"{fid}.json").strip()
                    mtime = fmeta.get("modified_time") or fmeta.get("modifiedTime")
                    md5 = fmeta.get("md5_checksum") or fmeta.get("md5Checksum")
                    folder_path = str(fmeta.get("folder_path") or "").strip()

                    if not fid:
                        continue
                    lower_folder = folder_path.lower()
                    if "/archive" in lower_folder or lower_folder.startswith("archive") or is_market_filename(fname):
                        files_skipped_market += 1
                        if mtime and (not latest_mtime or mtime > latest_mtime):
                            latest_mtime = mtime
                        continue

                    files_checked += 1
                    if self.store.is_file_unchanged(fid, modified_time=mtime, md5_checksum=md5):
                        if mtime and (not latest_mtime or mtime > latest_mtime):
                            latest_mtime = mtime
                        continue

                    try:
                        raw_bytes, dl_meta = self.client.download_market_file(fid)
                        files_downloaded += 1
                        sha256 = hashlib.sha256(raw_bytes).hexdigest()
                        try:
                            parsed_preview = json.loads(raw_bytes.decode("utf-8-sig"))
                        except Exception as json_exc:
                            file_errors.append(f"{fname}: JSON lỗi ({json_exc})")
                            continue

                        # Drive sync only ingests strict Muse v1 (`schema_version == "1.0"`); discovery samples are manual-only.
                        is_strict_v1 = (
                            isinstance(parsed_preview, dict)
                            and str(parsed_preview.get("schema_version") or "").strip() == CREATOR_SCHEMA_VERSION
                            and is_creator_crawler_file_payload(parsed_preview, file_name=fname, folder_path=folder_path)
                        )
                        if not is_strict_v1:
                            files_skipped_market += 1
                            self.store.mark_ignored_remote_file(
                                file_id=fid,
                                file_name=fname,
                                modified_time=mtime or dl_meta.get("modified_time"),
                                md5_checksum=md5 or dl_meta.get("md5_checksum"),
                                sha256_checksum=sha256,
                                folder_path=folder_path or None,
                            )
                            if mtime and (not latest_mtime or mtime > latest_mtime):
                                latest_mtime = mtime
                            continue

                        res = self.store.ingest_creator_json(
                            raw_payload=raw_bytes,
                            file_name=fname,
                            file_id=fid,
                            source_kind="drive",
                            modified_time=mtime or dl_meta.get("modified_time"),
                            md5_checksum=md5 or dl_meta.get("md5_checksum"),
                            drive_folder_id=folder_path or scope_folder_id,
                        )
                        if not res.get("idempotent_skip"):
                            files_ingested += 1
                            videos_inserted += int(res.get("videos_inserted") or 0)
                            videos_deduped += int(res.get("videos_deduped") or 0)
                        if mtime and (not latest_mtime or mtime > latest_mtime):
                            latest_mtime = mtime
                    except Exception as file_exc:
                        file_errors.append(f"{fname}: {file_exc}")

                all_errors = list(file_errors)
                if pagination_error:
                    all_errors.append(pagination_error)
                all_ok = len(all_errors) == 0
                if all_ok and latest_mtime:
                    self.store.set_state("creator_drive_cursor", latest_mtime)

                now_iso = utc_now_iso()
                status = "completed" if all_ok else "partial_error"
                err_msg = "; ".join(all_errors) if all_errors else ""
                self.store.set_state("last_sync_at", now_iso)
                self.store.set_state("last_sync_status", "ok" if all_ok else "partial_error")
                self.store.set_state("last_error", err_msg)

                return {
                    "status": status,
                    "trigger_source": trigger_source,
                    "scope_folder_id": scope_folder_id,
                    "scoped_mismatch_requests": scoped_mismatch_requests,
                    "blocker": CREATOR_DRIVE_SYNC_BLOCKER if (scoped_mismatch_requests or files_ingested == 0) else None,
                    "files_checked": files_checked,
                    "files_downloaded": files_downloaded,
                    "files_ingested": files_ingested,
                    "files_skipped_market": files_skipped_market,
                    "videos_inserted": videos_inserted,
                    "videos_deduped": videos_deduped,
                    "error_message": err_msg or None,
                    "finished_at": now_iso,
                }
            finally:
                with self._state_lock:
                    self._is_syncing = False
