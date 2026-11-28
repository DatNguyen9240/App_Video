"""Domain constants, normalizers, and URL/metric parsers for Creator Crawler."""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse


CREATOR_SCHEMA_VERSION = "1.0"
SUPPORTED_PLATFORMS = ("douyin", "bilibili")

RIGHTS_STATUSES = (
    "pending_review",
    "approved_for_reup",
    "rejected",
    "needs_verification",
)

DEDUP_STATUSES = (
    "new",
    "duplicate_processed",
    "duplicate_in_queue",
    "duplicate_external",
)

FILTER_STATUSES = (
    "passed",
    "filtered_out",
    "needs_verification",
)

DEFAULT_CREATOR_FILTERS: Dict[str, Dict[str, Any]] = {
    "douyin": {
        "min_likes": 5000,
        "min_views": 0,
        "max_duration_seconds": 300,
        "max_age_days": 365,
        "old_video_exception_min_likes": 50000,
        "old_video_exception_min_views": None,
    },
    "bilibili": {
        "min_views": 50000,
        "min_likes": 5000,
        "max_duration_seconds": 300,
        "max_age_days": 365,
        "old_video_exception_min_views": 1000000,
        "old_video_exception_min_likes": 50000,
    },
}

_CST_TZ = timezone(timedelta(hours=8))

_MARKET_FILENAME_PREFIXES = (
    "threads_trend",
    "tiktok_crawl",
    "shopee_crawl",
    "trend_report",
    "daily_report",
    "registry_",
    "manifest",
)

_CREATOR_FILENAME_HINTS = (
    "creator_crawl",
    "creator_crawler",
    "douyin_creator",
    "bilibili_creator",
)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format with trailing Z."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_platform(value: Any) -> Optional[str]:
    """Normalize supported creator crawler platform names."""
    s = str(value or "").strip().lower()
    if s in ("douyin", "dy"):
        return "douyin"
    if s in ("bilibili", "bili"):
        return "bilibili"
    return None


def detect_platform_from_url(url: str) -> Optional[str]:
    """Detect Douyin or Bilibili platform from a profile or video URL."""
    s = str(url or "").strip().lower()
    if not s:
        return None
    if "douyin.com" in s or "iesdouyin.com" in s:
        return "douyin"
    if "bilibili.com" in s or "b23.tv" in s:
        return "bilibili"
    return None


def parse_profile_identifier(profile_url: str, platform: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Extract canonical profile ID (`sec_uid` or Bilibili `mid`) from profile URL."""
    raw = str(profile_url or "").strip()
    plat = normalize_platform(platform) or detect_platform_from_url(raw)
    if not raw:
        return {"platform": plat, "creator_id": None}

    parsed = urlparse(raw)
    path = parsed.path or ""
    if plat == "douyin":
        m = re.search(r"/user/([A-Za-z0-9_\-]+)", path)
        return {"platform": "douyin", "creator_id": m.group(1) if m else None}
    if plat == "bilibili":
        m = re.search(r"/(?:space/)?(\d+)(?:/|$)", path)
        if parsed.netloc.lower().startswith("space.bilibili.com"):
            m_space = re.search(r"^/(\d+)", path)
            return {"platform": "bilibili", "creator_id": m_space.group(1) if m_space else None}
        return {"platform": "bilibili", "creator_id": m.group(1) if m else None}
    return {"platform": plat, "creator_id": None}


def extract_video_id_from_url(video_url: str, platform: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Extract `(url_kind, video_id)` from Douyin or Bilibili video/note URL."""
    raw = str(video_url or "").strip()
    if not raw:
        return None, None
    plat = normalize_platform(platform) or detect_platform_from_url(raw)
    parsed = urlparse(raw)
    path = parsed.path or ""
    qs = parse_qs(parsed.query or "")

    if plat == "douyin" or "douyin.com" in raw.lower():
        m_note = re.search(r"/note/(\d+)", path)
        if m_note:
            return "note", m_note.group(1)
        m_vid = re.search(r"/video/(\d+)", path)
        if m_vid:
            return "video", m_vid.group(1)
        modal_vals = qs.get("modal_id") or []
        for mv in modal_vals:
            if re.fullmatch(r"\d+", str(mv).strip()):
                return "video", str(mv).strip()
        return None, None

    if plat == "bilibili" or "bilibili.com" in raw.lower():
        m_bv = re.search(r"/video/(BV[0-9A-Za-z]+)", path, flags=re.IGNORECASE)
        if m_bv:
            bvid = m_bv.group(1)
            return "video", "BV" + bvid[2:] if bvid.lower().startswith("bv") else bvid
        m_av = re.search(r"/video/(av\d+)", path, flags=re.IGNORECASE)
        if m_av:
            return "video", m_av.group(1).lower()
        return None, None

    return None, None


def canonicalize_video_url(
    video_url: str,
    platform: Optional[str] = None,
    video_id: Optional[str] = None,
) -> str:
    """Return canonical deduplicated URL stripped of tracking parameters."""
    raw = str(video_url or "").strip()
    plat = normalize_platform(platform) or detect_platform_from_url(raw)
    kind, extracted_id = extract_video_id_from_url(raw, plat)
    vid = extracted_id or (str(video_id).strip() if video_id else "")

    if plat == "douyin" and vid:
        prefix = "note" if kind == "note" else "video"
        return f"https://www.douyin.com/{prefix}/{vid}"
    if plat == "bilibili" and vid:
        return f"https://www.bilibili.com/video/{vid}"
    if raw:
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    return raw


def parse_duration_seconds(value: Any) -> Optional[int]:
    """Parse integer duration or `MM:SS` / `HH:MM:SS` strings while keeping `0 != None`."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value < 0:
            return None
        return int(round(value))

    s = str(value).strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        if len(parts) in (2, 3):
            try:
                nums = [int(p.strip()) for p in parts]
                if any(n < 0 for n in nums):
                    return None
                if len(nums) == 2:
                    return nums[0] * 60 + nums[1]
                return nums[0] * 3600 + nums[1] * 60 + nums[2]
            except ValueError:
                return None

    m_sec = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|giây)?", s, flags=re.IGNORECASE)
    if m_sec:
        return int(round(float(m_sec.group(1))))
    return None


def parse_display_count(value: Any) -> Optional[int]:
    """Parse display count strings like `77.4K`, `1.9亿`, `2.5万` without altering exact fields."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value)) if value >= 0 else None

    s = str(value).strip().replace(",", "").replace(" ", "")
    if not s or s.lower() in ("null", "none", "-", "--", "n/a"):
        return None

    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(k|m|b|w|万|亿)?", s, flags=re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    mult = {
        "": 1,
        "k": 1_000,
        "w": 10_000,
        "万": 10_000,
        "m": 1_000_000,
        "亿": 100_000_000,
        "b": 1_000_000_000,
    }[unit]
    return int(round(num * mult))


