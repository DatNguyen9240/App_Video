"""
MODULE 2: RENDER THẺ THREADS (Card Generator)
==============================================
Chức năng:
  - Vẽ thẻ bài đăng (Post card) giống giao diện Threads tối
  - Vẽ thẻ bình luận (Comment card)
  - Xuất ra file PNG để kiểm tra trực quan

TEST ĐỘC LẬP: python src/02_card_gen.py
  → Mở thư mục output/cards/ để xem ảnh kết quả
"""

import os
import math
import textwrap
from PIL import Image, ImageDraw, ImageFont

# ─── Cấu hình giao diện (Threads Dark Mode) ───────────────────────────────────

CARD_W = 1000           # Chiều rộng card (px)
PAD = 48                # Padding xung quanh nội dung
AVATAR_SIZE = 72        # Đường kính avatar
FONT_DIR = r"C:\Windows\Fonts"

# Màu sắc
BG_COLOR       = (20,  20,  20)      # Nền card, gần đen
CARD_BG        = (28,  28,  28)      # Nền bài viết
CARD_COMMENT   = (24,  24,  24)      # Nền comment (tối hơn chút)
TEXT_PRIMARY   = (240, 240, 240)     # Chữ chính
TEXT_SECONDARY = (140, 140, 140)     # Chữ phụ (username, like count)
TEXT_USERNAME  = (200, 200, 200)     # Màu username
LINE_COLOR     = (50,  50,  50)      # Đường phân cách
VERIFIED_BLUE  = (0,   149, 246)     # Màu dấu tích xanh
LIKE_RED       = (255, 80,  80)      # Màu tim đỏ

# ─── Load font ────────────────────────────────────────────────────────────────

def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)

FONT_USERNAME   = _font("segoeuib.ttf", 32)   # Username đậm
FONT_BODY       = _font("segoeui.ttf",  34)    # Nội dung bài viết
FONT_BODY_SM    = _font("segoeui.ttf",  30)    # Nội dung bình luận
FONT_META       = _font("segoeui.ttf",  26)    # Like count, metadata
FONT_BADGE      = _font("segoeuib.ttf", 22)    # Chữ trong avatar


# ─── Hàm tiện ích ─────────────────────────────────────────────────────────────

