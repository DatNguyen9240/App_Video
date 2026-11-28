"""Muse Schema v1.0/v1.1 parser and Douyin discovery sample adapter."""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    CREATOR_SCHEMA_VERSION,
    CREATOR_STOP_REASONS,
    SUPPORTED_CREATOR_SCHEMA_VERSIONS,
    SUPPORTED_PLATFORMS,
    canonicalize_video_url,
    coerce_exact_int,
    detect_platform_from_url,
    extract_video_id_from_url,
    normalize_bilibili_secondary_id,
    normalize_platform,
    parse_display_count,
    parse_duration_seconds,
    parse_profile_identifier,
    parse_published_at,
    utc_now_iso,
)


class CreatorParseError(ValueError):
    """Raised when an input JSON file violates Creator Crawler structural rules."""


def is_acceptance_sample(bundle: Dict[str, Any]) -> bool:
    """Muse marks the acceptance exports with the reserved manual_sample_ request ID."""
    return str(bundle.get("request_id") or "").strip().lower().startswith("manual_sample_")


def normalize_item_metrics(raw_item: Dict[str, Any]) -> Dict[str, Any]:
    """Keep exact counts, display labels, and estimates independently."""
    metrics = {}
    for field in ("views", "likes", "comments", "shares", "favorites"):
        exact = coerce_exact_int(raw_item.get(field))
        display = str(raw_item.get(f"{field}_display") or "").strip() or None
        estimate = parse_display_count(display)
        if estimate is None:
            estimate = coerce_exact_int(raw_item.get(f"{field}_approx"))
        metrics.update({field: exact, f"{field}_display": display, f"{field}_approx": estimate})
        if field in ("views", "likes"):
            metrics[f"effective_{field}"] = exact if exact is not None else estimate
    return metrics


def _is_note_entry(raw_url: str, content_type: Any, url_kind: Optional[str]) -> bool:
    ct = str(content_type or "").strip().lower()
    if url_kind == "note" or "/note/" in str(raw_url or "").lower():
        return True
    if ct in ("note", "note_image_text", "image_text", "article", "pic", "image"):
        return True
    return False


def _validate_item_identity(
    *,
    platform: str,
    creator_id: str,
    raw_item: Dict[str, Any],
    strict_identity: bool = False,
) -> Tuple[bool, str, str, str]:
    """Validate URL/platform/video_id/creator_id consistency for one item.

    Returns `(is_valid_video, exclusion_reason, canonical_url, normalized_video_id)`.
    """
    raw_url = str(raw_item.get("video_url") or raw_item.get("url") or "").strip()
    raw_video_id = str(raw_item.get("video_id") or "").strip()
    item_creator_id = str(raw_item.get("creator_id") or "").strip()
    content_type = raw_item.get("content_type") if "content_type" in raw_item else raw_item.get("type")

    url_platform = detect_platform_from_url(raw_url)
    if strict_identity and raw_url and not url_platform:
        return False, "invalid_canonical_video_url", raw_url, raw_video_id
    url_kind, extracted_vid = extract_video_id_from_url(raw_url, platform)
    if _is_note_entry(raw_url, content_type, url_kind):
        return False, "note_excluded", raw_url, raw_video_id

    if not raw_url:
        return False, "missing_video_url", raw_url, raw_video_id

    if url_platform and url_platform != platform:
        return False, f"url_platform_mismatch:{url_platform}!={platform}", raw_url, raw_video_id

    if item_creator_id and creator_id and item_creator_id != creator_id:
        return False, "creator_id_mismatch", raw_url, raw_video_id
    if strict_identity and (url_kind != "video" or not extracted_vid):
        return False, "invalid_canonical_video_url", raw_url, raw_video_id
    if strict_identity and (not item_creator_id or not raw_video_id or content_type != "video"):
        return False, "missing_video_identity", raw_url, raw_video_id

    if not raw_video_id and not extracted_vid:
        return False, "missing_video_id", raw_url, ""

    if raw_video_id and extracted_vid and raw_video_id.lower() != extracted_vid.lower():
        secondary = str(raw_item.get("video_id_secondary") or "").strip()
        if not (secondary and extracted_vid.lower().lstrip("av") == secondary.lower()):
            return False, "video_id_url_mismatch", raw_url, raw_video_id

    final_vid = extracted_vid or raw_video_id
    if platform == "douyin" and not re.fullmatch(r"\d+", final_vid):
        return False, "invalid_douyin_video_id", raw_url, final_vid
    if platform == "bilibili" and not re.fullmatch(r"(?:BV[0-9A-Za-z]+|av\d+)", final_vid, flags=re.IGNORECASE):
        return False, "invalid_bilibili_video_id", raw_url, final_vid

    canonical = canonicalize_video_url(raw_url, platform=platform, video_id=final_vid)
    return True, "", canonical, final_vid


