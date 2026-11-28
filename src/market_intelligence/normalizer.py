"""
Multi-version Market Intelligence JSON Normalizer (Data Contract V1 / V2 / V3.1).

Supports Threads (including V3.1 `threads_trend_*.json`), TikTok, and Shopee payloads
across schema variants (POC 001, POC 002, Pilot 001, E2E V1/corrected_v2, and V3.1).
Strictly preserves missing fields as None (never fabricates absent data, never turns null into 0).
Distinguishes crawl payloads from strategy / audit / data_contract / research docs,
and ignores Archive/ folders cleanly.
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


SUPPORTED_PLATFORMS = {"threads", "tiktok", "shopee"}
NON_CRAWL_ROLES = {
    "data_contract",
    "crawl_strategy",
    "quality_audit",
    "market_report",
    "research_doc",
    "capability_test",
    "identity_index",
}


def is_archive_path(folder_path: Optional[str]) -> bool:
    """Return True if folder_path points anywhere inside an Archive/ directory."""
    if not folder_path:
        return False
    parts = [p.strip().lower() for p in re.split(r"[\\/]+", str(folder_path)) if p.strip()]
    return "archive" in parts


def _clean_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        return s if s else None
    return str(val).strip() or None


def _clean_int(val: Any) -> Optional[int]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        s = val.strip()
        # Never corrupt rounded display strings like "513K" or "1.2M" into "513" or "12"
        if re.search(r"[a-zA-Z]", s):
            return None
        digits = re.sub(r"[^\d-]", "", s)
        if digits and digits != "-":
            try:
                return int(digits)
            except ValueError:
                return None
    return None


def _clean_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except ValueError:
            return None
    return None


def _platform_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return None
    if "threads.net" in host or "threads.com" in host:
        return "threads"
    if "tiktok.com" in host or "vm.tiktok" in host:
        return "tiktok"
    if "shopee." in host or "shp.ee" in host:
        return "shopee"
    return None


def _normalize_platform_token(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    lower = val.lower()
    for p in ("threads", "tiktok", "shopee"):
        if re.search(rf"(?:^|[^a-z]){p}(?:$|[^a-z])", lower):
            return p
    return None


def classify_json_file(
    raw_obj: Any,
    file_name: Optional[str] = None,
    folder_path: Optional[str] = None,
) -> str:
    """
    Classify JSON file role strictly so data_contract, strategy, audit, research/test docs,
    and report files are never mixed into crawl_data records.
    """
    fname = (file_name or "").lower()
    if isinstance(raw_obj, dict):
        if "post_identity_index" in fname or (
            isinstance(raw_obj.get("entries"), list)
            and ("completeness_audit" in raw_obj or "index" in raw_obj or "primary_key" in raw_obj)
        ):
            return "identity_index"
        if "contract_amendment" in fname or (
            "document" in raw_obj and "proposed_version" in raw_obj
        ):
            return "data_contract"
        if "data_contract" in fname or (
            "contract" in raw_obj
            and ("global_rules" in raw_obj or "update_rules_for_app_video" in raw_obj or "threads_trend_record" in raw_obj)
        ):
            return "data_contract"
        if "strategy" in fname or (
            "strategy" in raw_obj
            and (
                "schema_changes_additive_only" in raw_obj
                or "buckets" in raw_obj
                or "target_metrics" in raw_obj
                or "trend_tiers" in raw_obj
            )
        ):
            return "crawl_strategy"
        if "audit" in fname or ("audit" in raw_obj and "verdict" in raw_obj):
            return "quality_audit"
        if "capability_test" in fname or "engagement_verification_map" in raw_obj:
            return "capability_test"
        if "discovery_sources" in fname or (
            isinstance(raw_obj.get("sources"), list)
            and "sections" not in raw_obj
            and "platform_results" not in raw_obj
            and "telegram_summary" not in raw_obj
        ):
            return "research_doc"

        has_item_list = any(
            isinstance(raw_obj.get(k), list)
            for k in ("posts", "items", "records", "products", "videos", "threads")
        )
        if fname.startswith(("market_report", "trend_report")) and not has_item_list:
            return "market_report"
        if not has_item_list and any(
            k in raw_obj
            for k in (
                "sections",
                "report",
                "analysis",
                "market_report",
                "platform_results",
                "opportunity_ranking",
                "telegram_summary",
            )
        ):
            return "market_report"
    return "crawl_data"


def is_corrected_payload(raw_obj: Any, file_name: Optional[str] = None) -> bool:
    """Return True if payload or filename represents a corrected_v2/corrected revision."""
    fname = (file_name or "").lower()
    if "_corrected" in fname:
        return True
    if isinstance(raw_obj, dict):
        fv = str(raw_obj.get("file_version") or "").lower()
        if "corrected" in fv or raw_obj.get("corrects_file") or raw_obj.get("corrects_report"):
            return True
    return False


def detect_platform(
    item: Dict[str, Any],
    envelope: Optional[Dict[str, Any]] = None,
    file_name: Optional[str] = None,
    platform_hint: Optional[str] = None,
) -> str:
    """Detect source platform accurately without misclassifying handles like @shopee_vn on Threads."""
    envelope = envelope or {}

    for candidate in (
        item.get("platform"),
        envelope.get("platform"),
        item.get("source_platform"),
        envelope.get("source_platform"),
    ):
        found = _normalize_platform_token(_clean_str(candidate))
        if found:
            return found

    for url_key in ("post_url", "url", "item_url", "product_url", "video_url", "link"):
        p_url = _platform_from_url(_clean_str(item.get(url_key)))
        if p_url:
            return p_url

    for candidate in (
        item.get("source_query"),
        item.get("metrics_source"),
        item.get("source"),
        envelope.get("source"),
        envelope.get("crawl"),
        envelope.get("poc"),
    ):
        found = _normalize_platform_token(_clean_str(candidate))
        if found:
            return found

    for candidate in (platform_hint, file_name):
        found = _normalize_platform_token(_clean_str(candidate))
        if found:
            return found

    return "unknown"


def detect_schema_version(raw_obj: Any, file_role: str = "crawl_data", file_name: Optional[str] = None) -> str:
    """Identify the JSON schema variant (V1, V2, V3.1, V3.2) for auditability."""
    fname = (file_name or "").lower()
    if file_role != "crawl_data":
        if isinstance(raw_obj, dict):
            has_v32 = bool(
                str(raw_obj.get("version") or "").startswith("3.2")
                or "counters" in raw_obj
                or isinstance((raw_obj.get("sections") or {}).get("9_growth_tracking"), list)
                or "10_priority_actions" in (raw_obj.get("sections") or {})
            )
            if file_role == "market_report" and (
                fname.startswith("trend_report") or isinstance(raw_obj.get("sections"), dict) or "run_status" in raw_obj
            ):
                return f"{file_role}:{'v3.2' if has_v32 else 'v3.1'}"
            if raw_obj.get("version") or raw_obj.get("schema_version"):
                return f"{file_role}:{raw_obj.get('schema_version') or raw_obj.get('version')}"
        return file_role
    if isinstance(raw_obj, dict):
        explicit = _clean_str(raw_obj.get("schema_version") or raw_obj.get("version"))
        if explicit:
            return explicit
        records = raw_obj.get("records")
        if "counters" in raw_obj or (
            isinstance(records, list)
            and records
            and isinstance(records[0], dict)
            and any(k in records[0] for k in ("views_display", "views_precision", "classification", "similar_content"))
        ):
            return "data_contract_v3_2"
        if fname.startswith("threads_trend") or any(
            k in raw_obj for k in ("tier_counts", "group_counts", "trends_snapshot", "screening")
        ):
            return "data_contract_v3_1"
        if isinstance(records, list) and records and isinstance(records[0], dict):
            if any(k in records[0] for k in ("trend_tier", "verification_status", "viral_analysis", "post_pk")):
                return "data_contract_v3_1"
        if str(raw_obj.get("file_version") or "").lower() == "corrected_v2":
            return "data_contract_v2_corrected"
        if "poc" in raw_obj and isinstance(raw_obj.get("posts"), list):
            return "muse_threads_poc_001"
        if "run_id" in raw_obj and isinstance(raw_obj.get("posts"), list):
            posts = raw_obj["posts"]
            if posts and isinstance(posts[0], dict):
                if "data_quality" in posts[0] or "deal_status" in posts[0] or "url_status" in posts[0]:
                    return "data_contract_v2"
                if "content_group" in posts[0] or "purchase_intent" in posts[0]:
                    return "muse_crawl_v2"
            return "muse_crawl_v1"
        for key in ("posts", "items", "records", "data", "results", "products"):
            if isinstance(raw_obj.get(key), list):
                return f"envelope_{key}_v1"
        return "single_object_v1"
    if isinstance(raw_obj, list):
        return "array_v1"
    return "unknown"


def extract_records(raw_obj: Any, file_role: str = "crawl_data") -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Extract envelope metadata and list of item dicts from crawl JSON payloads."""
    if isinstance(raw_obj, list):
        return {}, [x for x in raw_obj if isinstance(x, dict)]

    if not isinstance(raw_obj, dict):
        return {}, []

    envelope_meta = {
        k: v
        for k, v in raw_obj.items()
        if not isinstance(v, list) or k in ("files", "notable_signals_in_data", "upcoming_vouchers")
    }
    if file_role in NON_CRAWL_ROLES:
        return envelope_meta, []

    for list_key in ("posts", "items", "records", "data", "results", "products", "threads", "videos"):
        val = raw_obj.get(list_key)
        if isinstance(val, list):
            return envelope_meta, [x for x in val if isinstance(x, dict)]

    nested_data = raw_obj.get("data")
    if isinstance(nested_data, dict):
        for list_key in ("posts", "items", "records", "results", "products", "videos"):
            val = nested_data.get(list_key)
            if isinstance(val, list):
                return envelope_meta, [x for x in val if isinstance(x, dict)]

    if any(k in raw_obj for k in ("post_id", "item_id", "video_id", "url", "post_url", "content_summary", "content_summary_vi", "product_name")):
        return {}, [raw_obj]

    return envelope_meta, []