def coerce_exact_int(value: Any) -> Optional[int]:
    """Coerce an exact JSON numeric metric while strictly distinguishing `0` from `None`."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(round(value)) if value >= 0 else None
    s = str(value).strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    return None


def parse_published_at(
    raw_value: Any,
    platform: str,
    raw_display: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Parse timestamp (defaulting naive Douyin/Bilibili times to +08:00) and keep display text."""
    display = str(raw_display).strip() if raw_display not in (None, "") else None
    if raw_value in (None, ""):
        if not display:
            return None, None
        raw_value = display

    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        ts = float(raw_value)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        if ts > 1_000_000_000:
            dt = datetime.fromtimestamp(ts, tz=_CST_TZ).replace(microsecond=0)
            return dt.isoformat(), display or str(raw_value)
        return None, display or str(raw_value)

    text = str(raw_value).strip()
    if not display:
        display = text
    if not text:
        return None, None

    iso_candidate = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_CST_TZ)
        return dt.replace(microsecond=0).isoformat(), display
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=_CST_TZ)
            return dt.replace(microsecond=0).isoformat(), display
        except ValueError:
            continue

    return None, display


def is_market_filename(file_name: str) -> bool:
    """Return True when filename clearly belongs to Market Intelligence."""
    lower = str(file_name or "").strip().lower()
    if not lower:
        return False
    return any(lower.startswith(prefix) or f"/{prefix}" in lower for prefix in _MARKET_FILENAME_PREFIXES)


def is_creator_crawler_file_payload(
    payload: Any,
    file_name: str = "",
    folder_path: str = "",
) -> bool:
    """Identify whether a parsed JSON payload belongs strictly to Creator Crawler."""
    if not isinstance(payload, dict):
        return False
    if is_market_filename(file_name):
        return False

    # Reject Threads / Market Intelligence payloads explicitly
    if isinstance(payload.get("summary"), dict) and isinstance(payload.get("records"), list):
        return False
    if str(payload.get("platform") or "").strip().lower() == "threads":
        return False

    plat = normalize_platform(payload.get("platform"))
    if plat in SUPPORTED_PLATFORMS:
        if isinstance(payload.get("creator"), dict) and isinstance(payload.get("items"), list):
            return True
        if isinstance(payload.get("creators"), list):
            return True

    lower_name = str(file_name or "").strip().lower()
    lower_folder = str(folder_path or "").strip().lower()
    if any(h in lower_name or h in lower_folder for h in _CREATOR_FILENAME_HINTS):
        if isinstance(payload.get("items"), list) or isinstance(payload.get("creators"), list):
            return True
    return False
