"""Pure threshold evaluation, age-exception, verification, and sorting logic."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from .models import DEFAULT_CREATOR_FILTERS, normalize_platform, parse_published_at


def resolve_filter_config(
    platform: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge user filter overrides onto platform defaults cleanly."""
    plat = normalize_platform(platform) or "douyin"
    cfg = deepcopy(DEFAULT_CREATOR_FILTERS[plat])
    if not isinstance(overrides, dict):
        return cfg

    plat_block = overrides.get(plat) if isinstance(overrides.get(plat), dict) else overrides
    int_keys = (
        "min_likes",
        "min_views",
        "max_duration_seconds",
        "max_age_days",
        "old_video_exception_min_likes",
        "old_video_exception_min_views",
    )
    for key in int_keys:
        if key in plat_block:
            val = plat_block[key]
            if val is None or val == "":
                if "exception" in key:
                    cfg[key] = None
            else:
                try:
                    cfg[key] = max(0, int(val))
                except (TypeError, ValueError):
                    pass
    return cfg


def _is_near_threshold(value: Optional[int], threshold: Optional[int], ratio: float = 0.15) -> bool:
    if value is None or threshold is None or threshold <= 0:
        return False
    low = threshold * (1.0 - ratio)
    high = threshold * (1.0 + ratio)
    return low <= value <= high


def _parse_reference_datetime(reference_time: Any, platform: str) -> datetime:
    if isinstance(reference_time, datetime):
        return reference_time if reference_time.tzinfo else reference_time.replace(tzinfo=timezone.utc)
    if reference_time:
        iso, _ = parse_published_at(reference_time, platform=platform)
        if iso:
            return datetime.fromisoformat(iso)
    return datetime.now(timezone.utc)


