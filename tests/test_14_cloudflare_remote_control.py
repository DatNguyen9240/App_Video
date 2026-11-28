"""
Test Suite 14: Cloudflare Bridge, Incremental Sync, Remote D1 Queue & Flask API
===============================================================================
Uses an explicit MockCloudflareServer / fake requests transport to test local
integration cleanly even when APP_VIDEO_API_TOKEN is absent:
  - Authenticated Bearer token headers and strict secret masking
  - Incremental Drive sync (cursor + md5 skip) & single-flight concurrency lock
  - D1 queue state machine (queued -> running -> completed/failed)
  - Offline command queueing -> catch-up on online startup
  - Network-drop crash recovery (pending_ack retries without duplicate execution)
  - Strict business-command allowlist rejecting shell/arbitrary commands
  - Flask endpoints (/api/market/*, /api/remote/*) and UI tab rendering
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT_DIR, "src")
FIXTURES_DIR = os.path.join(ROOT_DIR, "tests", "fixtures", "market")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import app_ui
from market_intelligence import (
    CloudflareBridgeClient,
    MarketIntelligenceStore,
    MarketSyncService,
    RemoteCommandPoller,
    mask_secret,
)
from market_intelligence.normalizer import is_archive_path


class MockCloudflareTransportClient(CloudflareBridgeClient):
    """In-memory mock mimicking Worker V2 endpoints + D1 queue for deterministic local tests."""

    def __init__(self, api_token: str = "test-secret-token-1234567890"):
        self.mock_files = {}
        self.mock_commands = []
        self.mock_heartbeats = []
        self.mock_crawl_requests = []
        self.auth_headers_seen = []
        self.fail_next_complete = False
        self.conflict_next_complete = False
        self.list_call_count = 0
        self.download_call_count = 0
        self.slow_download_delay = 0.0
        super().__init__(
            worker_url="https://app-video-scheduler.doctortphat.workers.dev",
            api_token=api_token,
            client_id="test-client",
        )

    def reload_config(self):
        # Preserve explicit test token instead of reading workspace .env
        self.worker_url = (self._explicit_url or "https://app-video-scheduler.doctortphat.workers.dev").rstrip("/")
        self.api_token = (self._explicit_token or "").strip()
        self.client_id = self._explicit_client_id or "test-client"

    def add_drive_file(
        self,
        file_id: str,
        name: str,
        raw_bytes: bytes,
        modified_time: str,
        md5: str,
        platform_hint: str = "threads",
        folder_path: str = "Threads",
    ):
        self.mock_files[file_id] = {
            "file_id": file_id,
            "name": name,
            "folder_path": folder_path,
            "modified_time": modified_time,
            "md5_checksum": md5,
            "platform_hint": platform_hint,
            "bytes": raw_bytes,
        }

    def enqueue_d1_command(self, cmd_id: str, command_type: str, payload: dict = None):
        self.mock_commands.append({
            "id": cmd_id,
            "command_type": command_type,
            "payload": payload or {},
            "status": "queued",
            "attempts": 0,
            "result": None,
            "error_message": None,
        })

    def check_connection(self):
        if not self.is_configured():
            return {"configured": False, "connected": False, "status": "unconfigured", "error": "Missing token"}
        self.auth_headers_seen.append(self._headers()["Authorization"])
        return {"configured": True, "connected": True, "status": "connected", "health": {"ok": True, "db_ok": True, "drive_ok": True}}

    def list_market_files(self, cursor=None, limit=50, page_token=None):
        self.list_call_count += 1
        self.auth_headers_seen.append(self._headers()["Authorization"])
        ordered = sorted(
            self.mock_files.values(),
            key=lambda x: (str(x["modified_time"] or ""), str(x["file_id"])),
        )
        filtered = []
        tok_mtime, tok_fid = ("", "")
        if page_token and "|" in page_token:
            tok_mtime, tok_fid = page_token.split("|", 1)
        for f in ordered:
            fm = str(f["modified_time"] or "")
            fid = str(f["file_id"])
            if cursor and fm < cursor:
                continue
            if page_token and (fm, fid) <= (tok_mtime, tok_fid):
                continue
            filtered.append({k: v for k, v in f.items() if k != "bytes"})
        eff_lim = max(1, min(int(limit or 50), 100))
        page = filtered[:eff_lim]
        has_more = len(filtered) > eff_lim
        next_tok = f"{page[-1]['modified_time']}|{page[-1]['file_id']}" if (has_more and page) else None
        next_cur = page[-1]["modified_time"] if page else cursor
        all_active_ids = [
            str(f["file_id"])
            for f in ordered
            if not is_archive_path(f.get("folder_path")) and not is_archive_path(f.get("name"))
        ]
        res = {
            "ok": True,
            "files": page,
            "has_more": has_more,
            "next_page_token": next_tok,
            "next_cursor": next_cur,
        }
        if not page_token:
            res["active_file_ids"] = all_active_ids
        return res

    def download_market_file(self, file_id: str):
        if self.slow_download_delay > 0:
            time.sleep(self.slow_download_delay)
        self.download_call_count += 1
        f = self.mock_files[file_id]
        return f["bytes"], {
            "file_id": file_id,
            "file_name": f["name"],
            "modified_time": f["modified_time"],
            "md5_checksum": f["md5_checksum"],
        }

    def send_heartbeat(self, payload):
        self.mock_heartbeats.append(payload)
        return {"ok": True}

    def claim_next_command(self, lease_seconds: int = 120):
        for c in self.mock_commands:
            if c["status"] == "queued":
                c["status"] = "running"
                c["attempts"] += 1
                return dict(c)
        return None

    def complete_command(self, command_id, status, result=None, error_message=None, *, claimed_by=None, attempt=None):
        if self.fail_next_complete:
            self.fail_next_complete = False
            raise RuntimeError("Simulated network failure during ack")
        if self.conflict_next_complete:
            self.conflict_next_complete = False
            from market_intelligence.cloudflare_client import StaleClaimConflictError
            raise StaleClaimConflictError("Simulated HTTP 409 stale claim/attempt")
        self.complete_payloads = getattr(self, "complete_payloads", [])
        self.complete_payloads.append({"command_id": command_id, "claimed_by": claimed_by, "attempt": attempt})
        for c in self.mock_commands:
            if c["id"] == command_id:
                if c["status"] in ("completed", "failed", "cancelled"):
                    return {"ok": True, "already_finalized": True, "status": c["status"]}
                c["status"] = status
                c["result"] = result
                c["error_message"] = error_message
                return {"ok": True, "already_finalized": False, "status": status}
        raise RuntimeError("Command not found")

    def list_crawl_requests(self):
        return {
            "ok": True,
            "schedule": {"poll_profile": getattr(self, "poll_profile", "production_6h"), "expected_pickup_at": "2026-11-26T20:00:00Z"},
            "items": list(self.mock_crawl_requests),
        }

    def create_crawl_request(self, platform: str, keywords: str = "", requested_count: int = 50, poll_profile=None, dedup_token=None, options=None):
        for existing in self.mock_crawl_requests:
            if existing["platform"] == platform and existing["keywords"] == keywords and existing["requested_count"] == requested_count and existing["status"] in ("queued", "running"):
                return {"ok": True, "deduplicated": True, "request": existing}
        req = {
            "id": f"crw_{len(self.mock_crawl_requests) + 1}",
            "platform": platform,
            "keywords": keywords,
            "requested_count": requested_count,
            "options": dict(options or {}),
            "poll_profile": poll_profile or getattr(self, "poll_profile", "production_6h"),
            "expected_pickup_at": "2026-11-26T20:00:00Z",
            "status": "queued",
        }
        self.mock_crawl_requests.append(req)
        return {"ok": True, "deduplicated": False, "request": req}

    def cancel_crawl_request(self, crawl_id: str):
        for r in self.mock_crawl_requests:
            if r["id"] == crawl_id and r["status"] == "queued":
                r["status"] = "cancelled"
                return {"ok": True, "id": crawl_id, "status": "cancelled"}
        raise RuntimeError("cancel_crawl_request failed: HTTP 409")

    def set_crawl_schedule(self, poll_profile: str):
        self.poll_profile = "test_5m" if poll_profile == "test_5m" else "production_6h"
        return {"ok": True, "poll_profile": self.poll_profile, "expected_pickup_at": "2026-11-26T15:05:00Z"}

    def check_cross_run_dedup(self, items: list, **kwargs):
        reg = getattr(self, "mock_registry", {})
        existing = {it.get("dedup_key"): reg[it.get("dedup_key")] for it in (items or []) if it.get("dedup_key") in reg}
        return {"ok": True, "existing": existing, "existing_keys": list(existing.keys()), "existing_count": len(existing)}

    def record_cross_run_dedup(self, items: list, run_id: str = None, **kwargs):
        if not hasattr(self, "mock_registry"):
            self.mock_registry = {}
        new_c = 0
        dup_c = 0
        upd_c = 0
        for it in (items or []):
            k = it.get("dedup_key")
            if not k:
                continue
            prev = self.mock_registry.get(k)
            if not prev:
                self.mock_registry[k] = {"metrics": it.get("metrics") or {}, "run_id": run_id, "snapshot_count": 1}
                new_c += 1
            elif prev["metrics"] != (it.get("metrics") or {}):
                prev["metrics"] = it.get("metrics") or {}
                prev["snapshot_count"] += 1
                upd_c += 1
            else:
                prev["snapshot_count"] += 1
                dup_c += 1
        return {"ok": True, "new_count": new_c, "duplicate_count": dup_c, "updated_count": upd_c}


class TestCloudflareSyncAndRemoteControl(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.client = MockCloudflareTransportClient()
        with open(os.path.join(FIXTURES_DIR, "threads_crawl_poc_002.json"), "rb") as f:
            self.poc2_bytes = f.read()
        with open(os.path.join(FIXTURES_DIR, "threads_crawl_pilot_001.json"), "rb") as f:
            self.pilot1_bytes = f.read()

        self.store = MarketIntelligenceStore(data_dir=self.tmp_dir.name)
        self.sync_service = MarketSyncService(self.store, self.client, default_interval_sec=300)
        self.poller = RemoteCommandPoller(self.store, self.client, self.sync_service, poll_interval_sec=5)

    def tearDown(self):
        self.poller.stop(timeout=1.0)
        self.sync_service.stop(timeout=1.0)
        self.store.close()
        self.tmp_dir.cleanup()

    def test_secret_masking_and_unconfigured_graceful_handling(self):
        """Secrets are masked and missing APP_VIDEO_API_TOKEN returns unconfigured cleanly."""
        self.assertEqual(mask_secret("super_secret_token_9876"), "supe...9876")
        self.assertEqual(mask_secret(""), "<not_configured>")

        unconfigured_client = MockCloudflareTransportClient(api_token="")
        health = unconfigured_client.check_connection()
        self.assertFalse(health["configured"])
        self.assertEqual(health["status"], "unconfigured")

        unconfigured_sync = MarketSyncService(self.store, unconfigured_client)
        res = unconfigured_sync.sync_now(trigger_source="startup")
        self.assertEqual(res["status"], "failed")
        self.assertTrue(res.get("unconfigured"))

    def test_incremental_sync_and_single_flight_lock(self):
        """Incremental sync downloads only new files and single-flight lock prevents duplicate work."""
        self.client.add_drive_file("f_poc2", "threads_crawl_poc_002.json", self.poc2_bytes, "2026-11-26T03:15:20Z", "md5_a")

        res1 = self.sync_service.sync_now(trigger_source="startup")
        self.assertEqual(res1["status"], "completed")
        self.assertEqual(res1["files_downloaded"], 1)
        self.assertEqual(res1["records_inserted"], 10)
        self.assertTrue(all(h == "Bearer test-secret-token-1234567890" for h in self.client.auth_headers_seen))

        # Second sync with unchanged file skips download!
        res2 = self.sync_service.sync_now(trigger_source="scheduler")
        self.assertEqual(res2["files_downloaded"], 0)
        self.assertEqual(res2["records_inserted"], 0)

        # Add Pilot 001 -> downloads only Pilot 001 and deduplicates cleanly
        self.client.add_drive_file("f_pilot1", "threads_crawl_pilot_001.json", self.pilot1_bytes, "2026-11-26T05:31:33Z", "md5_b")
        res3 = self.sync_service.sync_now(trigger_source="manual_ui")
        self.assertEqual(res3["files_downloaded"], 1)
        self.assertGreater(res3["records_inserted"], 0)

        # Concurrent trigger_async_sync respects single-flight lock
        self.client.add_drive_file("f_poc2_v2", "threads_crawl_poc_002_copy.json", self.poc2_bytes, "2026-11-26T06:00:00Z", "md5_c")
        self.client.slow_download_delay = 0.25
        first_async = self.sync_service.trigger_async_sync("manual_ui")
        self.assertTrue(first_async["started"])
        time.sleep(0.05)
        second_async = self.sync_service.trigger_async_sync("manual_ui")
        self.assertFalse(second_async["started"])
        self.assertTrue(second_async["already_running"])
        # Wait for background sync to settle
        deadline = time.monotonic() + 3.0
        while self.sync_service.is_syncing and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_telegram_offline_queue_exactly_once_and_allowlist(self):
        """Offline queued command runs once when online; network ack drop retries without re-executing; arbitrary commands rejected."""
        self.client.add_drive_file("f_poc2", "threads_crawl_poc_002.json", self.poc2_bytes, "2026-11-26T03:15:20Z", "md5_a")

        # 1. Queue while App_Video is offline, then poll once App_Video comes online
        self.client.enqueue_d1_command("cmd_offline_1", "sync_now")
        self.client.fail_next_complete = True  # Simulate network drop right after local execution!
        out1 = self.poller.poll_once()
        self.assertEqual(out1["command_id"], "cmd_offline_1")
        self.assertEqual(out1["status"], "completed")
        self.assertEqual(self.client.download_call_count, 1)
        # Worker still shows 'running' because ack failed over network
        self.assertEqual(self.client.mock_commands[0]["status"], "running")

        # 2. Next poll flushes pending_ack WITHOUT re-running sync_now
        self.poller.poll_once()
        self.assertEqual(self.client.mock_commands[0]["status"], "completed")
        self.assertEqual(self.client.download_call_count, 1)

        # 3. Even if D1 lease expired and returned the same command_id again, local ledger prevents duplicate execution
        res_dup = self.poller.handle_claimed_command({"id": "cmd_offline_1", "command_type": "sync_now"})
        self.assertTrue(res_dup.get("reused_local_result"))
        self.assertEqual(self.client.download_call_count, 1)

        # 4. Business commands app_status & market_stats succeed
        self.client.enqueue_d1_command("cmd_status_2", "app_status")
        out2 = self.poller.poll_once()
        self.assertEqual(out2["status"], "completed")
        self.assertTrue(out2["result"]["online"])
        self.assertEqual(out2["result"]["total_items"], 10)

        # 5. Disallowed arbitrary/shell command is strictly rejected
        self.client.enqueue_d1_command("cmd_evil_3", "exec_shell")
        out3 = self.poller.poll_once()
        self.assertEqual(out3["status"], "failed")
        self.assertIn("disallowed", out3["error"].lower())
        self.assertEqual(self.client.mock_commands[2]["status"], "failed")

    def test_flask_market_and_remote_endpoints(self):
        """Flask API endpoints & Tab 4 UI integrate cleanly while keeping existing tabs intact."""
        self.client.add_drive_file("f_pilot1", "threads_crawl_pilot_001.json", self.pilot1_bytes, "2026-11-26T05:31:33Z", "md5_b")
        flask_tmp = tempfile.TemporaryDirectory()
        app_ui.reset_market_runtime_for_tests(data_dir=flask_tmp.name, client=self.client)
        app_ui.app.config["TESTING"] = True
        flask_client = app_ui.app.test_client()

        try:
            # Verify UI renders Tab 4 and preserves Tabs 1-3
            html = flask_client.get("/").get_data(as_text=True)
            self.assertIn("1. Studio Sáng Tạo", html)
            self.assertIn("2. Giám Sát Quy Trình", html)
            self.assertIn("3. Cấu Hình Đạo Diễn", html)
            self.assertIn("4. Market Intelligence", html)

            # Sync via Flask API (wait=1 for deterministic assertion)
            sync_resp = flask_client.post("/api/market/sync?wait=1")
            self.assertEqual(sync_resp.status_code, 200)
            self.assertTrue(sync_resp.get_json()["success"])

            # Status never leaks raw token
            status_resp = flask_client.get("/api/market/status")
            self.assertEqual(status_resp.status_code, 200)
            status_data = status_resp.get_json()
            self.assertNotIn("test-secret-token-1234567890", json.dumps(status_data))
            self.assertEqual(status_data["stats"]["total_items"], 50)

            # Update background sync interval
            cfg_resp = flask_client.post(
                "/api/market/config",
                data=json.dumps({"sync_interval_seconds": 180}),
                content_type="application/json",
            )
            self.assertEqual(cfg_resp.status_code, 200)
            self.assertEqual(cfg_resp.get_json()["sync_interval_seconds"], 180)

            # Filter & inspect item detail
            items_resp = flask_client.get("/api/market/items?platform=threads&content_group=deal_voucher&limit=5")
            self.assertEqual(items_resp.status_code, 200)
            items_data = items_resp.get_json()
            self.assertGreater(items_data["total"], 0)
            first_id = items_data["items"][0]["id"]

            detail_resp = flask_client.get(f"/api/market/items/{first_id}")
            self.assertEqual(detail_resp.status_code, 200)
            self.assertIn("raw_payload", detail_resp.get_json()["item"])

            # Crawl Center: all platforms, requested_count, dedup, cancel, schedule profile
            crw_resp = flask_client.post(
                "/api/remote/crawl_requests",
                data=json.dumps({"platform": "all", "requested_count": 100, "keywords": "shopee voucher"}),
                content_type="application/json",
            )
            self.assertEqual(crw_resp.status_code, 200)
            crw_data = crw_resp.get_json()
            self.assertEqual(crw_data["request"]["status"], "queued")
            self.assertEqual(crw_data["request"]["platform"], "all")
            self.assertEqual(crw_data["request"]["requested_count"], 100)
            crawl_id = crw_data["request"]["id"]

            # Duplicate active request deduplicates cleanly
            crw_dup = flask_client.post(
                "/api/remote/crawl_requests",
                data=json.dumps({"platform": "all", "requested_count": 100, "keywords": "shopee voucher"}),
                content_type="application/json",
            )
            self.assertTrue(crw_dup.get_json()["deduplicated"])

            # Cancel queued request
            cancel_resp = flask_client.post(f"/api/remote/crawl_requests/{crawl_id}/cancel")
            self.assertEqual(cancel_resp.status_code, 200)
            self.assertEqual(cancel_resp.get_json()["status"], "cancelled")

            # Schedule profile switch (production_6h <-> test_5m)
            sched_resp = flask_client.post(
                "/api/remote/crawl_schedule",
                data=json.dumps({"poll_profile": "test_5m"}),
                content_type="application/json",
            )
            self.assertEqual(sched_resp.status_code, 200)
            self.assertEqual(sched_resp.get_json()["poll_profile"], "test_5m")

            # Standardized 5-section market report endpoint
            rpt_resp = flask_client.get("/api/market/report?platform=all")
            self.assertEqual(rpt_resp.status_code, 200)
            rpt = rpt_resp.get_json()["report"]
            self.assertIn("trending_highlights", rpt)
            self.assertIn("affiliate_opportunities", rpt)
            self.assertIn("content_suggestions", rpt)
            self.assertIn("data_quality", rpt)
            self.assertIn("source_links", rpt)

            # Connection check, raw files list, sync runs, remote status
            conn_resp = flask_client.post("/api/market/test_connection")
            self.assertEqual(conn_resp.status_code, 200)
            self.assertTrue(conn_resp.get_json()["connected"])

            files_resp = flask_client.get("/api/market/files")
            self.assertEqual(files_resp.status_code, 200)
            self.assertEqual(len(files_resp.get_json()["files"]), 1)

            runs_resp = flask_client.get("/api/market/runs")
            self.assertEqual(runs_resp.status_code, 200)
            self.assertGreaterEqual(len(runs_resp.get_json()["runs"]), 1)

            remote_resp = flask_client.get("/api/remote/status")
            self.assertEqual(remote_resp.status_code, 200)
            self.assertIn("sync_now", remote_resp.get_json()["allowed_commands"])
        finally:
            rt = app_ui.get_market_runtime()
            rt["remote_poller"].stop(timeout=1.0)
            rt["sync_service"].stop(timeout=1.0)
            rt["store"].close()
            app_ui._market_runtime = None
            flask_tmp.cleanup()

    def test_startup_background_sync_and_poller_threads(self):
        """Background startup sync and remote poller threads start, execute, and stop without blocking."""
        self.client.add_drive_file("f_poc2", "threads_crawl_poc_002.json", self.poc2_bytes, "2026-11-26T03:15:20Z", "md5_a")

        self.sync_service.start(sync_on_startup=True)
        # Wait for startup sync to finish before checking market_stats via poller
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.store.get_stats()["total_items"] == 10 and not self.sync_service.is_syncing:
                break
            time.sleep(0.05)
        self.assertEqual(self.store.get_stats()["total_items"], 10)

        self.client.enqueue_d1_command("cmd_live_bg_1", "market_stats")
        self.poller.start()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.client.mock_commands[0]["status"] == "completed":
                break
            time.sleep(0.05)

        self.assertEqual(self.client.mock_commands[0]["status"], "completed")
        self.assertEqual(self.client.mock_commands[0]["result"]["total_items"], 10)

    def test_flask_v2_five_subtabs_and_tiktok_views_api(self):
        """Flask UI renders 5 Market Intelligence sub-tabs, serves TikTok views, and isolates needs_verification."""
        tt_corr = {
            "run_id": "e2e-crawl-20261126-1559",
            "platform": "tiktok",
            "status": "partial",
            "posts": [
                {
                    "rank": 1,
                    "author_handle": "@th.official.channel",
                    "video_id": "7704957293997067538",
                    "video_url": "https://www.tiktok.com/@th.official.channel/video/7704957293997067538",
                    "views": 25100000,
                    "likes": None,
                    "content_summary_vi": "Video xem nhiều nhất Việt Nam (25,1 triệu lượt xem).",
                    "source_type": "third_party",
                    "url_status": "suy_ra_tu_video_id_va_handle_chua_kiem_chung",
                    "affiliate_relevance": "khong_ro",
                    "data_quality": "needs_verification",
                }
            ],
        }
        sp_corr = {
            "run_id": "e2e-crawl-20261126-1559",
            "platform": "shopee",
            "status": "partial",
            "posts": [
                {
                    "product_name": "Kem duong da tay Nuxe 50ml",
                    "price_vnd": None,
                    "price_status": "khong_doc_duoc",
                    "deal_status": "dang_dien_ra",
                    "affiliate_eligible": False,
                    "data_quality": "needs_verification",
                    "content_summary_vi": "Sản phẩm Flash Sale đang diễn ra, thiếu giá.",
                }
            ],
        }
        rpt_corr = {
            "report": "Bao cao E2E Corrected V2",
            "run_id": "e2e-crawl-20261126-1559",
            "corrects_report": "market_report_e2e_20261126.json",
            "technical_status": "completed",
            "data_quality_status": "dat mot phan",
            "totals": {"records": 20, "business_usable_records": 9, "needs_verification_records": 11},
            "telegram_summary": "BÁO CÁO E2E — BẢN CORRECTED V2",
        }
        self.client.add_drive_file("f_tt_c", "tiktok_crawl_e2e_20261126_corrected_v2.json", json.dumps(tt_corr).encode("utf-8"), "2026-11-26T09:00:00Z", "m1")
        self.client.add_drive_file("f_sp_c", "shopee_crawl_e2e_20261126_corrected_v2.json", json.dumps(sp_corr).encode("utf-8"), "2026-11-26T09:00:00Z", "m2")
        self.client.add_drive_file("f_rp_c", "market_report_e2e_20261126_corrected_v2.json", json.dumps(rpt_corr).encode("utf-8"), "2026-11-26T09:05:00Z", "m3")

        flask_tmp = tempfile.TemporaryDirectory()
        app_ui.reset_market_runtime_for_tests(data_dir=flask_tmp.name, client=self.client)
        app_ui.app.config["TESTING"] = True
        flask_client = app_ui.app.test_client()

        try:
            html = flask_client.get("/").get_data(as_text=True)
            for label in ["Tổng quan", "Threads", "TikTok", "Shopee", "Báo cáo", "1. Studio Sáng Tạo"]:
                self.assertIn(label, html)

            sync_resp = flask_client.post("/api/market/sync?wait=1")
            self.assertEqual(sync_resp.status_code, 200)
            self.assertTrue(sync_resp.get_json()["success"])

            tt_resp = flask_client.get("/api/market/items?platform=tiktok&sort_by=views")
            self.assertEqual(tt_resp.status_code, 200)
            tt_items = tt_resp.get_json()["items"]
            self.assertEqual(len(tt_items), 1)
            self.assertEqual(tt_items[0]["views_count"], 25100000)
            self.assertIsNone(tt_items[0]["likes_count"])

            sp_aff = flask_client.get("/api/market/items?platform=shopee&affiliate_only=1")
            self.assertEqual(sp_aff.status_code, 200)
            self.assertEqual(sp_aff.get_json()["total"], 0)

            status_resp = flask_client.get("/api/market/status")
            stats = status_resp.get_json()["stats"]
            self.assertIn("by_quality", stats)
            self.assertIn("by_platform_quality", stats)
            self.assertIn("engagement_completeness", stats)

            rpt_resp = flask_client.get("/api/market/report?platform=all")
            rpt = rpt_resp.get_json()["report"]
            self.assertEqual(rpt["data_quality"]["data_quality_status"], "dat mot phan")
            self.assertEqual(len(rpt["affiliate_opportunities"]), 0)
        finally:
            rt = app_ui.get_market_runtime()
            rt["remote_poller"].stop(timeout=1.0)
            rt["sync_service"].stop(timeout=1.0)
            rt["store"].close()
            app_ui._market_runtime = None
            flask_tmp.cleanup()

    def test_start_studio_binds_localhost_by_default(self):
        """Regression (Issue #3): start_studio MUST bind 127.0.0.1 by default instead of 0.0.0.0."""
        seen_hosts = []
        orig_run = app_ui.app.run
        orig_disable = os.environ.get("APP_VIDEO_DISABLE_BG_WORKERS")
        orig_env_host = os.environ.pop("APP_VIDEO_HOST", None)
        try:
            os.environ["APP_VIDEO_DISABLE_BG_WORKERS"] = "1"
            app_ui.app.run = lambda *a, **kw: seen_hosts.append(kw.get("host"))
            app_ui.start_studio(port=5099, auto_open=False)
        finally:
            app_ui.app.run = orig_run
            if orig_disable is None:
                os.environ.pop("APP_VIDEO_DISABLE_BG_WORKERS", None)
            else:
                os.environ["APP_VIDEO_DISABLE_BG_WORKERS"] = orig_disable
            if orig_env_host is not None:
                os.environ["APP_VIDEO_HOST"] = orig_env_host
        self.assertEqual(seen_hosts, ["127.0.0.1"])

    def test_independent_heartbeat_during_slow_sync_command(self):
        """Regression (Issue #5): Heartbeat must keep firing independently while sync_now executes slowly."""
        self.client.add_drive_file("f_slow1", "threads_crawl_poc_002.json", self.poc2_bytes, "2026-11-26T03:15:20Z", "md5_s1")
        self.client.slow_download_delay = 0.28
        self.poller.heartbeat_interval_sec = 0.06
        self.poller.poll_interval_sec = 0.05

        self.client.enqueue_d1_command("cmd_slow_sync", "sync_now")
        self.poller.start()
        try:
            deadline = time.monotonic() + 3.0
            # Wait briefly while slow sync is in flight and ensure >= 2 heartbeats occurred concurrently
            while time.monotonic() < deadline:
                if len(self.client.mock_heartbeats) >= 2 and self.client.mock_commands[0]["status"] == "completed":
                    break
                time.sleep(0.03)
            # Crucially, check heartbeat didn't just wait until command finished: reset and trigger during active execution
            self.client.mock_heartbeats.clear()
            self.client.add_drive_file("f_slow2", "threads_crawl_pilot_001.json", self.pilot1_bytes, "2026-11-26T05:31:33Z", "md5_s2")
            self.client.enqueue_d1_command("cmd_slow_sync_2", "sync_now")
            t_wait = time.monotonic() + 0.24
            while time.monotonic() < t_wait:
                if self.client.mock_commands[1]["status"] == "completed" and len(self.client.mock_heartbeats) >= 2:
                    break
                time.sleep(0.02)
            self.assertGreaterEqual(len(self.client.mock_heartbeats), 2)
        finally:
            self.poller.stop(timeout=2.0)

    def test_stale_pending_ack_409_resolves_without_looping_and_sends_attempt(self):
        """Regression (Issue #4): Stale 409 conflict on pending_ack marks local ack resolved instead of infinite retries."""
        self.client.add_drive_file("f_ack1", "threads_crawl_poc_002.json", self.poc2_bytes, "2026-11-26T03:15:20Z", "md5_a1")
        self.client.enqueue_d1_command("cmd_stale_409", "sync_now")
        self.client.fail_next_complete = True
        self.poller.poll_once()
        self.assertEqual(len(self.store.list_pending_ack_commands()), 1)

        # Now Worker rejects stale claim with 409 Conflict -> pending_ack MUST clear cleanly
        self.client.conflict_next_complete = True
        self.poller.poll_once()
        self.assertEqual(self.store.list_pending_ack_commands(), [])

        # Normal complete sends claimed_by & attempt
        self.client.enqueue_d1_command("cmd_with_attempt", "app_status")
        self.poller.poll_once()
        last_payload = self.client.complete_payloads[-1]
        self.assertEqual(last_payload["command_id"], "cmd_with_attempt")
        self.assertEqual(last_payload["claimed_by"], "test-client")
        self.assertEqual(last_payload["attempt"], 1)

    def test_sync_paginates_over_100_files_with_equal_modified_time(self):
        """Regression (Issue #6): Sync must paginate >100 files sharing the exact same modified_time without dropping files."""
        shared_mtime = "2026-11-26T12:00:00Z"
        for idx in range(125):
            payload = {
                "run_id": "run-pagination-125",
                "platform": "threads",
                "posts": [{
                    "post_id": f"PG_{idx:03d}",
                    "post_url": f"https://www.threads.com/@user_{idx}/post/PG_{idx:03d}",
                    "author_handle": f"@user_{idx}",
                    "content_summary_vi": f"Bài test phân trang số {idx}",
                    "likes": idx + 1,
                }],
            }
            self.client.add_drive_file(
                f"fid_{idx:03d}",
                f"threads_page_{idx:03d}.json",
                json.dumps(payload).encode("utf-8"),
                shared_mtime,
                f"md5_{idx:03d}",
            )

        res = self.sync_service.sync_now(trigger_source="test_pagination")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["files_checked"], 125)
        self.assertEqual(self.store.get_stats()["total_items"], 125)

    def test_flask_v31_threads_tiers_archive_exclusion_and_real_report(self):
        """V3.1: Excludes Archive/ from Auto Sync, preserves historical V1/V2, exposes 5 Threads tiers & real trend_report_* via Flask."""
        with open(os.path.join(FIXTURES_DIR, "threads_trend_20261126.json"), "rb") as f:
            v3_trend_bytes = f.read()
        with open(os.path.join(FIXTURES_DIR, "trend_report_v3_20261126.json"), "rb") as f:
            v3_report_bytes = f.read()
        archived_old_report = {
            "report": "Old Draft Inside Archive Should Be Ignored",
            "run_id": "threads-trend-v3-archived",
            "run_status": "source_error",
            "generated_at": "2026-11-26T23:59:59+07:00",  # even newer timestamp inside Archive must NEVER win!
            "telegram_summary": "ARCHIVED DRAFT SHOULD NOT APPEAR",
        }
        # Historical V1/V2 + Active V3.1 + Archived drafts
        self.client.add_drive_file("f_hist_poc2", "threads_crawl_poc_002.json", self.poc2_bytes, "2026-11-26T03:15:20Z", "m_old", folder_path="Threads")
        self.client.add_drive_file("f_v3_th", "threads_trend_20261126.json", v3_trend_bytes, "2026-11-26T15:51:33Z", "m_v3t", folder_path="Threads")
        self.client.add_drive_file("f_v3_rp", "trend_report_v3_20261126.json", v3_report_bytes, "2026-11-26T15:51:34Z", "m_v3r", folder_path="Reports")
        self.client.add_drive_file(
            "f_arch_th",
            "threads_trend_archived.json",
            json.dumps({"run_id": "arch_run", "records": [{"post_id": "ARCH_ONLY_1", "post_url": "https://www.threads.com/@a/post/ARCH_ONLY_1", "trend_tier": "very_hot", "likes": 99999}]}).encode("utf-8"),
            "2026-11-26T16:00:00Z",
            "m_at",
            folder_path="Threads/Archive",
        )
        self.client.add_drive_file(
            "f_arch_rp",
            "trend_report_v3_archived.json",
            json.dumps(archived_old_report).encode("utf-8"),
            "2026-11-26T16:59:59Z",
            "m_ar",
            folder_path="Reports/Archive",
        )

        flask_tmp = tempfile.TemporaryDirectory()
        app_ui.reset_market_runtime_for_tests(data_dir=flask_tmp.name, client=self.client)
        app_ui.app.config["TESTING"] = True
        flask_client = app_ui.app.test_client()

        try:
            html = flask_client.get("/").get_data(as_text=True)
            for label in ["Very Hot", "Hot", "Emerging", "Watchlist", "Unverified", "trend_toan_thi_truong"]:
                self.assertIn(label, html)

            sync_resp = flask_client.post("/api/market/sync?wait=1")
            self.assertEqual(sync_resp.status_code, 200)
            sync_data = sync_resp.get_json()["result"]
            self.assertEqual(sync_data["files_checked"], 3)  # Archive/ files skipped!
            self.assertEqual(sync_data["records_inserted"], 18)  # 10 V1/V2 + 8 V3.1

            # Second sync MUST NOT re-download or duplicate records
            sync_resp2 = flask_client.post("/api/market/sync?wait=1")
            sync_data2 = sync_resp2.get_json()["result"]
            self.assertEqual(sync_data2["files_downloaded"], 0)
            self.assertEqual(sync_data2["records_inserted"], 0)

            # Query each tier explicitly via Flask API
            vh = flask_client.get("/api/market/items?platform=threads&trend_tier=very_hot&sort_by=engagement").get_json()
            self.assertEqual(vh["total"], 4)
            self.assertEqual(vh["items"][0]["post_id"], "DgM4DJRGhGL")
            self.assertEqual(vh["items"][0]["reposts_count"], 1579)
            self.assertNotIn("ARCH_ONLY_1", [it["post_id"] for it in vh["items"]])

            self.assertEqual(flask_client.get("/api/market/items?platform=threads&trend_tier=hot").get_json()["total"], 0)
            self.assertEqual(flask_client.get("/api/market/items?platform=threads&trend_tier=emerging").get_json()["total"], 2)
            self.assertEqual(flask_client.get("/api/market/items?platform=threads&trend_tier=watchlist").get_json()["total"], 2)
            self.assertEqual(flask_client.get("/api/market/items?platform=threads&trend_tier=unverified").get_json()["total"], 0)
            self.assertEqual(flask_client.get("/api/market/items?platform=threads&trend_tier=legacy").get_json()["total"], 10)

            rpt = flask_client.get("/api/market/report?platform=threads").get_json()["report"]
            self.assertEqual(rpt["run_id"], "threads-trend-v3-20261126-2241")
            self.assertEqual(rpt["run_status"], "completed")
            self.assertNotIn("ARCHIVED DRAFT", str(rpt.get("telegram_summary") or ""))

            stats = flask_client.get("/api/market/status").get_json()["stats"]
            self.assertEqual(stats["threads_by_trend_tier"]["very_hot"], 4)
            self.assertEqual(stats["threads_by_trend_tier"]["legacy"], 10)
            self.assertGreater(len(stats["trends_snapshot"]["topics"]), 10)
        finally:
            rt = app_ui.get_market_runtime()
            rt["remote_poller"].stop(timeout=1.0)
            rt["sync_service"].stop(timeout=1.0)
            rt["store"].close()
            app_ui._market_runtime = None
            flask_tmp.cleanup()

    def test_real_http_client_heartbeat_independent_from_slow_download(self):
        """Regression (Issue #5): Real CloudflareBridgeClient + real ThreadingHTTPServer slow download never blocks independent heartbeat session."""
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        slow_payload = {
            "run_id": "real-http-slow-run",
            "platform": "threads",
            "posts": [{
                "post_id": "REAL_HTTP_1",
                "post_url": "https://www.threads.com/@real/post/REAL_HTTP_1",
                "author_handle": "@real",
                "content_summary_vi": "Test HTTP client that cham.",
                "likes": 42,
            }],
        }
        download_started = threading.Event()
        download_finished_ts = []
        heartbeat_received_ts = []

        class SlowWorkerHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/api/app/market/files?"):
                    body = json.dumps({
                        "status": "ok",
                        "files": [{
                            "file_id": "f_real_slow",
                            "name": "threads_real_slow.json",
                            "folder_path": "Threads",
                            "modified_time": "2026-11-26T20:00:00Z",
                            "md5_checksum": "md5_real_slow",
                            "platform_hint": "threads",
                        }],
                        "has_more": False,
                        "next_page_token": None,
                    }).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if self.path == "/api/app/market/files/f_real_slow":
                    download_started.set()
                    time.sleep(0.9)
                    body = json.dumps(slow_payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("X-Drive-File-Id", "f_real_slow")
                    self.send_header("X-Drive-File-Name", "threads_real_slow.json")
                    self.send_header("X-Drive-Modified-Time", "2026-11-26T20:00:00Z")
                    self.send_header("X-Drive-Md5", "md5_real_slow")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    download_finished_ts.append(time.monotonic())
                    return

                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                if self.path == "/api/app/heartbeat":
                    heartbeat_received_ts.append(time.monotonic())
                    body = json.dumps({"ok": True}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), SlowWorkerHandler)
        srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
        srv_thread.start()
        host, port = srv.server_address
        real_client = CloudflareBridgeClient(
            worker_url=f"http://{host}:{port}",
            api_token="real-http-token-xyz",
            client_id="real-test-desktop",
            timeout_sec=(2.0, 5.0),
            heartbeat_timeout_sec=(1.0, 2.0),
        )
        self.assertIsNot(real_client._session, real_client._heartbeat_session)
        self.assertIsNot(real_client._http_lock, real_client._heartbeat_lock)

        real_sync = MarketSyncService(self.store, real_client, default_interval_sec=300)
        real_poller = RemoteCommandPoller(self.store, real_client, real_sync, poll_interval_sec=10, heartbeat_interval_sec=10)
        sync_results = []
        sync_thread = threading.Thread(target=lambda: sync_results.append(real_sync.sync_now(trigger_source="slow_http_test")), daemon=True)
        try:
            sync_thread.start()
            self.assertTrue(download_started.wait(timeout=3.0))

            # Fire two real HTTP heartbeats while download_market_file is actively sleeping 0.9s on _session/_http_lock
            hb_latencies = []
            for _ in range(2):
                t0 = time.monotonic()
                ok = real_poller.send_heartbeat_once()
                hb_latencies.append(time.monotonic() - t0)
                self.assertTrue(ok)
                time.sleep(0.05)

            sync_thread.join(timeout=5.0)
            self.assertEqual(len(sync_results), 1)
            self.assertEqual(sync_results[0]["status"], "completed")
            self.assertEqual(len(download_finished_ts), 1)
            self.assertEqual(len(heartbeat_received_ts), 2)
            # Both real HTTP heartbeats finished BEFORE the slow 0.9s download completed!
            self.assertTrue(all(hb_t < download_finished_ts[0] for hb_t in heartbeat_received_ts))
            self.assertTrue(all(lat < 0.4 for lat in hb_latencies))
        finally:
            real_client.close()
            srv.shutdown()
            srv.server_close()

    def test_pagination_partial_error_guards_never_commit_cursor(self):
        """Regression (Issue #6): Repeated token, missing token when has_more=true, or page limit overflow reports partial_error and never commits cursor."""
        self.store.set_state("drive_cursor", "2026-11-25T00:00:00Z")

        # 1. Repeated next_page_token
        orig_list = self.client.list_market_files
        try:
            def rep_tok(cursor=None, limit=50, page_token=None):
                return {
                    "ok": True,
                    "status": "ok",
                    "files": [{"file_id": "f1", "name": "t1.json", "modified_time": "2026-11-26T10:00:00Z"}],
                    "has_more": True,
                    "next_page_token": "tok_same",
                }
            self.client.list_market_files = rep_tok
            self.client.add_drive_file(
                "f1",
                "t1.json",
                json.dumps({"run_id": "r1", "posts": []}).encode("utf-8"),
                "2026-11-26T10:00:00Z",
                "m1",
            )
            res_rep = self.sync_service.sync_now(trigger_source="test_rep_tok")
            self.assertEqual(res_rep["status"], "partial_error")
            self.assertFalse(res_rep["cursor_committed"])
            self.assertEqual(self.store.get_state("drive_cursor"), "2026-11-25T00:00:00Z")
            self.assertEqual(self.store.get_state("last_sync_status"), "partial_error")

            # 2. Missing next_page_token while has_more=True
            self.client.list_market_files = lambda cursor=None, limit=50, page_token=None: {
                "ok": True,
                "files": [],
                "has_more": True,
                "next_page_token": None,
            }
            res_miss = self.sync_service.sync_now(trigger_source="test_miss_tok")
            self.assertEqual(res_miss["status"], "partial_error")
            self.assertIn("thiếu next_page_token", res_miss["pagination_error"])
            self.assertEqual(self.store.get_state("drive_cursor"), "2026-11-25T00:00:00Z")

            # 3. Exceeds max_list_pages while has_more=True
            bounded_sync = MarketSyncService(self.store, self.client, max_list_pages=2)
            page_counter = {"n": 0}
            def many_pages(cursor=None, limit=50, page_token=None):
                page_counter["n"] += 1
                return {
                    "ok": True,
                    "files": [],
                    "has_more": True,
                    "next_page_token": f"tok_{page_counter['n']}",
                }
            self.client.list_market_files = many_pages
            res_lim = bounded_sync.sync_now(trigger_source="test_page_lim")
            self.assertEqual(res_lim["status"], "partial_error")
            self.assertIn("vượt giới hạn 2 trang", res_lim["pagination_error"])
            self.assertEqual(self.store.get_state("drive_cursor"), "2026-11-25T00:00:00Z")

            # 4. Worker explicitly signals status='partial_error' -> Sync must abort cursor commit cleanly
            self.client.list_market_files = lambda cursor=None, limit=50, page_token=None: {
                "ok": True,
                "status": "partial_error",
                "partial_error": "repeated_page_token",
                "files": [{"file_id": "f1", "name": "t1.json", "modified_time": "2026-11-26T10:00:00Z"}],
                "has_more": False,
                "next_page_token": None,
            }
            res_wrk = self.sync_service.sync_now(trigger_source="test_worker_partial")
            self.assertEqual(res_wrk["status"], "partial_error")
            self.assertFalse(res_wrk["cursor_committed"])
            self.assertEqual(self.store.get_state("drive_cursor"), "2026-11-25T00:00:00Z")
        finally:
            self.client.list_market_files = orig_list

    def test_moved_to_archive_after_sqlite_sync_preserves_history_excludes_active(self):
        """Scenario #3: Files already synced to SQLite and later moved into Archive/ on Drive exit active reporting while preserving full DB + disk history."""
        with open(os.path.join(FIXTURES_DIR, "threads_crawl_poc_002.json"), "rb") as f:
            v2_bytes = f.read()
        with open(os.path.join(FIXTURES_DIR, "threads_trend_20261126.json"), "rb") as f:
            v3_posts_bytes = f.read()
        with open(os.path.join(FIXTURES_DIR, "trend_report_v3_20261126.json"), "rb") as f:
            v3_report_bytes = f.read()

        # Sync historical V2 + active V3.1 crawl & report into SQLite
        self.client.add_drive_file(
            "f_hist_v2",
            "threads_crawl_poc_002.json",
            v2_bytes,
            "2026-11-25T08:00:00Z",
            "md5_hist_v2",
            platform_hint="threads",
            folder_path="Threads",
        )
        self.client.add_drive_file(
            "f_v3_posts",
            "threads_trend_20261126.json",
            v3_posts_bytes,
            "2026-11-26T09:00:00Z",
            "md5_v3_posts",
            platform_hint="threads",
            folder_path="Threads",
        )
        self.client.add_drive_file(
            "f_v3_report",
            "trend_report_v3_20261126.json",
            v3_report_bytes,
            "2026-11-26T09:05:00Z",
            "md5_v3_report",
            platform_hint="threads",
            folder_path="Reports",
        )

        res1 = self.sync_service.sync_now(trigger_source="initial_sync")
        self.assertEqual(res1["status"], "completed")

        stats_before = self.store.get_stats()
        total_before = stats_before["total_items"]
        self.assertGreater(total_before, 3)
        self.assertIsNotNone(stats_before.get("trends_snapshot"))
        self.assertIsNotNone(stats_before.get("latest_v3_screening"))
        rep_before = self.store.build_evidence_report("all")
        self.assertEqual(rep_before.get("schema_version"), "v3.1")
        self.assertTrue(len(self.store.list_reports()) >= 1)

        with self.store._lock:
            raw_files_total_before = self.store._conn.execute("SELECT COUNT(*) FROM raw_drive_files").fetchone()[0]
            items_total_before = self.store._conn.execute("SELECT COUNT(*) FROM market_items").fetchone()[0]
            reports_total_before = self.store._conn.execute("SELECT COUNT(*) FROM market_reports").fetchone()[0]
            raw_paths_before = [
                r["raw_path"]
                for r in self.store._conn.execute("SELECT raw_path FROM raw_drive_files").fetchall()
            ]
        for p in raw_paths_before:
            self.assertTrue(p and os.path.exists(p), f"Expected raw file on disk: {p}")

        # Now move both V3.1 files into Archive/ on Drive (even WITHOUT bumping modified_time)
        self.client.mock_files["f_v3_posts"]["folder_path"] = "Threads/Archive"
        self.client.mock_files["f_v3_report"]["folder_path"] = "Reports/Archive"

        res2 = self.sync_service.sync_now(trigger_source="after_move_to_archive")
        self.assertEqual(res2["status"], "completed")

        # 1) Moved files no longer participate in active/current stats, items, trends_snapshot, screening, or reports
        stats_after = self.store.get_stats()
        self.assertLess(stats_after["total_items"], total_before)
        self.assertIsNone(stats_after.get("trends_snapshot"))
        self.assertIsNone(stats_after.get("latest_v3_screening"))
        self.assertEqual(len(self.store.list_reports()), 0)

        active_items = self.store.query_items(limit=200)["items"]
        active_file_ids = {it["raw_file_id"] for it in active_items}
        self.assertNotIn("f_v3_posts", active_file_ids)
        self.assertIn("f_hist_v2", active_file_ids)

        rep_after = self.store.build_evidence_report("all")
        self.assertIsNone(rep_after.get("latest_muse_report"))
        self.assertIsNone(rep_after.get("trends_snapshot"))

        # 2) Historical SQLite records & raw JSON files on disk MUST remain 100% intact (never deleted)
        with self.store._lock:
            raw_files_total_after = self.store._conn.execute("SELECT COUNT(*) FROM raw_drive_files").fetchone()[0]
            items_total_after = self.store._conn.execute("SELECT COUNT(*) FROM market_items").fetchone()[0]
            reports_total_after = self.store._conn.execute("SELECT COUNT(*) FROM market_reports").fetchone()[0]
            archived_flags = {
                r["file_id"]: int(r["is_archived"] or 0)
                for r in self.store._conn.execute("SELECT file_id, is_archived FROM raw_drive_files").fetchall()
            }
        self.assertEqual(raw_files_total_after, raw_files_total_before)
        self.assertEqual(items_total_after, items_total_before)
        self.assertEqual(reports_total_after, reports_total_before)
        self.assertEqual(archived_flags.get("f_v3_posts"), 1)
        self.assertEqual(archived_flags.get("f_v3_report"), 1)
        self.assertEqual(archived_flags.get("f_hist_v2"), 0)
        for p in raw_paths_before:
            self.assertTrue(os.path.exists(p), f"Historical raw file was deleted: {p}")

        # Explicit include_archived=True still sees full historical data
        self.assertEqual(len(self.store.list_reports(include_archived=True)), 1)
        self.assertEqual(self.store.query_items(limit=200, include_archived=True)["total"], items_total_before)

    def test_crawl_center_custom_count_polling_and_cross_run_filters(self):
        """Crawl Center supports exact custom requested_count, progress metrics, single 10s tab-scoped polling, activity filters, and pushes dedup registry on sync."""
        orig_runtime = app_ui.get_market_runtime
        app_ui.get_market_runtime = lambda: {
            "store": self.store,
            "client": self.client,
            "sync_service": self.sync_service,
            "remote_poller": self.poller,
        }
        try:
            client = app_ui.app.test_client()

            # 1) Exact custom requested_count passes through cleanly (e.g. 37) and rejects out-of-range
            post_ok = client.post(
                "/api/remote/crawl_requests",
                json={"platform": "threads", "requested_count": 37, "keywords": "kem chong nang"},
            )
            self.assertEqual(post_ok.status_code, 200)
            ok_json = post_ok.get_json()
            self.assertTrue(ok_json["ok"])
            self.assertEqual(ok_json["request"]["requested_count"], 37)

            post_bad = client.post(
                "/api/remote/crawl_requests",
                json={"platform": "threads", "requested_count": 0, "keywords": "bad"},
            )
            self.assertEqual(post_bad.status_code, 400)

            # 2) Ingest multi-run items & test /api/market/items?activity_filter=...
            run_a = {
                "run_id": "ui-run-1",
                "platform": "threads",
                "crawled_at": "2026-11-27T08:00:00Z",
                "records": [{"post_id": "UI_P1", "post_url": "https://threads.net/@a/post/UI_P1", "views": 100, "likes": 10}],
            }
            run_b = {
                "run_id": "ui-run-2",
                "platform": "threads",
                "crawled_at": "2026-11-27T12:00:00Z",
                "records": [
                    {"post_id": "UI_P1", "post_url": "https://threads.net/@a/post/UI_P1", "views": 800, "likes": 45},
                    {"post_id": "UI_P2", "post_url": "https://threads.net/@b/post/UI_P2", "views": None, "likes": 5},
                ],
            }
            self.client.add_drive_file("f_ui_1", "threads_trend_ui1.json", json.dumps(run_a).encode("utf-8"), "2026-11-27T08:00:00Z", "md5_ui1")
            s1 = self.sync_service.sync_now(trigger_source="test_ui_1")
            self.assertEqual(s1["new_count"], 1)
            self.client.add_drive_file("f_ui_2", "threads_trend_ui2.json", json.dumps(run_b).encode("utf-8"), "2026-11-27T12:00:00Z", "md5_ui2")
            s2 = self.sync_service.sync_now(trigger_source="test_ui_2")
            self.assertEqual(s2["new_count"], 1)
            self.assertEqual(s2["engagement_updated_count"], 1)
            self.assertEqual(s2["missing_views_count"], 1)
            self.assertGreaterEqual(len(self.client.mock_registry), 2)

            r_new = client.get("/api/market/items?activity_filter=new").get_json()
            self.assertEqual(r_new["total"], 1)
            self.assertEqual(r_new["items"][0]["post_id"], "UI_P2")

            r_growth = client.get("/api/market/items?activity_filter=growth").get_json()
            self.assertEqual(r_growth["total"], 1)
            self.assertEqual(r_growth["items"][0]["post_id"], "UI_P1")
            self.assertEqual(r_growth["items"][0]["last_views_delta"], 700)

            r_all = client.get("/api/market/items?activity_filter=all").get_json()
            self.assertEqual(r_all["total"], 2)

            # 3) Verify UI HTML/JS structure enforces Crawl Center single 10s tab-scoped timer, manual Làm mới, custom input, progress indicator, and zero form reset on poll
            html = client.get("/").get_data(as_text=True)
            self.assertIn('type="number"', html)
            self.assertIn("crawlRequestForm.requested_count", html)
            self.assertIn("refreshCrawlCenter(true)", html)
            self.assertIn("startCrawlCenterPolling()", html)
            self.assertIn("stopCrawlCenterPolling()", html)
            self.assertIn("}, 10000);", html)
            self.assertIn("if (this.isCrawlCenterRefreshing) return;", html)
            self.assertIn("Chưa đạt mục tiêu", html)
            self.assertIn("Bài mới", html)
            self.assertIn("Tăng trưởng", html)
            self.assertIn("Toàn bộ lịch sử", html)
            self.assertIn("Lịch sử Engagement Snapshots", html)
            self.assertIn("formatViewsDisplay(item)", html)
            self.assertIn("display_approx", html)
            self.assertIn("📤 Shares:", html)
            self.assertIn("🔁 Reposts:", html)
        finally:
            app_ui.get_market_runtime = orig_runtime

    def test_v32_fail_closed_outbox_recovery_and_batching_over_500(self):
        """V3.2: Client chunks >500 items without truncation, fails closed on registry outage, raises StaleClaimConflictError on 409, and recovers SQLite registry_sync_outbox on reconnect."""
        from unittest.mock import MagicMock
        from market_intelligence.cloudflare_client import StaleClaimConflictError

        # 1) Real CloudflareBridgeClient batching over 550 items & fail-closed / 409 handling
        real_client = CloudflareBridgeClient(
            worker_url="https://example.workers.dev",
            api_token="test-token-v32-real",
            client_id="laptop-a",
        )
        batch_sizes_seen = []

        def fake_req_ok(method, path, json_body=None, **kwargs):
            chunk = (json_body or {}).get("items") or []
            batch_sizes_seen.append((path, len(chunk)))
            resp = MagicMock()
            resp.ok = True
            resp.status_code = 200
            if path.endswith("/check"):
                resp.json.return_value = {"ok": True, "existing": {}, "existing_keys": []}
            else:
                resp.json.return_value = {
                    "ok": True,
                    "new_count": len(chunk),
                    "duplicate_count": 0,
                    "updated_count": 0,
                    "missing_views_count": 0,
                    "total_processed": len(chunk),
                }
            return resp

        real_client._request_with_retry = fake_req_ok
        large_items = [{"platform": "threads", "dedup_key": f"threads:post:P{i}", "post_id": f"P{i}"} for i in range(550)]
        chk = real_client.check_cross_run_dedup(large_items, batch_size=100)
        rec = real_client.record_cross_run_dedup(large_items, run_id="run_550", batch_size=100)
        self.assertEqual(len([b for b in batch_sizes_seen if b[0].endswith("/check")]), 6)
        self.assertEqual(sum(b[1] for b in batch_sizes_seen if b[0].endswith("/check")), 550)
        self.assertEqual(rec["total_processed"], 550)

        # Fail-closed 503 check
        def fake_req_503(method, path, json_body=None, **kwargs):
            resp = MagicMock()
            resp.ok = False
            resp.status_code = 503
            resp.text = '{"ok":false,"registry_unavailable":true}'
            return resp

        real_client._request_with_retry = fake_req_503
        with self.assertRaises(RuntimeError) as ctx_503:
            real_client.check_cross_run_dedup(large_items[:5])
        self.assertIn("RegistryUnavailable", str(ctx_503.exception))

        # Lease expired / conflict 409 check
        def fake_req_409(method, path, json_body=None, **kwargs):
            resp = MagicMock()
            resp.ok = False
            resp.status_code = 409
            resp.text = '{"ok":false,"conflict":true}'
            return resp

        real_client._request_with_retry = fake_req_409
        with self.assertRaises(StaleClaimConflictError):
            real_client.record_cross_run_dedup(large_items[:2], request_id="crw_1", claimed_by="w1", attempt=1)

        # 2) SyncService SQLite registry_sync_outbox crash/outage recovery across 250 items (3 chunks of 100, 100, 50)
        big_payload = {
            "run_id": "run_outbox_250",
            "platform": "threads",
            "crawled_at": "2026-11-27T13:00:00Z",
            "records": [
                {
                    "post_id": f"OB_{i}",
                    "post_url": f"https://threads.net/@ob/post/OB_{i}",
                    "views": 1000 + i,
                    "likes": 10,
                    "shares": 2,
                    "reposts": 1,
                }
                for i in range(250)
            ],
        }
        self.client.add_drive_file("f_ob_250", "threads_trend_ob250.json", json.dumps(big_payload).encode("utf-8"), "2026-11-27T13:00:00Z", "md5_ob250")

        rec_calls = {"count": 0}
        orig_rec = self.client.record_cross_run_dedup

        def flaky_record(items, run_id=None, **kwargs):
            rec_calls["count"] += 1
            if rec_calls["count"] == 2:
                raise RuntimeError("RegistryUnavailable: simulated mid-batch outage")
            return orig_rec(items, run_id=run_id)

        self.client.record_cross_run_dedup = flaky_record
        res_outage = self.sync_service.sync_now(trigger_source="test_outbox_1")
        self.assertEqual(res_outage["registry_sync_status"], "error")
        pending_after_fail = self.store.list_pending_registry_batches()
        self.assertEqual(len(pending_after_fail), 2)  # Chunk 1 succeeded & was acked; chunks 2 & 3 stay queued!

        # Recover registry and replay pending outbox batches on next sync
        self.client.record_cross_run_dedup = orig_rec
        res_recover = self.sync_service.sync_now(trigger_source="test_outbox_2")
        self.assertEqual(res_recover["registry_sync_status"], "ok")
        self.assertEqual(len(self.store.list_pending_registry_batches()), 0)
        self.assertEqual(len(self.client.mock_registry), 250)

    def test_worker_v2_crawl_queue_signal_sql_real_sqlite(self):
        """Real SQLite (D1 engine) validation of CRAWL_QUEUE_SIGNAL_SQL index plan & all queue/lease/registry states."""
        import sqlite3
        import re as _re
        from pathlib import Path as _Path

        root_p = _Path(ROOT_DIR)
        worker_src = (root_p / "cloudflare-worker" / "worker.v2.js").read_text(encoding="utf-8")
        m = _re.search(r"const\s+CRAWL_QUEUE_SIGNAL_SQL\s*=\s*`([^`]+)`", worker_src)
        self.assertIsNotNone(m, "CRAWL_QUEUE_SIGNAL_SQL must exist in worker.v2.js")
        sql = m.group(1)

        conn = sqlite3.connect(":memory:")
        conn.executescript((root_p / "cloudflare-worker" / "migrations" / "0001_v2_market_and_commands.sql").read_text(encoding="utf-8"))
        conn.executescript((root_p / "cloudflare-worker" / "migrations" / "0003_cross_run_dedup_registry.sql").read_text(encoding="utf-8"))
        conn.executescript((root_p / "cloudflare-worker" / "migrations" / "0004_seed_post_identity_index_171.sql").read_text(encoding="utf-8"))

        now_iso = "2026-11-27T12:10:00.000Z"
        past_iso = "2026-11-27T11:00:00.000Z"
        future_iso = "2026-11-27T13:00:00.000Z"

        # Verify zero unindexed table scans in SQLite query plan
        plan_text = "\n".join(str(r) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, (now_iso,)).fetchall())
        self.assertIn("USING", plan_text)
        self.assertNotIn("SCAN crawl_requests", plan_text)

        def eval_sig():
            return conn.execute(sql, (now_iso,)).fetchone()[0]

        # 1. Empty queue
        self.assertEqual(eval_sig(), 0)

        # 2. Only ALL / TikTok / Shopee queued -> 0
        for pid, plat in [("q_all", "all"), ("q_tk", "tiktok"), ("q_sh", "shopee")]:
            conn.execute(
                "INSERT INTO crawl_requests (id, platform, keywords, requested_count, status, attempts, max_attempts, created_at, updated_at) VALUES (?, ?, 'kw', 20, 'queued', 0, 3, ?, ?)",
                (pid, plat, now_iso, now_iso),
            )
        self.assertEqual(eval_sig(), 0)

        # 3. Threads queued added -> 1
        conn.execute(
            "INSERT INTO crawl_requests (id, platform, keywords, requested_count, status, attempts, max_attempts, created_at, updated_at) VALUES ('q_th1', 'threads', 'skincare', 50, 'queued', 0, 3, ?, ?)",
            (now_iso, now_iso),
        )
        self.assertEqual(eval_sig(), 1)

        # 4. Active running task (valid future lease) blocks -> 0
        conn.execute(
            "INSERT INTO crawl_requests (id, platform, keywords, requested_count, status, lease_expires_at, attempts, max_attempts, created_at, updated_at) VALUES ('r_act', 'threads', 'x', 10, 'running', ?, 1, 3, ?, ?)",
            (future_iso, now_iso, now_iso),
        )
        self.assertEqual(eval_sig(), 0)

        # 5. Lease expired on running task no longer blocks q_th1 -> 1
        conn.execute("UPDATE crawl_requests SET lease_expires_at = ? WHERE id = 'r_act'", (past_iso,))
        self.assertEqual(eval_sig(), 1)

        # 6. Pending/errored registry_sync_outbox blocks -> 0; drained -> 1
        conn.execute(
            "INSERT INTO registry_sync_outbox (id, request_id, run_id, payload_json, status, attempts, created_at, updated_at) VALUES ('out_1', 'prev', 'run_prev', '[]', 'error', 1, ?, ?)",
            (now_iso, now_iso),
        )
        self.assertEqual(eval_sig(), 0)
        conn.execute("DELETE FROM registry_sync_outbox")
        self.assertEqual(eval_sig(), 1)

        # 7. Completed request with registry_sync_status=error/pending blocks -> 0; ok -> 1
        conn.execute(
            "INSERT INTO crawl_requests (id, platform, keywords, requested_count, status, result_summary_json, attempts, max_attempts, created_at, updated_at) VALUES ('c_err', 'threads', 'x', 10, 'completed', '{\"registry_sync_status\":\"error\"}', 1, 3, ?, ?)",
            (now_iso, now_iso),
        )
        self.assertEqual(eval_sig(), 0)
        conn.execute("UPDATE crawl_requests SET result_summary_json = '{\"registry_sync_status\":\"ok\"}' WHERE id = 'c_err'")
        self.assertEqual(eval_sig(), 1)

        # 8. Consecutive Threads requests transition: running -> 0, completed -> 1 for second, completed -> 0
        conn.execute(
            "INSERT INTO crawl_requests (id, platform, keywords, requested_count, status, attempts, max_attempts, created_at, updated_at) VALUES ('q_th2', 'threads', 'makeup', 30, 'queued', 0, 3, '2026-11-27T12:10:05.000Z', '2026-11-27T12:10:05.000Z')"
        )
        conn.execute("UPDATE crawl_requests SET status = 'running', lease_expires_at = ?, attempts = 1 WHERE id = 'q_th1'", (future_iso,))
        self.assertEqual(eval_sig(), 0)
        conn.execute("UPDATE crawl_requests SET status = 'completed', result_summary_json = '{\"registry_sync_status\":\"ok\"}' WHERE id = 'q_th1'")
        self.assertEqual(eval_sig(), 1)
        conn.execute("UPDATE crawl_requests SET status = 'completed', result_summary_json = '{\"registry_sync_status\":\"ok\"}' WHERE id = 'q_th2'")
        self.assertEqual(eval_sig(), 0)
        conn.close()

    def test_worker_v2_phase2_single_flight_and_guarded_sql_real_sqlite(self):
        """Real SQLite validation of Phase 2 claim single-flight SQL, progress guard SQL, and fail-retry SQL."""
        import sqlite3
        from pathlib import Path as _Path

        root_p = _Path(ROOT_DIR)
        conn = sqlite3.connect(":memory:")
        conn.executescript((root_p / "cloudflare-worker" / "migrations" / "0001_v2_market_and_commands.sql").read_text(encoding="utf-8"))
        conn.executescript((root_p / "cloudflare-worker" / "migrations" / "0003_cross_run_dedup_registry.sql").read_text(encoding="utf-8"))

        t0 = "2026-11-27T14:20:00.000Z"
        lease_future = "2026-11-27T14:50:00.000Z"
        lease_past = "2026-11-27T13:50:00.000Z"

        conn.execute(
            "INSERT INTO crawl_requests (id, platform, keywords, requested_count, status, attempts, max_attempts, created_at, updated_at) VALUES ('crw_all_p2', 'all', 'k', 10, 'queued', 0, 3, '2026-11-27T14:00:00.000Z', ?)",
            (t0,),
        )
        conn.execute(
            "INSERT INTO crawl_requests (id, platform, keywords, requested_count, status, attempts, max_attempts, created_at, updated_at) VALUES ('crw_th1_p2', 'threads', 'beauty', 20, 'queued', 0, 3, '2026-11-27T14:01:00.000Z', ?)",
            (t0,),
        )
        conn.execute(
            "INSERT INTO crawl_requests (id, platform, keywords, requested_count, status, attempts, max_attempts, created_at, updated_at) VALUES ('crw_th2_p2', 'threads', 'fashion', 20, 'queued', 0, 3, '2026-11-27T14:02:00.000Z', ?)",
            (t0,),
        )

        claim_sql = """
            UPDATE crawl_requests
            SET status = 'running', claimed_by = ?, claimed_at = ?, lease_expires_at = ?, attempts = attempts + 1, updated_at = ?
            WHERE id = ?
              AND status = 'queued'
              AND lower(trim(platform)) = 'threads'
              AND COALESCE(attempts, 0) < COALESCE(max_attempts, 3)
              AND NOT EXISTS (
                  SELECT 1 FROM crawl_requests r_run
                  WHERE r_run.status = 'running'
                    AND (r_run.lease_expires_at IS NULL OR r_run.lease_expires_at >= ?)
              )
              AND NOT EXISTS (
                  SELECT 1 FROM registry_sync_outbox
                  WHERE status IN ('pending', 'error', 'processing', 'done')
              )
        """
        # Attempting to claim 'all' fails even when queued
        cur = conn.execute(claim_sql, ("muse-ai", t0, lease_future, t0, "crw_all_p2", t0))
        self.assertEqual(cur.rowcount, 0)

        # Claiming oldest Threads wins once
        cur = conn.execute(claim_sql, ("muse-ai", t0, lease_future, t0, "crw_th1_p2", t0))
        self.assertEqual(cur.rowcount, 1)

        # Single-flight blocks second Threads claim while crw_th1_p2 runs with valid lease
        cur = conn.execute(claim_sql, ("muse-ai", t0, lease_future, t0, "crw_th2_p2", t0))
        self.assertEqual(cur.rowcount, 0)

        # Progress guard SQL enforces owner, attempt, running status, and unexpired lease
        prog_sql = """
            UPDATE crawl_requests
            SET result_summary_json = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND claimed_by = ? AND attempts = ?
              AND (lease_expires_at IS NULL OR lease_expires_at >= ?)
        """
        self.assertEqual(conn.execute(prog_sql, ("{\"scanned\":10}", t0, "crw_th1_p2", "wrong", 1, t0)).rowcount, 0)
        self.assertEqual(conn.execute(prog_sql, ("{\"scanned\":10}", t0, "crw_th1_p2", "muse-ai", 2, t0)).rowcount, 0)
        self.assertEqual(conn.execute(prog_sql, ("{\"scanned\":10}", t0, "crw_th1_p2", "muse-ai", 1, t0)).rowcount, 1)

        # Expire lease -> progress update fails
        conn.execute("UPDATE crawl_requests SET lease_expires_at = ? WHERE id = 'crw_th1_p2'", (lease_past,))
        self.assertEqual(conn.execute(prog_sql, ("{\"scanned\":20}", t0, "crw_th1_p2", "muse-ai", 1, t0)).rowcount, 0)

        # Real SQLite check of insertMeasurementSnapshotInD1 (dedup_key, run_id) guard across Completion API vs Desktop Sync
        conn.execute(
            """
            INSERT INTO market_post_snapshots (
                id, snapshot_idempotency_key, dedup_key, platform, post_id, run_id, measured_at, classification, created_at
            ) VALUES ('snp_comp_1', 'threads:post:P_SYNC|run_sync_1|2026-11-27T14:25:00+07:00|:1:0::::', 'threads:post:P_SYNC', 'threads', 'P_SYNC', 'run_sync_1', '2026-11-27T14:25:00+07:00', 'NEW', '2026-11-27T14:25:00+07:00')
            """
        )
        dup_hit = conn.execute(
            """
            SELECT id FROM market_post_snapshots
            WHERE (dedup_key = ? OR (? IS NOT NULL AND platform = ? AND post_id = ?))
              AND run_id = ?
            LIMIT 1
            """,
            ("threads:post:P_SYNC", "P_SYNC", "threads", "P_SYNC", "run_sync_1"),
        ).fetchone()
        self.assertIsNotNone(dup_hit)
        conn.close()


