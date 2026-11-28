"""Domain constants, normalizers, and URL/metric parsers for Creator Crawler."""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse


CREATOR_SCHEMA_VERSION = "1.1"
SUPPORTED_CREATOR_SCHEMA_VERSIONS = ("1.0", CREATOR_SCHEMA_VERSION)
CREATOR_STOP_REASONS = (
    "target_reached", "profile_exhausted", "scan_limit_reached",
    "access_blocked", "captcha_required", "login_required", "rate_limited",
)
CREATOR_BLOCKED_STOP_REASONS = frozenset(CREATOR_STOP_REASONS[3:])
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
    try:
        parsed = urlparse(str(url or "").strip())
        host = parsed.hostname or ""
    except ValueError:
        return None
    if parsed.scheme not in ("https", "http") or parsed.username is not None:
        return None
    if host in ("douyin.com", "iesdouyin.com") or host.endswith((".douyin.com", ".iesdouyin.com")):
        return "douyin"
    if host in ("bilibili.com", "b23.tv") or host.endswith(".bilibili.com"):
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


def normalize_bilibili_secondary_id(value: Any) -> Optional[str]:
    """Normalize Bilibili secondary aid/BV alias consistently (e.g. `170001` -> `av170001`)."""
    s = str(value or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d+", s):
        return f"av{s}"
    if re.fullmatch(r"av\d+", s, flags=re.IGNORECASE):
        return s.lower()
    if re.fullmatch(r"BV[0-9A-Za-z]+", s, flags=re.IGNORECASE):
        return "BV" + s[2:]
    return s


def sanitize_job_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip any raw cookies, tokens, or credentials before persisting/returning job config."""
    if not isinstance(config, dict):
        return {}
    secret_keys = {
        "browser_cookies",
        "custom_cookies",
        "cookies",
        "cookie",
        "cookies_text",
        "token",
        "secret",
        "password",
        "authorization",
        "auth",
    }
    clean: Dict[str, Any] = {}
    has_custom_cookie = bool(str(config.get("custom_cookies") or config.get("cookies_text") or "").strip())
    browser_mode = str(config.get("browser_cookies") or "").strip()

    for k, v in config.items():
        kl = str(k).strip().lower()
        if kl in secret_keys or "cookie" in kl and kl != "cookie_credential_ref":
            continue
        clean[k] = v

    if "cookie_credential_ref" not in clean or not clean.get("cookie_credential_ref"):
        if has_custom_cookie:
            clean["cookie_credential_ref"] = "ephemeral_memory_only"
        elif browser_mode == "manual":
            clean["cookie_credential_ref"] = "manual_cookie_store"
        elif browser_mode:
            clean["cookie_credential_ref"] = f"browser_profile:{browser_mode[:24]}"
        else:
            clean["cookie_credential_ref"] = "none"
    return clean


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
        return int(value) if value >= 0 and value.is_integer() else None
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


def verify_local_mp4_output(video_path: Optional[str]) -> Tuple[bool, str]:
    """Verify a produced Reup MP4 file actually exists on disk, is non-empty, and has a valid container/stream."""
    import json as _json
    import os as _os
    import shutil as _shutil
    import subprocess as _subprocess

    path_str = str(video_path or "").strip()
    if not path_str:
        return False, "missing_output_path"
    if not _os.path.isfile(path_str):
        return False, f"output_file_not_found:{path_str}"
    try:
        size_bytes = _os.path.getsize(path_str)
    except OSError as exc:
        return False, f"output_stat_error:{exc}"
    if size_bytes < 512:
        return False, f"output_file_too_small:{size_bytes}"

    try:
        with open(path_str, "rb") as f:
            header = f.read(32)
    except OSError as exc:
        return False, f"output_read_error:{exc}"
    if b"ftyp" not in header:
        return False, "invalid_mp4_header"

    ffprobe_exe = _shutil.which("ffprobe")
    if not ffprobe_exe:
        try:
            import reup_pipeline as _reup_mod
            cand = _reup_mod.get_ffprobe_exe()
            if cand and (_shutil.which(cand) or _os.path.exists(cand)):
                ffprobe_exe = cand
        except Exception:
            ffprobe_exe = None

    if ffprobe_exe:
        try:
            proc = _subprocess.run(
                [ffprobe_exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path_str],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode != 0 or not proc.stdout:
                return False, f"ffprobe_failed:rc={proc.returncode}"
            meta = _json.loads(proc.stdout)
            dur = float((meta.get("format") or {}).get("duration") or 0.0)
            has_video = any(
                s.get("codec_type") == "video" and int(s.get("width") or 0) > 0 and int(s.get("height") or 0) > 0
                for s in (meta.get("streams") or [])
            )
            if not has_video or dur <= 0.0:
                return False, f"invalid_mp4_streams:has_video={has_video},duration={dur}"
        except Exception as exc:
            return False, f"ffprobe_exception:{exc}"

    return True, ""
