import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
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
import reup_pipeline
from creator_reup import (
    CREATOR_DRIVE_SYNC_BLOCKER,
    CreatorDriveSyncService,
    CreatorParseError,
    CreatorRequestCorrelationError,
    CreatorReupStore,
    build_creator_crawl_prompt,
    evaluate_video_filters,
    parse_creator_crawl_payload,
    parse_duration_seconds,
    sanitize_job_config,
    verify_local_mp4_output,
)


def _make_tiny_valid_mp4(dest: Path) -> Path:
    """Generate a tiny authentic MP4 file on disk via FFmpeg for real output validation."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = reup_pipeline.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:d=0.2:r=10",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    assert res.returncode == 0, f"FFmpeg tiny mp4 helper failed: {res.stderr}"
    return dest


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
    request_id=None,
    crawl_id=None,
    creator_id="MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
    items=None,
):
    return {
        "schema_version": "1.0",
        "platform": "douyin",
        "request_id": request_id,
        "crawl_id": crawl_id,
        "creator": {
            "creator_id": creator_id,
            "creator_handle": "cctvnews",
            "creator_name": "央视新闻",
            "profile_url": f"https://www.douyin.com/user/{creator_id}",
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
                "creator_id": creator_id,
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


def test_filter_regressions_duration_age_future_rounded_zero_null_and_bv_av_alias(tmp_path):
    ref = "2026-11-28T18:00:00+08:00"

    # Douyin boundaries: 299s vs 300s, 365d exact vs 365d+1s, future timestamp, 0 vs None, rounded near threshold
    dy_cases = [
        {"id": "dy_pass_365_exact", "likes": 5000, "dur": 299, "pub": "2025-11-28T18:00:00+08:00", "want": True, "status": "passed"},
        {"id": "dy_fail_365_plus_1s", "likes": 5000, "dur": 299, "pub": "2025-11-28T17:59:59+08:00", "want": False, "status": "filtered_out"},
        {"id": "dy_fail_300s", "likes": 10000, "dur": 300, "pub": "2026-11-01T18:00:00+08:00", "want": False, "status": "filtered_out"},
        {"id": "dy_fail_old_no_exc", "likes": 49999, "dur": 120, "pub": "2025-11-27T17:59:00+08:00", "want": False, "status": "filtered_out"},
        {"id": "dy_pass_old_exc", "likes": 50000, "dur": 120, "pub": "2024-01-01T12:00:00+08:00", "want": True, "status": "passed"},
        {"id": "dy_old_exc_still_checks_dur", "likes": 80000, "dur": 300, "pub": "2024-01-01T12:00:00+08:00", "want": False, "status": "filtered_out"},
        {"id": "dy_future_date_flagged", "likes": 20000, "dur": 45, "pub": "2026-11-29T18:00:00+08:00", "want": False, "status": "needs_verification"},
        {"id": "dy_small_clock_skew_ok", "likes": 20000, "dur": 45, "pub": "2026-11-28T18:05:00+08:00", "want": True, "status": "passed"},
        {"id": "dy_zero_likes_vs_null", "likes": 0, "dur": 45, "pub": "2026-11-28T12:00:00+08:00", "want": False, "status": "filtered_out"},
        {"id": "dy_null_likes", "likes": None, "dur": 45, "pub": "2026-11-28T12:00:00+08:00", "want": False, "status": "filtered_out"},
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
        assert ev["filter_status"] == c["status"], c["id"]

    # Zero vs null distinction checks
    zero_dur_ev = evaluate_video_filters(
        {"source_platform": "douyin", "likes": 9000, "effective_likes": 9000, "duration_seconds": 0, "published_at": "2026-11-28T12:00:00+08:00"},
        platform="douyin",
        reference_time=ref,
    )
    assert zero_dur_ev["needs_verification"] is True
    assert "zero_duration_check" in zero_dur_ev["verification_reasons"]
    assert parse_duration_seconds(0) == 0 and parse_duration_seconds(None) is None

    # Bilibili simulated boundaries + rounded near threshold + old video exception still requiring base likes>=5000 & <300s
    bili_payload = _build_bilibili_simulated_v1(
        items=[
            {"video_id": "BV1A", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1A", "views": 50000, "likes": 5000, "duration_seconds": 299, "published_at": "2025-11-28T18:00:00+08:00"},
            {"video_id": "BV1B", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1B", "views": 49999, "likes": 9000, "duration_seconds": 100, "published_at": "2026-11-01T18:00:00+08:00"},
            {"video_id": "BV1C", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1C", "views": 80000, "likes": 4999, "duration_seconds": 100, "published_at": "2026-11-01T18:00:00+08:00"},
            {"video_id": "BV1D", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1D", "views": 1200000, "likes": 4999, "duration_seconds": 120, "published_at": "2024-06-01T12:00:00+08:00"},
            {"video_id": "BV1E", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1E", "views": 1000000, "likes": 6000, "duration_seconds": 250, "published_at": "2024-06-01T12:00:00+08:00"},
            {"video_id": "BV1F", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1F", "views": 70000, "likes": 50000, "duration_seconds": 250, "published_at": "2024-06-01T12:00:00+08:00"},
            {"video_id": "BV1G", "creator_id": "946974", "video_url": "https://www.bilibili.com/video/BV1G", "views": None, "views_display": "51.0K", "likes": None, "likes_display": "5.1K", "metrics_precision": "display_rounded", "duration_seconds": 100, "published_at": "2026-11-01T18:00:00+08:00"},
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
    assert results["BV1D"]["passed_filter"] is False  # old exception only exempts age; base likes>=5000 still required
    assert results["BV1E"]["passed_filter"] is True and results["BV1E"]["age_exception_applied"] is True
    assert results["BV1F"]["passed_filter"] is True and results["BV1F"]["age_exception_applied"] is True
    assert results["BV1G"]["filter_status"] == "needs_verification"
    assert "rounded_views_near_threshold" in results["BV1G"]["verification_reasons"]
    assert "rounded_likes_near_threshold" in results["BV1G"]["verification_reasons"]

    # BV/AV alias dedup across ingests + jobs
    store = CreatorReupStore(data_dir=str(tmp_path / "bili_alias"))
    bv_file = _build_bilibili_simulated_v1(request_id=None, crawl_id=None)
    res1 = store.ingest_creator_json(raw_payload=bv_file, file_name="bili_bv.json", reference_time=ref)
    assert res1["videos_inserted"] == 1

    av_file = _build_bilibili_simulated_v1(
        request_id=None,
        crawl_id=None,
        items=[
            {
                "video_id": "av170001",
                "video_id_secondary": "BV1xx411c7mD",
                "creator_id": "946974",
                "video_url": "https://www.bilibili.com/video/av170001",
                "content_type": "video",
                "title": "Simulated Bilibili Pass Video AV Alias",
                "views": 65000,
                "likes": 6200,
                "duration_seconds": 299,
                "published_at": "2026-11-20T12:00:00+08:00",
                "metrics_precision": "exact",
            }
        ],
    )
    res2 = store.ingest_creator_json(raw_payload=av_file, file_name="bili_av.json", reference_time=ref)
    assert res2["videos_inserted"] == 0
    assert res2["videos_deduped"] == 1
    assert len(store.query_videos(platform="bilibili")) == 1


def test_prompt_generator_requires_qualified_count_and_consistent_rules():
    for plat, url in (
        ("bilibili", "https://space.bilibili.com/946974"),
        ("douyin", "https://www.douyin.com/user/MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M"),
    ):
        spec = build_creator_crawl_prompt(profile_url=url, requested_count=15, drive_folder_id="fld_reup_123")
        pt = spec["prompt_text"]
        assert "Thu thập tối đa" not in pt
        assert "15 video ĐẠT CHUẨN" in pt
        assert "scanned_count" in pt
        assert "qualified_count" in pt
        assert "KHÔNG TỰ Ý HẠ NGƯỠNG LỌC" in pt
        assert "stop_reason" in pt
        assert "CHỈ miễn giới hạn tuổi bài" in pt
        assert spec["request_id"] in pt and spec["crawl_id"] in pt
        if plat == "bilibili":
            assert "views >= 50000" in pt and "likes >= 5000" in pt


def test_strict_request_correlation_and_discovery_sample_isolation(tmp_path):
    store = CreatorReupStore(data_dir=str(tmp_path / "corr_db"))
    req_a = build_creator_crawl_prompt(
        profile_url="https://www.douyin.com/user/MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
        requested_count=10,
    )
    store.create_crawl_request(req_a)

    req_b = build_creator_crawl_prompt(
        profile_url="https://www.douyin.com/user/MS4wLjABAAAA_OTHER_CREATOR_999",
        requested_count=10,
    )
    store.create_crawl_request(req_b)

    # 1. Wrong request_id NEVER falls back to req_a or req_b even with valid crawl_id
    with pytest.raises(CreatorRequestCorrelationError):
        store.ingest_creator_json(
            raw_payload=_build_douyin_v1_payload(request_id="creq_WRONG", crawl_id=req_a["crawl_id"]),
            file_name="bad_req.json",
        )

    # 2. Mismatched request_id vs crawl_id raises error
    with pytest.raises(CreatorRequestCorrelationError):
        store.ingest_creator_json(
            raw_payload=_build_douyin_v1_payload(request_id=req_a["request_id"], crawl_id=req_b["crawl_id"]),
            file_name="mismatch_crawl.json",
        )

    # 3. Mismatched creator_id against request raises error
    with pytest.raises(CreatorRequestCorrelationError):
        store.ingest_creator_json(
            raw_payload=_build_douyin_v1_payload(
                request_id=req_b["request_id"],
                crawl_id=req_b["crawl_id"],
                creator_id="MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
            ),
            file_name="mismatch_creator.json",
        )

    # 4. Discovery sample manual import NEVER auto-closes production request req_a
    raw_disc = FIXTURE_DOUYIN.read_text(encoding="utf-8")
    disc_res = store.ingest_creator_json(
        raw_payload=raw_disc,
        file_name="discovery.json",
        source_kind="manual",
        reference_time="2026-11-28T18:06:00+07:00",
    )
    assert disc_res["source_format"] == "discovery_sample"
    assert disc_res["acceptance_only"] and not store.query_videos()
    assert disc_res["request_id"] is None
    assert store.get_crawl_request(req_a["request_id"])["status"] == "awaiting_muse"
    assert store.get_crawl_request(req_a["request_id"])["matched_file_count"] == 0

    # 5. Drive ingest of discovery sample is rejected cleanly
    with pytest.raises(CreatorParseError):
        store.ingest_creator_json(raw_payload=raw_disc, file_name="disc_drive.json", source_kind="drive")


def _as_muse_v11(payload, *, crawl_id="ccr_20261128_1937_test", stop_reason="target_reached", target_count=1):
    payload["schema_version"] = "1.1"
    payload["crawl_id"] = crawl_id
    payload["crawl"].pop("crawl_id", None)
    payload["crawl"]["stop_reason"] = stop_reason
    payload["stats"] = {
        "listed_count": 8,
        "enriched_count": len(payload["items"]),
        "target_count": target_count,
        "qualified_count": len(payload["items"]),
        "notes_excluded": 0,
        "filter_excluded": 0,
        "filter_exclusion_reasons": {"over_duration": 0},
        "duplicates_in_registry": None,
        "stop_reason": stop_reason,
    }
    return payload


def test_muse_v11_metrics_and_sample_import_only(tmp_path):
    store = CreatorReupStore(data_dir=str(tmp_path))
    req = build_creator_crawl_prompt(profile_url=_build_douyin_v1_payload()["creator"]["profile_url"])
    store.create_crawl_request(req)
    payload = _as_muse_v11(_build_douyin_v1_payload(request_id="manual_sample_douyin"))
    payload["items"][0].update({
        "likes_approx": 77400, "comments": 0, "shares": None, "shares_display": "10.5K",
        "shares_approx": 10500, "favorites_approx": 12000, "rights_status": "approved_for_reup",
    })
    parsed = parse_creator_crawl_payload(payload)
    item = parsed["items"][0]
    assert item["likes"] is None and item["likes_approx"] == 77400
    assert item["views"] is None and item["comments"] == 0
    assert item["shares"] is None and item["shares_approx"] == 10500
    assert item["favorites"] is None and item["favorites_approx"] == 12000
    assert parsed["creators"][0]["followers"] is None
    for source_kind in ("manual", "drive"):
        result = store.ingest_creator_json(raw_payload=payload, request_id=req["request_id"], source_kind=source_kind)
        assert result["acceptance_only"] and result["passed_count"] == 1
        assert result["stats"]["notes_excluded"] == 0 and result["stats"]["duplicates_in_registry"] is None
        assert result["videos_inserted"] == 0
    assert not store.query_videos() and not store.list_raw_files()
    assert store.get_crawl_request(req["request_id"])["status"] == "awaiting_muse"


def test_muse_v11_request_correlation_stop_and_replay(tmp_path):
    store = CreatorReupStore(data_dir=str(tmp_path))
    profile = _build_bilibili_simulated_v1()["creator"]["profile_url"]
    req_a = store.create_crawl_request(build_creator_crawl_prompt(
        profile_url=profile, requested_count=2, drive_folder_id="creator_root",
        filter_overrides={"min_views": 55000},
    ))
    req_b = store.create_crawl_request(build_creator_crawl_prompt(profile_url=profile, requested_count=2))
    payload = _as_muse_v11(_build_bilibili_simulated_v1(request_id=req_a["request_id"]), target_count=2)
    payload["crawl"]["filters"] = {"min_views": 0}

    with pytest.raises(CreatorRequestCorrelationError):
        store.ingest_creator_json(raw_payload=payload, request_id=req_b["request_id"])
    with pytest.raises(CreatorRequestCorrelationError):
        store.ingest_creator_json(raw_payload=payload, source_kind="drive", drive_folder_id="other_root")
    result = store.ingest_creator_json(raw_payload=payload, filter_overrides={"min_views": 0})
    assert result["passed_count"] == 0
    assert result["completion_note"] == "target_not_met_locally"
    assert store.get_crawl_request(req_a["request_id"])["status"] == "awaiting_muse"
    assert store.query_videos()[0]["rights_status"] == "pending_review"

    payload["request_id"] = req_b["request_id"]
    payload["crawl"]["request_id"] = req_b["request_id"]
    with pytest.raises(CreatorRequestCorrelationError):
        store.ingest_creator_json(raw_payload=payload)
    payload = _as_muse_v11(
        _build_bilibili_simulated_v1(request_id=req_a["request_id"]),
        crawl_id="bili_blocked", stop_reason="rate_limited", target_count=2,
    )
    store.ingest_creator_json(raw_payload=payload)
    assert store.get_crawl_request(req_a["request_id"])["status"] == "awaiting_muse"
    assert store.get_crawl_request(req_a["request_id"])["last_result"]["stop_reason"] == "rate_limited"
    payload = _as_muse_v11(
        _build_bilibili_simulated_v1(request_id=req_a["request_id"]),
        crawl_id="bili_final", stop_reason="scan_limit_reached", target_count=2,
    )
    store.ingest_creator_json(raw_payload=payload, file_id="bili_drive_result", source_kind="drive", drive_folder_id="creator_root")
    first = store.get_crawl_request(req_a["request_id"])
    assert first["status"] == "synced" and first["last_result"]["stop_reason"] == "scan_limit_reached"
    replay = store.ingest_creator_json(raw_payload=payload, file_id="bili_drive_result", source_kind="drive", drive_folder_id="creator_root")
    assert replay["idempotent_skip"] and replay["stats"]["qualified_count"] == 1
    assert store.get_crawl_request(req_a["request_id"])["matched_file_count"] == first["matched_file_count"]
    assert store.get_crawl_request(req_b["request_id"])["status"] == "awaiting_muse"
    assert len(store.query_videos()) == 1


def test_atomic_dedup_concurrency_production_validation_and_lifecycle(tmp_path):
    store = CreatorReupStore(data_dir=str(tmp_path / "atomic_db"))
    payload = _build_douyin_v1_payload(
        items=[
            {
                "video_id": "7713142637636668682",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/7713142637636668682",
                "content_type": "video",
                "title": "Pass Ready",
                "likes_display": "77.4K",
                "metrics_precision": "display_rounded",
                "duration_seconds": 39,
                "published_at": "2026-11-28 15:53",
            },
            {
                "video_id": "7713142637636668701",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/7713142637636668701",
                "content_type": "video",
                "title": "Needs Verification",
                "likes_display": "5.1K",
                "metrics_precision": "display_rounded",
                "duration_seconds": 45,
                "published_at": "2026-11-28 15:53",
            },
            {
                "video_id": "7713173603738520859",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/7713173603738520859",
                "content_type": "video",
                "title": "Filtered Out Low Likes",
                "likes": 2328,
                "duration_seconds": 31,
                "published_at": "2026-11-28 17:53",
            },
        ]
    )
    store.ingest_creator_json(raw_payload=payload, file_name="v1.json", reference_time="2026-11-28T18:06:00+07:00")

    k_pass = "douyin:7713142637636668682"
    k_verif = "douyin:7713142637636668701"
    k_fail = "douyin:7713173603738520859"
    store.update_video_rights([k_pass, k_verif, k_fail], rights_status="approved_for_reup")

    # Even with approved rights, needs_verification and filtered_out are BLOCKED
    ready_v, blk_v = store.check_videos_ready_for_production([k_verif, k_fail])
    assert ready_v == []
    assert blk_v[0]["reason"] == "needs_verification"
    assert blk_v[1]["reason"] == "filter_not_passed:filtered_out"

    # Concurrency race test: two simultaneous claim_and_create_reup_jobs calls -> only ONE job created
    barrier = threading.Barrier(2)
    race_results = []

    def _racer():
        barrier.wait(timeout=5)
        race_results.append(
            store.claim_and_create_reup_jobs(
                [k_pass],
                config={"voice": "nam_hai", "custom_cookies": "RAW_COOKIE_SHOULD_NOT_PERSIST"},
            )
        )

    t1 = threading.Thread(target=_racer)
    t2 = threading.Thread(target=_racer)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    wins = [r for r in race_results if len(r[0]) == 1]
    losses = [r for r in race_results if len(r[1]) == 1]
    assert len(wins) == 1 and len(losses) == 1
    assert losses[0][1][0]["reason"] == "already_in_queue"

    # Security verification: cookie NEVER written to SQLite config_json
    with sqlite3.connect(store.db_path) as conn:
        raw_cfg = conn.execute("SELECT config_json FROM creator_reup_jobs").fetchone()[0]
    assert "RAW_COOKIE_SHOULD_NOT_PERSIST" not in raw_cfg
    assert "ephemeral_memory_only" in raw_cfg

    # Crash/restart recovery clears orphaned queued/running jobs cleanly
    recovered = store.recover_interrupted_jobs()
    assert recovered == 1
    assert store.query_videos(search="7713142637636668682")[0]["dedup_status"] == "new"

    # MP4 verification blocks missing/corrupt outputs and accepts real ffmpeg MP4
    jobs2, blk2 = store.claim_and_create_reup_jobs([k_pass], config={"voice": "nam_hai"})
    assert len(jobs2) == 1 and blk2 == []
    jid = jobs2[0]["job_id"]
    ok_start, _, _ = store.reverify_and_start_job(jid)
    assert ok_start is True

    bad_corrupt = tmp_path / "corrupt.mp4"
    bad_corrupt.write_bytes(b"not_an_mp4_at_all")
    assert verify_local_mp4_output(str(bad_corrupt))[0] is False
    res_corrupt = store.update_reup_job_status(jid, status="completed", output_path=str(bad_corrupt))
    assert res_corrupt["status"] == "failed"
    assert store.query_videos(search="7713142637636668682")[0]["dedup_status"] == "new"

    # Now re-claim and complete with genuine FFmpeg MP4
    jobs3, _ = store.claim_and_create_reup_jobs([k_pass], config={"voice": "nam_hai"})
    jid3 = jobs3[0]["job_id"]
    real_mp4 = _make_tiny_valid_mp4(tmp_path / "valid_out.mp4")
    res_ok = store.update_reup_job_status(jid3, status="completed", output_path=str(real_mp4))
    assert res_ok["status"] == "completed"
    assert store.query_videos(search="7713142637636668682")[0]["dedup_status"] == "duplicate_processed"


def test_drive_sync_blocker_scope_and_market_isolation(tmp_path):
    store = CreatorReupStore(data_dir=str(tmp_path / "drive_scope_db"))
    req_spec = build_creator_crawl_prompt(
        profile_url="https://www.douyin.com/user/MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
        requested_count=10,
        drive_folder_id="fld_external_creator_only",
    )
    store.create_crawl_request(req_spec)

    raw_disc = FIXTURE_DOUYIN.read_text(encoding="utf-8")

    class FakeScopedClient:
        def reload_config(self):
            pass

        def is_configured(self):
            return True

        def list_market_files(self, cursor=None, limit=100, page_token=None):
            return {
                "ok": True,
                "scope_folder_id": "1qJK7InQpioHTdrWAyMvoVrCuZYf2D-2s",
                "has_more": False,
                "files": [
                    {"file_id": "f_mkt", "name": "threads_trend_20261128.json", "modified_time": "2026-11-28T10:00:00Z"},
                    {"file_id": "f_disc", "name": "creator_crawl_discovery.json", "modified_time": "2026-11-28T10:02:00Z"},
                ],
            }

        def download_market_file(self, file_id):
            if file_id == "f_disc":
                return raw_disc.encode("utf-8"), {"modified_time": "2026-11-28T10:02:00Z"}
            raise AssertionError("Should skip market prefix without downloading")

    sync_svc = CreatorDriveSyncService(store=store, client=FakeScopedClient())
    res = sync_svc.sync_now(trigger_source="test")
    assert res["status"] == "completed"
    assert res["scope_folder_id"] == "1qJK7InQpioHTdrWAyMvoVrCuZYf2D-2s"
    assert any(req_spec["request_id"] in x for x in res["scoped_mismatch_requests"])
    # Discovery file on Drive is NEVER ingested as strict v1
    assert res["files_ingested"] == 0
    assert CREATOR_DRIVE_SYNC_BLOCKER in res["blocker"]


def _set_tracker_idle():
    app_ui.tracker._cancel_event.clear()
    app_ui.tracker.pause_for_review("idle_for_test")


def test_flask_security_render_lock_recheck_cancellation_and_mp4_gate(tmp_path):
    os.environ["APP_VIDEO_DISABLE_BG_WORKERS"] = "1"
    app_ui.reset_creator_reup_runtime_for_tests(data_dir=str(tmp_path / "creator_flask_v2"))
    _set_tracker_idle()
    client = app_ui.app.test_client()

    # Default anti_reup / mirror_video must be False in UI template
    html = client.get("/").get_data(as_text=True)
    assert "antiReup: false" in html
    assert "mirrorVideo: false" in html

    # State surfaces Drive blocker clearly
    st_json = client.get("/api/creator_reup/state").get_json()
    assert st_json["dedup_scope"] == "local_sqlite_only"
    assert "01_Market_Data" in st_json["drive_sync_blocker"]

    # Import two valid passed Douyin videos
    payload = _build_douyin_v1_payload(
        items=[
            {
                "video_id": "7713142637636668682",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/7713142637636668682",
                "content_type": "video",
                "title": "Video 1",
                "likes_display": "77.4K",
                "metrics_precision": "display_rounded",
                "duration_seconds": 39,
                "published_at": "2026-11-28 15:53",
            },
            {
                "video_id": "7713142637636668683",
                "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                "video_url": "https://www.douyin.com/video/7713142637636668683",
                "content_type": "video",
                "title": "Video 2",
                "likes": 12000,
                "duration_seconds": 40,
                "published_at": "2026-11-28 16:00",
            },
        ]
    )
    imp = client.post(
        "/api/creator_reup/import_json",
        json={"json_text": json.dumps(payload), "file_name": "two_videos.json", "reference_time": "2026-11-28T18:06:00+07:00"},
    )
    assert imp.status_code == 200
    k1 = "douyin:7713142637636668682"
    k2 = "douyin:7713142637636668683"

    # 1. Blocked before rights approval
    assert client.post("/api/creator_reup/run_selected", json={"video_keys": [k1], "sync_execution": True}).status_code == 400

    # Approve both k1 and k2
    assert client.post("/api/creator_reup/videos/rights", json={"video_keys": [k1, k2], "rights_status": "approved_for_reup"}).status_code == 200

    # 2. Render lock cannot be bypassed even with sync_execution=True
    app_ui.tracker.reset()
    app_ui.tracker.start_step("script", "Active render lock test", percent=10)
    locked_resp = client.post(
        "/api/creator_reup/run_selected",
        json={"video_keys": [k1], "sync_execution": True},
    )
    assert locked_resp.status_code == 409
    _set_tracker_idle()

    # 3. Mid-batch rights revocation on k2 + security non-persistence + valid MP4 on k1
    real_out = _make_tiny_valid_mp4(tmp_path / "k1_out.mp4")
    received_auth = []

    def _mock_run_reup(**kwargs):
        received_auth.append((kwargs.get("browser_cookies"), kwargs.get("custom_cookies"), kwargs.get("anti_reup")))
        # While k1 executes, revoke rights on k2!
        app_ui.get_creator_reup_runtime()["store"].update_video_rights([k2], rights_status="rejected")
        return {
            "video_filename": "k1_out.mp4",
            "video_path": str(real_out),
            "video_title": "K1 OK",
            "caption": "Cap",
            "duration_sec": 39.0,
        }

    fake_reup_mod = types.SimpleNamespace(run_reup_pipeline=mock.MagicMock(side_effect=_mock_run_reup))
    real_load_mod = app_ui.load_mod

    with mock.patch.object(app_ui, "load_mod", side_effect=lambda n, f: fake_reup_mod if n == "reup_pipeline" else real_load_mod(n, f)):
        run_resp = client.post(
            "/api/creator_reup/run_selected",
            json={
                "video_keys": [k1, k2],
                "browser_cookies": "manual",
                "custom_cookies": "ttwid=MEMORY_SECRET_XYZ",
                "sync_execution": True,
            },
        )
        assert run_resp.status_code == 200
        run_json = run_resp.get_json()
        # k1 succeeded, k2 was skipped by per-video pre-run re-check cleanly!
        assert run_json["execution"]["succeeded"] == 1
        assert len(run_json["execution"]["failures"]) == 1
        assert fake_reup_mod.run_reup_pipeline.call_count == 1
        assert received_auth[0] == ("manual", "ttwid=MEMORY_SECRET_XYZ", False)
        assert "MEMORY_SECRET_XYZ" not in json.dumps(run_json)

        # State API & DB never leak custom_cookies
        state_after = client.get("/api/creator_reup/state").get_json()
        assert "MEMORY_SECRET_XYZ" not in json.dumps(state_after)

        # 4. Mid-batch cancellation on remaining queued items cleans up ALL jobs completely
        _set_tracker_idle()
        k3 = "douyin:7713142637636668684"
        payload_extra = _build_douyin_v1_payload(
            items=[
                {
                    "video_id": "7713142637636668684",
                    "creator_id": "MS4wLjABAAAAgq8cb7cn9ByhZbmx-XQDdRTvFzmJeBBXOUO4QflP96M",
                    "video_url": "https://www.douyin.com/video/7713142637636668684",
                    "content_type": "video",
                    "title": "Video 3",
                    "likes": 15000,
                    "duration_seconds": 40,
                    "published_at": "2026-11-28 16:05",
                }
            ]
        )
        assert client.post(
            "/api/creator_reup/import_json",
            json={"json_text": json.dumps(payload_extra), "file_name": "v3.json", "reference_time": "2026-11-28T18:06:00+07:00"},
        ).status_code == 200
        assert client.post("/api/creator_reup/videos/rights", json={"video_keys": [k2, k3], "rights_status": "approved_for_reup"}).status_code == 200

        def _cancelling_reup(**kwargs):
            raise app_ui.tracker.PipelineCancelledError("User cancelled")

        fake_cancel_mod = types.SimpleNamespace(run_reup_pipeline=mock.MagicMock(side_effect=_cancelling_reup))
        with mock.patch.object(app_ui, "load_mod", side_effect=lambda n, f: fake_cancel_mod if n == "reup_pipeline" else real_load_mod(n, f)):
            c_resp = client.post("/api/creator_reup/run_selected", json={"video_keys": [k2, k3], "sync_execution": True})
            assert c_resp.get_json()["execution"]["cancelled"] is True
        # BOTH interrupted k2 AND unstarted k3 must be cancelled, never wedged as duplicate_in_queue
        store_now = app_ui.get_creator_reup_runtime()["store"]
        assert store_now.query_videos(search="7713142637636668683")[0]["dedup_status"] == "new"
        assert store_now.query_videos(search="7713142637636668684")[0]["dedup_status"] == "new"
        assert all(j["status"] not in ("queued", "running") for j in store_now.list_reup_jobs())

    # Market DB remains completely empty/isolated
    mkt_rt = app_ui.reset_market_runtime_for_tests(data_dir=str(tmp_path / "market_isolation"))
    assert mkt_rt["store"].db_path != app_ui.get_creator_reup_runtime()["store"].db_path
    assert client.get("/api/market/items").get_json()["total"] == 0
    _set_tracker_idle()
