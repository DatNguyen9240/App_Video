"""
Standardized Muse Market Analysis Report Normalizer & Evidence Builder (Data Contract V2).

Normalizes Muse AI report JSONs (across POC 001 signals, Pilot 001, E2E V1,
and E2E corrected_v2 report schemas) into a canonical 5-section structure without
fabricating absent data or leaking platform/website names as products:
  1. trending_highlights        (Xu hướng nổi bật)
  2. consumer_needs_problems    (Nhu cầu, vấn đề người tiêu dùng)
  3. affiliate_opportunities    (Sản phẩm Affiliate tiềm năng + bằng chứng — chỉ bản ghi đủ điều kiện)
  4. content_suggestions        (Gợi ý text content & short video)
  5. data_quality + source_links (Chất lượng dữ liệu, tách technical_status vs data_quality_status & link nguồn)
"""

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


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
    """Return True ONLY if raw JSON payload is a real Muse report or POC signal payload (never audit/strategy/contract)."""
    if not isinstance(raw_obj, dict):
        return False
    # Never treat data_contract, crawl_strategy, or quality audit documents as a market analysis report
    if "contract" in raw_obj or "audit" in raw_obj or "strategy" in raw_obj:
        return False
    report_keys = {
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


def normalize_muse_report(raw_obj: Dict[str, Any], file_name: Optional[str] = None) -> Dict[str, Any]:
    """Normalize explicit Muse reports (POC, Pilot, E2E V1, E2E corrected_v2) into canonical 5 sections."""
    src = raw_obj
    for container_key in ("report", "analysis", "market_report"):
        if isinstance(raw_obj.get(container_key), dict):
            src = raw_obj[container_key]
            break

    fname_lower = (file_name or "").lower()
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
    totals = src.get("totals") if isinstance(src.get("totals"), dict) else {}

    # 1. Trending highlights
    raw_trends = (
        src.get("trending_highlights")
        or src.get("trends")
        or src.get("notable_signals_in_data")
        or src.get("opportunity_ranking")
        or []
    )
    trending_highlights: List[Dict[str, Any]] = []
    for t in _as_list(raw_trends):
        if isinstance(t, dict):
            trending_highlights.append(
                {
                    "title": t.get("title") or t.get("opportunity") or t.get("topic") or t.get("summary") or str(t),
                    "platform": t.get("platform") or platform,
                    "content_group": t.get("content_group") or t.get("type"),
                    "count": t.get("count") or t.get("rank"),
                    "confidence": t.get("confidence"),
                    "basis": t.get("basis"),
                    "evidence_urls": _as_list(t.get("evidence_urls") or t.get("urls") or t.get("sources")),
                    "evidence_ids": _as_list(t.get("evidence_ids") or t.get("post_ids")),
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

    # 2. Consumer needs & problems
    raw_needs = (
        src.get("consumer_needs_problems")
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
                    "evidence_urls": _as_list(n.get("evidence_urls") or n.get("urls") or n.get("sources")),
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
                    "evidence_urls": [],
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
        src.get("affiliate_opportunities")
        or src.get("affiliate_product_groups")
        or src.get("affiliate_products")
        or src.get("products")
        or []
    )
    affiliate_opportunities: List[Dict[str, Any]] = []
    for a in _as_list(raw_aff):
        if isinstance(a, dict):
            prod = a.get("product") or a.get("group") or a.get("name") or a.get("title")
            if is_platform_or_site_name(prod):
                continue
            affiliate_opportunities.append(
                {
                    "product": prod,
                    "brand_or_topic": a.get("brand_or_topic") or a.get("brand") or a.get("type"),
                    "purchase_intent": a.get("purchase_intent") or a.get("evidence_strength"),
                    "price": a.get("price"),
                    "evidence_summary": a.get("evidence_summary") or a.get("evidence") or a.get("reason"),
                    "evidence_urls": _as_list(a.get("evidence_urls") or a.get("url") or a.get("urls") or a.get("sources")),
                    "evidence_ids": _as_list(a.get("evidence_ids") or a.get("post_ids") or a.get("post_id")),
                }
            )

    # 4. Content suggestions (text + short video)
    raw_cs = src.get("content_suggestions") or {}
    if isinstance(raw_cs, list):
        text_posts = [str(x) for x in raw_cs if x]
        short_videos: List[Any] = []
    elif isinstance(raw_cs, dict):
        text_posts = _as_list(raw_cs.get("text_posts") or raw_cs.get("text"))
        short_videos = _as_list(raw_cs.get("short_videos") or raw_cs.get("videos") or raw_cs.get("short_video"))
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

    limitations = src.get("data_quality_and_limitations")
    limitation_note = "; ".join(str(x) for x in limitations if x) if isinstance(limitations, list) and limitations else None
    note_text = raw_dq.get("note") or src.get("method_note") or limitation_note or raw_obj.get("notes") or None

    data_quality = {
        "total_items": raw_dq.get("total_items") or totals.get("records") or source_run.get("records") or raw_obj.get("record_count") or len(posts) or None,
        "business_usable_records": totals.get("business_usable_records"),
        "needs_verification_records": totals.get("needs_verification_records"),
        "technical_status": src.get("technical_status") or raw_obj.get("technical_status"),
        "data_quality_status": src.get("data_quality_status") or raw_obj.get("data_quality_status"),
        "duplicates_skipped": raw_dq.get("duplicates_skipped") if "duplicates_skipped" in raw_dq else raw_obj.get("duplicates_skipped"),
        "verification": raw_obj.get("verification") if isinstance(raw_obj.get("verification"), dict) else None,
        "missing_sections": missing_sections,
        "note": note_text,
    }

    run_id = src.get("corrects_report") or source_run.get("run_id") or raw_obj.get("run_id") or file_name or title
    report_key_src = f"{raw_obj.get('run_id') or run_id}:{platform}"
    report_key = hashlib.sha256(report_key_src.encode("utf-8")).hexdigest()[:24]

    return {
        "report_key": report_key,
        "source_type": "muse_report_json",
        "source_label": f"Muse Report ({file_name or 'Drive JSON'})",
        "file_name": file_name,
        "title": str(title),
        "platform": platform,
        "is_corrected": is_corrected,
        "corrects_report": raw_obj.get("corrects_report"),
        "source_priority": 2 if is_corrected else 1,
        "request_id": raw_obj.get("request_id"),
        "run_id": source_run.get("run_id") or raw_obj.get("run_id"),
        "technical_status": data_quality["technical_status"],
        "data_quality_status": data_quality["data_quality_status"],
        "business_usable_records": data_quality["business_usable_records"],
        "totals": totals,
        "platform_results": platform_results,
        "telegram_summary": src.get("telegram_summary") or raw_obj.get("telegram_summary"),
        "generated_at": raw_obj.get("ended_at") or raw_obj.get("collected_at") or _utc_now_iso(),
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

        # Gate strictly on usable/qualified records for Affiliate opportunities
        is_needs_verif = dq == "needs_verification"
        is_shopee_ineligible = it.get("platform") == "shopee" and extra.get("affiliate_eligible") is False
        is_tiktok_irrelevant = it.get("platform") == "tiktok" and extra.get("affiliate_relevance") in ("khong_ro", "khong_lien_quan")
        if not is_needs_verif and not is_shopee_ineligible and not is_tiktok_irrelevant:
            if pi == "cao" or cg in ("deal_voucher", "review_so_sanh", "san_pham_quan_tam", "hoi_mua_xin_link"):
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
        raw_title = it.get("title") if it.get("title") and not is_platform_or_site_name(it.get("title")) else None
        summary_txt = (it.get("summary_vi") or it.get("text_content") or "").strip()

        product_label = (
            ", ".join(clean_entities[:3])
            if clean_entities
            else (clean_mentioned or raw_title or (summary_txt[:85] if summary_txt else None))
        )
        if not product_label or is_platform_or_site_name(product_label):
            continue

        affiliate_opportunities.append(
            {
                "product": product_label,
                "brand_or_topic": it.get("content_group"),
                "purchase_intent": it.get("purchase_intent"),
                "price": it.get("price"),
                "evidence_summary": (hook_app or summary_txt)[:200],
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
