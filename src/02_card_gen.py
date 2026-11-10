"""
MODULE 2: RENDER THẺ THREADS (Authentic Threads Card Generator)
================================================================
Chức năng:
  - Render thẻ bài đăng (Post card) & bình luận (Comment card) chuẩn Threads 100%:
    + Header chuẩn: Avatar tròn + Username + Tích xanh + Thời gian + Logo @threads
      TẤT CẢ CĂN THẲNG HÀNG TRỤC NGANG HOÀN HẢO (Pixel-Perfect Centerline).
    + Action Bar chuẩn Threads: Thả tim ♡, Bình luận 🗨, Đăng lại 🔁, Chia sẻ ✈
      Từng icon vẽ chuẩn vector và căn thẳng tuyệt đối trên cùng trục ngang với số liệu.
    + Thẻ bo góc mềm mại, đổ bóng 3D, nền trắng tinh khôi chuẩn Reels/Shorts.
"""

import os
import sys
import math
import random
import hashlib
from PIL import Image, ImageDraw, ImageFont

SRC_DIR      = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC_DIR)
import config as config_mod

# ─── Cấu hình kích thước & Thư mục (Đọc từ config/director_config.json) ─────────

def get_card_layout_cfg() -> dict:
    try:
        return config_mod.load_config().get("visual_layout", {})
    except Exception:
        return {}

_layout_cfg   = get_card_layout_cfg()
CARD_W        = _layout_cfg.get("card_width", 960)   # Chiều rộng card (px)
PAD           = 40             # Padding viền card
AVATAR_SIZE   = 64             # Đường kính avatar
RADIUS        = 24             # Bán kính bo góc card
SHADOW_OFFSET = 6              # Độ lệch đổ bóng 3D
ICON_SIZE     = 24             # Kích thước chuẩn icon action bar

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR   = os.path.join(BASE_DIR, "assets")
AVATARS_DIR  = os.path.join(ASSETS_DIR, "avatars")
FONT_DIR     = r"C:\Windows\Fonts"

# ─── Cấu hình màu sắc ─────────────────────────────────────────────────────────

def get_card_theme() -> str:
    theme = os.environ.get("CARD_THEME", "").strip().lower()
    env_file = os.path.join(BASE_DIR, ".env")
    if not theme and os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("CARD_THEME="):
                        theme = line.split("=", 1)[1].strip().lower()
        except Exception:
            pass
    return theme if theme in ["light", "dark"] else "light"


THEMES = {
    "light": {
        "card_bg":       (255, 255, 255),
        "card_outline":  (226, 230, 236, 255),
        "shadow":        (0,   0,   0,   30),
        "text_primary":  (18,  18,  18),
        "text_username": (0,   0,   0),
        "text_meta":     (120, 120, 120),
        "text_reply":    (110, 110, 115),
        "action_icon":   (40,  40,  40),
        "action_text":   (55,  55,  55),
        "logo_color":    (0,   0,   0),
    },
    "dark": {
        "card_bg":       (22,  22,  24),
        "card_outline":  (55,  58,  66,  255),
        "shadow":        (0,   0,   0,   90),
        "text_primary":  (242, 242, 245),
        "text_username": (255, 255, 255),
        "text_meta":     (140, 140, 145),
        "text_reply":    (145, 145, 150),
        "action_icon":   (200, 200, 205),
        "action_text":   (190, 190, 195),
        "logo_color":    (255, 255, 255),
    },
    "glass": {
        "card_bg":       (15,  23,  42),
        "card_outline":  (99,  102, 241, 200),
        "shadow":        (0,   0,   0,   120),
        "text_primary":  (248, 250, 252),
        "text_username": (255, 255, 255),
        "text_meta":     (148, 163, 184),
        "text_reply":    (165, 180, 252),
        "action_icon":   (199, 210, 254),
        "action_text":   (224, 231, 255),
        "logo_color":    (255, 255, 255),
    },
    "cyber_neon": {
        "card_bg":       (10,  10,  18),
        "card_outline":  (236, 72,  153, 220),
        "shadow":        (236, 72,  153, 60),
        "text_primary":  (255, 255, 255),
        "text_username": (34,  211, 238),
        "text_meta":     (148, 163, 184),
        "text_reply":    (244, 114, 182),
        "action_icon":   (236, 72,  153),
        "action_text":   (255, 255, 255),
        "logo_color":    (255, 255, 255),
    }
}

