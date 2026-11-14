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
import re
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
FONT_EMOJI_BODY = _load_font("seguiemj.ttf", 31) # Font Emoji màu cho Post
FONT_EMOJI_CMT  = _load_font("seguiemj.ttf", 30) # Font Emoji màu cho Comment


# ─── Hàm tiện ích xử lý text & số liệu ────────────────────────────────────────

def is_emoji_char(ch: str) -> bool:
    """Kiểm tra ký tự có thuộc dải Unicode Emoji không."""
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF or
        0x2600 <= cp <= 0x27BF or
        0xFE00 <= cp <= 0xFE0F or
        cp == 0x200D
    )


def split_text_emojis(text: str) -> list[tuple[str, bool]]:
    """Tách xâu thành các phân đoạn văn bản thường và phân đoạn Emoji màu."""
    segments = []
    curr = ""
    curr_is_emo = False
    for ch in text:
        emo = is_emoji_char(ch)
        if emo != curr_is_emo:
            if curr:
                segments.append((curr, curr_is_emo))
            curr = ch
            curr_is_emo = emo
        else:
            curr += ch
    if curr:
        segments.append((curr, curr_is_emo))
    return segments


def measure_text_with_emojis(draw: ImageDraw.Draw, text: str, font_text, font_emoji=None) -> float:
    """Đo chiều rộng thực tế khi kết hợp font chữ thường và font Emoji màu."""
    if not font_emoji:
        return draw.textlength(text, font=font_text)
    segments = split_text_emojis(text)
    total_w = 0.0
    for seg, is_emo in segments:
        f = font_emoji if is_emo else font_text
        total_w += draw.textlength(seg, font=f)
    return total_w


def draw_text_with_emojis(draw: ImageDraw.Draw, xy: tuple[int, int], text: str,
                           font_text, font_emoji, fill: tuple) -> float:
    """
    Vẽ văn bản kết hợp Emoji đa sắc màu siêu chân thực (True Color Emojis):
    - Chữ cái tiếng Việt vẽ bằng Segoe UI sắc nét
    - Icon emoji (😂, 💀, 🤡, 🤣...) vẽ bằng Segoe UI Emoji có màu sắc nguyên bản,
      căn THẲNG TUYỆT ĐỐI trên cùng trục ngang tâm điểm chữ (Pixel-Perfect Centerline).
    """
    x, y = xy
    segments = split_text_emojis(text)

    # Tính toán chính xác độ lệch tâm điểm dọc giữa font chữ và font emoji
    offset_y = 15.0
    if font_emoji:
        try:
            bbox_t = draw.textbbox((0, 0), "Agđy", font=font_text)
            center_t = (bbox_t[1] + bbox_t[3]) / 2.0
            bbox_e = draw.textbbox((0, 0), "😂", font=font_emoji)
            center_e = (bbox_e[1] + bbox_e[3]) / 2.0
            offset_y = center_t - center_e
        except Exception:
            offset_y = 15.0

    for seg, is_emo in segments:
        if is_emo and font_emoji:
            try:
                draw.text((x, y + offset_y), seg, font=font_emoji, embedded_color=True)
                w = draw.textlength(seg, font=font_emoji)
            except Exception:
                draw.text((x, y), seg, font=font_text, fill=fill)
                w = draw.textlength(seg, font=font_text)
        else:
            draw.text((x, y), seg, font=font_text, fill=fill)
            w = draw.textlength(seg, font=font_text)
        x += w
    return x


