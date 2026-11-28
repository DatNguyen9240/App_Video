"""
Outbound D1 Command Queue Poller for Telegram Remote Control.

Polls Cloudflare Worker (`/api/app/commands/claim`) without requiring any
open inbound port or tunnel. Enforces:
  - Strict predefined business-command allowlist (no arbitrary shell/code)
  - Exactly-once effective execution across restarts and network interruptions
  - Heartbeat reporting for Telegram online/offline status visibility
"""

import json
import threading
import time
from typing import Any, Dict, Optional

from .cloudflare_client import CloudflareBridgeClient, StaleClaimConflictError
from .storage import MarketIntelligenceStore
from .sync_service import MarketSyncService


ALLOWED_COMMANDS = frozenset(
    {
        "app_status",
        "sync_now",
        "last_sync_result",
        "market_stats",
    }
)


class RemoteCommandPoller:
    """Background worker polling D1 commands and sending App_Video heartbeats."""

    def __init__(
        self,
        store: MarketIntelligenceStore,
        client: CloudflareBridgeClient,
        sync_service: MarketSyncService,
        poll_interval_sec: int = 10,
        heartbeat_interval_sec: int = 30,
        heartbeat_client: Optional[CloudflareBridgeClient] = None,
    ):
        self.store = store
        self.client = client
        self.heartbeat_client = heartbeat_client or client
        self.sync_service = sync_service
        self.poll_interval_sec = max(3, int(poll_interval_sec))
        self.heartbeat_interval_sec = max(10, int(heartbeat_interval_sec))

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._last_heartbeat_ts = 0.0
        self._last_crawl_check_ts = 0.0
        self._last_poll_at: Optional[str] = None
        self._last_poll_error: Optional[str] = None
        self._lock = threading.Lock()

    def start(self):
        self._stop_event.clear()
        if not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="RemoteCommandPoller",
                daemon=True,
            )
            self._thread.start()
        if not (self._heartbeat_thread and self._heartbeat_thread.is_alive()):
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="RemoteHeartbeatLoop",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=timeout)

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "configured": self.client.is_configured(),
                "poll_interval_sec": self.poll_interval_sec,
                "heartbeat_interval_sec": self.heartbeat_interval_sec,
                "last_poll_at": self._last_poll_at,
                "last_poll_error": self._last_poll_error,
                "allowed_commands": sorted(ALLOWED_COMMANDS),
                "recent_commands": self.store.list_recent_remote_commands(limit=15),
            }

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                with self._lock:
                    self._last_poll_error = str(exc)
            try:
                if (time.monotonic() - self._last_crawl_check_ts) >= max(15.0, float(self.poll_interval_sec)):
                    self.check_crawl_results_once()
            except Exception:
                pass
            self._stop_event.wait(timeout=self.poll_interval_sec)

    def check_crawl_results_once(self) -> Dict[str, Any]:
        """Poll Worker crawl queue and trigger debounced auto-sync when new results exist."""
        self._last_crawl_check_ts = time.monotonic()
        self.client.reload_config()
        if not self.client.is_configured() or not hasattr(self.client, "list_crawl_requests"):
            return {"triggered": False, "reason": "unconfigured"}
        data = self.client.list_crawl_requests()
        items = data.get("items") if isinstance(data, dict) else None
        return self.sync_service.maybe_trigger_sync_for_crawl_requests(items)

    def _heartbeat_loop(self):
        while not self._stop_event.is_set():
            try:
                self.send_heartbeat_once()
            except Exception as exc:
                with self._lock:
                    self._last_poll_error = str(exc)
            self._stop_event.wait(timeout=self.heartbeat_interval_sec)

    def send_heartbeat_once(self) -> bool:
        hb_client = self.heartbeat_client
        hb_client.reload_config()
        if not hb_client.is_configured():
            return False
        stats = self.store.get_stats()
        try:
            latest_report = self.store.build_evidence_report(platform="all")
        except Exception:
            latest_report = None
        payload = {
            "app_status": "online",
            "sync_status": "syncing" if self.sync_service.is_syncing else stats.get("last_sync_status", "idle"),
            "last_sync_at": stats.get("last_sync_at"),
            "last_sync_summary": stats.get("latest_run") or {},
            "market_counts": {
                "total_items": stats.get("total_items", 0),
                "by_platform": stats.get("by_platform", {}),
                "by_quality": stats.get("by_quality", {}),
                "by_platform_quality": stats.get("by_platform_quality", {}),
            },
            "latest_report": latest_report,
            "sync_interval_seconds": stats.get("sync_interval_seconds", 300),
            "app_version": "2.4",
        }
        hb_client.send_heartbeat(payload)
        self._last_heartbeat_ts = time.monotonic()
        return True

    def flush_pending_acks(self):
        """Retry reporting locally completed commands whose D1 ack was interrupted by network drop."""
        pending = self.store.list_pending_ack_commands()
        for entry in pending:
            cmd_id = entry["command_id"]
            try:
                res_obj = json.loads(entry.get("result_json") or "{}")
            except Exception:
                res_obj = {}
            try:
                self.client.complete_command(
                    command_id=cmd_id,
                    status=entry["status"],
                    result=res_obj,
                    error_message=entry.get("error_message"),
                    claimed_by=entry.get("claimed_by") or self.client.client_id,
                    attempt=entry.get("attempt"),
                )
                self.store.mark_command_acked(cmd_id)
            except StaleClaimConflictError as conflict_exc:
                # Server rejected stale claim/attempt/lease -> retire pending_ack safely instead of infinite 409 loop
                self.store.mark_command_superseded(cmd_id, str(conflict_exc))
            except Exception:
                # Will retry on next poll cycle without re-executing command
                break

    def _execute_business_command(self, command_id: str, command_type: str) -> Dict[str, Any]:
        if command_type not in ALLOWED_COMMANDS:
            raise ValueError(f"Unsupported command type: {command_type}")

        if command_type == "app_status":
            stats = self.store.get_stats()
            return {
                "online": True,
                "client_id": self.client.client_id,
                "sync_status": "syncing" if self.sync_service.is_syncing else stats.get("last_sync_status", "idle"),
                "sync_interval_seconds": stats.get("sync_interval_seconds", 300),
                "last_sync_at": stats.get("last_sync_at"),
                "total_items": stats.get("total_items", 0),
                "by_platform": stats.get("by_platform", {}),
            }

        if command_type == "sync_now":
            return self.sync_service.sync_now(trigger_source="telegram_command", command_id=command_id)

        if command_type == "last_sync_result":
            stats = self.store.get_stats()
            return {
                "latest_run": stats.get("latest_run"),
                "last_sync_at": stats.get("last_sync_at"),
                "last_sync_status": stats.get("last_sync_status"),
                "total_items": stats.get("total_items", 0),
                "by_platform": stats.get("by_platform", {}),
            }

        if command_type == "market_stats":
            return self.store.get_stats()

        raise ValueError(f"Unsupported command type: {command_type}")

    def handle_claimed_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Process one claimed command idempotently."""
        command_id = str(cmd["id"])
        command_type = str(cmd.get("command_type") or "")
        attempt = cmd.get("attempts") if cmd.get("attempts") is not None else cmd.get("attempt")
        if attempt is not None:
            try:
                attempt = int(attempt)
            except Exception:
                attempt = None
        claimed_by = str(cmd.get("claimed_by") or self.client.client_id)
        lease_expires_at = cmd.get("lease_expires_at")

        # 1. Strict business-command allowlist check
        if command_type not in ALLOWED_COMMANDS:
            err = f"Rejected disallowed command type: {command_type}"
            self.store.save_command_result(
                command_id,
                command_type,
                "failed",
                error_message=err,
                acked=False,
                attempt=attempt,
                claimed_by=claimed_by,
                lease_expires_at=lease_expires_at,
            )
            try:
                self.client.complete_command(
                    command_id,
                    "failed",
                    error_message=err,
                    claimed_by=claimed_by,
                    attempt=attempt,
                )
                self.store.mark_command_acked(command_id)
            except StaleClaimConflictError as conflict_exc:
                self.store.mark_command_superseded(command_id, str(conflict_exc))
            except Exception:
                pass
            return {"command_id": command_id, "status": "failed", "error": err}

        # 2. Local idempotency ledger check (prevents duplicate execution across restarts / lease re-claims)
        existing = self.store.get_local_command(command_id)
        if existing and existing["status"] in ("completed", "failed"):
            try:
                res_obj = json.loads(existing.get("result_json") or "{}")
            except Exception:
                res_obj = {}
            eff_attempt = attempt if attempt is not None else existing.get("attempt")
            eff_owner = claimed_by or existing.get("claimed_by") or self.client.client_id
            try:
                self.client.complete_command(
                    command_id,
                    existing["status"],
                    result=res_obj,
                    error_message=existing.get("error_message"),
                    claimed_by=eff_owner,
                    attempt=eff_attempt,
                )
                self.store.mark_command_acked(command_id)
            except StaleClaimConflictError as conflict_exc:
                self.store.mark_command_superseded(command_id, str(conflict_exc))
            except Exception:
                pass
            return {"command_id": command_id, "status": existing["status"], "reused_local_result": True}

        # 3. Execute once and record result locally before acknowledging
        self.store.mark_command_running(
            command_id,
            command_type,
            attempt=attempt,
            claimed_by=claimed_by,
            lease_expires_at=lease_expires_at,
        )
        try:
            result = self._execute_business_command(command_id, command_type)
            status = "failed" if result.get("status") in ("failed", "partial_error") else "completed"
            err_msg = result.get("error_message")
        except Exception as exc:
            result = {}
            status = "failed"
            err_msg = str(exc)

        self.store.save_command_result(
            command_id=command_id,
            command_type=command_type,
            status=status,
            result=result,
            error_message=err_msg,
            acked=False,
            attempt=attempt,
            claimed_by=claimed_by,
            lease_expires_at=lease_expires_at,
        )

        try:
            self.client.complete_command(
                command_id=command_id,
                status=status,
                result=result,
                error_message=err_msg,
                claimed_by=claimed_by,
                attempt=attempt,
            )
            self.store.mark_command_acked(command_id)
        except StaleClaimConflictError as conflict_exc:
            self.store.mark_command_superseded(command_id, str(conflict_exc))
        except Exception:
            # Saved with pending_ack=1; flush_pending_acks() will deliver on next cycle
            pass

        return {"command_id": command_id, "status": status, "result": result, "error": err_msg}

    def poll_once(self) -> Optional[Dict[str, Any]]:
        """Run one poll cycle: flush pending acks + claim/execute up to 1 command (plus standalone heartbeat fallback)."""
        self.client.reload_config()
        if not self.client.is_configured():
            with self._lock:
                self._last_poll_error = "Missing CLOUDFLARE_APP_TOKEN (unconfigured)"
            return None

        hb_bg_alive = bool(self._heartbeat_thread and self._heartbeat_thread.is_alive())
        now_mono = time.monotonic()
        if not hb_bg_alive and (now_mono - self._last_heartbeat_ts >= self.heartbeat_interval_sec):
            try:
                self.send_heartbeat_once()
            except Exception as exc:
                with self._lock:
                    self._last_poll_error = str(exc)

        self.flush_pending_acks()

        cmd = self.client.claim_next_command(lease_seconds=300)
        from datetime import datetime, timezone
        with self._lock:
            self._last_poll_at = datetime.now(timezone.utc).isoformat()
            self._last_poll_error = None

        if not cmd:
            return None
        return self.handle_claimed_command(cmd)
