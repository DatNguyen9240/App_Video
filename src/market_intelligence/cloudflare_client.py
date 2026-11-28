"""
Authenticated Cloudflare Worker Bridge Client for App_Video.

Communicates exclusively with the Cloudflare Worker (`app-video-scheduler`)
over HTTPS using Bearer token authentication. Never stores Google Service
Account keys locally and never logs raw secrets.
"""

import os
import threading
import time
from typing import Any, Dict, Optional, Tuple
import requests


DEFAULT_WORKER_URL = "https://app-video-scheduler.doctortphat.workers.dev"
DEFAULT_CLIENT_ID = "app-video-desktop"
DEFAULT_MUSE_WEB_URL = "https://muse.ai/"


class StaleClaimConflictError(RuntimeError):
    """Raised when the Worker rejects completion due to stale claim ownership, attempt, or expired lease (HTTP 409)."""


def mask_secret(value: Optional[str]) -> str:
    """Safely mask secret tokens for UI display or logs."""
    if not value:
        return "<not_configured>"
    s = str(value).strip()
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}...{s[-4:]}"


def _read_env_file_vars(env_path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not env_path or not os.path.exists(env_path):
        return out
    try:
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


class CloudflareBridgeClient:
    """Authenticated HTTP client for App_Video <-> Cloudflare Worker API."""

    def __init__(
        self,
        worker_url: Optional[str] = None,
        api_token: Optional[str] = None,
        client_id: Optional[str] = None,
        env_path: Optional[str] = None,
        timeout_sec: Any = (10.0, 45.0),
        heartbeat_timeout_sec: Any = (5.0, 10.0),
        session: Optional[requests.Session] = None,
        heartbeat_session: Optional[requests.Session] = None,
    ):
        if env_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            env_path = os.path.join(base_dir, ".env")
        self.env_path = env_path
        self.timeout_sec = timeout_sec
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self._session = session or requests.Session()
        self._heartbeat_session = heartbeat_session or requests.Session()
        self._http_lock = threading.RLock()
        self._heartbeat_lock = threading.RLock()
        self._cfg_lock = threading.RLock()
        self._explicit_url = worker_url
        self._explicit_token = api_token
        self._explicit_client_id = client_id
        self.reload_config()

    def close(self):
        for sess in (getattr(self, "_session", None), getattr(self, "_heartbeat_session", None)):
            if sess is not None:
                try:
                    sess.close()
                except Exception:
                    pass

    def reload_config(self):
        with self._cfg_lock:
            env_vars = _read_env_file_vars(self.env_path)
            self.worker_url = (
                self._explicit_url
                or os.environ.get("CLOUDFLARE_WORKER_URL")
                or env_vars.get("CLOUDFLARE_WORKER_URL")
                or DEFAULT_WORKER_URL
            ).rstrip("/")
            self.api_token = (
                self._explicit_token
                if self._explicit_token is not None
                else (
                    os.environ.get("CLOUDFLARE_APP_TOKEN")
                    or env_vars.get("CLOUDFLARE_APP_TOKEN")
                    or os.environ.get("APP_VIDEO_API_TOKEN")
                    or env_vars.get("APP_VIDEO_API_TOKEN")
                    or ""
                )
            ).strip()
            self.client_id = (
                self._explicit_client_id
                or os.environ.get("CLOUDFLARE_CLIENT_ID")
                or env_vars.get("CLOUDFLARE_CLIENT_ID")
                or DEFAULT_CLIENT_ID
            ).strip()
            raw_muse_url = (
                os.environ.get("MUSE_WEB_URL")
                or env_vars.get("MUSE_WEB_URL")
                or DEFAULT_MUSE_WEB_URL
            ).strip()
            if not raw_muse_url.startswith(("http://", "https://")):
                raw_muse_url = DEFAULT_MUSE_WEB_URL
            self.muse_web_url = raw_muse_url

    def is_configured(self) -> bool:
        with self._cfg_lock:
            return bool(self.worker_url and self.api_token)

    def get_public_config_summary(self) -> Dict[str, Any]:
        with self._cfg_lock:
            return {
                "worker_url": self.worker_url,
                "client_id": self.client_id,
                "token_configured": bool(self.api_token),
                "token_masked": mask_secret(self.api_token),
                "muse_web_url": getattr(self, "muse_web_url", DEFAULT_MUSE_WEB_URL),
            }

    def _headers(self) -> Dict[str, str]:
        with self._cfg_lock:
            return {
                "Authorization": f"Bearer {self.api_token}",
                "X-App-Client-Id": self.client_id,
                "Accept": "application/json",
                "User-Agent": "AppVideo-BridgeClient/2.4",
            }

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        max_attempts: int = 3,
        use_heartbeat_session: bool = False,
        timeout_override: Any = None,
    ) -> requests.Response:
        with self._cfg_lock:
            if not (self.worker_url and self.api_token):
                raise RuntimeError("Missing CLOUDFLARE_APP_TOKEN (unconfigured)")
            url = f"{self.worker_url}{path}"
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "X-App-Client-Id": self.client_id,
                "Accept": "application/json",
                "User-Agent": "AppVideo-BridgeClient/2.4",
            }

        target_session = self._heartbeat_session if use_heartbeat_session else self._session
        target_lock = self._heartbeat_lock if use_heartbeat_session else self._http_lock
        req_timeout = timeout_override if timeout_override is not None else self.timeout_sec
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                with target_lock:
                    resp = target_session.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        timeout=req_timeout,
                    )
                if resp.status_code in (502, 503, 504) and attempt < max_attempts:
                    time.sleep(0.4 * attempt)
                    continue
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < max_attempts:
                    time.sleep(0.4 * attempt)
                    continue
                raise RuntimeError(f"Network error contacting Cloudflare Worker: {type(exc).__name__}") from exc

        raise RuntimeError(f"Cloudflare request failed: {last_exc}")

    def check_connection(self) -> Dict[str, Any]:
        self.reload_config()
        if not self.is_configured():
            return {
                "configured": False,
                "connected": False,
                "status": "unconfigured",
                "error": "Thiếu CLOUDFLARE_APP_TOKEN trong .env hoặc biến môi trường.",
            }
        try:
            resp = self._request_with_retry("GET", "/api/app/health", max_attempts=2)
            if resp.status_code in (401, 403):
                return {
                    "configured": True,
                    "connected": False,
                    "status": "auth_failed",
                    "status_code": resp.status_code,
                    "error": f"Xác thực Bearer token thất bại (HTTP {resp.status_code}).",
                }
            if not resp.ok:
                return {
                    "configured": True,
                    "connected": False,
                    "status": "error",
                    "status_code": resp.status_code,
                    "error": f"Worker trả về HTTP {resp.status_code}.",
                }
            data = resp.json()
            return {
                "configured": True,
                "connected": bool(data.get("ok", True)),
                "status": "connected" if data.get("ok", True) else "degraded",
                "status_code": resp.status_code,
                "health": data,
                "error": None,
            }
        except Exception as exc:
            return {
                "configured": True,
                "connected": False,
                "status": "unreachable",
                "error": str(exc),
            }

    def list_market_files(
        self,
        cursor: Optional[str] = None,
        limit: int = 50,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        if cursor:
            params["cursor"] = cursor
        if page_token:
            params["page_token"] = page_token
        resp = self._request_with_retry("GET", "/api/app/market/files", params=params)
        if not resp.ok:
            raise RuntimeError(f"list_market_files failed: HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("ok", True):
            raise RuntimeError(data.get("error", "Unknown error listing market files"))
        return data

    def download_market_file(self, file_id: str) -> Tuple[bytes, Dict[str, str]]:
        resp = self._request_with_retry("GET", f"/api/app/market/files/{file_id}")
        if not resp.ok:
            raise RuntimeError(f"download_market_file failed: HTTP {resp.status_code}")
        headers_meta = {
            "file_id": resp.headers.get("X-Drive-File-Id", file_id),
            "file_name": resp.headers.get("X-Drive-File-Name", ""),
            "modified_time": resp.headers.get("X-Drive-Modified-Time", ""),
            "md5_checksum": resp.headers.get("X-Drive-Md5", ""),
        }
        return resp.content, headers_meta

    def send_heartbeat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._request_with_retry(
            "POST",
            "/api/app/heartbeat",
            json_body=payload,
            max_attempts=1,
            use_heartbeat_session=True,
            timeout_override=self.heartbeat_timeout_sec,
        )
        if not resp.ok:
            raise RuntimeError(f"heartbeat failed: HTTP {resp.status_code}")
        return resp.json()

    def claim_next_command(self, lease_seconds: int = 120) -> Optional[Dict[str, Any]]:
        resp = self._request_with_retry(
            "POST",
            "/api/app/commands/claim",
            json_body={"lease_seconds": lease_seconds},
            max_attempts=2,
        )
        if not resp.ok:
            raise RuntimeError(f"claim_next_command failed: HTTP {resp.status_code}")
        data = resp.json()
        return data.get("command")

    def complete_command(
        self,
        command_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        *,
        claimed_by: Optional[str] = None,
        attempt: Optional[int] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "status": status,
            "result": result or {},
            "error_message": error_message,
        }
        if claimed_by is not None:
            body["claimed_by"] = claimed_by
        if attempt is not None:
            body["attempt"] = int(attempt)
        resp = self._request_with_retry(
            "POST",
            f"/api/app/commands/{command_id}/complete",
            json_body=body,
            max_attempts=3,
        )
        if resp.status_code == 409:
            raise StaleClaimConflictError(f"complete_command rejected as stale claim (HTTP 409): {resp.text[:200]}")
        if not resp.ok:
            raise RuntimeError(f"complete_command failed: HTTP {resp.status_code}")
        return resp.json()

    def list_crawl_requests(self) -> Dict[str, Any]:
        resp = self._request_with_retry("GET", "/api/app/crawl-requests", max_attempts=2)
        if not resp.ok:
            raise RuntimeError(f"list_crawl_requests failed: HTTP {resp.status_code}")
        return resp.json()

    def create_crawl_request(
        self,
        platform: str,
        keywords: str = "",
        requested_count: int = 50,
        poll_profile: Optional[str] = None,
        dedup_token: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "platform": platform,
            "keywords": keywords,
            "requested_count": max(1, min(int(requested_count or 50), 500)),
            "options": options or {},
        }
        if poll_profile:
            payload["poll_profile"] = poll_profile
        if dedup_token:
            payload["dedup_token"] = dedup_token
        resp = self._request_with_retry(
            "POST",
            "/api/app/crawl-requests",
            json_body=payload,
            max_attempts=2,
        )
        if not resp.ok:
            raise RuntimeError(f"create_crawl_request failed: HTTP {resp.status_code}")
        return resp.json()

    def cancel_crawl_request(self, crawl_id: str) -> Dict[str, Any]:
        resp = self._request_with_retry(
            "POST",
            f"/api/app/crawl-requests/{crawl_id}/cancel",
            json_body={},
            max_attempts=2,
        )
        if not resp.ok:
            raise RuntimeError(f"cancel_crawl_request failed: HTTP {resp.status_code}")
        return resp.json()

    def get_crawl_request_prompt(self, crawl_id: str) -> Dict[str, Any]:
        resp = self._request_with_retry(
            "GET",
            f"/api/app/crawl-requests/{crawl_id}/prompt",
            max_attempts=2,
        )
        if not resp.ok:
            raise RuntimeError(f"get_crawl_request_prompt failed: HTTP {resp.status_code}")
        return resp.json()

    def set_crawl_schedule(self, poll_profile: str) -> Dict[str, Any]:
        resp = self._request_with_retry(
            "POST",
            "/api/app/crawl-schedule",
            json_body={"poll_profile": poll_profile},
            max_attempts=2,
        )
        if not resp.ok:
            raise RuntimeError(f"set_crawl_schedule failed: HTTP {resp.status_code}")
        return resp.json()

    def check_cross_run_dedup(self, items: list, batch_size: int = 100) -> Dict[str, Any]:
        """
        Check historical post registry on Worker V2/D1 in full lossless batches.
        Fail-closed: raises RuntimeError if registry is unavailable (never treat lookup failure as empty history).
        """
        all_items = list(items or [])
        if not all_items:
            return {"ok": True, "existing": {}, "existing_keys": [], "existing_count": 0, "new_count": 0}
        step = max(1, min(int(batch_size or 100), 250))
        merged_existing: Dict[str, Any] = {}
        merged_keys: list = []
        seen_keys = set()
        for i in range(0, len(all_items), step):
            chunk = all_items[i : i + step]
            resp = self._request_with_retry(
                "POST",
                "/api/app/market/dedup/check",
                json_body={"items": chunk},
                max_attempts=2,
            )
            if not resp.ok:
                raise RuntimeError(f"RegistryUnavailable: check_cross_run_dedup failed HTTP {resp.status_code}")
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"RegistryUnavailable: {data.get('error') or 'unknown error'}")
            for k, v in (data.get("existing") or {}).items():
                merged_existing[k] = v
            for k in (data.get("existing_keys") or []):
                if k not in seen_keys:
                    seen_keys.add(k)
                    merged_keys.append(k)
        return {
            "ok": True,
            "existing": merged_existing,
            "existing_keys": merged_keys,
            "existing_count": len(merged_keys),
            "new_count": max(0, len(all_items) - len(merged_keys)),
        }

    def record_cross_run_dedup(
        self,
        items: list,
        run_id: Optional[str] = None,
        *,
        request_id: Optional[str] = None,
        claimed_by: Optional[str] = None,
        attempt: Optional[int] = None,
        batch_size: int = 100,
    ) -> Dict[str, Any]:
        """Atomically record ingested post keys/snapshots into shared Worker V2 D1 registry without truncating batches."""
        all_items = list(items or [])
        if not all_items:
            return {"ok": True, "new_count": 0, "duplicate_count": 0, "updated_count": 0, "missing_views_count": 0, "total_processed": 0}
        step = max(1, min(int(batch_size or 100), 250))
        totals = {"ok": True, "new_count": 0, "duplicate_count": 0, "updated_count": 0, "missing_views_count": 0, "total_processed": 0}
        for i in range(0, len(all_items), step):
            chunk = all_items[i : i + step]
            body: Dict[str, Any] = {"run_id": run_id, "items": chunk}
            if request_id is not None:
                body["request_id"] = request_id
            if claimed_by is not None:
                body["claimed_by"] = claimed_by
            if attempt is not None:
                body["attempt"] = int(attempt)
            resp = self._request_with_retry(
                "POST",
                "/api/app/market/dedup/record",
                json_body=body,
                max_attempts=2,
            )
            if resp.status_code == 409:
                raise StaleClaimConflictError(f"record_cross_run_dedup rejected (HTTP 409): {resp.text[:200]}")
            if not resp.ok:
                raise RuntimeError(f"RegistryUnavailable: record_cross_run_dedup failed HTTP {resp.status_code}")
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"RegistryUnavailable: {data.get('error') or 'unknown error'}")
            totals["new_count"] += int(data.get("new_count") or 0)
            totals["duplicate_count"] += int(data.get("duplicate_count") or 0)
            totals["updated_count"] += int(data.get("updated_count") or 0)
            totals["missing_views_count"] += int(data.get("missing_views_count") or 0)
            totals["total_processed"] += int(data.get("total_processed") or len(chunk))
        return totals