def evaluate_video_filters(
    item: Dict[str, Any],
    *,
    platform: Optional[str] = None,
    filter_overrides: Optional[Dict[str, Any]] = None,
    reference_time: Any = None,
) -> Dict[str, Any]:
    """Evaluate a normalized video record against Phase 1 Douyin/Bilibili filter rules."""
    plat = normalize_platform(platform or item.get("source_platform")) or "douyin"
    cfg = resolve_filter_config(plat, filter_overrides)
    ref_dt = _parse_reference_datetime(reference_time, plat)

    effective_likes = item.get("effective_likes")
    if effective_likes is None:
        effective_likes = item.get("likes")
    effective_views = item.get("effective_views")
    if effective_views is None:
        effective_views = item.get("views")

    duration_seconds = item.get("duration_seconds")
    published_at = item.get("published_at")
    metrics_precision = str(item.get("metrics_precision") or "exact").lower()
    likes_is_rounded = metrics_precision == "display_rounded" or (
        item.get("likes") is None and item.get("likes_display") is not None
    )

    exclusion_reasons: List[str] = []
    verification_reasons: List[str] = []

    # 1. Metric thresholds
    min_likes = int(cfg.get("min_likes") or 0)
    min_views = int(cfg.get("min_views") or 0)

    if plat == "bilibili":
        if effective_views is None:
            exclusion_reasons.append("missing_views")
            verification_reasons.append("missing_views")
        elif effective_views < min_views:
            exclusion_reasons.append(f"views_below_min:{effective_views}<{min_views}")
        elif _is_near_threshold(effective_views, min_views) and item.get("views") is None:
            verification_reasons.append("rounded_views_near_threshold")

    if effective_likes is None:
        exclusion_reasons.append("missing_likes")
        verification_reasons.append("missing_likes")
    else:
        if effective_likes < min_likes:
            exclusion_reasons.append(f"likes_below_min:{effective_likes}<{min_likes}")
        if likes_is_rounded and _is_near_threshold(effective_likes, min_likes):
            verification_reasons.append("rounded_likes_near_threshold")

    if plat == "douyin" and min_views > 0:
        if effective_views is None:
            exclusion_reasons.append("missing_views")
            verification_reasons.append("missing_views")
        elif effective_views < min_views:
            exclusion_reasons.append(f"views_below_min:{effective_views}<{min_views}")

    # 2. Duration strictly < max_duration_seconds (default 300s)
    max_dur = int(cfg.get("max_duration_seconds") or 300)
    if duration_seconds is None:
        exclusion_reasons.append("missing_duration")
        verification_reasons.append("missing_duration")
    else:
        if duration_seconds == 0:
            verification_reasons.append("zero_duration_check")
        if duration_seconds >= max_dur:
            exclusion_reasons.append(f"duration_exceeds_limit:{duration_seconds}>={max_dur}")

    # 3. Recency <= max_age_days (default 365 days) OR platform old-video exception
    max_age_days = int(cfg.get("max_age_days") or 365)
    age_exception_applied = False
    if not published_at:
        exclusion_reasons.append("missing_published_at")
        verification_reasons.append("missing_published_at")
    else:
        try:
            pub_dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            age_delta = ref_dt - pub_dt
            if age_delta > timedelta(days=max_age_days):
                exc_likes = cfg.get("old_video_exception_min_likes")
                exc_views = cfg.get("old_video_exception_min_views")
                likes_exc_ok = (
                    exc_likes is not None
                    and effective_likes is not None
                    and effective_likes >= int(exc_likes)
                )
                views_exc_ok = (
                    plat == "bilibili"
                    and exc_views is not None
                    and effective_views is not None
                    and effective_views >= int(exc_views)
                )
                if likes_exc_ok or views_exc_ok:
                    age_exception_applied = True
                    if likes_exc_ok and likes_is_rounded and _is_near_threshold(effective_likes, exc_likes):
                        verification_reasons.append("rounded_likes_near_age_exception")
                else:
                    exclusion_reasons.append(f"age_exceeds_days:{age_delta.days}>{max_age_days}")
        except ValueError:
            exclusion_reasons.append("invalid_published_at")
            verification_reasons.append("invalid_published_at")

    passed_filter = len(exclusion_reasons) == 0
    needs_verification = len(verification_reasons) > 0
    if passed_filter and not needs_verification:
        filter_status = "passed"
    elif needs_verification and passed_filter:
        filter_status = "needs_verification"
    elif needs_verification and not passed_filter:
        filter_status = "needs_verification" if any(
            r in verification_reasons
            for r in ("missing_duration", "missing_published_at", "rounded_likes_near_threshold", "rounded_views_near_threshold")
        ) and not any(
            r.startswith(("likes_below_min", "views_below_min", "duration_exceeds_limit", "age_exceeds_days"))
            for r in exclusion_reasons
        ) else "filtered_out"
    else:
        filter_status = "filtered_out"

    evaluated = dict(item)
    evaluated.update(
        {
            "filter_status": filter_status,
            "passed_filter": passed_filter,
            "age_exception_applied": age_exception_applied,
            "exclusion_reasons": exclusion_reasons,
            "needs_verification": needs_verification,
            "verification_reasons": verification_reasons,
            "applied_filters": cfg,
        }
    )
    return evaluated


def sort_and_filter_videos(
    items: Iterable[Dict[str, Any]],
    *,
    sort_by: str = "likes_desc",
    filter_status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter evaluated items by status and sort by likes, views, or recency."""
    list_items = list(items)
    if filter_status and filter_status != "all":
        list_items = [it for it in list_items if it.get("filter_status") == filter_status]

    def _pub_ts(it: Dict[str, Any]) -> float:
        raw = it.get("published_at")
        if not raw:
            return 0.0
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            return 0.0

    key = str(sort_by or "likes_desc").strip().lower()
    if key == "views_desc":
        list_items.sort(
            key=lambda it: (
                int(it.get("effective_views") if it.get("effective_views") is not None else (it.get("views") or -1)),
                int(it.get("effective_likes") if it.get("effective_likes") is not None else (it.get("likes") or -1)),
                _pub_ts(it),
            ),
            reverse=True,
        )
    elif key == "recent_desc":
        list_items.sort(
            key=lambda it: (
                _pub_ts(it),
                int(it.get("effective_likes") if it.get("effective_likes") is not None else (it.get("likes") or -1)),
            ),
            reverse=True,
        )
    else:
        list_items.sort(
            key=lambda it: (
                int(it.get("effective_likes") if it.get("effective_likes") is not None else (it.get("likes") or -1)),
                int(it.get("effective_views") if it.get("effective_views") is not None else (it.get("views") or -1)),
                _pub_ts(it),
            ),
            reverse=True,
        )
    return list_items
