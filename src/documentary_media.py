"""
MODULE: DOCUMENTARY & TRUE STORY MEDIA MANAGER (v1.0)
======================================================
Chức năng:
  - Quản lý và xử lý tư liệu hình ảnh / video thực tế cho các câu chuyện có thật (True Story / Case Study).
  - Tự động tạo Video tư liệu (Documentary Slideshow / Motion Video) chuẩn 960x560 @ 60 FPS
    để đặt ở vị trí CHÍNH GIỮA (Center Media Slot) trong bố cục Sandwich.
  - Tự động tạo thẻ Cutaway Proof Card (980x640px) bo góc 26px mềm mại cho các mốc cao trào
    (ví dụ: ảnh trước khi giặt và ảnh sau khi giặt của cú lừa áo phông).
"""

import os
import sys
import shutil
import subprocess
import time
from typing import List, Dict, Optional
from PIL import Image, ImageDraw, ImageFont

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
DOC_DIR = os.path.join(ASSETS_DIR, "documentary")
OUT_DIR = os.path.join(BASE_DIR, "output")
CARDS_DIR = os.path.join(OUT_DIR, "cards")


def get_ffmpeg_exe() -> str:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def create_documentary_center_video(
    image_paths: List[str],
    output_video_path: str,
    target_duration: float = 60.0,
    width: int = 960,
    height: int = 560,
    fps: int = 60,
    interval_s: float = 4.5
) -> Optional[str]:
    """
    Tạo video chuyển động tư liệu chuẩn 960x560 60 FPS từ danh sách ảnh sự kiện thật
    để phát liên tục tại khung chính giữa trong suốt thời lượng câu chuyện.
    """
    valid_images = [os.path.abspath(p) for p in image_paths if p and os.path.exists(p)]
    if not valid_images:
        print("  ⚠️ Không tìm thấy ảnh tư liệu hợp lệ để tạo video trung tâm!")
        return None

    os.makedirs(os.path.dirname(output_video_path), exist_ok=True)
    ffmpeg_exe = get_ffmpeg_exe()

    # Tính số chu kỳ cần thiết để video dài hơn target_duration
    single_cycle_duration = len(valid_images) * interval_s
    num_cycles = max(1, int(target_duration // single_cycle_duration) + 2)

    txt_path = output_video_path + ".concat.txt"
    lines = []
    for _ in range(num_cycles):
        for img in valid_images:
            safe_img = img.replace("\\", "/")
            lines.append(f"file '{safe_img}'\n")
            lines.append(f"duration {interval_s:.2f}\n")
    # File cuối cùng trong concat demuxer cần lặp lại 1 lần không kèm duration
    last_img = valid_images[-1].replace("\\", "/")
    lines.append(f"file '{last_img}'\n")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    # Render video bằng FFmpeg với framerate 60 FPS, crop vừa chuẩn width x height
    cmd = [
        ffmpeg_exe, "-y",
        "-f", "concat", "-safe", "0", "-i", txt_path,
        "-vf", f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast",
        output_video_path
    ]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and os.path.exists(output_video_path) and os.path.getsize(output_video_path) > 1000:
            print(f"  🎬 [TƯ LIỆU THẬT]: Đã tạo video tư liệu chính giữa 960x{height}px 60 FPS: {output_video_path} ({os.path.getsize(output_video_path) // 1024} KB)")
            try:
                os.remove(txt_path)
            except Exception:
                pass
            return output_video_path
        else:
            print(f"  ⚠️ Lỗi khi render video tư liệu bằng FFmpeg: {res.stderr[-300:]}")
            return None
    except Exception as e:
        print(f"  ❌ Ngoại lệ khi tạo video tư liệu: {e}")
        return None


def create_documentary_proof_card(
    image_path: str,
    output_card_path: str,
    title_badge: str = "TƯ LIỆU CÓ THẬT",
    caption_text: str = "",
    width: int = 980,
    height: int = 640
) -> str:
    """
    Render thẻ Cutaway Bằng Chứng Thực Tế chuẩn HD (980x640px)
    với bo góc tròn 26px, viền mỏng neon tinh tế, bóng đổ 3D sâu và nhãn xác thực.
    """
    os.makedirs(os.path.dirname(output_card_path), exist_ok=True)
    if not image_path or not os.path.exists(image_path):
        return ""

    raw_im = Image.open(image_path).convert("RGB")
    # Resize & Crop vừa vặn 980x640
    rw, rh = raw_im.size
    target_ratio = width / height
    raw_ratio = rw / rh
    if raw_ratio > target_ratio:
        new_w = int(rh * target_ratio)
        x0 = (rw - new_w) // 2
        raw_im = raw_im.crop((x0, 0, x0 + new_w, rh))
    else:
        new_h = int(rw / target_ratio)
        y0 = (rh - new_h) // 2
        raw_im = raw_im.crop((0, y0, rw, y0 + new_h))
    raw_im = raw_im.resize((width, height), Image.Resampling.LANCZOS)

    # Bo góc tròn bằng mask
    radius = 26
    mask = Image.new("L", (width, height), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle([(0, 0), (width, height)], radius=radius, fill=255)

    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    card.paste(raw_im, (0, 0), mask=mask)

    draw = ImageDraw.Draw(card)
    # Vẽ viền ngoài mỏng
    draw.rounded_rectangle([(1, 1), (width - 2, height - 2)], radius=radius, outline=(255, 255, 255, 200), width=2)

    # Nếu có badge hoặc caption, vẽ overlay mờ tinh tế ở góc
    font_bold = None
    for f_path in [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]:
        if os.path.exists(f_path):
            try:
                font_bold = ImageFont.truetype(f_path, 22)
                font_caption = ImageFont.truetype(f_path, 20)
                break
            except Exception:
                pass
    if not font_bold:
        font_bold = ImageFont.load_default()
        font_caption = font_bold

    if title_badge:
        # Vẽ pill badge ở góc trên bên trái
        badge_pad_x, badge_pad_y = 16, 8
        bbox = font_bold.getbbox(title_badge)
        bw = bbox[2] - bbox[0] + badge_pad_x * 2
        bh = bbox[3] - bbox[1] + badge_pad_y * 2
        bx, by = 28, 28
        # Đổ bóng nhẹ cho badge
        draw.rounded_rectangle([(bx - 1, by - 1), (bx + bw + 1, by + bh + 1)], radius=12, fill=(0, 0, 0, 180))
        draw.rounded_rectangle([(bx, by), (bx + bw, by + bh)], radius=12, fill=(239, 68, 68, 235))
        draw.text((bx + badge_pad_x, by + badge_pad_y - 2), title_badge, font=font_bold, fill=(255, 255, 255))

    if caption_text:
        # Vẽ thanh caption nền tối mờ ở đáy card
        cap_h = 60
        grad = Image.new("RGBA", (width, cap_h), (0, 0, 0, 190))
        card.paste(grad, (0, height - cap_h), mask=mask.crop((0, height - cap_h, width, height)))
        draw_card = ImageDraw.Draw(card)
        draw_card.text((32, height - cap_h + 16), caption_text, font=font_caption, fill=(255, 255, 255, 240))

    card.save(output_card_path, "PNG")
    print(f"  📸 [THẺ BẰNG CHỨNG TƯ LIỆU]: Đã xuất thẻ Cutaway {width}x{height}px: {output_card_path}")
    return output_card_path