def hex_to_rgb(h: str) -> tuple:
    h = str(h).lstrip("#")
    if len(h) == 6:
        try:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return (99, 102, 241)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Xuống dòng thủ công theo pixel width (hỗ trợ tiếng Việt tốt hơn textwrap)."""
    words = text.split(" ")
    lines = []
    current = ""
    dummy_img = Image.new("RGB", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)

    for word in words:
        test = (current + " " + word).strip()
        w = dummy_draw.textlength(test, font=font)
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_avatar(draw: ImageDraw.Draw, x: int, y: int, color_hex: str, initials: str, size: int = AVATAR_SIZE):
    """Vẽ avatar tròn có màu và chữ cái đầu."""
    r, g, b = hex_to_rgb(color_hex)
    # Vẽ hình tròn nền
    draw.ellipse([x, y, x + size, y + size], fill=(r, g, b))
    # Vẽ chữ cái đầu căn giữa
    char = initials[0].upper() if initials else "?"
    bbox = draw.textbbox((0, 0), char, font=FONT_BADGE)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = x + (size - tw) // 2 - bbox[0]
    ty = y + (size - th) // 2 - bbox[1]
    draw.text((tx, ty), char, font=FONT_BADGE, fill=(255, 255, 255))


def draw_verified_badge(draw: ImageDraw.Draw, x: int, y: int, size: int = 26):
    """Vẽ dấu tích xanh ✓ đơn giản bằng vòng tròn + dấu check."""
    draw.ellipse([x, y, x + size, y + size], fill=VERIFIED_BLUE)
    # Vẽ dấu check trắng (đơn giản bằng 2 đoạn thẳng)
    cx, cy = x + size // 2, y + size // 2
    pts = [
        (x + 6, cy),
        (cx - 2, y + size - 7),
        (x + size - 5, y + 7)
    ]
    draw.line(pts, fill=(255, 255, 255), width=3)


def format_likes(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


# ─── Render thẻ POST ──────────────────────────────────────────────────────────

def render_post_card(username: str, content: str, avatar_color: str,
                     is_verified: bool = False, likes: int = 0) -> Image.Image:
    """
    Tạo ảnh thẻ bài đăng Threads.
    Trả về đối tượng PIL Image (RGBA).
    """
    text_width = CARD_W - PAD * 2 - AVATAR_SIZE - 24  # Chiều rộng vùng text

    # Tính số dòng text để tính chiều cao card
    lines = wrap_text(content, FONT_BODY, text_width)
    text_height = len(lines) * 44

    card_height = PAD + AVATAR_SIZE + 24 + text_height + 60 + PAD
    card_height = max(card_height, 220)

    img = Image.new("RGBA", (CARD_W, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Nền card bo góc với viền mảnh tạo độ tương phản cao trên video nền
    draw.rounded_rectangle([0, 0, CARD_W - 1, card_height - 1],
                            radius=24, fill=(*CARD_BG, 255), outline=(65, 75, 95, 255), width=2)

    # Avatar
    av_x, av_y = PAD, PAD
    draw_avatar(draw, av_x, av_y, avatar_color, username)

    # Username + badge
    text_x = PAD + AVATAR_SIZE + 24
    name_y = av_y + 10
    draw.text((text_x, name_y), f"@{username}", font=FONT_USERNAME, fill=TEXT_USERNAME)

    if is_verified:
        name_w = int(draw.textlength(f"@{username}", font=FONT_USERNAME))
        draw_verified_badge(draw, text_x + name_w + 8, name_y + 4)

    # Nội dung bài đăng
    content_y = av_y + AVATAR_SIZE + 20
    for i, line in enumerate(lines):
        draw.text((PAD, content_y + i * 44), line, font=FONT_BODY, fill=TEXT_PRIMARY)

    # Đường kẻ phân cách + Like count
    meta_y = content_y + len(lines) * 44 + 16
    draw.line([(PAD, meta_y), (CARD_W - PAD, meta_y)], fill=LINE_COLOR, width=1)
    draw.text((PAD, meta_y + 12), f"❤  {format_likes(likes)}", font=FONT_META, fill=TEXT_SECONDARY)

    return img


# ─── Render thẻ COMMENT ───────────────────────────────────────────────────────

def render_comment_card(username: str, content: str, avatar_color: str,
                        is_verified: bool = False, likes: int = 0,
                        index: int = 1) -> Image.Image:
    """
    Tạo ảnh thẻ bình luận Threads.
    Trả về đối tượng PIL Image (RGBA).
    """
    text_width = CARD_W - PAD * 2 - AVATAR_SIZE - 24
    lines = wrap_text(content, FONT_BODY_SM, text_width)
    text_height = len(lines) * 40

    card_height = PAD + AVATAR_SIZE + 20 + text_height + 50 + PAD
    card_height = max(card_height, 180)

    img = Image.new("RGBA", (CARD_W, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Nền comment bo góc có viền mảnh và vạch accent màu indigo bên trái
    draw.rounded_rectangle([0, 0, CARD_W - 1, card_height - 1],
                            radius=20, fill=(*CARD_COMMENT, 255), outline=(55, 65, 85, 255), width=2)
    # Accent bar bên trái
    draw.rounded_rectangle([0, 0, 6, card_height - 1],
                            radius=4, fill=(99, 102, 241, 240))  # Màu indigo

    # Avatar nhỏ hơn (comment)
    av_size = 56
    av_x, av_y = PAD, PAD
    draw_avatar(draw, av_x, av_y, avatar_color, username, size=av_size)

    # Username
    text_x = PAD + av_size + 20
    draw.text((text_x, av_y + 8), f"@{username}", font=FONT_USERNAME, fill=TEXT_USERNAME)

    if is_verified:
        name_w = int(draw.textlength(f"@{username}", font=FONT_USERNAME))
        draw_verified_badge(draw, text_x + name_w + 8, av_y + 12)

    # Nội dung comment
    content_y = av_y + av_size + 16
    for i, line in enumerate(lines):
        draw.text((PAD, content_y + i * 40), line, font=FONT_BODY_SM, fill=TEXT_PRIMARY)

    # Like count
    meta_y = content_y + len(lines) * 40 + 10
    draw.text((PAD, meta_y), f"❤  {format_likes(likes)}", font=FONT_META, fill=TEXT_SECONDARY)

    # Số thứ tự comment (top-right)
    idx_text = f"#{index}"
    idx_w = int(draw.textlength(idx_text, font=FONT_META))
    draw.text((CARD_W - PAD - idx_w, meta_y), idx_text, font=FONT_META, fill=(80, 80, 80))

    return img


# ─── Hàm tiện ích: lưu ảnh ───────────────────────────────────────────────────

def save_card(img: Image.Image, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print(f"  [✓] Lưu card: {path}")


# ─── Main test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, r"d:\Dat\App_Video\src")
    from _01_content import SAMPLE_POST

    print("=" * 55)
    print("  MODULE 2: CARD GENERATOR — Đang render...")
    print("=" * 55)

    OUT_DIR = r"d:\Dat\App_Video\output\cards"

    # Render thẻ post
    post_img = render_post_card(
        username=SAMPLE_POST.username,
        content=SAMPLE_POST.content,
        avatar_color=SAMPLE_POST.avatar_color,
        is_verified=SAMPLE_POST.is_verified,
        likes=SAMPLE_POST.likes,
    )
    save_card(post_img, os.path.join(OUT_DIR, "post.png"))

    # Render từng comment
    for i, c in enumerate(SAMPLE_POST.comments, 1):
        cmt_img = render_comment_card(
            username=c.username,
            content=c.content,
            avatar_color=c.avatar_color,
            is_verified=c.is_verified,
            likes=c.likes,
            index=i,
        )
        save_card(cmt_img, os.path.join(OUT_DIR, f"comment_{i}.png"))

    print(f"\n✅ Module 2 OK — Mở thư mục để xem: {OUT_DIR}")
    os.startfile(OUT_DIR)