def _canonical_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        raw = url.strip()
        if not raw:
            return None
        parsed = urlparse(raw)
        if not parsed.scheme and not parsed.netloc:
            return raw
        scheme = (parsed.scheme or "https").lower()
        netloc = (parsed.netloc or "").lower()
        if netloc in ("threads.com", "www.threads.com", "www.threads.net", "threads.net"):
            netloc = "threads.net"
        elif netloc in ("tiktok.com", "m.tiktok.com", "www.tiktok.com"):
            netloc = "www.tiktok.com"
        clean_path = parsed.path.rstrip("/")
        return f"{scheme}://{netloc}{clean_path}"
    except Exception:
        return url.strip()


def build_dedup_key(
    platform: str,
    post_id: Optional[str],
    item_id: Optional[str],
    shop_id: Optional[str],
    external_id: Optional[str],
    url: Optional[str],
    author_handle: Optional[str],
    published_at: Optional[str],
    title: Optional[str],
    text_content: Optional[str],
    run_id: Optional[str] = None,
    rank: Optional[int] = None,
    frame_start_at: Optional[str] = None,
) -> str:
    """
    Build deterministic cross-run deduplication key:
      1. Primary: platform + post_id / platform-native item ID (Shopee shop_id+item_id or item_id, TikTok video_id/post_id, Threads post_id)
      2. Secondary fallback: canonical_url across crawl runs
      3. Tertiary fallback: external_id / platform-specific legacy composite or content hash
    """
    plat = platform or "unknown"
    c_url = _canonical_url(url)

    if plat == "shopee":
        if shop_id and item_id:
            return f"shopee:item:{shop_id}:{item_id}"
        if item_id:
            return f"shopee:item:{item_id}"
        if post_id:
            return f"shopee:post:{post_id}"
        if c_url:
            return f"shopee:url:{c_url}"
        if external_id:
            return f"shopee:ext:{external_id}"
        if title:
            clean_name = re.sub(r"\s+", " ", title.strip().lower())
            name_hash = hashlib.sha256(clean_name.encode("utf-8")).hexdigest()[:16]
            scope = run_id or frame_start_at or "global"
            return f"shopee:name:{scope}:{name_hash}"
    elif plat == "tiktok":
        if item_id:
            return f"tiktok:item:{item_id}"
        if post_id:
            return f"tiktok:post:{post_id}"
        if c_url:
            return f"tiktok:url:{c_url}"
        if external_id:
            return f"tiktok:ext:{external_id}"
        if author_handle and run_id:
            rank_part = str(rank) if rank is not None else "na"
            return f"tiktok:handle_run:{run_id}:{author_handle.strip().lower()}:{rank_part}"
    elif plat == "threads":
        if post_id:
            return f"threads:post:{post_id}"
        if item_id:
            return f"threads:item:{item_id}"
        if c_url:
            return f"threads:url:{c_url}"
    else:
        if post_id:
            return f"{plat}:post:{post_id}"
        if item_id:
            return f"{plat}:item:{item_id}"
        if c_url:
            return f"{plat}:url:{c_url}"

    if external_id:
        return f"{plat}:ext:{external_id}"

    fingerprint_src = json.dumps(
        [plat, author_handle or "", published_at or "", title or "", text_content or ""],
        ensure_ascii=False,
        sort_keys=False,
    )
    digest = hashlib.sha256(fingerprint_src.encode("utf-8")).hexdigest()[:24]
    return f"{plat}:hash:{digest}"


