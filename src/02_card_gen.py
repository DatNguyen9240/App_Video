"""
MODULE 2: RENDER THẺ THREADS (Authentic Threads Card Generator)
================================================================
Chức năng:
  - Render thẻ bài đăng (Post card) chuẩn giao diện Threads 100% như thật:
    + Logo @threads chính hãng ở góc trên bên phải
    + Avatar chân thực (chọn tự động từ bộ avatar phong phú)
    + Tên người dùng đậm + dấu tích xanh chính chủ + thời gian đăng (e.g. 12 giờ)
    + Nội dung căn lề chuẩn, font chữ nét căng, dễ đọc
    + Thanh tương tác chuẩn Threads: Thả tim ♡, Bình luận 🗨, Đăng lại 🔁, Chia sẻ ✈
    + Thẻ bo góc mềm mại, có đổ bóng nhẹ (Drop Shadow) nổi bật trên mọi nền video
  - Render thẻ bình luận (Comment card) như thật:
    + Nhãn 'Trả lời @tác_giả' chuẩn Threads
    + Số liệu tương tác thật (tim, phản hồi, chia sẻ)
    + Avatar và username riêng biệt cho từng người bình luận
  - Hỗ trợ cả Light Theme (mặc định - chuẩn theo ảnh mẫu) và Dark Theme

TEST ĐỘC LẬP: python src/02_card_gen.py
"""

import os
import math
import random
import hashlib
from PIL import Image, ImageDraw, ImageFont

# ─── Cấu hình kích thước & Thư mục ─────────────────────────────────────────────

CARD_W       = 960            # Chiều rộng card (px) - chuẩn trong khung 1080x1920
PAD          = 40             # Padding viền card
AVATAR_SIZE  = 64             # Đường kính avatar
RADIUS       = 24             # Bán kính bo góc card
SHADOW_OFFSET = 6             # Độ lệch đổ bóng 3D

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR   = os.path.join(BASE_DIR, "assets")
AVATARS_DIR  = os.path.join(ASSETS_DIR, "avatars")
FONT_DIR     = r"C:\Windows\Fonts"

# ─── Cấu hình màu sắc (Light & Dark Theme) ────────────────────────────────────

def get_card_theme() -> str:
    """Đọc theme từ .env hoặc mặc định là 'light' (chuẩn như ảnh Threads mẫu)."""
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
        "action_icon":   (45,  45,  45),
        "action_text":   (65,  65,  65),
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
    """Xuống dòng mượt mà theo pixel width (hỗ trợ tiếng Việt hoàn hảo)."""
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


# ─── Bộ vẽ Icon Vector chuẩn Threads ───────────────────────────────────────────

def draw_heart_icon(draw: ImageDraw.Draw, x: int, y: int, size: int = 26,
                    color: tuple = (40, 40, 40), width: int = 2):
    """Vẽ icon trái tim rỗng (Outline Heart) chuẩn Threads."""
    scale = size / 32.0
    pts = []
    for deg in range(0, 360, 4):
        t = math.radians(deg)
        hx = 16 * (math.sin(t) ** 3)
        hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((x + size / 2 + hx * scale * 0.95, y + size / 2 + hy * scale * 0.95 + 2 * scale))
    draw.polygon(pts, outline=color, width=width)


def draw_comment_bubble_icon(draw: ImageDraw.Draw, x: int, y: int, size: int = 26,
                             color: tuple = (40, 40, 40), fill_bg: tuple = (255, 255, 255), width: int = 2):
    """Vẽ icon bong bóng bình luận (Chat Bubble) chuẩn Threads."""
    draw.ellipse([x, y, x + size, y + int(size * 0.85)], outline=color, width=width)
    tail = [
        (x + int(size * 0.22), y + int(size * 0.72)),
        (x + int(size * 0.04), y + int(size * 0.98)),
        (x + int(size * 0.44), y + int(size * 0.82))
    ]
    draw.polygon(tail, fill=fill_bg, outline=color, width=width)


def draw_repost_icon(draw: ImageDraw.Draw, x: int, y: int, size: int = 26,
                     color: tuple = (40, 40, 40), width: int = 2):
    """Vẽ icon đăng lại (Loop Repost Arrows) chuẩn Threads."""
    r = int(size * 0.36)
    cx, cy = int(x + size / 2), int(y + size / 2)
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=200, end=340, fill=color, width=width)
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=20, end=160, fill=color, width=width)
    # Mũi tên hai chiều
    draw.polygon([(cx + r - 2, cy - 6), (cx + r + 6, cy + 1), (cx + r - 7, cy + 4)], fill=color)
    draw.polygon([(cx - r + 2, cy + 6), (cx - r - 6, cy - 1), (cx - r + 7, cy - 4)], fill=color)


