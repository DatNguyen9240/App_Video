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

from .cloudflare_client import CloudflareBridgeClient
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
    ):
        self.store = store
        self.client = client
        self.sync_service = sync_service
        self.poll_interval_sec = max(3, int(poll_interval_sec))
        self.heartbeat_interval_sec = max(10, int(heartbeat_interval_sec))

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_heartbeat_ts = 0.0
        self._last_poll_at: Optional[str] = None
        self._last_poll_error: Optional[str] = None
        self._lock = threading.Lock()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="RemoteCommandPoller",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

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
            self._stop_event.wait(timeout=self.poll_interval_sec)

    def send_heartbeat_once(self) -> bool:
        self.client.reload_config()
        if not self.client.is_configured():
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
        self.client.send_heartbeat(payload)
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
                )
                self.store.mark_command_acked(cmd_id)
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

        # 1. Strict business-command allowlist check
        if command_type not in ALLOWED_COMMANDS:
            err = f"Rejected disallowed command type: {command_type}"
            self.store.save_command_result(command_id, command_type, "failed", error_message=err, acked=False)
            try:
                self.client.complete_command(command_id, "failed", error_message=err)
                self.store.mark_command_acked(command_id)
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
            try:
                self.client.complete_command(
                    command_id,
                    existing["status"],
                    result=res_obj,
                    error_message=existing.get("error_message"),
                )
                self.store.mark_command_acked(command_id)
            except Exception:
                pass
            return {"command_id": command_id, "status": existing["status"], "reused_local_result": True}

        # 3. Execute once and record result locally before acknowledging
        self.store.mark_command_running(command_id, command_type)
        try:
            result = self._execute_business_command(command_id, command_type)
            status = "failed" if result.get("status") == "failed" else "completed"
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
        )

        try:
            self.client.complete_command(
                command_id=command_id,
                status=status,
                result=result,
                error_message=err_msg,
            )
            self.store.mark_command_acked(command_id)
        except Exception:
            # Saved with pending_ack=1; flush_pending_acks() will deliver on next cycle
            pass

        return {"command_id": command_id, "status": status, "result": result, "error": err_msg}

    def poll_once(self) -> Optional[Dict[str, Any]]:
        """Run one poll cycle: heartbeat + flush pending acks + claim/execute up to 1 command."""
        self.client.reload_config()
        if not self.client.is_configured():
            with self._lock:
                self._last_poll_error = "Missing CLOUDFLARE_APP_TOKEN (unconfigured)"
            return None

        now_mono = time.monotonic()
        if now_mono - self._last_heartbeat_ts >= self.heartbeat_interval_sec:
            try:
                self.send_heartbeat_once()
            except Exception as exc:
                with self._lock:
                    self._last_poll_error = str(exc)

        self.flush_pending_acks()

        cmd = self.client.claim_next_command(lease_seconds=120)
        from datetime import datetime, timezone
        with self._lock:
            self._last_poll_at = datetime.now(timezone.utc).isoformat()
            self._last_poll_error = None

        if not cmd:
            return None
        return self.handle_claimed_command(cmd)