def normalize_record(
    item: Dict[str, Any],
    envelope: Optional[Dict[str, Any]] = None,
    file_name: Optional[str] = None,
    platform_hint: Optional[str] = None,
    schema_version: str = "unknown",
    item_index: int = 0,
    is_corrected: bool = False,
) -> Dict[str, Any]:
    """
    Normalize one raw Market Intelligence item into canonical Data Contract V2 form.
    Missing fields remain None; nothing is fabricated; null never becomes 0.
    """
    envelope = envelope or {}
    platform = detect_platform(item, envelope=envelope, file_name=file_name, platform_hint=platform_hint)

    post_id = _clean_str(item.get("post_id"))
    item_id = _clean_str(
        item.get("item_id")
        or item.get("video_id")
        or item.get("aweme_id")
        or item.get("product_id")
    )
    shop_id = _clean_str(item.get("shop_id") or item.get("shopid"))
    numeric_post_id = _clean_str(item.get("numeric_post_id"))
    rank_val = _clean_int(item.get("rank"))
    run_id = _clean_str(item.get("run_id") or envelope.get("run_id"))
    external_id = post_id or item_id or numeric_post_id or _clean_str(item.get("id"))

    author_handle = _clean_str(
        item.get("author_handle")
        or item.get("author_username")
        or item.get("username")
        or item.get("author")
        or item.get("shop_name")
        or item.get("seller_name")
    )
    author_name = _clean_str(item.get("author_name") or item.get("shop_title"))

    title = _clean_str(item.get("title") or item.get("product_name") or item.get("name"))
    summary_vi = _clean_str(item.get("content_summary_vi"))
    text_content = _clean_str(
        item.get("content_full_text")
        or summary_vi
        or item.get("content_summary")
        or item.get("text")
        or item.get("caption")
        or item.get("description")
    )
    # Keep item URL strict (never swap Shopee's missing product_url with evidence_url)
    url = _clean_str(
        item.get("post_url")
        or item.get("video_url")
        or item.get("product_url")
        or item.get("item_url")
        or item.get("url")
    )

    content_group = _clean_str(
        item.get("content_group")
        or item.get("contentGroup")
        or item.get("topic_group")
    )
    purchase_intent = _clean_str(
        item.get("purchase_intent")
        or item.get("purchaseIntent")
        or item.get("buyer_intent")
    )

    published_at = _clean_str(
        item.get("posted_at_local")
        or item.get("posted_at")
        or item.get("posted_at_utc")
        or item.get("published_at")
        or item.get("created_at")
        or item.get("frame_start_at")
    )
    crawled_at = _clean_str(
        item.get("crawled_at")
        or item.get("crawl_time")
        or envelope.get("crawled_at")
        or envelope.get("crawl_time")
        or item.get("collected_at")
        or envelope.get("ended_at")
        or envelope.get("collected_at")
        or envelope.get("started_at")
        or item.get("views_measured_at")
    )
    measured_at = _clean_str(
        item.get("views_measured_at")
        or item.get("measured_at")
        or item.get("crawled_at")
        or crawled_at
    )
    language = _clean_str(item.get("language"))

    likes_count = _clean_int(item.get("likes") if "likes" in item else item.get("likes_count"))
    comments_count = _clean_int(
        item.get("comment_count")
        if "comment_count" in item
        else (item.get("replies_or_comments_count") if "replies_or_comments_count" in item else item.get("comments_count"))
    )
    shares_count = _clean_int(item.get("shares") if "shares" in item else item.get("shares_count"))
    reposts_count = _clean_int(item.get("reposts") if "reposts" in item else item.get("repost_count"))
    quotes_count = _clean_int(item.get("quotes") if "quotes" in item else item.get("quote_count"))
    views_count = _clean_int(item.get("views") if "views" in item else (item.get("play_count") if "play_count" in item else item.get("views_count")))
    price = _clean_float(item.get("price_vnd") if "price_vnd" in item else item.get("price"))
    sold_count = _clean_int(
        item.get("sold_count")
        if "sold_count" in item
        else (item.get("sold") if "sold" in item else item.get("historical_sold"))
    )
    rating = _clean_float(item.get("rating") if "rating" in item else item.get("rating_star"))

    # Data Contract V3.2 views, identity, shares/reposts & classification metadata
    raw_views_val = item.get("views") if "views" in item else (item.get("play_count") if "play_count" in item else item.get("views_count"))
    views_display = _clean_str(item.get("views_display"))
    if not views_display and isinstance(raw_views_val, str) and re.search(r"[kKmMbB]", raw_views_val):
        views_display = raw_views_val.strip()
    views_precision = _clean_str(item.get("views_precision"))
    if views_precision:
        views_precision = views_precision.lower()
    elif views_display and views_count is None:
        views_precision = "rounded_display"
    views_source = _clean_str(item.get("views_source"))
    views_measured_at = _clean_str(item.get("views_measured_at"))
    views_evidence_url = _clean_str(item.get("views_evidence_url"))
    classification = _clean_str(item.get("classification"))
    if classification:
        classification = classification.upper()
    content_hash = _clean_str(item.get("content_hash"))
    similar_content = bool(item.get("similar_content")) if item.get("similar_content") is not None else None
    similar_to_post_id = _clean_str(item.get("similar_to_post_id"))
    engagement_rate_on_views = _clean_float(item.get("engagement_rate_on_views"))
    engagement_rate_approx = bool(item.get("engagement_rate_approx")) if item.get("engagement_rate_approx") is not None else None

    # Data Contract V3.1 first-class fields (kept None on V1/V2 if absent)
    post_pk = _clean_str(item.get("post_pk") or item.get("pk"))
    trend_tier = _clean_str(item.get("trend_tier"))
    if trend_tier:
        trend_tier = trend_tier.lower()
    trend_group = _clean_str(item.get("trend_group"))
    if trend_group:
        trend_group = trend_group.lower()
    verification_status = _clean_str(item.get("verification_status"))
    if verification_status:
        verification_status = verification_status.lower()
    metrics_source = _clean_str(item.get("metrics_source"))
    viral_analysis = item.get("viral_analysis") if isinstance(item.get("viral_analysis"), dict) else None
    evidence_url = _clean_str(
        item.get("evidence_url")
        or ((viral_analysis or {}).get("evidence_url"))
        or url
    )

    data_quality = _clean_str(item.get("data_quality"))
    raw_issues = item.get("quality_issues")
    quality_issues = [str(q) for q in raw_issues if q] if isinstance(raw_issues, list) else []

    metrics = {
        k: v
        for k, v in {
            "likes": likes_count,
            "comments": comments_count,
            "reposts": reposts_count,
            "quotes": quotes_count,
            "shares": shares_count,
            "views": views_count,
            "price": price,
            "sold": sold_count,
            "rating": rating,
        }.items()
        if v is not None
    }

    extra_meta: Dict[str, Any] = {}
    envelope_fallback_keys = {
        "request_id",
        "run_id",
        "run_status",
        "file_version",
        "corrects_file",
        "technical_status",
        "data_quality_status",
        "source_type",
        "upcoming_vouchers",
        "trending_context",
    }
    for meta_key in (
        "request_id",
        "run_id",
        "run_status",
        "search_groups",
        "entities_mentioned",
        "mentioned_products_brands_topics",
        "purchase_intent_comments",
        "content_summary",
        "content_summary_vi",
        "content_note",
        "comments_note",
        "source_query",
        "type",
        "numeric_post_id",
        "post_pk",
        "posted_at_utc",
        "posted_at_local",
        "currency",
        "category",
        "crawl_bucket",
        "author_type",
        "hook_analysis",
        "viral_analysis",
        "enrichment_status",
        "source_type",
        "metrics_source",
        "verification_status",
        "trend_tier",
        "trend_group",
        "theme_tags",
        "url_status",
        "caption",
        "hashtags",
        "affiliate_relevance",
        "affiliate_reason",
        "evidence_url",
        "rank",
        "deal_status",
        "frame_start_at",
        "frame_end_at",
        "price_status",
        "original_price",
        "price_original",
        "stock_signal",
        "selection_signal",
        "affiliate_eligible",
        "reposts",
        "quotes",
        "file_version",
        "corrects_file",
        "technical_status",
        "data_quality_status",
        "upcoming_vouchers",
        "trending_context",
        "views_display",
        "views_precision",
        "views_source",
        "views_measured_at",
        "views_evidence_url",
        "classification",
        "content_hash",
        "similar_content",
        "similar_to_post_id",
        "engagement_rate_on_views",
        "engagement_rate_approx",
        "first_seen_at",
        "last_seen_at",
        "observation_count",
    ):
        if meta_key in item:
            val = item.get(meta_key)
        elif meta_key in envelope_fallback_keys:
            val = envelope.get(meta_key)
        else:
            val = None
        if val is not None and val != "":
            extra_meta[meta_key] = val
    if viral_analysis and viral_analysis.get("affiliate_angle") is not None:
        extra_meta["affiliate_angle"] = viral_analysis.get("affiliate_angle")
    if evidence_url and "evidence_url" not in extra_meta:
        extra_meta["evidence_url"] = evidence_url
    if "affiliate_eligible" in item:
        extra_meta["affiliate_eligible"] = bool(item["affiliate_eligible"])
    if data_quality is not None:
        extra_meta["data_quality"] = data_quality
    if quality_issues:
        extra_meta["quality_issues"] = quality_issues
    if is_corrected:
        extra_meta["is_corrected"] = True

    canon_url_raw = _clean_str(item.get("canonical_url")) or url
    dedup_key = build_dedup_key(
        platform=platform,
        post_id=post_id,
        item_id=item_id,
        shop_id=shop_id,
        external_id=external_id,
        url=canon_url_raw,
        author_handle=author_handle,
        published_at=published_at,
        title=title,
        text_content=text_content,
        run_id=run_id,
        rank=rank_val,
        frame_start_at=_clean_str(item.get("frame_start_at")),
    )

    return {
        "dedup_key": dedup_key,
        "platform": platform,
        "external_id": external_id,
        "post_id": post_id,
        "post_pk": post_pk,
        "item_id": item_id,
        "shop_id": shop_id,
        "author_handle": author_handle,
        "author_name": author_name,
        "title": title,
        "text_content": text_content,
        "summary_vi": summary_vi or text_content,
        "url": url,
        "canonical_url": _canonical_url(canon_url_raw),
        "evidence_url": evidence_url,
        "content_group": content_group,
        "purchase_intent": purchase_intent,
        "language": language,
        "published_at": published_at,
        "crawled_at": crawled_at,
        "measured_at": measured_at,
        "likes_count": likes_count,
        "comments_count": comments_count,
        "shares_count": shares_count,
        "reposts_count": reposts_count,
        "quotes_count": quotes_count,
        "views_count": views_count,
        "views_display": views_display,
        "views_precision": views_precision,
        "views_source": views_source,
        "views_measured_at": views_measured_at,
        "views_evidence_url": views_evidence_url,
        "classification": classification,
        "content_hash": content_hash,
        "similar_content": similar_content,
        "similar_to_post_id": similar_to_post_id,
        "engagement_rate_on_views": engagement_rate_on_views,
        "engagement_rate_approx": engagement_rate_approx,
        "record_first_seen_at": _clean_str(item.get("first_seen_at")),
        "record_last_seen_at": _clean_str(item.get("last_seen_at")),
        "record_observation_count": _clean_int(item.get("observation_count")),
        "price": price,
        "sold_count": sold_count,
        "rating": rating,
        "trend_tier": trend_tier,
        "trend_group": trend_group,
        "verification_status": verification_status,
        "metrics_source": metrics_source,
        "data_quality": data_quality,
        "quality_issues": quality_issues,
        "is_corrected": is_corrected,
        "source_priority": 2 if is_corrected else 1,
        "run_id": run_id,
        "metrics": metrics,
        "extra_meta": extra_meta,
        "schema_version": schema_version,
        "raw_item_index": item_index,
        "raw_payload_json": json.dumps(item, ensure_ascii=False),
    }


