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


class MockCloudflareTransportClient(CloudflareBridgeClient):
    """In-memory mock mimicking Worker V2 endpoints + D1 queue for deterministic local tests."""

    def __init__(self, api_token: str = "test-secret-token-1234567890"):
        self.mock_files = {}
        self.mock_commands = []
        self.mock_heartbeats = []
        self.mock_crawl_requests = []
        self.auth_headers_seen = []
        self.fail_next_complete = False
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

    def add_drive_file(self, file_id: str, name: str, raw_bytes: bytes, modified_time: str, md5: str, platform_hint: str = "threads"):
        self.mock_files[file_id] = {
            "file_id": file_id,
            "name": name,
            "folder_path": "Threads",
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

    def list_market_files(self, cursor=None, limit=50):
        self.list_call_count += 1
        self.auth_headers_seen.append(self._headers()["Authorization"])
        files = []
        for f in sorted(self.mock_files.values(), key=lambda x: x["modified_time"]):
            if cursor and f["modified_time"] < cursor:
                continue
            files.append({k: v for k, v in f.items() if k != "bytes"})
        return {"ok": True, "files": files[:limit]}

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

    def complete_command(self, command_id, status, result=None, error_message=None):
        if self.fail_next_complete:
            self.fail_next_complete = False
            raise RuntimeError("Simulated network failure during ack")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
