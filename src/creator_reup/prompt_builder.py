"""Muse prompt builder and ID generator for Creator Crawler (Douyin / Bilibili)."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .filters import resolve_filter_config
from .models import CREATOR_SCHEMA_VERSION, CREATOR_STOP_REASONS, detect_platform_from_url, normalize_platform, parse_profile_identifier


def generate_crawl_ids(now: Optional[datetime] = None) -> Tuple[str, str]:
    """Generate correlated `(request_id, crawl_id)` identifiers."""
    dt = now or datetime.now(timezone.utc)
    stamp = dt.strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"creq_{stamp}_{suffix}", f"ccrawl_{stamp}_{suffix}"


def build_creator_crawl_prompt(
    *,
    profile_url: str,
    platform: Optional[str] = None,
    requested_count: int = 20,
    filter_overrides: Optional[Dict[str, Any]] = None,
    drive_folder_id: Optional[str] = None,
    request_id: Optional[str] = None,
    crawl_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build structured Muse crawl request metadata and copy-ready prompt text."""
    clean_url = str(profile_url or "").strip()
    if not clean_url:
        raise ValueError("Thiếu profile URL của kênh Douyin/Bilibili.")

    plat = normalize_platform(platform) or detect_platform_from_url(clean_url)
    if plat not in ("douyin", "bilibili"):
        raise ValueError("Profile URL không thuộc Douyin hoặc Bilibili.")

    prof_info = parse_profile_identifier(clean_url, plat)
    creator_id = prof_info.get("creator_id") or ""
    count_val = max(1, min(int(requested_count or 20), 200))
    cfg = resolve_filter_config(plat, filter_overrides)

    if not request_id or not crawl_id:
        gen_req, gen_crawl = generate_crawl_ids()
        request_id = request_id or gen_req
        crawl_id = crawl_id or gen_crawl

    folder_note = (
        f"- Target Google Drive Folder ID: `{drive_folder_id}`\n"
        if drive_folder_id
        else "- Target Google Drive Folder: Lưu vào thư mục JSON kết quả Creator Crawler (hoặc gửi file JSON trực tiếp để Import thủ công vào tab Reup).\n"
    )

    if plat == "bilibili":
        threshold_lines = (
            f"- Điều kiện chính: `views >= {cfg['min_views']}` VÀ `likes >= {cfg['min_likes']}`.\n"
            f"- Thời lượng: `duration_seconds < {cfg['max_duration_seconds']}` giây (299s đạt, 300s loại).\n"
            f"- Độ mới: đăng trong `{cfg['max_age_days']}` ngày gần nhất (không tính ngày tương lai).\n"
            f"- Ngoại lệ video cũ (> {cfg['max_age_days']} ngày vẫn giữ nếu): "
            f"`views >= {cfg['old_video_exception_min_views']}` HOẶC `likes >= {cfg['old_video_exception_min_likes']}` "
            f"(CHỈ miễn giới hạn tuổi bài; vẫn BẮT BUỘC đạt ngưỡng cơ bản `views >= {cfg['min_views']} AND likes >= {cfg['min_likes']}` và `duration_seconds < {cfg['max_duration_seconds']}`)."
        )
    else:
        threshold_lines = (
            f"- Điều kiện chính: `likes >= {cfg['min_likes']}` (Douyin Web cho phép `views: null`; nếu chỉ có nhãn làm tròn giữ nguyên `likes: null` + `likes_display`).\n"
            f"- Thời lượng: `duration_seconds < {cfg['max_duration_seconds']}` giây (299s đạt, 300s loại).\n"
            f"- Độ mới: đăng trong `{cfg['max_age_days']}` ngày gần nhất (không tính ngày tương lai).\n"
            f"- Ngoại lệ video cũ (> {cfg['max_age_days']} ngày vẫn giữ nếu): "
            f"`likes >= {cfg['old_video_exception_min_likes']}` "
            f"(CHỈ miễn giới hạn tuổi bài; vẫn BẮT BUỘC `duration_seconds < {cfg['max_duration_seconds']}`)."
        )

    plat_title = "Douyin" if plat == "douyin" else "Bilibili"
    id_hint = creator_id or "<creator_id>"
    muse_filters = {
        "min_likes": cfg["min_likes"],
        "min_views": cfg["min_views"] if plat == "bilibili" else cfg["min_views"] or None,
        "max_duration_seconds": cfg["max_duration_seconds"],
        "published_within_days": cfg["max_age_days"],
        "old_video_exception": {
            "min_likes": cfg["old_video_exception_min_likes"],
            "min_views": cfg["old_video_exception_min_views"] if plat == "bilibili" else None,
            "logic": "OR" if plat == "bilibili" else "likes_only",
        },
        "target_count": count_val,
        "sort": "views desc, likes desc" if plat == "bilibili" else "likes_approx desc",
    }
    prompt_text = (
        f"[MUSE CREATOR CRAWLER — {plat_title.upper()}]\n"
        f"request_id: {request_id}\n"
        f"crawl_id: Muse tự sinh theo mẫu ccr_<YYYYMMDD>_<HHmm>_<platform>_<creator_id_rút_gọn>.\n"
        f"Mã nội bộ chỉ dùng cho bản 1.0 cũ, không đưa vào crawl_id của 1.1: {crawl_id}\n"
        f"platform: {plat}\n"
        f"profile_url: {clean_url}\n"
        f"target_count: {count_val}\n"
        f"{folder_note}\n"
        f"filters: {json.dumps(muse_filters, ensure_ascii=False)}\n"
        f"YÊU CẦU THU THẬP & LỌC VIDEO:\n"
        f"1. Mục tiêu là tìm ĐỦ {count_val} video ĐẠT CHUẨN từ trang creator `{clean_url}` (`qualified_count = {count_val}`). "
        f"`listed_count` (scanned_count trong bản cũ) và `qualified_count` là hai bộ đếm riêng.\n"
        f"2. Loại bỏ toàn bộ bài ảnh/chữ (`/note/`, `note_image_text`). Chỉ lấy `content_type: \"video\"`.\n"
        f"3. Mở chi tiết video để lấy chính xác `published_at`, `duration_seconds` (hoặc `MM:SS`), `likes`/`likes_display`, `views`, `comments`, `shares`, `thumbnail`, `video_url`.\n"
        f"{threshold_lines}\n"
        f"4. KHÔNG TỰ Ý HẠ NGƯỠNG LỌC. Ghi cùng `stop_reason` vào `crawl` và `stats`: {', '.join(CREATOR_STOP_REASONS)}. "
        f"Chỉ thu thập metadata bằng phương thức được phép. Dừng khi bị chặn hoặc cần đăng nhập/CAPTCHA.\n\n"
        f"QUY TẮC DỮ LIỆU & SCHEMA (BẮT BUỘC):\n"
        f"- Tuân thủ `CREATOR_CRAWL_OUTPUT_SCHEMA_v1` với `schema_version: \"{CREATOR_SCHEMA_VERSION}\"`.\n"
        f"- Ghi `request_id: \"{request_id}\"` và `crawl_id` do Muse sinh ở root JSON. Ghi bộ lọc thực tế vào `crawl.filters`.\n"
        f"- `stats` gồm `listed_count`, `enriched_count`, `notes_excluded`, `filter_excluded`, `filter_exclusion_reasons`, `qualified_count`, `target_count`, `duplicates_in_registry` (null nếu chưa kiểm tra) và `stop_reason`.\n"
        f"- Phân biệt `0` và `null`. Với nhãn `77.4K`, giữ `likes: null`, `likes_display: \"77.4K\"`, `likes_approx: 77400` và `metrics_precision: \"display_rounded\"`. Giá trị xấp xỉ chỉ dùng để lọc, xếp hạng.\n"
        f"- Đặt tên file JSON: `creator_crawl_{plat}_{id_hint}_<YYYYMMDD>_<HHmm>.json`."
    )

    return {
        "request_id": request_id,
        "crawl_id": crawl_id,
        "platform": plat,
        "profile_url": clean_url,
        "creator_id": creator_id or None,
        "requested_count": count_val,
        "filters": cfg,
        "drive_folder_id": drive_folder_id or None,
        "prompt_text": prompt_text,
    }