def extract_v32_counters(raw_obj: Any, legacy_fallback_qualified: bool = False) -> Dict[str, Any]:
    """
    Extract canonical Data Contract V3.2 root/screening counters strictly without fabricating absent operands.
    Verifies reconciliation:
      unique_candidate_count == new_qualified_count + unverified_count + duplicate_history_count + updated_metrics_count + rejected_count
    (`similar_content_count` is tracked outside the reconciliation sum).
    """
    if not isinstance(raw_obj, dict):
        return {}
    sections = raw_obj.get("sections") if isinstance(raw_obj.get("sections"), dict) else {}
    c = raw_obj.get("counters") if isinstance(raw_obj.get("counters"), dict) else {}
    s1 = (
        sections.get("1_screening")
        or raw_obj.get("section_1_screening")
        or raw_obj.get("screening")
        or {}
    )
    if not isinstance(s1, dict):
        s1 = {}
    req_ctx = raw_obj.get("request_context") if isinstance(raw_obj.get("request_context"), dict) else {}

    def pick_int(*vals: Any) -> Optional[int]:
        for v in vals:
            res = _clean_int(v)
            if res is not None:
                return res
        return None

    requested_count = pick_int(c.get("requested_count"), req_ctx.get("requested_count"), raw_obj.get("requested_count"), s1.get("requested_count"))
    scanned_count = pick_int(c.get("scanned_count"), c.get("scanned_posts"), s1.get("scanned_count"), s1.get("scanned_posts"))
    unique_candidate_count = pick_int(c.get("unique_candidate_count"), s1.get("unique_candidate_count"))
    new_count = pick_int(c.get("new_count"), s1.get("new_count"))
    new_qualified_count = pick_int(
        c.get("new_qualified_count"),
        s1.get("new_qualified_count"),
        s1.get("qualified_count") if legacy_fallback_qualified else None,
        s1.get("qualified_posts") if legacy_fallback_qualified else None,
    )
    duplicate_current_run_count = pick_int(c.get("duplicate_current_run_count"), s1.get("duplicate_current_run_count"))
    if duplicate_current_run_count is None and scanned_count is not None and unique_candidate_count is not None:
        duplicate_current_run_count = max(0, scanned_count - unique_candidate_count)
    duplicate_history_count = pick_int(
        c.get("duplicate_history_count"),
        s1.get("duplicate_history_count"),
        s1.get("duplicate_count"),
        s1.get("duplicates"),
    )
    updated_metrics_count = pick_int(
        c.get("updated_metrics_count"),
        s1.get("updated_metrics_count"),
        s1.get("updated_count"),
    )
    similar_content_count = pick_int(c.get("similar_content_count"), s1.get("similar_content_count"))
    missing_views_count = pick_int(c.get("missing_views_count"), s1.get("missing_views_count"))
    unverified_count = pick_int(c.get("unverified_count"), s1.get("unverified_count"))
    rejected_count = pick_int(
        c.get("rejected_count"),
        c.get("rejected"),
        s1.get("rejected_count"),
        s1.get("rejected_posts"),
        s1.get("rejected"),
    )

    if "target_achieved" in c and isinstance(c.get("target_achieved"), bool):
        target_achieved: Optional[bool] = bool(c["target_achieved"])
    elif new_qualified_count is not None and requested_count is not None:
        target_achieved = bool(new_qualified_count >= requested_count)
    else:
        target_achieved = None

    stop_reason = _clean_str(c.get("stop_reason") or s1.get("stop_reason") or raw_obj.get("stop_reason"))

    reconcile_terms = (
        unique_candidate_count,
        new_qualified_count,
        unverified_count,
        duplicate_history_count,
        updated_metrics_count,
        rejected_count,
    )
    if all(v is not None for v in reconcile_terms):
        expected_sum = (
            new_qualified_count
            + unverified_count
            + duplicate_history_count
            + updated_metrics_count
            + rejected_count
        )
        counters_reconciled: Optional[bool] = (unique_candidate_count == expected_sum)
        reconciliation_diff: Optional[int] = unique_candidate_count - expected_sum
    else:
        counters_reconciled = None
        reconciliation_diff = None

    return {
        "requested_count": requested_count,
        "scanned_count": scanned_count,
        "unique_candidate_count": unique_candidate_count,
        "new_count": new_count,
        "new_qualified_count": new_qualified_count,
        "duplicate_current_run_count": duplicate_current_run_count,
        "duplicate_history_count": duplicate_history_count,
        "updated_metrics_count": updated_metrics_count,
        "similar_content_count": similar_content_count,
        "missing_views_count": missing_views_count,
        "unverified_count": unverified_count,
        "rejected_count": rejected_count,
        "target_achieved": target_achieved,
        "stop_reason": stop_reason,
        "counters_reconciled": counters_reconciled,
        "reconciliation_diff": reconciliation_diff,
    }