class _PromptCapableMockClient(MockCloudflareTransportClient):
    """Extends MockCloudflareTransportClient with Manual Prompt Trigger fields."""

    def get_public_config_summary(self):
        return {"configured": True, "muse_web_url": "https://muse.ai/"}

    def create_crawl_request(self, platform: str, keywords: str = "", requested_count: int = 50, poll_profile=None, dedup_token=None, options=None):
        res = super().create_crawl_request(
            platform=platform,
            keywords=keywords,
            requested_count=requested_count,
            poll_profile=poll_profile,
            dedup_token=dedup_token,
            options=options,
        )
        req = res["request"]
        plat = str(req.get("platform") or "threads").lower()
        cv = "3.2" if plat == "threads" else "v2"
        req["display_status"] = "waiting_prompt"
        req["display_status_label"] = "Chờ gửi prompt"
        req["contract_version"] = cv
        req["manual_prompt"] = (
            f"Thực hiện crawl {plat}, mục tiêu {req['requested_count']} bài.\n"
            f"Request ID: {req['id']}\nPlatform: {plat}\nContract Version: {cv}\nKeywords: {keywords or '(default)'}"
        )
        res["manual_prompt"] = req["manual_prompt"]
        res["muse_web_url"] = "https://muse.ai/"
        return res

    def get_crawl_request_prompt(self, crawl_id: str):
        for req in self.mock_crawl_requests:
            if req.get("id") == crawl_id:
                return {
                    "ok": True,
                    "request": req,
                    "manual_prompt": req.get("manual_prompt", f"Request ID: {crawl_id}"),
                    "muse_web_url": "https://muse.ai/",
                }
        return {"ok": False, "error": "Not found"}