def draw_share_icon(draw: ImageDraw.Draw, x: int, y: int, size: int = 26,
                    color: tuple = (40, 40, 40), width: int = 2):
    """Vẽ icon chia sẻ máy bay giấy (Paper Airplane) chuẩn Threads."""
    pts = [
        (x + 2, y + 2),
        (x + size - 2, y + int(size * 0.42)),
        (x + 2, y + size - 2),
        (x + int(size * 0.35), y + int(size * 0.48))
    ]
    draw.polygon(pts, outline=color, width=width)


def draw_verified_badge(draw: ImageDraw.Draw, x: int, y: int, size: int = 22):
    """Vẽ dấu tích xanh chính chủ Threads (Verified Badge)."""
    draw.ellipse([x, y, x + size, y + size], fill=VERIFIED_BLUE)
    cx, cy = x + size // 2, y + size // 2
    pts = [
        (x + 5, cy),
        (cx - 2, y + size - 6),
        (x + size - 5, y + 6)
    ]
    draw.line(pts, fill=(255, 255, 255), width=2)


def draw_threads_logo(draw: ImageDraw.Draw, x: int, y: int, color: tuple = (0, 0, 0)):
    """Vẽ logo @threads nhận diện thương hiệu chuẩn ở góc phải trên."""
    draw.text((x, y), "@threads", font=FONT_LOGO, fill=color)


# ─── Bộ quản lý Avatar chân thực ──────────────────────────────────────────────