def is_valid_v32_views_provenance(item: Dict[str, Any]) -> bool:
    """
    Validate full Data Contract V3.2 views provenance criteria:
      - Non-negative integer views_count
      - Non-empty views_display (e.g. '513K', '3.6K', '850')
      - Allowed views_precision ('rounded_display', 'display_approx', 'exact')
      - Valid views_source ('threads_web_browser' for Threads)
      - Valid ISO timestamp/date views_measured_at
      - Valid HTTP/HTTPS views_evidence_url (Threads host when platform == 'threads')
    Having only numeric views without provenance MUST NOT qualify.
    """
    if not isinstance(item, dict):
        return False
    views_cnt = item.get("views_count")
    if views_cnt is None:
        views_cnt = _clean_int(item.get("views"))
    if views_cnt is None or isinstance(views_cnt, bool) or int(views_cnt) < 0:
        return False

    plat = str(item.get("platform") or "threads").strip().lower() or "threads"
    v_disp = _clean_str(item.get("views_display"))
    if not v_disp or not re.match(r"^\d+(?:[.,]\d+)?\s*[KMBkmb]?$", v_disp):
        return False

    v_prec = (_clean_str(item.get("views_precision")) or "").lower()
    if v_prec not in {"rounded_display", "display_approx", "exact"}:
        return False

    v_src = (_clean_str(item.get("views_source")) or "").lower()
    if plat == "threads":
        if v_src != "threads_web_browser":
            return False
    elif not v_src:
        return False

    v_meas = _clean_str(item.get("views_measured_at"))
    if not v_meas:
        return False
    try:
        iso_norm = v_meas[:-1] + "+00:00" if v_meas.endswith("Z") else v_meas
        datetime.fromisoformat(iso_norm)
    except Exception:
        return False

    v_ev = _clean_str(item.get("views_evidence_url"))
    if not v_ev:
        return False
    try:
        parsed_ev = urlparse(v_ev)
    except Exception:
        return False
    if parsed_ev.scheme.lower() not in {"http", "https"} or not parsed_ev.netloc:
        return False
    if plat == "threads":
        host = parsed_ev.netloc.lower()
        if "threads.net" not in host and "threads.com" not in host:
            return False
    return True


def normalize_payload(
    raw_obj: Any,
    file_name: Optional[str] = None,
    platform_hint: Optional[str] = None,
    folder_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize an entire JSON file payload and return file_role, schema_version, envelope, and items."""
    file_role = classify_json_file(raw_obj, file_name=file_name, folder_path=folder_path)
    is_corrected = is_corrected_payload(raw_obj, file_name=file_name)
    schema_version = detect_schema_version(raw_obj, file_role=file_role, file_name=file_name)
    envelope, raw_items = extract_records(raw_obj, file_role=file_role)
    normalized_items = [
        normalize_record(
            item,
            envelope=envelope,
            file_name=file_name,
            platform_hint=platform_hint,
            schema_version=schema_version,
            item_index=idx,
            is_corrected=is_corrected,
        )
        for idx, item in enumerate(raw_items)
    ]
    return {
        "file_role": file_role,
        "is_corrected": is_corrected,
        "schema_version": schema_version,
        "envelope": envelope,
        "items": normalized_items,
    }