VERIFIED_BLUE = (0, 149, 246)


# ─── Load Typography ──────────────────────────────────────────────────────────

def _load_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    path = os.path.join(FONT_DIR, font_name)
    if os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()

FONT_USERNAME = _load_font("segoeuib.ttf", 30)   # Username đậm
FONT_TIME     = _load_font("segoeui.ttf",  24)   # Thời gian (12 giờ, 2 giờ)
FONT_LOGO     = _load_font("segoeuib.ttf", 30)   # Logo @threads
FONT_BODY     = _load_font("segoeui.ttf",  34)   # Nội dung bài viết
FONT_BODY_CMT = _load_font("segoeui.ttf",  33)   # Nội dung bình luận
FONT_ACTION   = _load_font("segoeui.ttf",  23)   # Số đếm tương tác
FONT_REPLY    = _load_font("segoeui.ttf",  22)   # "Trả lời @tác_giả"


# ─── Hàm tiện ích xử lý text & số liệu ────────────────────────────────────────

def format_metric(n: int) -> str:
    """Format số lượng tương tác kiểu Threads: 21,1K | 4,2K | 850"""
    if n >= 1000:
        val = n / 1000.0
        return f"{val:.1f}".replace(".", ",") + "K"
    return str(max(1, n))


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw) -> list[str]:
    words = text.split()
    lines = []
    current = ""
    for w in words:
        test = (current + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


# ─── Vector Icon Generator Chuẩn Trục Tâm 100% ────────────────────────────────

def get_action_icon_img(kind: str, size: int = ICON_SIZE,
                        color: tuple = (40, 40, 40),
                        bg_fill: tuple = (255, 255, 255)) -> Image.Image:
    """
    Vẽ icon vector siêu sắc nét (supersampled 2x) và căn đúng tâm (size x size).
    Đảm bảo 100% không lệch pixel so với văn bản bên cạnh.
    """
    S = size * 2
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = 3

    if kind == "heart":
        pts = []
        for deg in range(0, 360, 3):
            t = math.radians(deg)
            hx = 16 * (math.sin(t) ** 3)
            hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
            pts.append((hx, hy))
        min_x, max_x = min(p[0] for p in pts), max(p[0] for p in pts)
        min_y, max_y = min(p[1] for p in pts), max(p[1] for p in pts)
        orig_w = max_x - min_x
        target_w = S * 0.82
        scale = target_w / orig_w
        cx, cy = S / 2, S / 2
        norm_pts = [
            (cx + (hx - (min_x + max_x) / 2) * scale, cy + (hy - (min_y + max_y) / 2) * scale)
            for hx, hy in pts
        ]
        d.polygon(norm_pts, outline=color, width=w)

    elif kind == "comment":
        bx1, by1 = S * 0.10, S * 0.12
        bx2, by2 = S * 0.90, S * 0.78
        d.ellipse([bx1, by1, bx2, by2], outline=color, width=w)
        tail = [
            (S * 0.28, S * 0.70),
            (S * 0.10, S * 0.90),
            (S * 0.46, S * 0.76)
        ]
        d.polygon(tail, fill=bg_fill, outline=color, width=w)

    elif kind == "repost":
        cx, cy = S / 2, S / 2
        r = S * 0.33
        d.arc([cx - r, cy - r, cx + r, cy + r], start=205, end=335, fill=color, width=w)
        d.arc([cx - r, cy - r, cx + r, cy + r], start=25, end=155, fill=color, width=w)
        d.polygon([(cx + r - 3, cy - 9), (cx + r + 10, cy + 2), (cx + r - 11, cy + 5)], fill=color)
        d.polygon([(cx - r + 3, cy + 9), (cx - r - 10, cy - 2), (cx - r + 11, cy - 5)], fill=color)

    elif kind == "share":
        pts = [
            (S * 0.12, S * 0.14),
            (S * 0.88, S * 0.50),
            (S * 0.12, S * 0.86),
            (S * 0.40, S * 0.50)
        ]
        d.polygon(pts, outline=color, width=w)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def get_verified_badge_img(size: int = 22) -> Image.Image:
    """Tạo badge tích xanh Threads căn tâm chuẩn."""
    S = size * 2
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, S - 1, S - 1], fill=VERIFIED_BLUE)
    cx, cy = S // 2, S // 2
    pts = [(int(S * 0.26), cy), (cx - 2, int(S * 0.72)), (int(S * 0.76), int(S * 0.30))]
    d.line(pts, fill=(255, 255, 255), width=4)
    return img.resize((size, size), Image.Resampling.LANCZOS)