def format_metric(n: int) -> str:
    """Format số lượng tương tác kiểu Threads: 21,1K | 4,2K | 850"""
    if n >= 1000:
        val = n / 1000.0
        return f"{val:.1f}".replace(".", ",") + "K"
    return str(max(1, n))


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw, font_emoji=None) -> list[str]:
    words = text.split()
    lines = []
    current = ""
    for w in words:
        test = (current + " " + w).strip()
        length = measure_text_with_emojis(draw, test, font, font_emoji)
        if length <= max_width:
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
                        bg_fill: tuple = (255, 255, 255),
                        fill_solid: bool = False) -> Image.Image:
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
        if fill_solid:
            d.polygon(norm_pts, fill=color, outline=color, width=w)
        else:
            d.polygon(norm_pts, outline=color, width=w)

    elif kind == "comment":
        bx1, by1 = S * 0.10, S * 0.12
        bx2, by2 = S * 0.90, S * 0.78
        if fill_solid:
            d.ellipse([bx1, by1, bx2, by2], fill=color, outline=color, width=w)
            tail = [
                (S * 0.28, S * 0.70),
                (S * 0.10, S * 0.90),
                (S * 0.46, S * 0.76)
            ]
            d.polygon(tail, fill=color, outline=color, width=w)
        else:
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
                     avatar_source: str = None,
                     attached_image_path: str = None,
                     visible_sentence_count: int = None,
                     total_sentences_content: str = None) -> Image.Image:
    t_name = theme or get_card_theme()
    t = THEMES.get(t_name, THEMES["light"])

    dummy = Image.new("RGB", (1, 1))
    d_dummy = ImageDraw.Draw(dummy)

    max_text_w = CARD_W - PAD * 2

    # Nếu bật chế độ Progressive Reveal, kích thước thẻ luôn cố định theo toàn bộ bài để không bao giờ bị giật
    full_text = total_sentences_content if total_sentences_content else content
    full_lines = wrap_text(full_text, FONT_BODY, max_text_w, d_dummy, FONT_EMOJI_BODY)
    line_h = 48
    text_h = len(full_lines) * line_h

    # Xử lý các dòng hiển thị cho bước hiện tại
    if visible_sentence_count is not None and total_sentences_content:
        raw_s = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", total_sentences_content.strip()) if s.strip()]
        partial_text = " ".join(raw_s[:visible_sentence_count])
        display_lines = wrap_text(partial_text, FONT_BODY, max_text_w, d_dummy, FONT_EMOJI_BODY)
    else:
        display_lines = wrap_text(content, FONT_BODY, max_text_w, d_dummy, FONT_EMOJI_BODY)

    # Xử lý ảnh minh họa đính kèm bên trong thẻ (nếu được bật)
    att_resized = None
    att_mask = None
    att_h = 0
    if attached_image_path and os.path.exists(attached_image_path):
        try:
            with Image.open(attached_image_path) as att_raw:
                raw_w, raw_h = att_raw.size
                target_w = max_text_w
                aspect = raw_h / max(1, raw_w)
                target_h = int(target_w * aspect)
                target_h = max(240, min(420, target_h))

                att_resized = att_raw.convert("RGBA").resize((target_w, target_h), Image.Resampling.LANCZOS)
                att_mask = Image.new("L", (target_w, target_h), 0)
                ImageDraw.Draw(att_mask).rounded_rectangle([0, 0, target_w - 1, target_h - 1], radius=18, fill=255)

                draw_border = ImageDraw.Draw(att_resized)
                draw_border.rounded_rectangle([0, 0, target_w - 1, target_h - 1], radius=18, outline=t["card_outline"], width=1)
                att_h = target_h + 16
        except Exception as e:
            print(f"  ⚠️ Lỗi xử lý ảnh minh họa đính kèm: {e}")

    card_h = PAD + AVATAR_SIZE + 22 + text_h + att_h + 26 + ICON_SIZE + PAD
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

    # 6. Nội dung bài viết (Hỗ trợ Emoji màu sắc thực thụ 100%)
    content_y = PAD + AVATAR_SIZE + 22
    for i, line in enumerate(display_lines):
        draw_text_with_emojis(draw, (PAD, content_y + i * line_h), line, FONT_BODY, FONT_EMOJI_BODY, t["text_primary"])

    # 6.1. Dán ảnh minh họa câu chuyện (nếu có)
    current_bottom_y = content_y + text_h
    if att_resized and att_mask:
        img_paste_y = current_bottom_y + 14
        img.paste(att_resized, (PAD, img_paste_y), att_mask)
        current_bottom_y = img_paste_y + att_resized.height

    # 7. Action Bar: CĂN THẲNG TRỤC NGANG 100% GIỮA ICON VÀ SỐ LIỆU
    action_center_y = current_bottom_y + 20 + ICON_SIZE // 2

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


