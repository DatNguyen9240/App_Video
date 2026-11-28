import copy
import io
import json
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
FIXTURE_DOUYIN = ROOT_DIR / "tests" / "fixtures" / "creator_reup" / "douyin_discovery_sample_20261128.json"

for p in (str(ROOT_DIR), str(SRC_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import app_ui
from creator_reup import (
    CreatorDriveSyncService,
    CreatorReupStore,
    build_creator_crawl_prompt,
    evaluate_video_filters,
    parse_creator_crawl_payload,
    parse_duration_seconds,
)


def _build_bilibili_simulated_v1(
    *,
    request_id="creq_sim_bili_01",
    crawl_id="ccrawl_sim_bili_01",
    items=None,
):
    """Build a schema-compliant simulated Bilibili v1 payload for unit tests.

    NOTE: Real Bilibili Muse output has not yet been provided/verified in production;
    this helper creates synthetic schema v1 data solely for local filter/parser verification.
    """
    return {
        "schema_version": "1.0",
        "platform": "bilibili",
        "request_id": request_id,
        "crawl_id": crawl_id,
        "creator": {
            "creator_id": "946974",
            "creator_handle": "影视飓风",
            "creator_name": "影视飓风",
            "profile_url": "https://space.bilibili.com/946974",
            "followers_display": "10.2M",
            "followers": 10200000,
        },
        "crawl": {
            "crawled_at": "2026-11-28T18:00:00+08:00",
            "method": "simulated_test_fixture",
            "session": "test",
            "request_id": request_id,
            "crawl_id": crawl_id,
        },
        "items": items if items is not None else [
            {
                "video_id": "BV1xx411c7mD",
                "video_id_secondary": "170001",
                "creator_id": "946974",
                "video_url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "content_type": "video",
                "title": "Simulated Bilibili Pass Video",
                "thumbnail": "https://i0.hdslb.com/bfs/archive/sim1.jpg",
                "views": 50000,
                "likes": 5000,
                "comments": 120,
                "shares": 45,
                "duration_seconds": 299,
                "published_at": "2026-11-20T12:00:00+08:00",
                "metrics_precision": "exact",
            }
        ],
    }


def _build_douyin_v1_payload(
    *,
    request_id="creq_dy_01",
    crawl_id="ccrawl_dy_01",
    items=None,
):
    return {
        "schema_version": "1.0",
        "platform": "douyin",
        "request_id": request_id,
        "crawl_id": crawl_id,
        "creator": {
            "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
            "creator_handle": "cctvnews",
            "creator_name": "央视新闻",
            "profile_url": "https://www.douyin.com/user/MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
            "followers_display": "1.9亿",
        },
        "crawl": {
            "crawled_at": "2026-11-28T18:06:00+08:00",
            "method": "muse_web_v1",
            "request_id": request_id,
            "crawl_id": crawl_id,
        },
        "items": items if items is not None else [
            {
                "video_id": "7713142637636668682",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/7713142637636668682?from_tab_name=main",
                "content_type": "video",
                "title": "High Three CPR Rescue",
                "thumbnail": "https://p3-sign.douyinpic.com/sim_thumb.jpg",
                "views": None,
                "likes": None,
                "likes_display": "77.4K",
                "metrics_precision": "display_rounded",
                "comments": 114,
                "shares": 1727,
                "duration_seconds": 39,
                "published_at": "2026-11-28 15:53",
            }
        ],
    }


def test_douyin_discovery_fixture_acceptance():
    raw_text = FIXTURE_DOUYIN.read_text(encoding="utf-8")
    bundle = parse_creator_crawl_payload(raw_text)

    assert bundle["source_format"] == "discovery_sample"
    assert len(bundle["creators"]) == 3
    # Architect acceptance: 2 valid videos parsed, 1 /note/ excluded, grid_sample not matched as videos
    assert bundle["stats"]["valid_videos_count"] == 2
    assert bundle["stats"]["notes_excluded"] == 1
    assert [it["source_video_id"] for it in bundle["items"]] == [
        "7713142637636668682",
        "7713173603738520859",
    ]

    first = bundle["items"][0]
    assert first["duration_seconds"] == 39
    assert first["published_at"] == "2026-11-28T15:53:00+08:00"
    assert first["published_at_display"] == "2026-11-28 15:53"
    assert first["likes"] is None
    assert first["likes_display"] == "77.4K"
    assert first["effective_likes"] == 77400
    assert first["metrics_precision"] == "display_rounded"
    assert first["views"] is None

    # Default filter at fixed measured_at keeps exactly 1 video
    ref_time = bundle["reference_time"]
    evaluated = [
        evaluate_video_filters(it, platform="douyin", reference_time=ref_time)
        for it in bundle["items"]
    ]
    passed = [ev for ev in evaluated if ev["passed_filter"]]
    failed = [ev for ev in evaluated if not ev["passed_filter"]]
    assert len(passed) == 1
    assert passed[0]["source_video_id"] == "7713142637636668682"
    assert len(failed) == 1
    assert failed[0]["source_video_id"] == "7713173603738520859"
    assert any(r.startswith("likes_below_min:2328<5000") for r in failed[0]["exclusion_reasons"])


def test_parser_strict_validation_zero_vs_null_and_verification_flags():
    payload = _build_douyin_v1_payload(
        items=[
            # Valid video with explicit 0 likes & 0 comments vs null views, plus near-threshold rounded display
            {
                "video_id": "7713142637636668699",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/7713142637636668699",
                "content_type": "video",
                "likes": 0,
                "views": None,
                "comments": 0,
                "duration_seconds": 0,
                "published_at": "2026-11-28 10:00",
            },
            # Excluded note even if tagged content_type=video because URL is /note/
            {
                "video_id": "7697195938004555953",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/note/7697195938004555953",
                "content_type": "video",
                "likes": 99999,
                "duration_seconds": 25,
                "published_at": "2026-11-28 10:00",
            },
            # Mismatched video_id vs URL -> must reject as invalid_excluded
            {
                "video_id": "1111111111111111111",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/2222222222222222222",
                "content_type": "video",
                "likes": 80000,
                "duration_seconds": 30,
                "published_at": "2026-11-28 10:00",
            },
            # Near-threshold rounded label -> needs_verification flagged
            {
                "video_id": "7713142637636668701",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/7713142637636668701",
                "content_type": "video",
                "likes": None,
                "likes_display": "5.1K",
                "metrics_precision": "display_rounded",
                "duration_display": "00:45",
                "published_at": "2026-11-28 10:00",
            },
        ]
    )
    bundle = parse_creator_crawl_payload(payload)
    assert bundle["stats"]["notes_excluded"] == 1
    assert bundle["stats"]["invalid_excluded"] == 1
    assert bundle["stats"]["valid_videos_count"] == 2

    zero_item = bundle["items"][0]
    assert zero_item["likes"] == 0 and zero_item["likes"] is not None
    assert zero_item["views"] is None
    assert zero_item["comments"] == 0 and zero_item["duration_seconds"] == 0
    assert parse_duration_seconds("00:39") == 39
    assert parse_duration_seconds(0) == 0
    assert parse_duration_seconds(None) is None

    rounded_near = evaluate_video_filters(
        bundle["items"][1],
        platform="douyin",
        reference_time="2026-11-28T18:00:00+08:00",
    )
    assert rounded_near["passed_filter"] is True
    assert rounded_near["needs_verification"] is True
    assert rounded_near["filter_status"] == "needs_verification"
    assert "rounded_likes_near_threshold" in rounded_near["verification_reasons"]


def test_filter_boundaries_douyin_and_simulated_bilibili():
    ref = "2026-11-28T18:00:00+08:00"

    # Douyin boundaries: 5000 likes pass, 4999 fail; 299s pass, 300s fail; 365d pass, 366d fail unless >=50k likes
    dy_cases = [
        {"id": "dy_pass_edge", "likes": 5000, "dur": 299, "pub": "2025-11-28T18:00:00+08:00", "want": True, "exc": False},
        {"id": "dy_fail_likes", "likes": 4999, "dur": 299, "pub": "2026-11-01T18:00:00+08:00", "want": False, "exc": False},
        {"id": "dy_fail_dur", "likes": 10000, "dur": 300, "pub": "2026-11-01T18:00:00+08:00", "want": False, "exc": False},
        {"id": "dy_fail_age", "likes": 49999, "dur": 120, "pub": "2025-11-27T17:59:00+08:00", "want": False, "exc": False},
        {"id": "dy_pass_old_exc", "likes": 50000, "dur": 120, "pub": "2024-01-01T12:00:00+08:00", "want": True, "exc": True},
    ]
    for c in dy_cases:
        ev = evaluate_video_filters(
            {
                "source_platform": "douyin",
                "likes": c["likes"],
                "effective_likes": c["likes"],
                "views": None,
                "duration_seconds": c["dur"],
                "published_at": c["pub"],
            },
            platform="douyin",
            reference_time=ref,
        )
        assert ev["passed_filter"] is c["want"], c["id"]
        assert ev["age_exception_applied"] is c["exc"], c["id"]

    # Simulated Bilibili boundaries: views>=50000 AND likes>=5000, <300s, <=365d; old exception views>=1M OR likes>=50k
    bili_payload = _build_bilibili_simulated_v1(
        items=[
            # Pass exact boundary
            {"video_id": "BV1A", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1A", "views": 50000, "likes": 5000, "duration_seconds": 299, "published_at": "2025-11-28T18:00:00+08:00"},
            # Fail: views 49999 even though likes high (AND required)
            {"video_id": "BV1B", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1B", "views": 49999, "likes": 9000, "duration_seconds": 100, "published_at": "2026-11-01T18:00:00+08:00"},
            # Fail: likes 4999 even though views >= 50000
            {"video_id": "BV1C", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1C", "views": 80000, "likes": 4999, "duration_seconds": 100, "published_at": "2026-11-01T18:00:00+08:00"},
            # Fail: duration 300s
            {"video_id": "BV1D", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1D", "views": 90000, "likes": 8000, "duration_seconds": 300, "published_at": "2026-11-01T18:00:00+08:00"},
            # Fail: >365d & neither 1M views nor 50k likes
            {"video_id": "BV1E", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1E", "views": 999999, "likes": 49999, "duration_seconds": 100, "published_at": "2024-06-01T12:00:00+08:00"},
            # Pass old exception via >= 1M views
            {"video_id": "BV1F", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1F", "views": 1000000, "likes": 6000, "duration_seconds": 250, "published_at": "2024-06-01T12:00:00+08:00"},
            # Pass old exception via >= 50k likes
            {"video_id": "BV1G", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1G", "views": 70000, "likes": 50000, "duration_seconds": 250, "published_at": "2024-06-01T12:00:00+08:00"},
        ]
    )
    bundle = parse_creator_crawl_payload(bili_payload)
    results = {
        it["source_video_id"]: evaluate_video_filters(it, platform="bilibili", reference_time=ref)
        for it in bundle["items"]
    }
    assert results["BV1A"]["passed_filter"] is True
    assert results["BV1B"]["passed_filter"] is False
    assert results["BV1C"]["passed_filter"] is False
    assert results["BV1D"]["passed_filter"] is False
    assert results["BV1E"]["passed_filter"] is False
    assert results["BV1F"]["passed_filter"] is True and results["BV1F"]["age_exception_applied"] is True
    assert results["BV1G"]["passed_filter"] is True and results["BV1G"]["age_exception_applied"] is True


def test_prompt_builder_generates_correlatable_ids():
    spec = build_creator_crawl_prompt(
        profile_url="https://space.bilibili.com/946974",
        requested_count=15,
        drive_folder_id="fld_reup_123",
    )
    assert spec["platform"] == "bilibili"
    assert spec["creator_id"] == "946974"
    assert spec["request_id"].startswith("creq_")
    assert spec["crawl_id"].startswith("ccrawl_")
    assert spec["request_id"] in spec["prompt_text"]
    assert spec["crawl_id"] in spec["prompt_text"]
    assert "fld_reup_123" in spec["prompt_text"]
    assert "views >= 50000" in spec["prompt_text"]
    assert "likes >= 5000" in spec["prompt_text"]


def test_drive_sync_isolates_market_files_and_is_idempotent(tmp_path):
    store = CreatorReupStore(data_dir=str(tmp_path / "creator_reup"))
    req_spec = build_creator_crawl_prompt(
        profile_url="https://www.douyin.com/user/MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
        requested_count=10,
    )
    store.create_crawl_request(req_spec)

    dy_payload = _build_douyin_v1_payload(
        request_id=req_spec["request_id"],
        crawl_id=req_spec["crawl_id"],
    )

    class FakeBridgeClient:
        def __init__(self):
            self.downloaded = []

        def reload_config(self):
            pass

        def is_configured(self):
            return True

        def list_market_files(self, cursor=None, limit=100, page_token=None):
            if page_token is None:
                return {
                    "ok": True,
                    "has_more": True,
                    "next_page_token": "p2",
                    "files": [
                        {"file_id": "f_mkt_1", "name": "threads_trend_20261128.json", "modified_time": "2026-11-28T10:00:00Z", "md5_checksum": "m1"},
                        {"file_id": "f_arch_1", "name": "creator_crawl_old.json", "folder_path": "archive/2026", "modified_time": "2026-11-28T10:01:00Z", "md5_checksum": "a1"},
                    ],
                }
            return {
                "ok": True,
                "has_more": False,
                "files": [
                    {"file_id": "f_misc_mkt", "name": "daily_snapshot.json", "modified_time": "2026-11-28T10:02:00Z", "md5_checksum": "u1"},
                    {"file_id": "f_creator_1", "name": "creator_crawl_douyin_cctv.json", "modified_time": "2026-11-28T10:05:00Z", "md5_checksum": "c1"},
                ],
            }

        def download_market_file(self, file_id):
            self.downloaded.append(file_id)
            if file_id == "f_misc_mkt":
                raw = json.dumps({"platform": "threads", "summary": {"total": 1}, "records": [{"id": "x"}]}).encode("utf-8")
                return raw, {"modified_time": "2026-11-28T10:02:00Z", "md5_checksum": "u1"}
            if file_id == "f_creator_1":
                raw = json.dumps(dy_payload, ensure_ascii=False).encode("utf-8")
                return raw, {"modified_time": "2026-11-28T10:05:00Z", "md5_checksum": "c1"}
            raise AssertionError(f"Unexpected download of {file_id}")

    client = FakeBridgeClient()
    sync_svc = CreatorDriveSyncService(store=store, client=client)

    res1 = sync_svc.sync_now(trigger_source="test")
    assert res1["status"] == "completed"
    assert res1["files_ingested"] == 1
    assert res1["videos_inserted"] == 1
    assert "f_mkt_1" not in client.downloaded  # Market Intelligence prefix skipped without downloading
    assert "f_arch_1" not in client.downloaded  # Archive skipped without downloading
    assert "f_misc_mkt" in client.downloaded    # Non-market name checked & rejected cleanly

    req_after = store.get_crawl_request(req_spec["request_id"])
    assert req_after["status"] == "synced"
    assert req_after["matched_file_count"] == 1

    # Re-sync with same modified_time/checksum is 100% idempotent
    client.downloaded.clear()
    res2 = sync_svc.sync_now(trigger_source="test_repeat")
    assert res2["status"] == "completed"
    assert res2["files_ingested"] == 0
    assert res2["videos_inserted"] == 0
    assert client.downloaded == []


def test_flask_phase1_workflow_rights_dedup_and_single_reup_execution(tmp_path):
    app_ui.reset_creator_reup_runtime_for_tests(data_dir=str(tmp_path / "creator_reup_flask"))
    app_ui.tracker.reset()
    client = app_ui.app.test_client()

    # 1. Create crawl request & prompt
    req_resp = client.post(
        "/api/creator_reup/requests",
        json={
            "profile_url": "https://www.douyin.com/user/MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
            "requested_count": 20,
        },
    )
    assert req_resp.status_code == 200
    req_data = req_resp.get_json()
    assert req_data["ok"] is True
    req_id = req_data["request"]["request_id"]

    # 2. Import Douyin discovery fixture manually with explicit reference_time
    raw_fixture = FIXTURE_DOUYIN.read_text(encoding="utf-8")
    imp_resp = client.post(
        "/api/creator_reup/import_json",
        json={
            "json_text": raw_fixture,
            "file_name": "douyin_discovery_sample_20261128.json",
            "request_id": req_id,
            "reference_time": "2026-11-28T18:06:00+07:00",
        },
    )
    assert imp_resp.status_code == 200
    imp_data = imp_resp.get_json()
    assert imp_data["ok"] is True
    assert imp_data["import_result"]["valid_videos_count"] == 2
    assert imp_data["import_result"]["notes_excluded"] == 1
    assert imp_data["import_result"]["passed_count"] == 1

    # 3. Query passed video sorted by likes_desc
    vid_resp = client.get("/api/creator_reup/videos?filter_status=passed&sort_by=likes_desc")
    assert vid_resp.status_code == 200
    videos = vid_resp.get_json()["videos"]
    assert len(videos) == 1
    target_video = videos[0]
    target_key = target_video["video_key"]
    assert target_video["rights_status"] == "pending_review"
    assert target_video["dedup_status"] == "new"

    fake_reup_mod = types.SimpleNamespace(
        run_reup_pipeline=mock.MagicMock(
            return_value={
                "video_filename": "reup_creator_test.mp4",
                "video_path": str(tmp_path / "reup_creator_test.mp4"),
                "video_title": "Test Creator Reup",
                "caption": "Test caption",
                "duration_sec": 39.0,
            }
        )
    )

    real_load_mod = app_ui.load_mod

    def _patched_load_mod(name, fname):
        if name == "reup_pipeline":
            return fake_reup_mod
        return real_load_mod(name, fname)

    with mock.patch.object(app_ui, "load_mod", side_effect=_patched_load_mod):
        # 4. Usage-rights gate MUST block execution while rights_status == 'pending_review'
        blocked_resp = client.post(
            "/api/creator_reup/run_selected",
            json={"video_keys": [target_key], "sync_execution": True},
        )
        assert blocked_resp.status_code == 400
        assert blocked_resp.get_json()["ok"] is False
        assert fake_reup_mod.run_reup_pipeline.call_count == 0

        # 5. Approve usage rights for this single video
        rights_resp = client.post(
            "/api/creator_reup/videos/rights",
            json={
                "video_keys": [target_key],
                "rights_status": "approved_for_reup",
                "rights_note": "Approved for Phase 1 test",
            },
        )
        assert rights_resp.status_code == 200
        assert rights_resp.get_json()["updated"] == 1

        # 6. Execute Reup Pipeline cleanly on the 1 approved video
        app_ui.tracker.reset()
        run_resp = client.post(
            "/api/creator_reup/run_selected",
            json={
                "video_keys": [target_key],
                "voice": "nam_hai",
                "translation_mode": "viral",
                "frame_mode": "blur_bg",
                "sync_execution": True,
            },
        )
        assert run_resp.status_code == 200
        run_data = run_resp.get_json()
        assert run_data["ok"] is True
        assert run_data["execution"]["succeeded"] == 1
        assert fake_reup_mod.run_reup_pipeline.call_count == 1
        assert fake_reup_mod.run_reup_pipeline.call_args.kwargs["source_url"] == "https://www.douyin.com/video/7713142637636668682"

        # 7. Dedup gate MUST block re-running an already-processed video (HTTP 409)
        app_ui.tracker.reset()
        dup_resp = client.post(
            "/api/creator_reup/run_selected",
            json={"video_keys": [target_key], "sync_execution": True},
        )
        assert dup_resp.status_code == 409
        assert dup_resp.get_json()["blocked"][0]["reason"] == "already_processed"
        assert fake_reup_mod.run_reup_pipeline.call_count == 1

    # 8. State check & Market Intelligence DB remains completely isolated
    all_videos_resp = client.get("/api/creator_reup/videos?sort_by=recent_desc")
    assert all_videos_resp.status_code == 200
    videos_after = all_videos_resp.get_json()["videos"]
    matched = next(v for v in videos_after if v["video_key"] == target_key)
    assert matched["dedup_status"] == "duplicate_processed"

    mkt_rt = app_ui.reset_market_runtime_for_tests(data_dir=str(tmp_path / "market_isolation_check"))
    creator_rt = app_ui.get_creator_reup_runtime()
    assert mkt_rt["store"].db_path != creator_rt["store"].db_path
    mkt_items_resp = client.get("/api/market/items")
    assert mkt_items_resp.status_code == 200
    assert mkt_items_resp.get_json()["total"] == 0

    # 9. Multipart upload & sorting by views_desc works on simulated Bilibili fixture
    bili_file_bytes = json.dumps(_build_bilibili_simulated_v1(), ensure_ascii=False).encode("utf-8")
    up_resp = client.post(
        "/api/creator_reup/import_json",
        data={"file": (io.BytesIO(bili_file_bytes), "creator_crawl_bilibili_946974.json")},
        content_type="multipart/form-data",
    )
    assert up_resp.status_code == 200
    assert up_resp.get_json()["import_result"]["videos_inserted"] == 1
    bili_sorted = client.get("/api/creator_reup/videos?platform=bilibili&sort_by=views_desc").get_json()["videos"]
    assert len(bili_sorted) == 1
    assert bili_sorted[0]["source_video_id"] == "BV1xx411c7mD"

    app_ui.tracker.reset()