def get_realistic_avatar(username: str, size: int = AVATAR_SIZE) -> Image.Image:
    """
    Lấy avatar chân thực cho user từ thư mục assets/avatars/.
    Mỗi user luôn nhận avatar nhất quán qua hash username.
    """
    os.makedirs(AVATARS_DIR, exist_ok=True)
    available_files = [
        f for f in os.listdir(AVATARS_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ]

    if available_files:
        hash_val = int(hashlib.md5(username.encode("utf-8")).hexdigest(), 16)
        chosen = available_files[hash_val % len(available_files)]
        try:
            raw_img = Image.open(os.path.join(AVATARS_DIR, chosen)).convert("RGBA")
            raw_img = raw_img.resize((size, size), Image.Resampling.LANCZOS)
            # Tạo mask hình tròn
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
            raw_img.putalpha(mask)
            return raw_img
        except Exception:
            pass

    # Tạo avatar gradient nếu chưa có file
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size, size], fill=(100, 149, 237, 255))
    char = username[0].upper() if username else "T"
    d.text((size // 2 - 10, size // 2 - 16), char, font=FONT_USERNAME, fill=(255, 255, 255))
    return img


# ─── Render thẻ POST (Bài đăng chính) ─────────────────────────────────────────

def render_post_card(username: str, content: str, avatar_color: str = None,
                     is_verified: bool = False, likes: int = 0,
                     time_str: str = "12 giờ", theme: str = None) -> Image.Image:
    """
    Render thẻ bài đăng Threads 100% như thật:
    - Bo góc mềm mại, đổ bóng 3D
    - Tên user + thời gian đăng + logo @threads
    - Nội dung bài đăng rõ nét
    - Thanh tương tác: Tim, Bình luận, Repost, Chia sẻ
    """
    t_name = theme or get_card_theme()
    t = THEMES.get(t_name, THEMES["light"])

    dummy = Image.new("RGB", (1, 1))
    d_dummy = ImageDraw.Draw(dummy)

    max_text_w = CARD_W - PAD * 2
    lines = wrap_text(content, FONT_BODY, max_text_w, d_dummy)
    line_h = 46
    text_h = len(lines) * line_h

    card_h = PAD + AVATAR_SIZE + 20 + text_h + 30 + 42 + PAD
    card_h = max(card_h, 240)

    img = Image.new("RGBA", (CARD_W, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Đổ bóng (Drop Shadow 3D)
    draw.rounded_rectangle(
        [2, SHADOW_OFFSET, CARD_W - 3, card_h - 1],
        radius=RADIUS,
        fill=t["shadow"]
    )

    # 2. Mặt card chính
    draw.rounded_rectangle(
        [0, 0, CARD_W - 1, card_h - SHADOW_OFFSET - 1],
        radius=RADIUS,
        fill=(*t["card_bg"], 255),
        outline=t["card_outline"],
        width=2
    )

    # 3. Avatar tròn chân thực
    av_img = get_realistic_avatar(username, size=AVATAR_SIZE)
    img.paste(av_img, (PAD, PAD), av_img)

    # 4. Username + Verified Badge + Thời gian
    user_x = PAD + AVATAR_SIZE + 16
    draw.text((user_x, PAD + 6), username, font=FONT_USERNAME, fill=t["text_username"])
    user_w = draw.textlength(username, font=FONT_USERNAME)

    curr_x = user_x + user_w
    if is_verified:
        draw_verified_badge(draw, int(curr_x + 8), PAD + 10, size=22)
        curr_x += 34

    draw.text((curr_x + 10, PAD + 12), time_str, font=FONT_TIME, fill=t["text_meta"])

    # 5. Logo @threads ở góc trên bên phải
    logo_w = draw.textlength("@threads", font=FONT_LOGO)
    draw_threads_logo(draw, int(CARD_W - PAD - logo_w), PAD + 8, color=t["logo_color"])

    # 6. Nội dung bài viết
    content_y = PAD + AVATAR_SIZE + 22
    for i, line in enumerate(lines):
        draw.text((PAD, content_y + i * line_h), line, font=FONT_BODY, fill=t["text_primary"])

    # 7. Thanh tương tác (Action Bar)
    action_y = content_y + text_h + 20
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
    for act, val in actions:
        if act == "heart":
            draw_heart_icon(draw, act_x, action_y, size=26, color=t["action_icon"])
        elif act == "comment":
            draw_comment_bubble_icon(draw, act_x, action_y, size=26, color=t["action_icon"], fill_bg=t["card_bg"])
        elif act == "repost":
            draw_repost_icon(draw, act_x, action_y, size=26, color=t["action_icon"])
        elif act == "share":
            draw_share_icon(draw, act_x, action_y, size=26, color=t["action_icon"])

        val_w = draw.textlength(val, font=FONT_ACTION)
        draw.text((act_x + 34, action_y + 2), val, font=FONT_ACTION, fill=t["action_text"])
        act_x += 34 + int(val_w) + gap

    return img


# ─── Render thẻ COMMENT (Bình luận như thật) ──────────────────────────────────

def render_comment_card(username: str, content: str, avatar_color: str = None,
                        is_verified: bool = False, likes: int = 0,
                        index: int = 1, reply_to: str = None,
                        time_str: str = None, theme: str = None) -> Image.Image:
    """
    Render thẻ bình luận Threads đẹp như thật:
    - Có nhãn 'Trả lời @tác_giả' chuẩn Threads
    - Avatar và tên người bình luận riêng biệt
    - Thời gian tương đối (e.g. 2 giờ, 45 phút)
    - Thanh tương tác: Tim, Phản hồi, Đăng lại, Chia sẻ
    """
    t_name = theme or get_card_theme()
    t = THEMES.get(t_name, THEMES["light"])

    dummy = Image.new("RGB", (1, 1))
    d_dummy = ImageDraw.Draw(dummy)

    max_text_w = CARD_W - PAD * 2
    lines = wrap_text(content, FONT_BODY_CMT, max_text_w, d_dummy)
    line_h = 44
    text_h = len(lines) * line_h

    # Thời gian ngẫu nhiên tự nhiên nếu chưa chỉ định
    if not time_str:
        time_options = ["15 phút", "45 phút", "1 giờ", "2 giờ", "3 giờ", "5 giờ"]
        time_str = time_options[(index - 1) % len(time_options)]

    reply_h = 28 if reply_to else 0
    card_h = PAD + AVATAR_SIZE + 18 + reply_h + text_h + 24 + 42 + PAD
    card_h = max(card_h, 220)

    img = Image.new("RGBA", (CARD_W, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Đổ bóng (Drop Shadow 3D)
    draw.rounded_rectangle(
        [2, SHADOW_OFFSET, CARD_W - 3, card_h - 1],
        radius=RADIUS,
        fill=t["shadow"]
    )

    # 2. Mặt card chính
    draw.rounded_rectangle(
        [0, 0, CARD_W - 1, card_h - SHADOW_OFFSET - 1],
        radius=RADIUS,
        fill=(*t["card_bg"], 255),
        outline=t["card_outline"],
        width=2
    )

    # 3. Avatar tròn
    av_img = get_realistic_avatar(username, size=AVATAR_SIZE)
    img.paste(av_img, (PAD, PAD), av_img)

    # 4. Username + Verified + Thời gian
    user_x = PAD + AVATAR_SIZE + 16
    draw.text((user_x, PAD + 6), username, font=FONT_USERNAME, fill=t["text_username"])
    user_w = draw.textlength(username, font=FONT_USERNAME)

    curr_x = user_x + user_w
    if is_verified:
        draw_verified_badge(draw, int(curr_x + 8), PAD + 10, size=22)
        curr_x += 34

    draw.text((curr_x + 10, PAD + 12), time_str, font=FONT_TIME, fill=t["text_meta"])

    # 5. Logo @threads ở góc trên bên phải
    logo_w = draw.textlength("@threads", font=FONT_LOGO)
    draw_threads_logo(draw, int(CARD_W - PAD - logo_w), PAD + 8, color=t["logo_color"])

    # 6. Dòng phụ: "Trả lời @tác_giả" nếu có
    cur_y = PAD + AVATAR_SIZE + 16
    if reply_to:
        clean_reply = reply_to.lstrip("@")
        draw.text((PAD, cur_y), f"Trả lời @{clean_reply}", font=FONT_REPLY, fill=t["text_reply"])
        cur_y += reply_h

    # 7. Nội dung bình luận
    for i, line in enumerate(lines):
        draw.text((PAD, cur_y + i * line_h), line, font=FONT_BODY_CMT, fill=t["text_primary"])

    # 8. Thanh tương tác
    action_y = cur_y + text_h + 18
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
    for act, val in actions:
        if act == "heart":
            draw_heart_icon(draw, act_x, action_y, size=26, color=t["action_icon"])
        elif act == "comment":
            draw_comment_bubble_icon(draw, act_x, action_y, size=26, color=t["action_icon"], fill_bg=t["card_bg"])
        elif act == "repost":
            draw_repost_icon(draw, act_x, action_y, size=26, color=t["action_icon"])
        elif act == "share":
            draw_share_icon(draw, act_x, action_y, size=26, color=t["action_icon"])

        val_w = draw.textlength(val, font=FONT_ACTION)
        draw.text((act_x + 34, action_y + 2), val, font=FONT_ACTION, fill=t["action_text"])
        act_x += 34 + int(val_w) + gap

    return img


# ─── Lưu ảnh ──────────────────────────────────────────────────────────────────

def save_card(img: Image.Image, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print(f"  [✓] Lưu card: {path}")


# ─── Test Module 2 Độc lập ────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    OUT_DIR = os.path.join(BASE_DIR, "output", "cards")

    sample_json = os.path.join(BASE_DIR, "output", "sample_post.json")
    if os.path.exists(sample_json):
        with open(sample_json, "r", encoding="utf-8") as f:
            post_data = json.load(f)
    else:
        post_data = {
            "username": "tkeoenk",
            "content": "đàn ông mà không có bầu được thì chả có giá trị gì",
            "likes": 21100,
            "is_verified": False,
            "comments": [
                {"username": "hoangnam.dev", "content": "Tự nhiên thấy mình vô dụng ghê, chắc mai phải đăng ký khóa học sinh con cấp tốc quá 😂", "likes": 3420, "is_verified": True},
                {"username": "trungkien_99", "content": "Ủa alo, rồi ai trả tiền viện phí với mua tã bỉm vậy bạn ơi 💀", "likes": 1850, "is_verified": False}
            ]
        }

    print("=" * 60)
    print("  🎨 MODULE 2: RENDER THẺ THREADS CHUẨN XU HƯỚNG TIKTOK / REELS")
    print("=" * 60)

    # 1. Post card
    p_img = render_post_card(
        username=post_data["username"],
        content=post_data["content"],
        is_verified=post_data.get("is_verified", False),
        likes=post_data.get("likes", 21100),
        time_str="12 giờ"
    )
    save_card(p_img, os.path.join(OUT_DIR, "post.png"))

    # 2. Comment cards
    author = post_data.get("username", "tkeoenk")
    for i, c in enumerate(post_data.get("comments", []), 1):
        c_img = render_comment_card(
            username=c["username"],
            content=c["content"],
            is_verified=c.get("is_verified", False),
            likes=c.get("likes", 1200),
            index=i,
            reply_to=author
        )
        save_card(c_img, os.path.join(OUT_DIR, f"comment_{i}.png"))

    print(f"\n✅ Đã render xong tất cả thẻ Threads tại: {OUT_DIR}")