def create_popup_illustration_card(image_path: str, output_path: str,
                                   badge_text: str = "",
                                   caption_text: str = None) -> str:
    """
    Tạo khung ảnh minh họa nổi bên ngoài (Popup Cutaway Card) cực kỳ sắc nét:
    - Bo góc mềm mại (26px)
    - Viền trắng thanh lịch (Sleek modern border), đổ bóng 3D sâu
    - Nếu không truyền badge_text hoặc caption_text -> KHÔNG vẽ chữ, giữ ảnh thuần túy 100%
    """
    if not image_path or not os.path.exists(image_path):
        return ""
    try:
        w, h = 940, 620
        canvas = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        # 1. Đổ bóng 3D sâu
        draw.rounded_rectangle([15, 20, w + 25, h + 30], radius=28, fill=(0, 0, 0, 180))

        # 2. Khung viền trắng thanh lịch sang trọng
        draw.rounded_rectangle([18, 18, w + 22, h + 22], radius=26, fill=(20, 20, 25, 255),
                               outline=(255, 255, 255, 220), width=3)

        # 3. Dán ảnh minh họa bo góc lớn sắc nét (Center-crop giữ đúng tỷ lệ không bị méo ảnh)
        with Image.open(image_path) as raw_im:
            raw_w, raw_h = raw_im.size
            target_ratio = w / h
            im_ratio = raw_w / raw_h
            if im_ratio > target_ratio:
                crop_w = int(raw_h * target_ratio)
                crop_h = raw_h
                crop_x = (raw_w - crop_w) // 2
                crop_y = 0
            else:
                crop_w = raw_w
                crop_h = int(raw_w / target_ratio)
                crop_x = 0
                crop_y = (raw_h - crop_h) // 2
            
            cropped = raw_im.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
            im = cropped.convert("RGBA").resize((w, h), Image.Resampling.LANCZOS)
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=24, fill=255)
            canvas.paste(im, (20, 20), mask)

        # Lọc bỏ emoji để tránh hiện ô vuông font chữ []
        import re
        emoji_pattern = re.compile(
            "[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]",
            flags=re.UNICODE
        )
        clean_caption = emoji_pattern.sub("", caption_text).strip() if caption_text else ""
        clean_badge = emoji_pattern.sub("", badge_text).strip() if badge_text else ""

        # 4. Banner giải thích ngữ cảnh trò đùa ở đáy ảnh (chỉ vẽ khi có clean_caption)
        if clean_caption:
            banner_h = 74
            banner = Image.new("RGBA", (w, banner_h), (10, 15, 30, 235))
            b_draw = ImageDraw.Draw(banner)
            b_draw.line([(0, 0), (w, 0)], fill=(255, 255, 255, 120), width=1)
            cap_w = b_draw.textlength(clean_caption, font=FONT_BODY)
            cap_x = max(20, (w - int(cap_w)) // 2)
            b_draw.text((cap_x, 18), clean_caption, font=FONT_BODY, fill=(255, 255, 255))
            canvas.paste(banner, (20, 20 + h - banner_h), banner)

        # 5. Huy hiệu góc trên (chỉ vẽ khi có clean_badge)
        if clean_badge:
            badge_w = max(180, int(draw.textlength(clean_badge, font=FONT_TIME)) + 36)
            badge = Image.new("RGBA", (badge_w, 42), (0, 0, 0, 0))
            bg_draw = ImageDraw.Draw(badge)
            bg_draw.rounded_rectangle([0, 0, badge_w - 1, 41], radius=14, fill=(15, 23, 42, 240),
                                      outline=(255, 255, 255, 200), width=2)
            bg_draw.text((18, 10), clean_badge, fill=(255, 255, 255), font=FONT_TIME)
            canvas.paste(badge, (38, 38), badge)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        canvas.save(output_path)
        return output_path

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        canvas.save(output_path)
        return output_path
    except Exception as e:
        print(f"  ⚠️ Lỗi tạo khung ảnh minh họa nổi: {e}")
        return ""


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
    lines = wrap_text(content, FONT_BODY_CMT, max_text_w, d_dummy, FONT_EMOJI_CMT)
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

    # 7. Nội dung bình luận (Hỗ trợ Emoji màu sắc thực thụ 100%)
    for i, line in enumerate(lines):
        draw_text_with_emojis(draw, (PAD, cur_y + i * line_h), line, FONT_BODY_CMT, FONT_EMOJI_CMT, t["text_primary"])

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


def render_outro_cta_card(question_text: str = None, output_path: str = None) -> Image.Image:
    """
    Render thẻ Outro Call-To-Action (Kích thích bình luận & tương tác cuối video).
    Tích hợp Vector Icon chuẩn xịn (Trái tim đỏ & Bong bóng bình luận) 100% không bị lỗi ô vuông font chữ!
    """
    q_str = question_text or "Còn bạn thì sao? Đã bao giờ gặp tình huống này chưa?"
    # Lọc bỏ emoji ký tự để tránh lỗi font ô vuông (tofu box)
    for emo in ["👇", "❤️", "😱", "😂", "💀", "🔥", "✨", "🗨️", "👉"]:
        q_str = q_str.replace(emo, "").strip()

    w, h = 960, 310
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    # Đổ bóng 3D
    draw.rounded_rectangle([2, 10, w - 3, h - 1], radius=28, fill=(0, 0, 0, 160))

    # Nền Acrylic tối sang trọng
    draw.rounded_rectangle([0, 0, w - 1, h - 11], radius=28, fill=(15, 20, 32, 248),
                           outline=(99, 102, 241, 255), width=3)

    # Header badge: GÓC BÀN LUẬN DRAMA (Có icon vector micro/chat mini)
    badge_title = "GÓC BÀN LUẬN DRAMA"
    bw = int(draw.textlength(badge_title, font=FONT_TIME)) + 56
    draw.rounded_rectangle([(w - bw)//2, 22, (w + bw)//2, 66], radius=16,
                           fill=(236, 72, 153, 235))
    draw.text(((w - int(draw.textlength(badge_title, font=FONT_TIME)))//2, 32),
              badge_title, fill=(255, 255, 255), font=FONT_TIME)

    # Câu hỏi kích thích tương tác
    lines = wrap_text(q_str, FONT_BODY, w - 80, draw)
    line_y = 95
    for l in lines[:2]:
        lw = draw.textlength(l, font=FONT_BODY)
        draw.text(((w - int(lw))//2, line_y), l, fill=(255, 255, 255), font=FONT_BODY)
        line_y += 42

    # 2 Nút tương tác kêu gọi hành động (CÓ ICON VECTOR CHUẨN XỊN 100%)
    btn_h = 60
    btn_y = 208
    btn1_w = 340
    btn2_w = 440
    total_btns_w = btn1_w + 24 + btn2_w
    start_bx = (w - total_btns_w) // 2

    # Nút 1: Thả tim (Có Icon Trái Tim Đỏ Vector)
    draw.rounded_rectangle([start_bx, btn_y, start_bx + btn1_w, btn_y + btn_h], radius=18,
                           fill=(30, 41, 59, 230), outline=(239, 68, 68, 180), width=2)
    heart_icon = get_action_icon_img("heart", size=26, color=(239, 68, 68), fill_solid=True)
    b1_txt = "Thả tim tương tác"
    b1_tw = draw.textlength(b1_txt, font=FONT_ACTION)
    b1_content_w = 26 + 12 + int(b1_tw)
    b1_content_x = start_bx + (btn1_w - b1_content_w) // 2
    card.paste(heart_icon, (b1_content_x, btn_y + (btn_h - 26)//2), heart_icon)
    draw.text((b1_content_x + 26 + 12, btn_y + 19), b1_txt, fill=(255, 255, 255), font=FONT_ACTION)

    # Nút 2: Để lại bình luận (Có Icon Bong Bóng Chat Vector)
    b2_x = start_bx + btn1_w + 24
    draw.rounded_rectangle([b2_x, btn_y, b2_x + btn2_w, btn_y + btn_h], radius=18,
                           fill=(99, 102, 241, 230), outline=(199, 210, 254, 200), width=2)
    comment_icon = get_action_icon_img("comment", size=26, color=(255, 255, 255), fill_solid=True)
    b2_txt = "Bình luận câu chuyện của bạn"
    b2_tw = draw.textlength(b2_txt, font=FONT_ACTION)
    b2_content_w = 26 + 12 + int(b2_tw)
    b2_content_x = b2_x + (btn2_w - b2_content_w) // 2
    card.paste(comment_icon, (b2_content_x, btn_y + (btn_h - 26)//2), comment_icon)
    draw.text((b2_content_x + 26 + 12, btn_y + 19), b2_txt, fill=(255, 255, 255), font=FONT_ACTION)

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        card.save(output_path)
    return card


def render_video_thumbnail(post, bg_frame_path: str, output_path: str) -> str:
    """
    Tự động render ảnh bìa Thumbnail TikTok/Reels sắc nét 1080x1920 (Clickbait Cover Maker).
    """
    from PIL import ImageFilter
    try:
        # 1. Nền video làm mờ nhẹ để tôn nội dung
        if bg_frame_path and os.path.exists(bg_frame_path):
            bg = Image.open(bg_frame_path).convert("RGBA").resize((1080, 1920), Image.Resampling.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(12))
        else:
            bg = Image.new("RGBA", (1080, 1920), (10, 15, 28, 255))

        # Phủ lớp tối nhẹ
        darken = Image.new("RGBA", (1080, 1920), (0, 0, 0, 100))
        bg.paste(darken, (0, 0), darken)

        # 2. Render thẻ bài viết chính đặt ở giữa
        p_av = getattr(post, "avatar_url", "") or getattr(post, "avatar_path", "")
        post_img = render_post_card(
            post.username, post.content, post.avatar_color, post.is_verified, post.likes,
            theme="light", avatar_source=p_av
        )
        post_y = 480
        bg.paste(post_img, ((1080 - post_img.width)//2, post_y), post_img)

        # 3. Huy hiệu giật gân (Clickbait Hook Sticker) phía trên thẻ
        draw = ImageDraw.Draw(bg)
        hook_text = "CÁI KẾT BẤT NGỜ! 😱"
        hw = int(draw.textlength(hook_text, font=FONT_TIME)) + 60
        hh = 60
        hx = (1080 - hw) // 2
        hy = post_y - 85
        draw.rounded_rectangle([hx, hy, hx + hw, hy + hh], radius=20,
                               fill=(255, 215, 0, 255), outline=(0, 0, 0, 255), width=3)
        draw.text((hx + 30, hy + 14), hook_text, fill=(0, 0, 0), font=FONT_TIME)

        # 4. Tiêu đề lớn dưới đáy
        title = getattr(post, "video_title", "") or "Drama Cuộc Sống"
        tw = int(draw.textlength(title, font=FONT_BODY))
        if tw > 0:
            bx = (1080 - tw - 48) // 2
            by = post_y + post_img.height + 60
            draw.rounded_rectangle([bx, by, bx + tw + 48, by + 68], radius=22,
                                   fill=(15, 23, 42, 240), outline=(99, 102, 241, 255), width=2)
            draw.text((bx + 24, by + 14), title, fill=(255, 255, 255), font=FONT_BODY)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        bg.convert("RGB").save(output_path, quality=95)
        print(f"  [✓] Đã tạo ảnh bìa Thumbnail: {output_path}")
        return output_path
    except Exception as e:
        print(f"  ⚠️ Lỗi tạo ảnh bìa Thumbnail: {e}")
        return ""


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
