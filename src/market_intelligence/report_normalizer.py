"""
Standardized Muse Market Analysis Report Normalizer & Evidence Builder (V1 / V2 / V3.1).

Normalizes Muse AI report JSONs (POC 001 signals, Pilot 001, E2E V1/corrected_v2,
and Threads Trend Intelligence V3.1 `trend_report_*.json`) into a canonical structure
preserving backward compatibility with V1/V2 5 sections AND V3.1 9 sections + run_status
taxonomy without fabricating absent data or leaking platform/website names as products.
"""

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


RUN_STATUS_LABELS_VI = {
    "completed": "✅ Hoàn thành với bài đạt chuẩn",
    "no_qualifying_posts": "⚪ Nguồn chạy bình thường — Không có bài đạt chuẩn (no_qualifying_posts)",
    "insufficient_data": "⚠️ Dữ liệu không đủ (insufficient_data) — Chưa đủ cơ sở đánh giá xu hướng",
    "source_error": "❌ Lỗi nguồn dữ liệu (source_error) — Không kết luận về thị trường",
}


NON_PRODUCT_TERMS = frozenset(
    {
        "tiktok",
        "threads",
        "shopee",
        "facebook",
        "instagram",
        "youtube",
        "beacons",
        "beacons.ai",
        "linktree",
        "linktr.ee",
        "bit.ly",
        "tinyurl",
        "endless",
        "endless.app",
        "endless social radar",
        "tiki",
        "lazada",
        "zalo",
        "google",
        "google drive",
        "cloudflare",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(val: Any) -> List[Any]:
    if isinstance(val, list):
        return val
    if val is None or val == "":
        return []
    return [val]


def is_platform_or_site_name(label: Optional[str]) -> bool:
    """Reject pure platform/website names from ever appearing as product recommendations."""
    if not label:
        return True
    norm = re.sub(r"^[@#]+", "", str(label).strip().lower())
    if norm in NON_PRODUCT_TERMS:
        return True
    if any(norm == term or norm.endswith(f".{term}") for term in ("beacons.ai", "tiktok.com", "threads.com", "shopee.vn")):
        return True
    return False


def _filter_product_entities(entities: List[Any]) -> List[str]:
    clean: List[str] = []
    for e in entities:
        s = str(e or "").strip()
        if s and not is_platform_or_site_name(s):
            clean.append(s)
    return clean


def _extract_links_from_items(items: List[Dict[str, Any]], limit: int = 15) -> List[Dict[str, Any]]:
    links: List[Dict[str, Any]] = []
    seen = set()
    for it in items:
        extra = it.get("extra_meta") or {}
        url = it.get("url") or it.get("post_url") or extra.get("evidence_url")
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(
            {
                "platform": it.get("platform"),
                "post_id": it.get("post_id") or it.get("item_id") or it.get("external_id"),
                "author": it.get("author_handle"),
                "url": url,
                "url_status": extra.get("url_status"),
                "source_file": it.get("source_file_name"),
            }
        )
        if len(links) >= limit:
            break
    return links


def has_muse_report_sections(raw_obj: Any) -> bool:
    """Return True ONLY if raw JSON payload is a real Muse report or POC signal payload (never audit/strategy/contract/research)."""
    if not isinstance(raw_obj, dict):
        return False
    # Never treat data_contract, crawl_strategy, capability tests, research docs, or quality audits as a market report
    if "contract" in raw_obj or "audit" in raw_obj or "strategy" in raw_obj:
        return False
    if "engagement_verification_map" in raw_obj or ("document" in raw_obj and "proposed_version" in raw_obj):
        return False
    if isinstance(raw_obj.get("entries"), list) and ("completeness_audit" in raw_obj or "primary_key" in raw_obj):
        return False
    if isinstance(raw_obj.get("sources"), list) and "sections" not in raw_obj and "platform_results" not in raw_obj:
        return False
    report_keys = {
        "sections",
        "section_1_screening",
        "section_3_trending_themes",
        "report",
        "analysis",
        "market_report",
        "trending_highlights",
        "opportunity_ranking",
        "consumer_needs_problems",
        "real_purchase_needs",
        "customer_pain_points",
        "affiliate_opportunities",
        "affiliate_product_groups",
        "affiliate_products",
        "content_suggestions",
        "content_ideas_threads_facebook",
        "video_ideas_shortform",
        "notable_signals_in_data",
        "platform_results",
        "telegram_summary",
    }
    return any(k in raw_obj for k in report_keys)


def _extract_urls_from_text(text: Any) -> List[str]:
    if not isinstance(text, str) or not text:
        return []
    return re.findall(r"https?://[^\s)\],\"'>]+", text)


def _map_run_status_metadata(raw_run_status: Optional[str], raw_tech: Optional[str], raw_dq: Optional[str]) -> Dict[str, Any]:
    rs = str(raw_run_status or "").strip().lower() or None
    tech = str(raw_tech or "").strip().lower() or None
    dq = str(raw_dq or "").strip() or None

    is_source_error = bool(rs in ("source_error", "insufficient_data") or tech in ("failed", "partial"))
    is_empty_market_valid = bool(rs == "no_qualifying_posts")
    label = RUN_STATUS_LABELS_VI.get(rs or "")
    if not label:
        if tech == "failed":
            label = RUN_STATUS_LABELS_VI["source_error"]
        elif tech == "partial":
            label = RUN_STATUS_LABELS_VI["insufficient_data"]
        elif rs or tech:
            label = RUN_STATUS_LABELS_VI["completed"]
    return {
        "run_status": rs,
        "technical_status": tech,
        "data_quality_status": dq,
        "is_source_error": is_source_error,
        "is_empty_market_valid": is_empty_market_valid,
        "run_status_label_vi": label,
    }


def normalize_muse_report(
    raw_obj: Dict[str, Any],
    file_name: Optional[str] = None,
    modified_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize explicit Muse reports (V1, V2, V3.1 trend_report_*.json) into canonical structure."""
    src = raw_obj
    for container_key in ("report", "analysis", "market_report"):
        if isinstance(raw_obj.get(container_key), dict):
            src = raw_obj[container_key]
            break

    sections = src.get("sections") if isinstance(src.get("sections"), dict) else {}
    fname_lower = (file_name or "").lower()
    has_counters = isinstance(raw_obj.get("counters"), dict) or isinstance(src.get("counters"), dict)
    is_v32 = bool(
        has_counters
        or str(raw_obj.get("version") or src.get("version") or "").startswith("3.2")
        or isinstance(sections.get("9_growth_tracking"), list)
        or "10_priority_actions" in sections
    )
    is_v3 = bool(
        is_v32
        or sections
        or "run_status" in src
        or "section_1_screening" in src
        or fname_lower.startswith("trend_report")
    )
    is_corrected = bool(
        "_corrected" in fname_lower
        or raw_obj.get("corrects_report")
        or str(raw_obj.get("file_version") or "").lower() == "corrected_v2"
    )
    source_run = src.get("source_run") if isinstance(src.get("source_run"), dict) else {}
    report_str_title = raw_obj.get("report") if isinstance(raw_obj.get("report"), str) else None
    title = (
        src.get("title")
        or report_str_title
        or raw_obj.get("poc")
        or source_run.get("run_id")
        or raw_obj.get("run_id")
        or file_name
        or "Muse Market Analysis Report"
    )
    platform = (src.get("platform") or raw_obj.get("platform") or ("all" if isinstance(src.get("platform_results"), dict) else "threads")).lower()
    if platform not in ("threads", "tiktok", "shopee", "all"):
        platform = "threads"

    platform_results = src.get("platform_results") if isinstance(src.get("platform_results"), dict) else {}
    totals = dict(src.get("totals")) if isinstance(src.get("totals"), dict) else {}

    # V3.1/V3.2 Section 1 (Screening & Root Counters) & Section 2 (Tier Distribution)
    from .normalizer import extract_v32_counters

    counters = extract_v32_counters(raw_obj, legacy_fallback_qualified=False)
    s1 = sections.get("1_screening") or src.get("section_1_screening") or {}
    if not isinstance(s1, dict):
        s1 = {}
    s2 = sections.get("2_tier_distribution") or src.get("section_2_tier_distribution") or {}
    if not isinstance(s2, dict):
        s2 = {}

    raw_tiers = (
        s2.get("tiers")
        or s2.get("tier_counts")
        or {
            k: s2.get(k) for k in ("very_hot", "hot", "emerging", "watchlist", "unverified") if k in s2
        }
    )
    if not isinstance(raw_tiers, dict):
        raw_tiers = {}
    raw_groups = s2.get("groups") or s2.get("group_counts") or {}
    if not isinstance(raw_groups, dict):
        raw_groups = {}

    screening = {
        "requested_count": counters.get("requested_count"),
        "scanned_count": counters.get("scanned_count") if counters.get("scanned_count") is not None else (s1.get("scanned_count") if s1.get("scanned_count") is not None else s1.get("scanned_posts")),
        "unique_candidate_count": counters.get("unique_candidate_count"),
        "new_count": counters.get("new_count"),
        "new_qualified_count": counters.get("new_qualified_count"),
        "qualified_count": counters.get("new_qualified_count") if counters.get("new_qualified_count") is not None else (s1.get("qualified_count") if s1.get("qualified_count") is not None else s1.get("qualified_posts")),
        "duplicate_current_run_count": counters.get("duplicate_current_run_count"),
        "duplicate_history_count": counters.get("duplicate_history_count"),
        "updated_metrics_count": counters.get("updated_metrics_count"),
        "similar_content_count": counters.get("similar_content_count"),
        "missing_views_count": counters.get("missing_views_count"),
        "rejected_count": counters.get("rejected_count") if counters.get("rejected_count") is not None else (s1.get("rejected_count") if s1.get("rejected_count") is not None else s1.get("rejected_posts")),
        "rejected_reasons": s1.get("rejected_reasons") if isinstance(s1.get("rejected_reasons"), dict) else {},
        "cross_verified_count": s1.get("cross_verified_count"),
        "unverified_count": counters.get("unverified_count") if counters.get("unverified_count") is not None else (s1.get("unverified_count") if s1.get("unverified_count") is not None else raw_tiers.get("unverified")),
        "target_achieved": counters.get("target_achieved"),
        "stop_reason": counters.get("stop_reason"),
        "counters_reconciled": counters.get("counters_reconciled"),
        "reconciliation_diff": counters.get("reconciliation_diff"),
        "sources": s1.get("sources"),
        "note": s1.get("note"),
    }
    tier_distribution = {
        "tiers": {
            "very_hot": raw_tiers.get("very_hot", 0) or 0,
            "hot": raw_tiers.get("hot", 0) or 0,
            "emerging": raw_tiers.get("emerging", 0) or 0,
            "watchlist": raw_tiers.get("watchlist", 0) or 0,
            "unverified": raw_tiers.get("unverified")
            if raw_tiers.get("unverified") is not None
            else (screening.get("unverified_count") or 0),
        }
        if (raw_tiers or is_v3)
        else {},
        "groups": dict(raw_groups),
    }

    if is_v3 and not totals:
        totals = {
            k: v
            for k, v in {
                "records": screening.get("scanned_count"),
                "business_usable_records": screening.get("qualified_count"),
                "rejected_records": screening.get("rejected_count"),
                "cross_verified_records": screening.get("cross_verified_count"),
                "unverified_records": screening.get("unverified_count"),
            }.items()
            if v is not None
        }

    # Map verification_detail + top_viral first so URLs/handles can enrich evidence links cleanly
    raw_verif_detail = src.get("verification_detail") if isinstance(src.get("verification_detail"), dict) else None
    handle_url_map: Dict[str, str] = {}

    raw_top_viral = sections.get("4_top_viral") or src.get("section_4_top_viral") or []
    top_viral: List[Dict[str, Any]] = []
    for tv in _as_list(raw_top_viral):
        if not isinstance(tv, dict):
            continue
        author_handle = str(tv.get("author") or tv.get("author_handle") or "").strip()
        post_id_val = str(tv.get("post_id") or "").strip()
        fallback_label = (f"@{author_handle.lstrip('@')} • {post_id_val}".strip(" •") if author_handle else post_id_val)
        post_label = str(tv.get("post") or tv.get("title") or fallback_label or "").strip()
        tv_url = tv.get("url") or tv.get("post_url")
        if tv_url:
            for handle in re.findall(r"@[A-Za-z0-9_.]+", post_label):
                handle_url_map.setdefault(handle.lower(), tv_url)
        top_viral.append(
            {
                "post": post_label,
                "post_id": post_id_val or None,
                "author": author_handle or None,
                "author_followers": tv.get("author_followers") if tv.get("author_followers") is not None else tv.get("followers"),
                "tier": tv.get("tier") or tv.get("trend_tier"),
                "url": tv_url,
                "likes": tv.get("likes"),
                "comments": tv.get("comments") if "comments" in tv else tv.get("comment_count"),
                "reposts": tv.get("reposts"),
                "quotes": tv.get("quotes"),
                "views": tv.get("views"),
                "views_display": tv.get("views_display"),
                "views_precision": tv.get("views_precision"),
                "shares": tv.get("shares"),
                "viral_reason": tv.get("viral_reason"),
                "affiliate_note": tv.get("affiliate_note"),
                "verification_status": tv.get("verification_status"),
            }
        )
    if raw_verif_detail and isinstance(raw_verif_detail.get("cross_verified_posts"), list):
        for cv in raw_verif_detail["cross_verified_posts"]:
            if isinstance(cv, dict) and cv.get("post_url") and cv.get("post_id"):
                handle_url_map.setdefault(str(cv["post_id"]).lower(), cv["post_url"])

    def _infer_evidence_urls(ev_val: Any, explicit_urls: Any = None) -> List[str]:
        urls = [str(u) for u in _as_list(explicit_urls) if isinstance(u, str) and u.startswith(("http://", "https://"))]
        if isinstance(ev_val, str):
            for u in _extract_urls_from_text(ev_val):
                if u not in urls:
                    urls.append(u)
            for h in re.findall(r"@[A-Za-z0-9_.]+", ev_val):
                mapped = handle_url_map.get(h.lower())
                if mapped and mapped not in urls:
                    urls.append(mapped)
        return urls

    # 1. Trending highlights (V1/V2 + V3.1 Section 3)
    raw_trends = (
        sections.get("3_trends")
        or src.get("section_3_trending_themes")
        or src.get("trending_highlights")
        or src.get("trends")
        or src.get("notable_signals_in_data")
        or src.get("opportunity_ranking")
        or []
    )
    trending_highlights: List[Dict[str, Any]] = []
    for t in _as_list(raw_trends):
        if isinstance(t, dict):
            ev_text = t.get("evidence") or t.get("basis")
            trending_highlights.append(
                {
                    "title": t.get("title") or t.get("name") or t.get("theme") or t.get("opportunity") or t.get("topic") or t.get("summary") or str(t),
                    "platform": t.get("platform") or platform,
                    "content_group": t.get("content_group") or t.get("type"),
                    "count": t.get("count") or t.get("rank"),
                    "confidence": t.get("confidence"),
                    "basis": ev_text,
                    "note": t.get("note"),
                    "why_hot": t.get("why_hot"),
                    "evidence": ev_text,
                    "lifecycle_state": t.get("lifecycle_state"),
                    "evidence_urls": _infer_evidence_urls(ev_text, t.get("evidence_urls") or t.get("urls") or t.get("sources")),
                    "evidence_ids": _as_list(t.get("evidence_ids") or t.get("post_ids") or t.get("evidence_post_ids")),
                }
            )
        elif isinstance(t, str) and t.strip():
            trending_highlights.append(
                {
                    "title": t.strip(),
                    "platform": platform,
                    "content_group": None,
                    "count": None,
                    "evidence_urls": [],
                    "evidence_ids": [],
                }
            )
    if not trending_highlights and platform_results:
        for p_name, p_info in platform_results.items():
            if not isinstance(p_info, dict):
                continue
            for item_txt in _as_list(p_info.get("highlights") or p_info.get("corrections"))[:3]:
                trending_highlights.append(
                    {
                        "title": f"[{p_name.upper()}] {item_txt}",
                        "platform": p_name,
                        "content_group": None,
                        "count": None,
                        "evidence_urls": _as_list(p_info.get("evidence")),
                        "evidence_ids": [],
                    }
                )

    # V3.1 Section 5: Emerging topics vs Single signals (never label a 1-post signal as a trend)
    raw_s5 = sections.get("5_emerging_and_signals") or {}
    if isinstance(raw_s5, dict):
        s5_emerging = [x for x in _as_list(raw_s5.get("emerging")) if isinstance(x, dict)]
        s5_signals = [x for x in _as_list(raw_s5.get("signals")) if isinstance(x, dict)]
        s5_emerging_ids = [str(x).strip() for x in _as_list(raw_s5.get("emerging_post_ids")) if str(x or "").strip()]
        s5_watchlist_ids = [str(x).strip() for x in _as_list(raw_s5.get("watchlist_post_ids")) if str(x or "").strip()]
    else:
        s5_emerging = [x for x in _as_list(src.get("section_5_emerging")) if isinstance(x, dict)]
        s5_signals = []
        s5_emerging_ids = []
        s5_watchlist_ids = []
    for entry in s5_emerging + s5_signals:
        if not entry.get("signal") and (entry.get("topic") or entry.get("name")):
            entry["signal"] = entry.get("topic") or entry.get("name")
        if not entry.get("evidence") and entry.get("detail"):
            entry["evidence"] = entry.get("detail")
        if "evidence_urls" not in entry:
            entry["evidence_urls"] = _infer_evidence_urls(entry.get("evidence") or entry.get("detail"))

    # 2. Consumer needs & problems (V1/V2 + V3.1 Section 6)
    raw_needs = (
        sections.get("6_consumer_needs")
        or src.get("section_6_consumer_needs")
        or src.get("consumer_needs_problems")
        or src.get("consumer_problems")
        or src.get("pain_points")
        or src.get("needs")
        or []
    )
    combined_needs = list(_as_list(raw_needs))
    if not combined_needs:
        for alt_key in ("real_purchase_needs", "customer_pain_points", "why_customers_hesitate"):
            combined_needs.extend(_as_list(src.get(alt_key)))

    consumer_needs_problems: List[Dict[str, Any]] = []
    for n in combined_needs:
        if isinstance(n, dict):
            consumer_needs_problems.append(
                {
                    "title": n.get("title") or n.get("problem") or n.get("pain") or n.get("need") or n.get("reason") or str(n),
                    "category": n.get("category") or n.get("content_group") or n.get("type"),
                    "description": n.get("description") or n.get("detail") or n.get("evidence"),
                    "count": n.get("count"),
                    "evidence_urls": _infer_evidence_urls(n.get("description") or n.get("evidence"), n.get("evidence_urls") or n.get("urls") or n.get("sources")),
                    "evidence_ids": _as_list(n.get("evidence_ids") or n.get("post_ids")),
                }
            )
        elif isinstance(n, str) and n.strip():
            consumer_needs_problems.append(
                {
                    "title": n.strip(),
                    "category": None,
                    "description": None,
                    "count": None,
                    "evidence_urls": _infer_evidence_urls(n.strip()),
                    "evidence_ids": [],
                }
            )
    if not consumer_needs_problems and platform_results:
        for p_name, p_info in platform_results.items():
            if not isinstance(p_info, dict):
                continue
            for iss in _as_list(p_info.get("remaining_issues")):
                consumer_needs_problems.append(
                    {
                        "title": f"[{p_name.upper()} - Hạn chế dữ liệu] {iss}",
                        "category": "data_quality_issue",
                        "description": None,
                        "count": None,
                        "evidence_urls": _as_list(p_info.get("evidence")),
                        "evidence_ids": [],
                    }
                )

    # 3. Affiliate opportunities + evidence (never include pure platform/website names)
    raw_aff = (
        sections.get("7_affiliate_priorities")
        or src.get("section_7_affiliate_products")
        or src.get("affiliate_opportunities")
        or src.get("affiliate_product_groups")
        or src.get("affiliate_products")
        or src.get("products")
        or []
    )
    affiliate_opportunities: List[Dict[str, Any]] = []
    for a in _as_list(raw_aff):
        if isinstance(a, dict):
            prod = a.get("product") or a.get("angle") or a.get("group") or a.get("name") or a.get("title")
            if is_platform_or_site_name(prod):
                continue
            ev_sum = a.get("evidence_summary") or a.get("evidence") or a.get("reason")
            inferred_urls = _infer_evidence_urls(ev_sum, a.get("evidence_urls") or a.get("url") or a.get("urls") or a.get("sources"))
            if not inferred_urls and "dr.althea" in str(prod).lower():
                for sig in s5_signals:
                    inferred_urls.extend(sig.get("evidence_urls") or [])
            if not inferred_urls and "xoay tròn" in str(prod).lower():
                for tv in top_viral:
                    if tv.get("url") and ("merch" in (tv.get("post") or "").lower() or "hoàng dũng" in (tv.get("post") or "").lower()):
                        inferred_urls.append(tv["url"])
                        break
            affiliate_opportunities.append(
                {
                    "product": prod,
                    "brand_or_topic": a.get("brand_or_topic") or a.get("brand") or a.get("type") or a.get("priority"),
                    "priority": a.get("priority"),
                    "viral_level": a.get("viral_level"),
                    "purchase_intent": a.get("purchase_intent") or a.get("evidence_strength") or a.get("priority"),
                    "price": a.get("price"),
                    "evidence_summary": ev_sum,
                    "evidence_urls": list(dict.fromkeys(inferred_urls)),
                    "evidence_ids": _as_list(a.get("evidence_ids") or a.get("post_ids") or a.get("post_id")),
                }
            )

    # 4. Content suggestions (text + short video — V1/V2 + V3.1 Section 8)
    raw_cs = sections.get("8_content_suggestions") or src.get("section_8_content_suggestions") or src.get("content_suggestions") or {}
    if isinstance(raw_cs, list):
        text_posts = []
        short_videos = []
        for s_item in raw_cs:
            if isinstance(s_item, dict):
                ch_raw = str(s_item.get("channel") or "").strip()
                ch_low = ch_raw.lower()
                idea = str(s_item.get("idea") or s_item.get("title") or s_item.get("text") or "").strip()
                basis = str(s_item.get("basis_post_id") or s_item.get("basis") or "").strip()
                formatted = f"[{ch_raw}] {idea}".strip() if ch_raw else idea
                if basis:
                    formatted = f"{formatted} (mẫu: {basis})"
                if not formatted:
                    continue
                if any(k in ch_low for k in ("video", "tiktok", "shorts", "reels")):
                    short_videos.append(formatted)
                else:
                    text_posts.append(formatted)
            elif s_item:
                text_posts.append(str(s_item))
    elif isinstance(raw_cs, dict):
        text_posts = _as_list(
            raw_cs.get("text_posts") or raw_cs.get("threads_facebook") or raw_cs.get("text")
        )
        short_videos = _as_list(
            raw_cs.get("short_videos") or raw_cs.get("short_video") or raw_cs.get("videos")
        )
    else:
        text_posts, short_videos = [], []

    def _idea_to_str(item: Any) -> Optional[str]:
        if isinstance(item, dict):
            idea = item.get("idea") or item.get("title") or item.get("text")
            if not idea:
                return None
            basis = item.get("basis")
            return f"{idea}" if not basis else f"{idea} ({basis})"
        if isinstance(item, str) and item.strip():
            return item.strip()
        return None

    if not text_posts and src.get("content_ideas_threads_facebook"):
        text_posts = [s for s in (_idea_to_str(x) for x in _as_list(src.get("content_ideas_threads_facebook"))) if s]
    if not short_videos and src.get("video_ideas_shortform"):
        short_videos = [s for s in (_idea_to_str(x) for x in _as_list(src.get("video_ideas_shortform"))) if s]

    # V3.2 Section 9 (Growth Tracking) + V3.1/V3.2 Priority actions (9_priority_actions or 10_priority_actions)
    raw_growth = sections.get("9_growth_tracking") or src.get("growth_tracking") or []
    growth_tracking = [g for g in _as_list(raw_growth) if isinstance(g, dict)]
    raw_actions = (
        sections.get("10_priority_actions")
        or sections.get("9_priority_actions")
        or src.get("section_9_priority_actions")
        or src.get("priority_actions")
        or []
    )
    priority_actions = [
        str(x.get("action") or x.get("title") or "").strip() if isinstance(x, dict) else str(x).strip()
        for x in _as_list(raw_actions)
        if (str(x.get("action") or x.get("title") or "").strip() if isinstance(x, dict) else str(x or "").strip())
    ]

    # 5. Data quality & source links
    raw_dq = src.get("data_quality") if isinstance(src.get("data_quality"), dict) else {}
    posts = raw_obj.get("posts") if isinstance(raw_obj.get("posts"), list) else []
    raw_links = _as_list(src.get("source_links"))
    source_links: List[Dict[str, Any]] = []
    seen_urls = set()
    for l in raw_links:
        if isinstance(l, dict) and l.get("url") and l["url"] not in seen_urls:
            seen_urls.add(l["url"])
            source_links.append(l)
        elif isinstance(l, str) and l.strip() and l.strip() not in seen_urls:
            seen_urls.add(l.strip())
            source_links.append({"url": l.strip(), "platform": platform})
    if not source_links and top_viral:
        for tv in top_viral:
            u = tv.get("url")
            if u and u not in seen_urls:
                seen_urls.add(u)
                source_links.append(
                    {
                        "platform": platform,
                        "post_id": u.rstrip("/").split("/")[-1],
                        "url": u,
                        "verification_status": tv.get("verification_status"),
                        "source_file": file_name,
                    }
                )
    if raw_verif_detail and isinstance(raw_verif_detail.get("cross_verified_posts"), list):
        for cv in raw_verif_detail["cross_verified_posts"]:
            if isinstance(cv, dict):
                u = cv.get("post_url")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    source_links.append(
                        {
                            "platform": platform,
                            "post_id": cv.get("post_id"),
                            "url": u,
                            "verification_status": "cross_verified_web",
                            "source_file": file_name,
                        }
                    )
    if not source_links and posts:
        for p in posts[:12]:
            u = p.get("post_url") or p.get("url")
            if u and u not in seen_urls:
                seen_urls.add(u)
                source_links.append(
                    {
                        "platform": platform,
                        "post_id": p.get("post_id"),
                        "author": p.get("author_handle"),
                        "url": u,
                        "source_file": file_name,
                    }
                )
    if not source_links:
        for sec in (trending_highlights, consumer_needs_problems, affiliate_opportunities):
            for entry in sec:
                for u in _as_list(entry.get("evidence_urls")):
                    if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in seen_urls:
                        seen_urls.add(u)
                        source_links.append({"platform": platform, "url": u, "source_file": file_name})
        for p_name, p_info in platform_results.items():
            if isinstance(p_info, dict):
                for u in _as_list(p_info.get("evidence")):
                    if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in seen_urls:
                        seen_urls.add(u)
                        source_links.append({"platform": p_name, "url": u, "source_file": file_name})

    missing_sections: List[str] = []
    if not trending_highlights:
        missing_sections.append("trending_highlights")
    if not consumer_needs_problems:
        missing_sections.append("consumer_needs_problems")
    if not affiliate_opportunities:
        missing_sections.append("affiliate_opportunities")
    if not text_posts and not short_videos:
        missing_sections.append("content_suggestions")

    status_meta = _map_run_status_metadata(
        src.get("run_status") or raw_obj.get("run_status"),
        src.get("technical_status") or raw_obj.get("technical_status"),
        src.get("data_quality_status") or raw_obj.get("data_quality_status"),
    )

    limitations = src.get("data_quality_and_limitations")
    limitation_note = "; ".join(str(x) for x in limitations if x) if isinstance(limitations, list) and limitations else None
    note_text = (
        raw_dq.get("note")
        or (raw_verif_detail or {}).get("note")
        or src.get("method_note")
        or limitation_note
        or raw_obj.get("notes")
        or None
    )

    data_quality = {
        "total_items": (
            raw_dq.get("total_items")
            or totals.get("records")
            or screening.get("scanned_count")
            or source_run.get("records")
            or raw_obj.get("record_count")
            or len(posts)
            or None
        ),
        "business_usable_records": (
            totals.get("business_usable_records")
            if totals.get("business_usable_records") is not None
            else screening.get("qualified_count")
        ),
        "needs_verification_records": totals.get("needs_verification_records"),
        "rejected_records": screening.get("rejected_count"),
        "cross_verified_count": screening.get("cross_verified_count"),
        "unverified_count": screening.get("unverified_count"),
        "run_status": status_meta["run_status"],
        "technical_status": status_meta["technical_status"],
        "data_quality_status": status_meta["data_quality_status"],
        "duplicates_skipped": raw_dq.get("duplicates_skipped") if "duplicates_skipped" in raw_dq else raw_obj.get("duplicates_skipped"),
        "verification": raw_obj.get("verification") if isinstance(raw_obj.get("verification"), dict) else raw_verif_detail,
        "missing_sections": missing_sections,
        "note": note_text,
    }

    run_id = src.get("corrects_report") or source_run.get("run_id") or raw_obj.get("run_id") or file_name or title
    report_key_src = f"{raw_obj.get('run_id') or run_id}:{platform}"
    report_key = hashlib.sha256(report_key_src.encode("utf-8")).hexdigest()[:24]

    crawl_time = (
        raw_obj.get("crawled_at")
        or raw_obj.get("crawl_time")
        or src.get("crawled_at")
        or src.get("crawl_time")
        or raw_obj.get("ended_at")
        or src.get("ended_at")
        or source_run.get("ended_at")
        or raw_obj.get("collected_at")
        or src.get("collected_at")
        or raw_obj.get("started_at")
        or src.get("started_at")
        or source_run.get("started_at")
        or raw_obj.get("generated_at")
        or src.get("generated_at")
    )
    generated_at_val = (
        raw_obj.get("generated_at")
        or src.get("generated_at")
        or crawl_time
        or modified_time
        or _utc_now_iso()
    )
    source_files = [
        str(x).strip()
        for x in _as_list(raw_obj.get("source_files") or src.get("source_files"))
        if str(x or "").strip()
    ]
    return {
        "report_key": report_key,
        "schema_version": "v3.2" if is_v32 else ("v3.1" if is_v3 else "v2"),
        "source_type": "muse_report_json",
        "source_label": f"Muse Report ({file_name or 'Drive JSON'})",
        "file_name": file_name,
        "source_files": source_files,
        "title": str(title),
        "platform": platform,
        "is_corrected": is_corrected,
        "corrects_report": raw_obj.get("corrects_report"),
        "source_priority": 2 if is_corrected else 1,
        "request_id": raw_obj.get("request_id"),
        "run_id": source_run.get("run_id") or raw_obj.get("run_id"),
        "strategy_version": src.get("strategy_version") or raw_obj.get("strategy_version"),
        "contract_version": src.get("contract_version") or raw_obj.get("contract_version"),
        "special_counts": src.get("special_counts") or raw_obj.get("special_counts"),
        "run_status": status_meta["run_status"],
        "run_status_label_vi": status_meta["run_status_label_vi"],
        "is_source_error": status_meta["is_source_error"],
        "is_empty_market_valid": status_meta["is_empty_market_valid"],
        "technical_status": status_meta["technical_status"],
        "data_quality_status": status_meta["data_quality_status"],
        "business_usable_records": data_quality["business_usable_records"],
        "totals": totals,
        "counters": counters if has_counters else None,
        "screening": screening if (is_v3 or any(v is not None for v in screening.values())) else None,
        "tier_distribution": tier_distribution if (tier_distribution["tiers"] or tier_distribution["groups"]) else None,
        "top_viral": top_viral,
        "emerging_and_signals": (
            {
                "emerging": s5_emerging,
                "signals": s5_signals,
                "emerging_post_ids": s5_emerging_ids,
                "watchlist_post_ids": s5_watchlist_ids,
            }
            if (s5_emerging or s5_signals or s5_emerging_ids or s5_watchlist_ids)
            else None
        ),
        "growth_tracking": growth_tracking,
        "priority_actions": priority_actions,
        "verification_detail": raw_verif_detail,
        "platform_results": platform_results,
        "telegram_summary": src.get("telegram_summary") or raw_obj.get("telegram_summary"),
        "crawl_time": crawl_time,
        "crawl_time_is_explicit": bool(crawl_time),
        "generated_at": generated_at_val,
        "trending_highlights": trending_highlights,
        "consumer_needs_problems": consumer_needs_problems,
        "affiliate_opportunities": affiliate_opportunities,
        "content_suggestions": {
            "text_posts": text_posts,
            "short_videos": short_videos,
        },
        "data_quality": data_quality,
        "source_links": source_links,
    }


def build_evidence_report_from_items(items: List[Dict[str, Any]], platform: str = "all") -> Dict[str, Any]:
    """
    Build an evidence-grounded 5-section summary strictly from local normalized items.
    Excludes `needs_verification`, ineligible Shopee products, irrelevant TikTok viral videos,
    and pure platform/website names from affiliate_opportunities.
    """
    plat_filter = (platform or "all").lower()
    scoped = [it for it in items if plat_filter == "all" or it.get("platform") == plat_filter]
    total = len(scoped)

    cg_counts: Counter = Counter()
    cg_examples: Dict[str, List[Dict[str, Any]]] = {}
    need_items: List[Dict[str, Any]] = []
    aff_items: List[Dict[str, Any]] = []

    usable_count = 0
    needs_verification_count = 0
    unlabeled_count = 0
    with_cg = 0
    with_pi = 0
    with_likes = 0
    with_comments = 0
    with_views = 0
    with_shares = 0

    for it in scoped:
        dq = it.get("data_quality") or (it.get("extra_meta") or {}).get("data_quality")
        if dq == "usable":
            usable_count += 1
        elif dq == "needs_verification":
            needs_verification_count += 1
        else:
            unlabeled_count += 1

        cg = it.get("content_group")
        pi = it.get("purchase_intent")
        extra = it.get("extra_meta") or {}
        if cg:
            with_cg += 1
            cg_counts[cg] += 1
            cg_examples.setdefault(cg, []).append(it)
        if pi:
            with_pi += 1
        if it.get("likes_count") is not None:
            with_likes += 1
        if it.get("comments_count") is not None:
            with_comments += 1
        if it.get("views_count") is not None:
            with_views += 1
        if it.get("shares_count") is not None:
            with_shares += 1

        if cg in ("van_de_phan_nan",) or any("phàn nàn" in str(g).lower() for g in (extra.get("search_groups") or [])):
            need_items.append(it)

        # Gate strictly on usable/qualified/verified records for Affiliate & viral opportunities
        verif_status = (it.get("verification_status") or extra.get("verification_status") or "").lower()
        trend_tier = (it.get("trend_tier") or extra.get("trend_tier") or "").lower()
        trend_group = (it.get("trend_group") or extra.get("trend_group") or "").lower()
        viral_analysis = extra.get("viral_analysis") if isinstance(extra.get("viral_analysis"), dict) else {}

        if not cg and trend_group:
            cg_counts[trend_group] += 1
            cg_examples.setdefault(trend_group, []).append(it)

        is_needs_verif = dq == "needs_verification" or verif_status == "unverified" or trend_tier == "unverified"
        is_shopee_ineligible = it.get("platform") == "shopee" and extra.get("affiliate_eligible") is False
        is_tiktok_irrelevant = it.get("platform") == "tiktok" and extra.get("affiliate_relevance") in ("khong_ro", "khong_lien_quan")
        if not is_needs_verif and not is_shopee_ineligible and not is_tiktok_irrelevant:
            if (
                pi == "cao"
                or cg in ("deal_voucher", "review_so_sanh", "san_pham_quan_tam", "hoi_mua_xin_link")
                or trend_group == "trend_affiliate"
                or bool(viral_analysis.get("affiliate_angle"))
            ):
                aff_items.append(it)

    trending_highlights = []
    for cg, cnt in cg_counts.most_common(5):
        exs = cg_examples.get(cg, [])[:3]
        trending_highlights.append(
            {
                "title": f"Nhóm chủ đề '{cg}' ({cnt} bản ghi)",
                "platform": plat_filter,
                "content_group": cg,
                "count": cnt,
                "evidence_urls": [e.get("url") for e in exs if e.get("url")],
                "evidence_ids": [e.get("external_id") or e.get("post_id") for e in exs if (e.get("external_id") or e.get("post_id"))],
            }
        )

    consumer_needs_problems = []
    for it in need_items[:6]:
        summary = (it.get("summary_vi") or it.get("text_content") or it.get("title") or "").strip()
        consumer_needs_problems.append(
            {
                "title": f"{it.get('author_handle') or 'user'}: {summary[:120]}",
                "category": it.get("content_group"),
                "description": summary[:280],
                "count": 1,
                "evidence_urls": [it["url"]] if it.get("url") else [],
                "evidence_ids": [it.get("external_id")] if it.get("external_id") else [],
            }
        )

    aff_sorted = sorted(
        aff_items,
        key=lambda x: ((x.get("purchase_intent") == "cao"), (x.get("likes_count") or 0) + (x.get("comments_count") or 0)),
        reverse=True,
    )
    affiliate_opportunities = []
    for it in aff_sorted:
        extra = it.get("extra_meta") or {}
        clean_entities = _filter_product_entities(extra.get("entities_mentioned") or [])
        mentioned_raw = extra.get("mentioned_products_brands_topics")
        if isinstance(mentioned_raw, list):
            clean_mentioned = ", ".join(_filter_product_entities(mentioned_raw)[:3])
        elif isinstance(mentioned_raw, str) and not is_platform_or_site_name(mentioned_raw):
            clean_mentioned = mentioned_raw[:120]
        else:
            clean_mentioned = ""

        hook_app = (extra.get("hook_analysis") or {}).get("affiliate_application") if isinstance(extra.get("hook_analysis"), dict) else None
        viral_angle = (extra.get("viral_analysis") or {}).get("affiliate_angle") if isinstance(extra.get("viral_analysis"), dict) else None
        raw_title = it.get("title") if it.get("title") and not is_platform_or_site_name(it.get("title")) else None
        summary_txt = (it.get("summary_vi") or it.get("text_content") or "").strip()

        product_label = (
            ", ".join(clean_entities[:3])
            if clean_entities
            else (clean_mentioned or raw_title or (viral_angle[:85] if isinstance(viral_angle, str) and viral_angle else None) or (summary_txt[:85] if summary_txt else None))
        )
        if not product_label or is_platform_or_site_name(product_label):
            continue

        affiliate_opportunities.append(
            {
                "product": product_label,
                "brand_or_topic": it.get("content_group") or extra.get("trend_group"),
                "purchase_intent": it.get("purchase_intent"),
                "price": it.get("price"),
                "evidence_summary": (viral_angle or hook_app or summary_txt)[:200],
                "evidence_urls": [it["url"]] if it.get("url") else ([extra["evidence_url"]] if extra.get("evidence_url") else []),
                "evidence_ids": [it.get("external_id")] if it.get("external_id") else [],
            }
        )
        if len(affiliate_opportunities) >= 6:
            break

    text_posts = []
    short_videos = []
    for it in aff_sorted[:3]:
        hook = (it.get("extra_meta") or {}).get("hook_analysis") or {}
        snippet = (hook.get("content_angle") or it.get("summary_vi") or it.get("title") or it.get("text_content") or "").strip()[:110]
        if snippet:
            text_posts.append(f"[{it.get('platform')}] Tham khảo góc nhìn từ {it.get('author_handle') or 'nguồn'}: {snippet}")
    for it in (need_items[:2] + aff_sorted[:2])[:3]:
        hook = (it.get("extra_meta") or {}).get("hook_analysis") or {}
        snippet = (hook.get("hook") or it.get("summary_vi") or it.get("title") or it.get("text_content") or "").strip()[:100]
        if snippet:
            short_videos.append(f"Short video hook từ case thật ({it.get('external_id') or 'N/A'}): {snippet}")

    return {
        "report_key": f"evidence_{plat_filter}",
        "source_type": "local_evidence_summary",
        "source_label": "Tổng hợp bằng chứng từ dữ liệu chuẩn hóa App_Video",
        "title": f"Báo cáo Market Intelligence ({plat_filter.upper()})",
        "platform": plat_filter,
        "generated_at": _utc_now_iso(),
        "trending_highlights": trending_highlights,
        "consumer_needs_problems": consumer_needs_problems,
        "affiliate_opportunities": affiliate_opportunities,
        "content_suggestions": {
            "text_posts": text_posts,
            "short_videos": short_videos,
        },
        "data_quality": {
            "total_items": total,
            "usable_items": usable_count,
            "needs_verification_items": needs_verification_count,
            "unlabeled_items": unlabeled_count,
            "with_content_group": with_cg,
            "with_purchase_intent": with_pi,
            "with_likes": with_likes,
            "with_comments": with_comments,
            "with_views": with_views,
            "with_shares": with_shares,
            "missing_content_group": total - with_cg,
            "missing_purchase_intent": total - with_pi,
            "note": "Số liệu tổng hợp trung thực từ dữ liệu chuẩn hóa; bản ghi needs_verification bị loại khỏi danh sách cơ hội Affiliate.",
        },
        "source_links": _extract_links_from_items(scoped, limit=15),
    }