def _build_normalized_item(
    *,
    platform: str,
    creator_meta: Dict[str, Any],
    raw_item: Dict[str, Any],
    request_id: Optional[str],
    crawl_id: Optional[str],
    crawled_at_default: Optional[str],
    strict_identity: bool = False,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Normalize a single item, returning `(valid_video_or_none, excluded_entry_or_none)`."""
    creator_id = str(creator_meta.get("creator_id") or "").strip()
    is_valid, reason, canonical_url, final_vid = _validate_item_identity(
        platform=platform,
        creator_id=creator_id,
        raw_item=raw_item,
        strict_identity=strict_identity,
    )
    if not is_valid:
        return None, {
            "video_id": str(raw_item.get("video_id") or "").strip() or None,
            "video_url": str(raw_item.get("video_url") or raw_item.get("url") or "").strip() or None,
            "creator_id": creator_id,
            "reason": reason,
        }

    raw_secondary = raw_item.get("video_id_secondary")
    if platform == "bilibili":
        norm_sec = normalize_bilibili_secondary_id(raw_secondary)
        # Also retain original raw primary/secondary relationship if URL carried BV and JSON gave aid (or vice versa)
        raw_vid_str = normalize_bilibili_secondary_id(raw_item.get("video_id"))
        if not norm_sec and raw_vid_str and raw_vid_str.lower() != final_vid.lower():
            norm_sec = raw_vid_str
        secondary_video_key = f"bilibili:{norm_sec}" if (norm_sec and norm_sec.lower() != final_vid.lower()) else None
    else:
        norm_sec = str(raw_secondary or "").strip() or None
        secondary_video_key = None

    metrics = normalize_item_metrics(raw_item)

    raw_precision = str(raw_item.get("metrics_precision") or "").strip().lower()
    if raw_precision in ("exact", "display_rounded"):
        metrics_precision = raw_precision
    elif any(metrics[field] is None and metrics[f"{field}_approx"] is not None for field in ("likes", "views")):
        metrics_precision = "display_rounded"
    else:
        metrics_precision = "exact"

    dur_raw = raw_item.get("duration_seconds")
    if dur_raw is None and "duration_display" in raw_item:
        dur_raw = raw_item.get("duration_display")
    duration_seconds = parse_duration_seconds(dur_raw)

    published_at, published_at_display = parse_published_at(
        raw_item.get("published_at"),
        platform=platform,
        raw_display=raw_item.get("published_at_display"),
    )

    crawled_at_val, _ = parse_published_at(
        raw_item.get("crawled_at") or crawled_at_default,
        platform=platform,
    )

    normalized = {
        "video_key": f"{platform}:{final_vid}",
        "source_platform": platform,
        "source_video_id": final_vid,
        "video_id_secondary": norm_sec,
        "secondary_video_key": secondary_video_key,
        "creator_id": creator_id,
        "creator_handle": creator_meta.get("creator_handle"),
        "creator_name": creator_meta.get("creator_name"),
        "profile_url": creator_meta.get("profile_url"),
        "request_id": request_id,
        "crawl_id": crawl_id,
        "video_url": canonical_url,
        "raw_video_url": str(raw_item.get("video_url") or raw_item.get("url") or canonical_url).strip(),
        "content_type": "video",
        "title": (str(raw_item.get("title") or "").strip() or None),
        "thumbnail": (str(raw_item.get("thumbnail") or "").strip() or None),
        **metrics,
        "metrics_precision": metrics_precision,
        "duration_seconds": duration_seconds,
        "published_at": published_at,
        "published_at_display": published_at_display,
        "metrics_source": str(raw_item.get("metrics_source") or "").strip() or None,
        "registry_status": str(raw_item.get("registry_status") or "unknown").strip().lower(),
        "crawled_at": crawled_at_val or utc_now_iso(),
        "raw_item": raw_item,
    }
    return normalized, None


def _normalize_creator_header(platform: str, creator: Dict[str, Any], *, strict_identity: bool = False) -> Dict[str, Any]:
    if not isinstance(creator, dict):
        raise CreatorParseError("Thiếu object `creator`.")
    profile_url = str(creator.get("profile_url") or "").strip()
    creator_id = str(creator.get("creator_id") or creator.get("sec_uid") or "").strip()
    if strict_identity and detect_platform_from_url(profile_url) != platform:
        raise CreatorParseError("Schema 1.1 cần profile_url chuẩn khớp nền tảng và creator_id.")
    if not profile_url and not creator_id:
        raise CreatorParseError("Thiếu cả `creator.creator_id` lẫn `creator.profile_url`.")

    if profile_url:
        prof_parsed = parse_profile_identifier(profile_url, platform)
        if strict_identity and not prof_parsed["creator_id"]:
            raise CreatorParseError("Schema 1.1 cần ID của creator trong profile_url.")
        if prof_parsed["platform"] and prof_parsed["platform"] != platform:
            raise CreatorParseError(
                f"Creator profile_url ({prof_parsed['platform']}) không khớp platform ({platform})."
            )
        if creator_id and prof_parsed["creator_id"] and creator_id != prof_parsed["creator_id"]:
            raise CreatorParseError(
                f"creator_id ({creator_id}) không khớp ID trong profile_url ({prof_parsed['creator_id']})."
            )
        creator_id = creator_id or str(prof_parsed["creator_id"] or "").strip()

    if not creator_id:
        raise CreatorParseError("Không xác định được `creator_id` (`sec_uid` hoặc Bilibili `mid`).")

    followers_display = str(creator.get("followers_display")).strip() if creator.get("followers_display") not in (None, "") else None
    followers = coerce_exact_int(creator.get("followers"))
    return {
        "profile_key": f"{platform}:{creator_id}",
        "platform": platform,
        "creator_id": creator_id,
        "creator_handle": (str(creator.get("creator_handle") or creator.get("douyin_id") or "").strip() or None),
        "creator_name": (str(creator.get("creator_name") or creator.get("name") or "").strip() or creator_id),
        "profile_url": profile_url,
        "followers_display": followers_display,
        "followers": followers,
    }


def _parse_v1_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    schema_ver = str(payload.get("schema_version") or "").strip()
    if schema_ver not in SUPPORTED_CREATOR_SCHEMA_VERSIONS:
        raise CreatorParseError(f"schema_version không hỗ trợ: {schema_ver!r} (cần '1.0' hoặc '1.1').")

    platform = normalize_platform(payload.get("platform"))
    if platform not in SUPPORTED_PLATFORMS:
        raise CreatorParseError(f"platform không hỗ trợ: {payload.get('platform')!r}.")

    strict_identity = schema_ver == CREATOR_SCHEMA_VERSION
    creator_meta = _normalize_creator_header(platform, payload.get("creator") or {}, strict_identity=strict_identity)
    crawl_meta = payload.get("crawl") if isinstance(payload.get("crawl"), dict) else {}
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise CreatorParseError("Thiếu mảng `items` trong Creator Crawl JSON v1.")

    request_id = str(payload.get("request_id") or crawl_meta.get("request_id") or "").strip() or None
    crawl_id = str(payload.get("crawl_id") or crawl_meta.get("crawl_id") or "").strip() or None
    for key in ("request_id", "crawl_id"):
        if payload.get(key) and crawl_meta.get(key) and str(payload[key]).strip() != str(crawl_meta[key]).strip():
            raise CreatorParseError(f"{key} trong root và crawl không khớp.")
    if schema_ver == CREATOR_SCHEMA_VERSION and not all(
        isinstance(payload.get(key), str) and payload[key].strip() for key in ("request_id", "crawl_id")
    ):
        raise CreatorParseError("Schema 1.1 cần request_id và crawl_id ở root.")
    drive_folder_id = str(
        payload.get("drive_folder_id") or crawl_meta.get("drive_folder_id") or ""
    ).strip() or None
    crawled_at_default = crawl_meta.get("crawled_at") or payload.get("crawled_at")

    valid_items: List[Dict[str, Any]] = []
    excluded_items: List[Dict[str, Any]] = []
    notes_excluded = 0
    invalid_excluded = 0

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            invalid_excluded += 1
            continue
        if strict_identity and platform == "douyin" and crawl_meta.get("method") == "douyin_web_ui":
            if any(raw_item.get(key) is not None for key in ("views", "views_display", "views_approx")):
                invalid_excluded += 1
                excluded_items.append({"video_id": raw_item.get("video_id"), "reason": "douyin_web_views_must_be_null"})
                continue
        norm, exc = _build_normalized_item(
            platform=platform,
            creator_meta=creator_meta,
            raw_item=raw_item,
            request_id=request_id,
            crawl_id=crawl_id,
            crawled_at_default=crawled_at_default,
            strict_identity=strict_identity,
        )
        if norm is not None:
            valid_items.append(norm)
        elif exc is not None:
            excluded_items.append(exc)
            if exc["reason"] == "note_excluded":
                notes_excluded += 1
            else:
                invalid_excluded += 1

    raw_stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    crawl_stop = crawl_meta.get("stop_reason")
    stats_stop = raw_stats.get("stop_reason")
    stop_reason = str(crawl_stop or stats_stop or payload.get("stop_reason") or "").strip() or None
    if schema_ver == CREATOR_SCHEMA_VERSION:
        if stop_reason not in CREATOR_STOP_REASONS:
            raise CreatorParseError(f"stop_reason không hợp lệ: {stop_reason!r}.")
        if crawl_stop and stats_stop and crawl_stop != stats_stop:
            raise CreatorParseError("stop_reason trong crawl và stats không khớp.")
        if not parse_published_at(crawled_at_default, platform)[0]:
            raise CreatorParseError("Schema 1.1 cần crawl.crawled_at hợp lệ.")
        if not coerce_exact_int(raw_stats.get("target_count")):
            raise CreatorParseError("Schema 1.1 cần stats.target_count là số dương.")

    def count(*keys: str, default: Optional[int] = None) -> Optional[int]:
        for key in keys:
            value = coerce_exact_int(raw_stats.get(key))
            if value is not None:
                return value
        return default

    listed_count = count("listed_count", "scanned_count", "grid_items_seen", default=len(raw_items))
    return {
        "schema_version": schema_ver,
        "source_format": "muse_v1",
        "platform": platform,
        "request_id": request_id,
        "crawl_id": crawl_id,
        "drive_folder_id": drive_folder_id,
        "stop_reason": stop_reason,
        "crawl": crawl_meta,
        "reference_time": (crawled_at_default if strict_identity else crawl_meta.get("crawled_at")) or payload.get("measured_at"),
        "filters_hint": crawl_meta.get("filters") if isinstance(crawl_meta.get("filters"), dict) else {},
        "creators": [creator_meta],
        "items": valid_items,
        "excluded_items": excluded_items,
        "stats": {
            "total_input_items": len(raw_items),
            "scanned_count": listed_count,
            "listed_count": listed_count,
            "enriched_count": count("enriched_count"),
            "qualified_count": count("qualified_count", "videos_kept", default=len(valid_items)),
            "qualified_beyond_target": count("qualified_beyond_target"),
            "target_count": count("target_count"),
            "filter_excluded": count("filter_excluded", "below_filter_excluded"),
            "filter_exclusion_reasons": {
                key: coerce_exact_int(value) for key, value in
                (raw_stats["filter_exclusion_reasons"].items() if isinstance(raw_stats.get("filter_exclusion_reasons"), dict) else ())
            },
            "duplicates_in_registry": count("duplicates_in_registry"),
            "valid_videos_count": len(valid_items),
            "notes_excluded": max(count("notes_excluded", default=0), notes_excluded),
            "invalid_excluded": invalid_excluded,
            "grid_items_diagnostic": count("grid_items_seen", default=0),
            "stop_reason": stop_reason,
        },
    }


def _parse_discovery_sample_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt multi-creator discovery sample JSON (like `douyin_discovery_sample_20261128.json`).

    Explicitly ignores `grid_sample[]` for item matching because grid entries lack `video_id`.
    """
    platform = normalize_platform(payload.get("platform"))
    if platform not in SUPPORTED_PLATFORMS:
        raise CreatorParseError(f"Discovery sample có platform không hỗ trợ: {payload.get('platform')!r}.")

    creators_raw = payload.get("creators")
    if not isinstance(creators_raw, list) or not creators_raw:
        raise CreatorParseError("Discovery payload thiếu mảng `creators`.")

    measured_at = payload.get("measured_at")
    request_id = str(payload.get("request_id") or "").strip() or None
    crawl_id = str(payload.get("crawl_id") or "").strip() or None
    drive_folder_id = str(payload.get("drive_folder_id") or "").strip() or None

    creators: List[Dict[str, Any]] = []
    valid_items: List[Dict[str, Any]] = []
    excluded_items: List[Dict[str, Any]] = []
    notes_excluded = 0
    invalid_excluded = 0
    grid_diag_total = 0
    total_enriched = 0

    for raw_c in creators_raw:
        if not isinstance(raw_c, dict):
            continue
        creator_meta = _normalize_creator_header(platform, raw_c)
        creators.append(creator_meta)
        grid_diag_total += len(raw_c.get("grid_sample") or [])

        enriched = raw_c.get("enriched_items") or []
        for raw_item in enriched:
            if not isinstance(raw_item, dict):
                invalid_excluded += 1
                continue
            total_enriched += 1
            norm, exc = _build_normalized_item(
                platform=platform,
                creator_meta=creator_meta,
                raw_item=raw_item,
                request_id=request_id,
                crawl_id=crawl_id,
                crawled_at_default=measured_at,
            )
            if norm is not None:
                valid_items.append(norm)
            elif exc is not None:
                excluded_items.append(exc)
                if exc["reason"] == "note_excluded":
                    notes_excluded += 1
                else:
                    invalid_excluded += 1

    return {
        "schema_version": "discovery_adapter_1.0",
        "source_format": "discovery_sample",
        "platform": platform,
        "request_id": request_id,
        "crawl_id": crawl_id,
        "drive_folder_id": drive_folder_id,
        "reference_time": measured_at,
        "filters_hint": {},
        "creators": creators,
        "items": valid_items,
        "excluded_items": excluded_items,
        "stats": {
            "total_input_items": total_enriched,
            "valid_videos_count": len(valid_items),
            "notes_excluded": notes_excluded,
            "invalid_excluded": invalid_excluded,
            "grid_items_diagnostic": grid_diag_total,
        },
    }


def parse_creator_crawl_payload(raw_payload: Any) -> Dict[str, Any]:
    """Parse Muse Creator Crawler v1 JSON or adapt a discovery sample payload."""
    if isinstance(raw_payload, (bytes, bytearray)):
        raw_payload = raw_payload.decode("utf-8-sig")
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise CreatorParseError(f"JSON không hợp lệ: {exc}") from exc
    else:
        payload = raw_payload

    if not isinstance(payload, dict):
        raise CreatorParseError("Payload Creator Crawl phải là JSON object.")

    if "schema_version" in payload:
        return _parse_v1_payload(payload)
    if isinstance(payload.get("creators"), list):
        return _parse_discovery_sample_payload(payload)
    raise CreatorParseError("Không nhận diện được JSON Creator Crawler (cần schema_version 1.0/1.1 hoặc mẫu creators[]).")