# ─── Quản lý Avatar Người Thật ────────────────────────────────────────────────

def get_realistic_avatar(username: str, size: int = AVATAR_SIZE, avatar_source: str = None) -> Image.Image:
    """
    Tạo avatar người thật siêu chân thực 100% chuẩn Threads:
    - Nếu có avatar_source (URL mạng hoặc đường dẫn file ảnh): Tải/đọc ảnh thật đó
    - Nếu không: Tự động ánh xạ thông minh sang kho ảnh chân dung người thật (real_male / real_female)
    - Cắt tròn siêu mịn bằng mask 4x Supersampling
    - Bo viền nhẹ 1.5px tinh tế chuẩn Threads/Instagram.
    """
    os.makedirs(AVATARS_DIR, exist_ok=True)
    raw_img = None

    # 1. Nếu người dùng chỉ định URL mạng hoặc file ảnh cụ thể
    if avatar_source:
        avatar_source = str(avatar_source).strip()
        if avatar_source.startswith(("http://", "https://")):
            cache_name = "cache_" + hashlib.md5(avatar_source.encode("utf-8")).hexdigest()[:12] + ".jpg"
            cache_path = os.path.join(AVATARS_DIR, cache_name)
            if not os.path.exists(cache_path):
                try:
                    import urllib.request
                    req = urllib.request.Request(avatar_source, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        with open(cache_path, "wb") as f:
                            f.write(resp.read())
                except Exception:
                    pass
            if os.path.exists(cache_path):
                try:
                    raw_img = Image.open(cache_path).convert("RGBA")
                except Exception:
                    pass
        else:
            cand_path = avatar_source if os.path.isabs(avatar_source) else os.path.join(AVATARS_DIR, avatar_source)
            if os.path.exists(cand_path):
                try:
                    raw_img = Image.open(cand_path).convert("RGBA")
                except Exception:
                    pass

    # 2. Tự động chọn ảnh từ kho ảnh chân dung người thật (assets/avatars/real_*.jpg)
    if raw_img is None:
        available_files = [
            f for f in os.listdir(AVATARS_DIR)
            if f.lower().endswith((".png", ".jpg", ".jpeg")) and f.startswith("real_")
        ]
        if not available_files:
            available_files = [
                f for f in os.listdir(AVATARS_DIR)
                if f.lower().endswith((".png", ".jpg", ".jpeg"))
            ]

        if available_files:
            u_lower = username.lower()
            fem_keywords = ["nu", "gai", "girl", "thotim", "my", "lan", "chi", "trang", "hoa", "linh", "ngoc", "mai", "anh", "vy"]
            male_keywords = ["nam", "boy", "hai", "dev", "it", "sep", "boss", "duc", "tuan", "hoang", "minh", "quan", "hung", "cong"]

            is_fem = any(k in u_lower for k in fem_keywords)
            is_male = any(k in u_lower for k in male_keywords)

            fem_files = [f for f in available_files if "female" in f]
            male_files = [f for f in available_files if "male" in f]

            hash_val = int(hashlib.md5(username.encode("utf-8")).hexdigest(), 16)
            if is_fem and fem_files:
                chosen = fem_files[hash_val % len(fem_files)]
            elif is_male and male_files:
                chosen = male_files[hash_val % len(male_files)]
            else:
                chosen = available_files[hash_val % len(available_files)]

            try:
                raw_img = Image.open(os.path.join(AVATARS_DIR, chosen)).convert("RGBA")
            except Exception:
                pass

    # 3. Fallback dự phòng
    if raw_img is None:
        raw_img = Image.new("RGBA", (size, size), (59, 130, 246, 255))
        d = ImageDraw.Draw(raw_img)
        char = username[0].upper() if username else "T"
        d.text((size // 2 - 8, size // 2 - 12), char, font=FONT_USERNAME, fill=(255, 255, 255))

    # 4. Cắt tròn siêu mịn (Supersampling 4x) & Bo viền tinh tế
    S = size * 4
    raw_img = raw_img.resize((S, S), Image.Resampling.LANCZOS)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, S - 1, S - 1], fill=255)
    raw_img.putalpha(mask)

    draw_border = ImageDraw.Draw(raw_img)
    draw_border.ellipse([1, 1, S - 2, S - 2], outline=(200, 205, 215, 130), width=6)

    return raw_img.resize((size, size), Image.Resampling.LANCZOS)


# ─── Render Thẻ POST (Bài đăng chính) ─────────────────────────────────────────

def render_post_card(username: str, content: str, avatar_color: str = None,
                     is_verified: bool = False, likes: int = 0,
                     time_str: str = "12 giờ", theme: str = None,
                     avatar_source: str = None) -> Image.Image:
    t_name = theme or get_card_theme()
    t = THEMES.get(t_name, THEMES["light"])

    dummy = Image.new("RGB", (1, 1))
    d_dummy = ImageDraw.Draw(dummy)

    max_text_w = CARD_W - PAD * 2
    lines = wrap_text(content, FONT_BODY, max_text_w, d_dummy)
    line_h = 46
    text_h = len(lines) * line_h

    card_h = PAD + AVATAR_SIZE + 22 + text_h + 26 + ICON_SIZE + PAD
    card_h = max(card_h, 240)

    img = Image.new("RGBA", (CARD_W, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Đổ bóng (Drop Shadow 3D)
    draw.rounded_rectangle([2, SHADOW_OFFSET, CARD_W - 3, card_h - 1], radius=RADIUS, fill=t["shadow"])

    # 2. Mặt card chính
    draw.rounded_rectangle([0, 0, CARD_W - 1, card_h - SHADOW_OFFSET - 1], radius=RADIUS,
                           fill=(*t["card_bg"], 255), outline=t["card_outline"], width=2)

    # 3. Avatar tròn người thật chân thực
    av_img = get_realistic_avatar(username, size=AVATAR_SIZE, avatar_source=avatar_source)
    img.paste(av_img, (PAD, PAD), av_img)

    # 4. Header: CĂN THẲNG TRỤC NGANG HOÀN TOÀN (Center Y = PAD + AVATAR_SIZE / 2)
    header_center_y = PAD + AVATAR_SIZE // 2

    user_x = PAD + AVATAR_SIZE + 16
    u_bbox = draw.textbbox((0, 0), username, font=FONT_USERNAME)
    u_y = int(header_center_y - (u_bbox[1] + u_bbox[3]) / 2)
    draw.text((user_x, u_y), username, font=FONT_USERNAME, fill=t["text_username"])
    user_w = draw.textlength(username, font=FONT_USERNAME)

    curr_x = user_x + user_w
    if is_verified:
        badge = get_verified_badge_img(size=22)
        badge_y = int(header_center_y - 11)
        img.paste(badge, (int(curr_x + 8), badge_y), badge)
        curr_x += 34

    t_bbox = draw.textbbox((0, 0), time_str, font=FONT_TIME)
    t_y = int(header_center_y - (t_bbox[1] + t_bbox[3]) / 2)
    draw.text((curr_x + 10, t_y), time_str, font=FONT_TIME, fill=t["text_meta"])

    # 5. Logo @threads căn thẳng hàng với header
    l_bbox = draw.textbbox((0, 0), "@threads", font=FONT_LOGO)
    logo_w = draw.textlength("@threads", font=FONT_LOGO)
    logo_y = int(header_center_y - (l_bbox[1] + l_bbox[3]) / 2)
    draw.text((int(CARD_W - PAD - logo_w), logo_y), "@threads", font=FONT_LOGO, fill=t["logo_color"])

    # 6. Nội dung bài viết
    content_y = PAD + AVATAR_SIZE + 22
    for i, line in enumerate(lines):
        draw.text((PAD, content_y + i * line_h), line, font=FONT_BODY, fill=t["text_primary"])

    # 7. Action Bar: CĂN THẲNG TRỤC NGANG 100% GIỮA ICON VÀ SỐ LIỆU
    action_center_y = content_y + text_h + 20 + ICON_SIZE // 2

    likes_val = likes if likes > 0 else random.randint(12000, 35000)
    comments_cnt = max(18, likes_val // 55)
    reposts_cnt  = max(9, likes_val // 22)
    shares_cnt   = max(5, likes_val // 40)

    actions = [
        ("heart",   format_metric(likes_val)),
        ("comment", format_metric(comments_cnt)),
        ("repost",  format_metric(reposts_cnt)),
        ("share",   format_metric(shares_cnt))
    ]

    act_x = PAD
    gap = 48
    for kind, val in actions:
        # Icon căn đúng trục action_center_y
        icon = get_action_icon_img(kind, size=ICON_SIZE, color=t["action_icon"], bg_fill=t["card_bg"])
        icon_y = int(action_center_y - ICON_SIZE / 2)
        img.paste(icon, (act_x, icon_y), icon)

        # Số liệu căn đúng trục action_center_y
        val_bbox = draw.textbbox((0, 0), val, font=FONT_ACTION)
        val_y = int(action_center_y - (val_bbox[1] + val_bbox[3]) / 2)
        draw.text((act_x + ICON_SIZE + 8, val_y), val, font=FONT_ACTION, fill=t["action_text"])

        val_w = draw.textlength(val, font=FONT_ACTION)
        act_x += ICON_SIZE + 8 + int(val_w) + gap

    return img


# ─── Render Thẻ COMMENT (Bình luận như thật) ──────────────────────────────────

def render_comment_card(username: str, content: str, avatar_color: str = None,
                        is_verified: bool = False, likes: int = 0,
                        index: int = 1, reply_to: str = None,
                        time_str: str = None, theme: str = None,
                        avatar_source: str = None) -> Image.Image:
    t_name = theme or get_card_theme()
    t = THEMES.get(t_name, THEMES["light"])

    dummy = Image.new("RGB", (1, 1))
    d_dummy = ImageDraw.Draw(dummy)

    max_text_w = CARD_W - PAD * 2
    lines = wrap_text(content, FONT_BODY_CMT, max_text_w, d_dummy)
    line_h = 44
    text_h = len(lines) * line_h

    if not time_str:
        time_options = ["15 phút", "45 phút", "1 giờ", "2 giờ", "3 giờ", "5 giờ"]
        time_str = time_options[(index - 1) % len(time_options)]

    reply_h = 28 if reply_to else 0
    card_h = PAD + AVATAR_SIZE + 18 + reply_h + text_h + 24 + ICON_SIZE + PAD
    card_h = max(card_h, 220)

    img = Image.new("RGBA", (CARD_W, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Đổ bóng (Drop Shadow 3D)
    draw.rounded_rectangle([2, SHADOW_OFFSET, CARD_W - 3, card_h - 1], radius=RADIUS, fill=t["shadow"])

    # 2. Mặt card chính
    draw.rounded_rectangle([0, 0, CARD_W - 1, card_h - SHADOW_OFFSET - 1], radius=RADIUS,
                           fill=(*t["card_bg"], 255), outline=t["card_outline"], width=2)

    # 3. Avatar người thật chân thực
    av_img = get_realistic_avatar(username, size=AVATAR_SIZE, avatar_source=avatar_source)
    img.paste(av_img, (PAD, PAD), av_img)

    # 4. Header: CĂN THẲNG TRỤC NGANG HOÀN TOÀN (Center Y = PAD + AVATAR_SIZE / 2)
    header_center_y = PAD + AVATAR_SIZE // 2

    user_x = PAD + AVATAR_SIZE + 16
    u_bbox = draw.textbbox((0, 0), username, font=FONT_USERNAME)
    u_y = int(header_center_y - (u_bbox[1] + u_bbox[3]) / 2)
    draw.text((user_x, u_y), username, font=FONT_USERNAME, fill=t["text_username"])
    user_w = draw.textlength(username, font=FONT_USERNAME)

    curr_x = user_x + user_w
    if is_verified:
        badge = get_verified_badge_img(size=22)
        badge_y = int(header_center_y - 11)
        img.paste(badge, (int(curr_x + 8), badge_y), badge)
        curr_x += 34

    t_bbox = draw.textbbox((0, 0), time_str, font=FONT_TIME)
    t_y = int(header_center_y - (t_bbox[1] + t_bbox[3]) / 2)
    draw.text((curr_x + 10, t_y), time_str, font=FONT_TIME, fill=t["text_meta"])

    # 5. Logo @threads
    l_bbox = draw.textbbox((0, 0), "@threads", font=FONT_LOGO)
    logo_w = draw.textlength("@threads", font=FONT_LOGO)
    logo_y = int(header_center_y - (l_bbox[1] + l_bbox[3]) / 2)
    draw.text((int(CARD_W - PAD - logo_w), logo_y), "@threads", font=FONT_LOGO, fill=t["logo_color"])

    # 6. Nhãn "Trả lời @tác_giả"
    cur_y = PAD + AVATAR_SIZE + 16
    if reply_to:
        clean_reply = reply_to.lstrip("@")
        draw.text((PAD, cur_y), f"Trả lời @{clean_reply}", font=FONT_REPLY, fill=t["text_reply"])
        cur_y += reply_h

    # 7. Nội dung bình luận
    for i, line in enumerate(lines):
        draw.text((PAD, cur_y + i * line_h), line, font=FONT_BODY_CMT, fill=t["text_primary"])

    # 8. Action Bar: CĂN THẲNG TRỤC NGANG 100%
    action_center_y = cur_y + text_h + 18 + ICON_SIZE // 2

    cmt_likes = likes if likes > 0 else random.randint(350, 4800)
    replies_cnt = max(1, cmt_likes // 45)
    reposts_cnt = max(1, cmt_likes // 80)

    actions = [
        ("heart",   format_metric(cmt_likes)),
        ("comment", format_metric(replies_cnt)),
        ("repost",  format_metric(reposts_cnt)),
        ("share",   "Chia sẻ")
    ]

    act_x = PAD
    gap = 48
    for kind, val in actions:
        icon = get_action_icon_img(kind, size=ICON_SIZE, color=t["action_icon"], bg_fill=t["card_bg"])
        icon_y = int(action_center_y - ICON_SIZE / 2)
        img.paste(icon, (act_x, icon_y), icon)

        val_bbox = draw.textbbox((0, 0), val, font=FONT_ACTION)
        val_y = int(action_center_y - (val_bbox[1] + val_bbox[3]) / 2)
        draw.text((act_x + ICON_SIZE + 8, val_y), val, font=FONT_ACTION, fill=t["action_text"])

        val_w = draw.textlength(val, font=FONT_ACTION)
        act_x += ICON_SIZE + 8 + int(val_w) + gap

    return img


# ─── Lưu ảnh ──────────────────────────────────────────────────────────────────

def save_card(img: Image.Image, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print(f"  [✓] Lưu card: {path}")


if __name__ == "__main__":
    import json
    OUT_DIR = os.path.join(BASE_DIR, "output", "cards")

    sample_json = os.path.join(BASE_DIR, "output", "sample_post.json")
    if os.path.exists(sample_json):
        with open(sample_json, "r", encoding="utf-8") as f:
            post_data = json.load(f)
    else:
        print("ℹ️ Chưa có file kịch bản, đang gọi Threads Radar để lấy bài trending động...")
        import importlib.util
        radar_spec = importlib.util.spec_from_file_location("radar", os.path.join(SRC_DIR, "threads_radar.py"))
        radar_mod = importlib.util.module_from_spec(radar_spec)
        radar_spec.loader.exec_module(radar_mod)
        p_obj = radar_mod.crawl_trending_threads_story()
        with open(sample_json, "r", encoding="utf-8") as f:
            post_data = json.load(f)

    p_img = render_post_card(
        username=post_data["username"],
        content=post_data["content"],
        is_verified=post_data.get("is_verified", False),
        likes=post_data.get("likes", 21100),
        time_str="12 giờ"
    )
    save_card(p_img, os.path.join(OUT_DIR, "post.png"))

    for i, c in enumerate(post_data.get("comments", []), 1):
        c_img = render_comment_card(
            username=c["username"],
            content=c["content"],
            is_verified=c.get("is_verified", False),
            likes=c.get("likes", 1200),
            index=i,
            reply_to=post_data["username"]
        )
        save_card(c_img, os.path.join(OUT_DIR, f"comment_{i}.png"))

    print(f"\n✅ Đã render xong tất cả thẻ với trục icon thẳng 100%!")