class TestManualPromptTriggerAndAutoSync(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.client = _PromptCapableMockClient()

    def tearDown(self):
        try:
            rt = app_ui.get_market_runtime()
            rt["remote_poller"].stop(timeout=1.0)
            rt["sync_service"].stop(timeout=1.0)
            rt["store"].close()
        except Exception:
            pass
        app_ui._market_runtime = None
        self.tmp_dir.cleanup()

    def test_crawl_center_manual_prompt_ui_and_prompt_copy_non_duplication(self):
        app_ui.reset_market_runtime_for_tests(data_dir=self.tmp_dir.name, client=self.client)
        app_ui.app.config["TESTING"] = True
        flask_client = app_ui.app.test_client()

        html = flask_client.get("/").get_data(as_text=True)
        for token in [
            "Tạo & Copy Prompt",
            "Copy lại Prompt",
            "Mở Muse",
            "Chờ gửi prompt",
            "Đang crawl",
            "Đang đồng bộ",
            "Hoàn tất",
            "platform: 'threads'",
            "lease_minutes: 30",
            "Crawl Strategy 3.3",
            "60 phút",
            "120 phút",
            "getMarketSyncSignature",
            "getCrawlDisplayState",
            "Hook đã tắt — chế độ Manual Prompt Trigger",
        ]:
            self.assertIn(token, html)

        create_resp = flask_client.post(
            "/api/remote/crawl_requests",
            json={"platform": "threads", "requested_count": 10, "keywords": "serum vitamin c"},
        )
        self.assertEqual(create_resp.status_code, 200)
        created = create_resp.get_json()
        self.assertTrue(created["ok"])
        self.assertEqual(created["muse_web_url"], "https://muse.ai/")
        req_id = created["request"]["id"]
        first_prompt = created["manual_prompt"]
        self.assertIn(req_id, first_prompt)
        self.assertEqual(len(self.client.mock_crawl_requests), 1)

        # Copying existing prompt repeatedly MUST NOT create duplicate crawl requests
        for _ in range(3):
            p_resp = flask_client.get(f"/api/remote/crawl_requests/{req_id}/prompt")
            self.assertEqual(p_resp.status_code, 200)
            p_json = p_resp.get_json()
            self.assertTrue(p_json["ok"])
            self.assertEqual(p_json["manual_prompt"], first_prompt)
            self.assertEqual(len(self.client.mock_crawl_requests), 1)

        # Invalid Threads lease duration rejected with 400
        bad_dur = flask_client.post(
            "/api/remote/crawl_requests",
            json={"platform": "threads", "requested_count": 10, "lease_minutes": 999, "keywords": "test"},
        )
        self.assertEqual(bad_dur.status_code, 400)

        # Valid Threads duration (60 phút -> 3600s) forwarded cleanly inside options
        dur_resp = flask_client.post(
            "/api/remote/crawl_requests",
            json={"platform": "threads", "requested_count": 15, "lease_minutes": 60, "keywords": "skincare"},
        )
        self.assertEqual(dur_resp.status_code, 200)
        self.assertEqual(self.client.mock_crawl_requests[-1]["options"].get("lease_seconds"), 3600)

    def test_desktop_auto_sync_triggers_on_metrics_only_change_and_debounces(self):
        initial_post = {
            "run_id": "run_auto_1",
            "platform": "threads",
            "crawled_at": "2026-11-28T01:00:00Z",
            "posts": [{
                "post_id": "P_AUTO_1",
                "post_url": "https://www.threads.com/@shop.vn/post/P_AUTO_1",
                "author_handle": "@shop.vn",
                "content_summary_vi": "Review kem chống nắng kiềm dầu",
                "views": None,
                "likes": 12,
                "comments": 2,
            }],
        }
        self.client.add_drive_file(
            "f_auto_1",
            "threads_trend_20261128_0800.json",
            json.dumps(initial_post).encode("utf-8"),
            "2026-11-28T01:00:00Z",
            "md5_auto_1",
            folder_path="Threads",
        )
        app_ui.reset_market_runtime_for_tests(data_dir=self.tmp_dir.name, client=self.client)
        app_ui.app.config["TESTING"] = True
        rt = app_ui.get_market_runtime()
        flask_client = app_ui.app.test_client()

        # Initial baseline sync: 1 item, 0 views
        first_res = rt["sync_service"].sync_now(trigger_source="initial_baseline")
        self.assertEqual(first_res["status"], "completed")
        base_stats = flask_client.get("/api/market/status").get_json()["stats"]
        self.assertEqual(base_stats["total_items"], 1)
        self.assertEqual(base_stats["engagement_completeness"]["with_views"], 0)

        # Simulate Worker completing while desktop was running/offline with SAME total_items (1) but updated metrics + report
        updated_post = {
            "run_id": "run_auto_2",
            "request_id": "crw_auto_metrics",
            "attempt": 1,
            "platform": "threads",
            "crawled_at": "2026-11-28T03:00:00Z",
            "posts": [{
                "post_id": "P_AUTO_1",
                "post_url": "https://www.threads.com/@shop.vn/post/P_AUTO_1",
                "author_handle": "@shop.vn",
                "content_summary_vi": "Review kem chống nắng kiềm dầu",
                "views": 18400,
                "likes": 195,
                "comments": 24,
                "views_display": "18.4K",
                "views_precision": "exact_badge",
                "views_source": "threads_web_browser",
                "views_measured_at": "2026-11-28T03:00:00Z",
                "views_evidence_url": "https://www.threads.com/@shop.vn/post/P_AUTO_1",
            }],
        }
        updated_report = {
            "contract_version": "3.2",
            "request_id": "crw_auto_metrics",
            "run_id": "run_auto_2",
            "attempt": 1,
            "platform": "threads",
            "generated_at": "2026-11-28T03:01:00Z",
            "run_status": "completed",
            "stop_reason": "target_reached",
            "counters": {"scanned_count": 1, "unique_candidate_count": 1, "new_qualified_count": 0, "updated_metrics_count": 1},
            "telegram_summary": "Updated metrics P_AUTO_1 with 18.4K views",
        }
        self.client.add_drive_file(
            "f_auto_2",
            "threads_trend_20261128_1000.json",
            json.dumps(updated_post).encode("utf-8"),
            "2026-11-28T03:01:00Z",
            "md5_auto_2",
            folder_path="Threads",
        )
        self.client.add_drive_file(
            "f_rpt_2",
            "trend_report_20261128_1000.json",
            json.dumps(updated_report).encode("utf-8"),
            "2026-11-28T03:01:05Z",
            "md5_rpt_2",
            folder_path="Reports",
        )
        self.client.mock_crawl_requests.append({
            "id": "crw_auto_metrics",
            "platform": "threads",
            "status": "completed",
            "display_status": "completed",
            "updated_at": "2099-01-01T00:00:00Z",
            "report_file_id": "f_rpt_2",
            "result_summary": updated_report,
        })

        # Wrap sync_now to count actual executions & confirm debounce + single-flight
        sync_triggers = []
        orig_sync_now = rt["sync_service"].sync_now
        def spy_sync_now(*args, **kwargs):
            sync_triggers.append(kwargs.get("trigger_source") or (args[0] if args else "sync"))
            return orig_sync_now(*args, **kwargs)
        rt["sync_service"].sync_now = spy_sync_now

        # Polling check_crawl_results_once triggers background sync ONCE
        rt["remote_poller"].check_crawl_results_once()
        # Immediate repeat via GET /api/remote/crawl_requests & direct helper MUST debounce cleanly
        flask_client.get("/api/remote/crawl_requests")
        rt["sync_service"].maybe_trigger_sync_for_crawl_requests(self.client.mock_crawl_requests)

        deadline = time.monotonic() + 5.0
        new_stats = None
        while time.monotonic() < deadline:
            st_body = flask_client.get("/api/market/status").get_json()
            new_stats = st_body["stats"]
            if (not st_body.get("is_syncing")) and new_stats["engagement_completeness"]["with_views"] == 1:
                break
            time.sleep(0.05)

        self.assertEqual(len(sync_triggers), 1)
        self.assertIsNotNone(new_stats)
        self.assertEqual(new_stats["total_items"], 1)  # unchanged total_items
        self.assertEqual(new_stats["engagement_completeness"]["with_views"], 1)
        items_after = flask_client.get("/api/market/items?platform=threads").get_json()["items"]
        self.assertEqual(items_after[0]["views_count"], 18400)
        self.assertEqual(items_after[0]["likes_count"], 195)
        rpt_after = flask_client.get("/api/market/report?platform=threads").get_json()["report"]
        self.assertEqual(rpt_after["run_id"], "run_auto_2")

    def test_run_centric_flask_endpoints_and_ui_controls(self):
        """Flask API & rendered UI support run_key filtering, runs_catalog, run comparison, and isolated platform counters."""
        app_ui.reset_market_runtime_for_tests(data_dir=self.tmp_dir.name, client=self.client)
        app_ui.app.config["TESTING"] = True
        rt = app_ui.get_market_runtime()
        flask_client = app_ui.app.test_client()
        store = rt["store"]

        run1 = {
            "contract_version": "3.2",
            "run_id": "run_flask_1",
            "platform": "threads",
            "crawled_at": "2026-11-27T09:00:00Z",
            "records": [
                {
                    "post_id": "FLK_1",
                    "post_url": "https://threads.net/@flk/post/FLK_1",
                    "author_handle": "@flk",
                    "likes": 110,
                    "views": 4000,
                    "views_display": "4K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-27T09:00:00Z",
                    "views_evidence_url": "https://threads.net/@flk/post/FLK_1",
                    "classification": "NEW",
                    "tier": "emerging",
                }
            ],
        }
        run2 = {
            "contract_version": "3.2",
            "run_id": "run_flask_2",
            "platform": "threads",
            "crawled_at": "2026-11-28T09:00:00Z",
            "records": [
                {
                    "post_id": "FLK_1",
                    "post_url": "https://threads.net/@flk/post/FLK_1",
                    "author_handle": "@flk",
                    "likes": 620,
                    "views": 25000,
                    "views_display": "25K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-28T09:00:00Z",
                    "views_evidence_url": "https://threads.net/@flk/post/FLK_1",
                    "classification": "UPDATED_METRICS",
                }
            ],
        }
        tiktok_run = {
            "run_id": "run_flask_tt",
            "platform": "tiktok",
            "crawled_at": "2026-11-27T12:00:00Z",
            "records": [
                {"video_id": "TT_FLK_1", "video_url": "https://tiktok.com/@a/video/TT_FLK_1", "play_count": 9000, "likes": 300}
            ],
        }
        store.ingest_raw_file("fid_flk_1", "threads_trend_flk1.json", json.dumps(run1).encode("utf-8"), modified_time="2026-11-27T09:00:00Z")
        store.ingest_raw_file("fid_flk_tt", "tiktok_crawl_flk.json", json.dumps(tiktok_run).encode("utf-8"), modified_time="2026-11-27T12:00:00Z")
        store.ingest_raw_file("fid_flk_2", "threads_trend_flk2.json", json.dumps(run2).encode("utf-8"), modified_time="2026-11-28T09:00:00Z")

        cat_res = flask_client.get("/api/market/runs_catalog")
        self.assertEqual(cat_res.status_code, 200)
        cat_data = cat_res.get_json()
        self.assertTrue(cat_data["ok"])
        self.assertEqual(cat_data["latest_run_key"], "run_flask_2")
        self.assertEqual(len(cat_data["runs"]), 3)

        # Point-in-time run_key=run_flask_1 must return likes=110 (not 620 from run_flask_2)
        items_r1 = flask_client.get("/api/market/items?platform=threads&run_key=run_flask_1").get_json()
        self.assertEqual(items_r1["total"], 1)
        self.assertEqual(items_r1["items"][0]["likes_count"], 110)

        # Global status separates per-platform activity (Threads=1 vs Global=2)
        st_global = flask_client.get("/api/market/status").get_json()["stats"]
        self.assertEqual(st_global["total_items"], 2)
        self.assertEqual(st_global["by_platform_activity"]["threads"]["total"], 1)
        self.assertEqual(st_global["by_platform_activity"]["tiktok"]["total"], 1)

        # Run-scoped report
        rep_r1 = flask_client.get("/api/market/report?platform=threads&run_key=run_flask_1").get_json()["report"]
        self.assertEqual(rep_r1["run_id"], "run_flask_1")
        self.assertFalse(rep_r1["has_muse_report"])

        # Compare validation & happy path
        bad_cmp = flask_client.get("/api/market/runs/compare?base_run_key=run_flask_1")
        self.assertEqual(bad_cmp.status_code, 400)
        good_cmp = flask_client.get("/api/market/runs/compare?base_run_key=run_flask_1&target_run_key=run_flask_2").get_json()
        self.assertTrue(good_cmp["ok"])
        self.assertEqual(good_cmp["summary"]["total_likes_delta"], 510)
        self.assertEqual(good_cmp["summary"]["total_views_delta"], 21000)

        # Rendered HTML contains Run-centric controls, 7 Run Tabs & Crawl Center wide button
        html = flask_client.get("/").get_data(as_text=True)
        for marker in (
            "Theo lượt crawl",
            "Toàn bộ lịch sử",
            "So sánh tăng trưởng 2 lượt",
            "Tổng quan lượt crawl",
            "Bài mới đạt chuẩn",
            "Bài cập nhật tương tác",
            "Bài trùng lịch sử",
            "Bài chưa xác minh",
            "Bài bị loại",
            "Báo cáo phân tích",
            "formatRatioMetric",
            "activePlatformActivity",
            "userPinnedRunKey",
            "Tạo & Copy Prompt",
        ):
            self.assertIn(marker, html)

    def test_run_centric_v33_special_filter_and_growth_tracking_api(self):
        app_ui.reset_market_runtime_for_tests(data_dir=self.tmp_dir.name, client=self.client)
        app_ui.app.config["TESTING"] = True
        rt = app_ui.get_market_runtime()
        flask_client = app_ui.app.test_client()
        store = rt["store"]
        payload = {
            "contract_version": "3.2",
            "strategy_version": "3.3",
            "run_id": "run_threads_v33_api",
            "platform": "threads",
            "crawled_at": "2026-11-28T05:10:00Z",
            "records": [
                {
                    "post_id": "API_V33_1",
                    "post_url": "https://threads.net/@nat/post/API_V33_1",
                    "author": {"username": "@nat", "followers": 100},
                    "likes": 500,
                    "views": 10000,
                    "views_display": "10K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-28T05:10:00Z",
                    "views_evidence_url": "https://threads.net/@nat/post/API_V33_1",
                    "classification": "NEW",
                    "tier": "hot",
                    "natural_viral": True,
                    "buyer_intent": False,
                },
                {
                    "post_id": "API_V33_2",
                    "post_url": "https://threads.net/@buy/post/API_V33_2",
                    "author": {"username": "@buy", "followers": 200},
                    "likes": 250,
                    "views": 5000,
                    "views_display": "5K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-28T05:10:00Z",
                    "views_evidence_url": "https://threads.net/@buy/post/API_V33_2",
                    "classification": "NEW",
                    "tier": "emerging",
                    "natural_viral": False,
                    "buyer_intent": True,
                },
            ],
        }
        rep = {
            "contract_version": "3.2",
            "strategy_version": "3.3",
            "run_id": "run_threads_v33_api",
            "platform": "threads",
            "generated_at": "2026-11-28T05:11:00Z",
            "sections": {
                "9_growth_tracking": [
                    {
                        "post_id": "API_V33_G",
                        "author": "@grower",
                        "theme": "Gadget",
                        "likes_before": 100,
                        "likes_after": 400,
                        "comments_before": 5,
                        "comments_after": 22,
                        "views_now_display": "12K",
                        "author_followers": 500,
                    }
                ]
            },
        }
        store.ingest_raw_file("fid_v33_d", "threads_trend_v33.json", json.dumps(payload).encode("utf-8"), modified_time="2026-11-28T05:10:00Z")
        store.ingest_raw_file("fid_v33_r", "trend_report_v33.json", json.dumps(rep).encode("utf-8"), modified_time="2026-11-28T05:11:00Z")

        cat = flask_client.get("/api/market/runs_catalog").get_json()
        self.assertEqual(cat["runs"][0]["strategy_version"], "3.3")
        self.assertEqual(cat["runs"][0]["contract_version"], "3.2")

        nat_res = flask_client.get("/api/market/items?platform=threads&run_key=run_threads_v33_api&special_filter=natural_viral").get_json()
        self.assertEqual([it["post_id"] for it in nat_res["items"]], ["API_V33_1"])
        self.assertEqual(nat_res["items"][0]["views_to_followers_ratio"], 100.0)

        buy_res = flask_client.get("/api/market/items?platform=threads&run_key=run_threads_v33_api&special_filter=buyer_intent").get_json()
        self.assertEqual([it["post_id"] for it in buy_res["items"]], ["API_V33_2"])

        rep_res = flask_client.get("/api/market/report?platform=threads&run_key=run_threads_v33_api").get_json()["report"]
        self.assertEqual(rep_res["strategy_version"], "3.3")
        self.assertEqual(rep_res["growth_tracking"][0]["post_id"], "API_V33_G")


if __name__ == "__main__":
    unittest.main(verbosity=2)
