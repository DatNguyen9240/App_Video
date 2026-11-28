"""Canonical Threads Crawl Strategy 3.3 modes, topic presets, lookback, and exclusion helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


THREADS_CONTRACT_V32 = "3.2"
THREADS_CONTRACT_V33 = "3.3"
THREADS_SKILL_NAME = "threads_crawl"
THREADS_SKILL_VERSION = "1.2"
THREADS_QUALITY_POLICY_VERSION = "3.3-hot-only-r2-20261128"
THREADS_DEFAULT_LEASE_MINUTES_V33 = 60
THREADS_MAX_LEASE_RENEWALS_PER_SESSION = 2
THREADS_LEASE_RENEW_INCREMENT_MINUTES = 30

THREADS_QUALITY_POLICY_SPEC: Dict[str, Any] = {
    "policy_version": THREADS_QUALITY_POLICY_VERSION,
    "policy_mode": "hot_and_very_hot_only",
    "target_metric": "quality_qualified_count",
    "legacy_target_metric": "new_qualified_count",
    "emerging_counted_toward_target": False,
    "watchlist_counted_toward_target": False,
    "qualified_tiers": ["very_hot", "hot"],
    "traffic_content_requires_buyer_intent_or_affiliate": False,
    "requires_verified_views": True,
    "requires_verified_likes": True,
    "tiers": {
        "very_hot": "views >= 50000 OR likes >= 1000",
        "hot": "views > 5000 OR likes >= 200",
    },
}

THREADS_STRATEGY_VERSION = "3.3 (sửa đổi 28/11/2026)"
THREADS_DEFAULT_LOOKBACK_HOURS = 24
THREADS_ALLOWED_LOOKBACK_HOURS = (24, 48, 72, 168)

THREADS_CRAWL_MODES: List[Dict[str, Any]] = [
    {
        "id": "balanced",
        "label": "Toàn thị trường",
        "enabled": True,
        "requires_contract_update": False,
        "summary": "Tham khảo 40% Natural Viral, 40% Affiliate, 20% Market Trend (linh hoạt, không ép cứng)",
    },
    {
        "id": "natural_viral",
        "label": "Săn nội dung viral tự nhiên",
        "enabled": True,
        "requires_contract_update": False,
        "summary": "Ưu tiên bài creator nhỏ / người thường có lượt xem & lan truyền tự nhiên cao",
    },
    {
        "id": "affiliate",
        "label": "Tìm sản phẩm Affiliate",
        "enabled": True,
        "requires_contract_update": False,
        "summary": "Săn sản phẩm review chân thực, deal/voucher dễ gắn giỏ hàng Affiliate",
    },
    {
        "id": "buyer_intent",
        "label": "Tìm nhu cầu mua hàng",
        "enabled": True,
        "requires_contract_update": False,
        "summary": "Săn bài hỏi mua, xin tư vấn, tìm giải pháp & bình luận xin link",
    },
    {
        "id": "market_trend",
        "label": "Theo dõi xu hướng thị trường",
        "enabled": True,
        "requires_contract_update": False,
        "summary": "Theo dõi xu hướng mới đạt chuẩn Hot / Very Hot, slang & hành vi tiêu dùng",
    },
    {
        "id": "traffic_content",
        "label": "Săn content kéo Traffic",
        "enabled": True,
        "requires_contract_update": False,
        "summary": "Săn meme, chơi chữ, relatable, hot takes, POV kéo reach & follow (không cần Affiliate)",
    },
    {
        "id": "growth_tracking",
        "label": "Theo dõi tăng trưởng bài cũ",
        "enabled": False,
        "requires_contract_update": True,
        "disabled_reason": (
            "Data Contract V3.2 hiện lấy new_qualified_count làm chuẩn hoàn thành (requested_count) "
            "và chưa có API liệt kê bài lịch sử từ D1 Registry để đo lại. "
            "Cần nâng cấp Data Contract V3.2 + Custom Connector (/api/crawl/registry/targets) trước khi bật."
        ),
        "summary": "Đo lại bài lịch sử cần theo dõi tăng trưởng (chờ nâng cấp Data Contract V3.2 & danh sách mục tiêu D1)",
    },
]

THREADS_MODE_BY_ID: Dict[str, Dict[str, Any]] = {m["id"]: m for m in THREADS_CRAWL_MODES}
THREADS_MODE_ALIASES: Dict[str, str] = {
    "balanced": "balanced",
    "natural_viral": "natural_viral",
    "viral": "natural_viral",
    "affiliate": "affiliate",
    "aff": "affiliate",
    "buyer_intent": "buyer_intent",
    "buyer": "buyer_intent",
    "market_trend": "market_trend",
    "trend": "market_trend",
    "traffic_content": "traffic_content",
    "traffic": "traffic_content",
    "growth_tracking": "growth_tracking",
    "growth": "growth_tracking",
}

THREADS_TOPIC_PRESETS: List[Dict[str, Any]] = [
    # Commerce / Đời sống / Ngành hàng (14)
    {"id": "beauty", "label": "Mỹ phẩm & Làm đẹp", "group": "commerce_lifestyle", "keywords": ["skincare", "kem chống nắng", "son môi", "trị mụn", "review mỹ phẩm"]},
    {"id": "fashion", "label": "Thời trang", "group": "commerce_lifestyle", "keywords": ["phối đồ", "outfit đi làm", "local brand", "váy xinh", "giày dép"]},
    {"id": "home_living", "label": "Nhà cửa & Gia dụng", "group": "commerce_lifestyle", "keywords": ["decor phòng", "đồ gia dụng", "dọn nhà", "máy lọc không khí", "đồ bếp"]},
    {"id": "tech", "label": "Công nghệ", "group": "commerce_lifestyle", "keywords": ["đồ công nghệ", "tai nghe", "sạc dự phòng", "app hay", "mẹo iphone"]},
    {"id": "mom_baby", "label": "Mẹ & Bé", "group": "commerce_lifestyle", "keywords": ["đồ cho bé", "ăn dặm", "bỉm sữa", "chăm con", "mẹ bỉm review"]},
    {"id": "food_drink", "label": "Ăn uống", "group": "commerce_lifestyle", "keywords": ["đồ ăn vặt", "quán ngon", "cà phê", "matcha", "review đồ ăn"]},
    {"id": "health", "label": "Sức khỏe", "group": "commerce_lifestyle", "keywords": ["giảm cân", "ngủ ngon", "vitamin", "tập gym", "chăm sóc sức khỏe"]},
    {"id": "pets", "label": "Thú cưng", "group": "commerce_lifestyle", "keywords": ["nuôi mèo", "nuôi chó", "cát mèo", "hạt thú cưng", "chăm boss"]},
    {"id": "deals_vouchers", "label": "Săn deal & Voucher", "group": "commerce_lifestyle", "keywords": ["săn sale", "mã giảm giá", "deal hời", "shopee live", "giá tốt"]},
    {"id": "life_family", "label": "Đời sống & Gia đình", "group": "commerce_lifestyle", "keywords": ["tâm sự đời sống", "chuyện gia đình", "ở trọ", "tiết kiệm tiền", "cuộc sống"]},
    {"id": "entertainment_humor", "label": "Giải trí & Hài hước", "group": "commerce_lifestyle", "keywords": ["chuyện hài", "giải trí", "clip vui", "cười xỉu", "tin giải trí"]},
    {"id": "work_income", "label": "Công việc & Thu nhập", "group": "commerce_lifestyle", "keywords": ["đi làm", "kiếm thêm thu nhập", "freelance", "công sở", "chuyện nghề"]},
    {"id": "affiliate_tips", "label": "Kinh nghiệm Affiliate", "group": "commerce_lifestyle", "keywords": ["làm affiliate", "gắn giỏ hàng", "kéo đơn", "hoa hồng", "chia sẻ affiliate"]},
    {"id": "useful_tips", "label": "Mẹo hữu ích", "group": "commerce_lifestyle", "keywords": ["mẹo vặt", "mẹo hay mỗi ngày", "life hack", "kinh nghiệm thật"]},
    # Giải trí / Traffic (10)
    {"id": "meme_humor", "label": "Meme & Hài hước", "group": "traffic_entertainment", "keywords": ["meme hài", "ảnh chế", "cà khịa nhẹ", "dở khóc dở cười", "cười xỉu"]},
    {"id": "wordplay_twist", "label": "Chơi chữ & Plot Twist", "group": "traffic_entertainment", "keywords": ["chơi chữ", "bẻ lái", "plot twist", "tưởng vậy mà không phải"]},
    {"id": "random_thoughts", "label": "Random Thoughts", "group": "traffic_entertainment", "keywords": ["suy nghĩ vẩn vơ", "tự nhiên nghĩ tới", "chuyện nửa đêm", "tâm trạng vu vơ"]},
    {"id": "relatable_content", "label": "Relatable Content", "group": "traffic_entertainment", "keywords": ["giống tui ghê", "ai cũng từng", "đúng tim đen", "chuyện đi làm đi học"]},
    {"id": "hot_takes", "label": "Hot Takes", "group": "traffic_entertainment", "keywords": ["quan điểm cá nhân", "góc nhìn trái chiều", "nói thật lòng", "tranh luận văn minh"]},
    {"id": "childhood_nostalgia", "label": "Tuổi thơ & Hoài niệm", "group": "traffic_entertainment", "keywords": ["tuổi thơ 8x 9x", "hoài niệm", "ngày xưa", "ký ức học trò"]},
    {"id": "satire", "label": "Châm biếm", "group": "traffic_entertainment", "keywords": ["châm biếm hài hước", "đùa sâu cay", "hiện thực vui nhộn"]},
    {"id": "storytelling_pov", "label": "Storytelling & POV", "group": "traffic_entertainment", "keywords": ["pov", "kể chuyện thật", "nhật ký ngắn", "tình huống bất ngờ"]},
    {"id": "trend_remix", "label": "Trend Remix", "group": "traffic_entertainment", "keywords": ["bắt trend", "remix trend", "biến tấu trend", "trend việt"]},
    {"id": "reaction_entertainment", "label": "Reaction & Nội dung giải trí", "group": "traffic_entertainment", "keywords": ["reaction", "bình luận hài", "giải trí nhẹ", "xem gì hôm nay"]},
]

THREADS_TOPIC_BY_ID: Dict[str, Dict[str, Any]] = {t["id"]: t for t in THREADS_TOPIC_PRESETS}
THREADS_TOPIC_BY_LABEL_LOWER: Dict[str, Dict[str, Any]] = {t["label"].lower(): t for t in THREADS_TOPIC_PRESETS}

THREADS_EXCLUSION_DEFAULTS: Dict[str, bool] = {
    "exclude_celebrity": True,
    "exclude_fandom": True,
    "exclude_pure_ads": True,
    "vn_only": True,
}


def get_mode_label(mode_id: Optional[str]) -> Optional[str]:
    if not mode_id:
        return None
    key = str(mode_id).strip().lower()
    mapped = THREADS_MODE_ALIASES.get(key, key)
    item = THREADS_MODE_BY_ID.get(mapped)
    return item["label"] if item else str(mode_id).strip()


def normalize_topics_list(raw_topics: Any, *, strict: bool = False) -> Tuple[List[str], List[str]]:
    """Normalize topic IDs or labels into `(canonical_ids, canonical_labels)`."""
    if raw_topics is None or raw_topics == "":
        return [], []
    if isinstance(raw_topics, str):
        stripped = raw_topics.strip()
        if stripped.startswith("["):
            try:
                import json
                parsed = json.loads(stripped)
                raw_items = parsed if isinstance(parsed, list) else []
            except Exception:
                raw_items = [x.strip() for x in stripped.split(",")]
        else:
            raw_items = [x.strip() for x in stripped.replace(";", ",").split(",")]
    elif isinstance(raw_topics, (list, tuple, set)):
        raw_items = list(raw_topics)
    else:
        raw_items = []

    ids: List[str] = []
    labels: List[str] = []
    seen = set()
    for entry in raw_items:
        s = str(entry or "").strip()
        if not s:
            continue
        low = s.lower()
        found = THREADS_TOPIC_BY_ID.get(low) or THREADS_TOPIC_BY_LABEL_LOWER.get(low)
        if not found:
            if strict:
                raise ValueError(f"Chủ đề Threads không hợp lệ: '{s}'")
            # Preserve unknown historical label as-is when reading legacy files non-strictly
            if low not in seen:
                seen.add(low)
                ids.append(s)
                labels.append(s)
            continue
        if found["id"] not in seen:
            seen.add(found["id"])
            ids.append(found["id"])
            labels.append(found["label"])
    return ids, labels


def validate_threads_strategy_options(raw_body: Dict[str, Any], opts: Dict[str, Any]) -> Dict[str, Any]:
    """Validate & normalize Threads Crawl Strategy options from UI/API body."""
    out = dict(opts or {})

    raw_mode = out.get("crawl_mode")
    if raw_mode is None:
        raw_mode = raw_body.get("crawl_mode")
    mode_key = str(raw_mode or "balanced").strip().lower()
    mode_id = THREADS_MODE_ALIASES.get(mode_key, mode_key)
    mode_obj = THREADS_MODE_BY_ID.get(mode_id)
    if not mode_obj:
        raise ValueError(f"crawl_mode không hợp lệ: '{raw_mode}'.")
    if not mode_obj.get("enabled", True):
        raise ValueError(mode_obj.get("disabled_reason") or f"Mode '{mode_id}' chưa được bật.")
    out["crawl_mode"] = mode_id

    raw_topics = out.get("topics")
    if raw_topics is None and "topics" in raw_body:
        raw_topics = raw_body.get("topics")
    topic_ids, _ = normalize_topics_list(raw_topics, strict=True)
    out["topics"] = topic_ids

    raw_lookback = out.get("lookback_hours")
    if raw_lookback is None and "lookback_hours" in raw_body:
        raw_lookback = raw_body.get("lookback_hours")
    if raw_lookback is None or raw_lookback == "":
        lb_int = THREADS_DEFAULT_LOOKBACK_HOURS
    else:
        if isinstance(raw_lookback, bool):
            raise ValueError("lookback_hours phải là 24, 48, 72 hoặc 168.")
        s_lb = str(raw_lookback).strip().lower()
        if s_lb in ("7d", "7 ngày", "7ngay"):
            lb_int = 168
        else:
            try:
                lb_int = int(s_lb.rstrip("h"))
            except (TypeError, ValueError) as exc:
                raise ValueError("lookback_hours phải là 24, 48, 72 hoặc 168.") from exc
        if lb_int not in THREADS_ALLOWED_LOOKBACK_HOURS:
            raise ValueError("lookback_hours chỉ hỗ trợ: 24h, 48h, 72h hoặc 168h (7 ngày).")
    out["lookback_hours"] = lb_int

    raw_excl = out.get("exclusions") if isinstance(out.get("exclusions"), dict) else raw_body.get("exclusions")
    excl_src = raw_excl if isinstance(raw_excl, dict) else {}
    exclusions = {}
    for k, def_val in THREADS_EXCLUSION_DEFAULTS.items():
        v = excl_src.get(k, out.get(k, def_val))
        exclusions[k] = bool(v) if isinstance(v, bool) else def_val
    out["exclusions"] = exclusions
    out["strategy_version"] = THREADS_STRATEGY_VERSION
    return out


def get_crawl_config_payload(helper_compatibility: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return canonical config dictionary for Crawl Center UI & `/api/remote/crawl_config`."""
    return {
        "strategy_version": THREADS_STRATEGY_VERSION,
        "contract_version": THREADS_CONTRACT_V33,
        "legacy_contract_version": THREADS_CONTRACT_V32,
        "skill_name": THREADS_SKILL_NAME,
        "skill_version": THREADS_SKILL_VERSION,
        "quality_policy_version": THREADS_QUALITY_POLICY_VERSION,
        "quality_policy": THREADS_QUALITY_POLICY_SPEC,
        "hot_only": True,
        "qualified_tiers": ["very_hot", "hot"],
        "default_mode": "balanced",
        "default_lookback_hours": THREADS_DEFAULT_LOOKBACK_HOURS,
        "internal_lease_minutes": THREADS_DEFAULT_LEASE_MINUTES_V33,
        "max_lease_renewals_per_session": THREADS_MAX_LEASE_RENEWALS_PER_SESSION,
        "lease_renew_minutes": THREADS_LEASE_RENEW_INCREMENT_MINUTES,
        "helper_compatibility": helper_compatibility,
        "modes": THREADS_CRAWL_MODES,
        "topics": THREADS_TOPIC_PRESETS,
        "lookback_options": [
            {"hours": 24, "label": "24h (mặc định, mở rộng tối đa 48h)"},
            {"hours": 48, "label": "48h"},
            {"hours": 72, "label": "72h (mở rộng rõ ràng)"},
            {"hours": 168, "label": "7 ngày / 168h (mở rộng rõ ràng)"},
        ],
        "exclusion_defaults": dict(THREADS_EXCLUSION_DEFAULTS),
    }
