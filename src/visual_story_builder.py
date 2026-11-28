"""
MODULE: VISUAL DOCUMENTARY STORY BUILDER (HÌNH + VIDEO KỂ CHUYỆN)
==================================================================
Chức năng:
  - Dựng video dọc 9:16 (1080x1920 @ 60 FPS) chuẩn TikTok / Shorts theo phong cách Visual Documentary.
  - HÌNH ẢNH & VIDEO LÀ TRỌNG TÂM: Xen kẽ liên tục các clip video tư liệu thực tế và ảnh chụp
    sắc nét với hiệu ứng phóng to/lia máy (Ken Burns).
  - Nền mờ Bokeh điện ảnh (Cinematic Depth of Field) loại bỏ hoàn toàn viền đen.
  - Thẻ tiêu đề tinh gọn phong cách thời sự/hồ sơ kỳ thú ở phía trên.
  - Phụ đề động đậm nét (Kinetic Subtitles) chữ vàng viền đen ở 1/3 dưới màn hình.
  - Giọng đọc dẫn chuyện liền mạch (Narrator) + Foley SFX + Nhạc nền kịch tính ducking.
"""

import os
import sys
import shutil
import subprocess
import time
from typing import List, Dict
from PIL import Image, ImageDraw, ImageFont

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
SFX_DIR = os.path.join(ASSETS_DIR, "sfx")
BGM_DIR = os.path.join(ASSETS_DIR, "bgm")
OUT_DIR = os.path.join(BASE_DIR, "output")
FINAL_DIR = os.path.join(OUT_DIR, "final")
CARDS_DIR = os.path.join(OUT_DIR, "cards")
AUDIO_DIR = os.path.join(OUT_DIR, "audio")

sys.path.insert(0, SRC_DIR)
import _loader
import pipeline_tracker as default_tracker
tts_mod = _loader._load("03_tts.py")
bg_mod = _loader._load("04_bg_audio.py")
config_mod = _loader._load("config.py")


def get_ffmpeg_exe() -> str:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def get_audio_duration_seconds(audio_path: str) -> float:
    """Đo thời lượng file audio bằng FFmpeg."""
    ffmpeg_exe = get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-i", audio_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in res.stderr.splitlines():
        if "Duration:" in line:
            import re
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", line)
            if m:
                h, mins, s = m.groups()
                return int(h) * 3600 + int(mins) * 60 + float(s)
    return 5.0


def create_header_badge_image(title: str, subtitle: str, output_path: str, width: int = 1080, height: int = 220) -> str:
    """Tạo thẻ tiêu đề phong cách biên tập báo chí / phim tài liệu (gọn, sắc nét, không khung AI)."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    font_title = None
    font_sub = None
    for f_path in [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]:
        if os.path.exists(f_path):
            try:
                font_title = ImageFont.truetype(f_path, 38)
                font_sub = ImageFont.truetype(f_path, 20)
                break
            except Exception:
                pass
    if not font_title:
        font_title = ImageFont.load_default()
        font_sub = font_title

    import textwrap
    clean_title = (title or "").strip()
    lines = textwrap.wrap(clean_title, width=40)[:2]
    if not lines:
        lines = ["TƯ LIỆU LỊCH SỬ"]

    kicker = (subtitle or "TƯ LIỆU THỰC TẾ").strip().upper()
    # Thay dấu chấm tròn kiểu AI bằng gạch dọc biên tập
    kicker = kicker.replace("•", "|")

    # Nền tối mờ nhẹ ở góc chữ thay vì đóng hộp cứng kiểu AI
    bx, by = 40, 26

    # Thanh nhấn dọc bên trái kiểu tạp chí / đài truyền hình
    bar_h = 84 if len(lines) == 1 else 130
    draw.rectangle([(bx, by + 6), (bx + 7, by + bar_h)], fill=(245, 158, 11, 255))

    # Kicker chữ vàng hổ phách (không đóng khung pill)
    tx = bx + 22
    draw.text((tx + 2, by + 7), kicker, font=font_sub, fill=(0, 0, 0, 220))
    draw.text((tx, by + 5), kicker, font=font_sub, fill=(251, 191, 36, 255))

    # Tiêu đề chính cỡ lớn, đổ bóng sâu trực tiếp lên nền gradient
    y_cursor = by + 36
    for ln in lines:
        draw.text((tx + 3, y_cursor + 3), ln, font=font_title, fill=(0, 0, 0, 235))
        draw.text((tx, y_cursor), ln, font=font_title, fill=(255, 255, 255, 255))
        y_cursor += 46

    im.save(output_path, "PNG")
    return output_path


def create_cinematic_scrim_overlay(output_path: str, width: int = 1080, height: int = 1920) -> str:
    """Tạo lớp gradient mờ trên & dưới (0..280px và 1120..1920px) để xóa viền cắt cứng và làm nổi chữ."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    # Top scrim cho header (y = 0..290)
    for y in range(290):
        t = y / 290.0
        alpha = int(225 * ((1.0 - t) ** 1.6))
        if alpha > 0:
            draw.line([(0, y), (width, y)], fill=(8, 11, 18, alpha))

    # Bottom scrim hòa mượt mép dưới ảnh vào vùng phụ đề (y = 1120..1920)
    fade_start = 1120
    fade_end = 1520
    for y in range(fade_start, height):
        if y <= fade_end:
            t = (y - fade_start) / float(fade_end - fade_start)
            alpha = int(245 * (t ** 1.4))
        else:
            alpha = 245
        draw.line([(0, y), (width, y)], fill=(8, 11, 18, alpha))

    im.save(output_path, "PNG")
    return output_path


def create_scene_badge_pill(badge_text: str, output_path: str) -> str:
    """Tạo tem nhãn phân cảnh Lower-Third phong cách phóng sự (góc sắc, thanh nhấn dọc)."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    text = (badge_text or "TƯ LIỆU").strip().upper()[:30]
    font = None
    for f_path in [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]:
        if os.path.exists(f_path):
            try:
                font = ImageFont.truetype(f_path, 21)
                break
            except Exception:
                pass
    if not font:
        font = ImageFont.load_default()

    tmp_im = Image.new("RGBA", (640, 60), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp_im)
    bbox = tmp_draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    w = max(160, tw + 42)
    h = 42

    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=3, fill=(12, 14, 18, 215))
    draw.rectangle([(0, 0), (5, h - 1)], fill=(245, 158, 11, 255))
    draw.text((18, 9), text, font=font, fill=(248, 250, 252, 255))
    im.save(output_path, "PNG")
    return output_path


def create_cinematic_scene_slide(badge: str, title: str, text: str, output_path: str, width: int = 1020, height: int = 620) -> str:
    """Tạo slide hình ảnh điện ảnh dự phòng tự động khi một phân cảnh chưa có ảnh chụp từ web."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    im = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(im)

    # Gradient nền điện ảnh
    for y in range(height):
        ratio = y / height
        r = int(15 + ratio * 20)
        g = int(23 + ratio * 15)
        b = int(42 + ratio * 25)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Viền kính mờ
    draw.rounded_rectangle([(10, 10), (width - 10, height - 10)], radius=24, outline=(255, 255, 255, 50), width=2)

    font_badge = None
    font_text = None
    for f_path in [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]:
        if os.path.exists(f_path):
            try:
                font_badge = ImageFont.truetype(f_path, 24)
                font_text = ImageFont.truetype(f_path, 28)
                break
            except Exception:
                pass
    if not font_badge:
        font_badge = ImageFont.load_default()
        font_text = font_badge

    # Badge pill
    badge_text = badge.upper() if badge else "TƯ LIỆU CÓ THẬT"
    draw.rounded_rectangle([(40, 40), (420, 95)], radius=14, fill=(225, 29, 72))
    draw.text((56, 54), badge_text[:28], font=font_badge, fill=(255, 255, 255))

    # Text tóm tắt cảnh
    import textwrap
    lines = textwrap.wrap(text, width=42)[:4]
    y_pos = 160
    for line in lines:
        draw.text((50, y_pos), line, font=font_text, fill=(241, 245, 249))
        y_pos += 48

    im.save(output_path, "JPEG", quality=92)
    return output_path


MOTION_STYLE_PRESETS = {
    "tiktok_split": {
        "name": "TikTok / Reels Split-Screen",
        "use_split_screen": True,
        "chunk_size": 3,
        "font_size": 64,
        "margin_v": 820,
        "default_rate": "+18%",
    },
    "kinetic_type": {
        "name": "Kinetic Typography (Chữ Động Cỡ Lớn)",
        "use_split_screen": False,
        "chunk_size": 2,
        "font_size": 74,
        "margin_v": 460,
        "default_rate": "+20%",
    },
    "explainer_doc": {
        "name": "Explainer / Phóng Sự & Bản Đồ",
        "use_split_screen": False,
        "chunk_size": 3,
        "font_size": 68,
        "margin_v": 460,
        "default_rate": "+18%",
    },
    "ad_ecommerce": {
        "name": "Quảng Cáo / E-Commerce Nhịp Nhanh",
        "use_split_screen": False,
        "chunk_size": 3,
        "font_size": 68,
        "margin_v": 460,
        "default_rate": "+20%",
    },
    "motion_clean": {
        "name": "Motion Minimalist (Data / Manim)",
        "use_split_screen": False,
        "chunk_size": 3,
        "font_size": 64,
        "margin_v": 460,
        "default_rate": "+18%",
    },
}


def build_ass_subtitle_file(
    scenes_data: List[Dict],
    output_ass: str,
    chunk_size: int = 4,
    font_size: int = 60,
    margin_v: int = 835,
) -> str:
    """Tạo file phụ đề ASS kiểu CapCut với hiệu ứng pop-in mượt và viền đen dày."""
    os.makedirs(os.path.dirname(output_ass), exist_ok=True)

    hl_size = font_size + 2
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Segoe UI,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,4.5,2.5,2,60,60,{margin_v},1
Style: Highlight,Segoe UI,{hl_size},&H0000D7FF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,5.0,3.0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for sc in scenes_data:
        start_s = sc["start"]
        end_s = sc["end"]

        # Format thời gian ASS: h:mm:ss.cc
        def fmt(t):
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = t % 60
            return f"{h}:{m:02d}:{s:05.2f}"

        words = sc["text"].split()
        c_size = max(1, int(chunk_size))
        num_chunks = max(1, (len(words) + c_size - 1) // c_size)
        chunk_dur = (end_s - start_s) / num_chunks

        for c_idx in range(num_chunks):
            c_start = start_s + c_idx * chunk_dur
            c_end = min(end_s, c_start + chunk_dur)
            sub_words = words[c_idx * c_size : (c_idx + 1) * c_size]
            if not sub_words:
                continue
            sub_text = " ".join(sub_words)
            has_num = any(ch.isdigit() for ch in sub_text)
            style_name = "Highlight" if (c_idx % 2 == 1 or "!" in sub_text or has_num) else "Default"
            pop_tag = r"{\fscx110\fscy110\t(0,110,\fscx100\fscy100)}"
            # Layer 0: Nền cụm từ pop-in mượt
            events.append(f"Dialogue: 0,{fmt(c_start)},{fmt(c_end)},{style_name},,0,0,0,,{pop_tag}{sub_text}")

            # Layer 1: Tô sáng từng từ đang đọc (Active-Word Karaoke không giật khung)
            active_color = "&H0000E5FF&" if (c_idx % 2 == 0 and not has_num) else "&H0038BDF8&"
            weights = [max(2, len(w)) for w in sub_words]
            total_w = sum(weights)
            w_cursor = c_start
            for w_idx in range(len(sub_words)):
                w_dur = (c_end - c_start) * (weights[w_idx] / total_w)
                w_start = w_cursor
                w_end = c_end if w_idx == len(sub_words) - 1 else min(c_end, w_cursor + w_dur)
                w_cursor = w_end

                parts = []
                for j, w_txt in enumerate(sub_words):
                    if j == w_idx:
                        parts.append(r"{\alpha&H00&\c" + active_color + r"}" + w_txt)
                    else:
                        parts.append(r"{\alpha&HFF&}" + w_txt)
                events.append(f"Dialogue: 1,{fmt(w_start)},{fmt(w_end)},{style_name},,0,0,0,,{' '.join(parts)}")

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")

    return output_ass


DEFAULT_TROJAN_SCENES = [
    {
        "index": 1,
        "type": "video",
        "path": "assets/documentary/trojan_tshirt/clips/clip1_festival.mp4",
        "badge": "LỄ HỘI ROCK CỰC ĐOAN",
        "text": "Năm 2011, tại Gera nước Đức, hơn 250 phần tử thuộc nhóm Tân Phát Xít đã đổ về một lễ hội nhạc Rock cực đoan.",
        "sfx": "tension_riser",
        "voice": "adam"
    },
    {
        "index": 2,
        "type": "image",
        "path": "assets/documentary/trojan_tshirt/trojan_before.jpg",
        "badge": "250 ÁO MIỄN PHÍ",
        "text": "Tại cổng vào, ban tổ chức bất ngờ phát miễn phí 250 chiếc áo phông đen cực ngầu, in hình đầu lâu cùng dòng chữ nổi loạn Hardcore Rebels.",
        "sfx": "ting",
        "voice": "adam"
    },
    {
        "index": 3,
        "type": "video",
        "path": "assets/documentary/trojan_tshirt/clips/clip2_distribution.mp4",
        "badge": "TRANH NHAU MẶC ÁO",
        "text": "Tất cả các thành viên đều vô cùng hào hứng, tranh nhau nhận áo và mặc ngay tại chỗ mà không hề nghi ngờ bất kỳ điều gì.",
        "sfx": "none",
        "voice": "adam"
    },
    {
        "index": 4,
        "type": "video",
        "path": "assets/documentary/trojan_tshirt/clips/clip3_washing.mp4",
        "badge": "BÍ MẬT MÁY GIẶT",
        "text": "Thế nhưng, sau đêm nhạc cuồng nhiệt, họ mang chiếc áo về nhà và cho vào máy giặt. Và điều kinh ngạc đã xảy ra ngay sau đó.",
        "sfx": "sub_drop",
        "voice": "adam"
    },
    {
        "index": 5,
        "type": "image",
        "path": "assets/documentary/trojan_tshirt/trojan_after.jpg",
        "badge": "CÚ LỪA THẾ KỶ",
        "text": "Lớp mực đặc biệt trôi sạch, hình đầu lâu biến mất hoàn toàn, để lộ ra thông điệp: Chiếc áo đã giúp bạn thoát xác, và chúng tôi có thể giúp bạn từ bỏ chủ nghĩa cực đoan!",
        "sfx": "boom",
        "voice": "adam"
    },
    {
        "index": 6,
        "type": "video",
        "path": "assets/documentary/trojan_tshirt/clips/clip4_media_reaction.mp4",
        "badge": "CHẤN ĐỘNG TRUYỀN THÔNG",
        "text": "Đây chính là chiến dịch Trojan T-Shirt chấn động nước Đức của tổ chức Exit Deutschland. Một pha bẻ lái thế kỷ khiến truyền thông toàn cầu ngả mũ thán phục!",
        "sfx": "applause",
        "voice": "adam"
    }
]


def render_visual_documentary_video(
    story_title: str,
    story_badge: str,
    scenes: List[Dict],
    output_filename: str = None,
    tracker = None,
    motion_style: str = "tiktok_split",
) -> str:
    """
    Quy trình dựng video Visual Documentary (Hình ảnh + Video tư liệu) hoàn chỉnh 60 FPS:
    1. Áp dụng Motion Style preset (tiktok_split, kinetic_type, explainer_doc, ad_ecommerce, motion_clean).
    2. Tạo audio TTS từng phân cảnh bằng giọng dẫn chuyện truyền cảm.
    3. Render từng phân cảnh video (clip thật hoặc ảnh Ken Burns) + Split-Screen Satisfying (nếu bật).
    4. Ghép nối các phân cảnh bằng xfade mượt, gắn Header + CapCut ASS Subtitle + Progress Bar.
    5. Mix nhạc nền ducking (sidechaincompress) và hiệu ứng âm thanh chuyển cảnh Foley.
    """
    style_cfg = MOTION_STYLE_PRESETS.get(motion_style, MOTION_STYLE_PRESETS["tiktok_split"])
    ffmpeg_exe = get_ffmpeg_exe()
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(FINAL_DIR, exist_ok=True)

    temp_dir = os.path.join(OUT_DIR, "doc_temp")
    os.makedirs(temp_dir, exist_ok=True)

    if not output_filename:
        output_filename = f"visual_doc_{int(time.time())}.mp4"
    final_mp4 = os.path.join(FINAL_DIR, output_filename)

    print("\n" + "═" * 65)
    print("🎬 KHỞI ĐỘNG XƯỞNG DỰNG PHIM VISUAL DOCUMENTARY (HÌNH + VIDEO TƯ LIỆU)")
    print(f"📌 Tiêu đề: {story_title}")
    print("═" * 65)

    # 1. TẠO AUDIO TTS CHO TỪNG PHÂN CẢNH
    print("\n🎙️ [BƯỚC 1/4]: Đang tạo giọng đọc dẫn chuyện AI từng phân cảnh...")
    if tracker:
        tracker.start_step("tts", f"Đang tổng hợp giọng dẫn chuyện cho {len(scenes)} phân cảnh...", 35)
    scenes_data = []
    audio_files = []
    current_time = 0.0

    for idx, sc in enumerate(scenes, 1):
        default_tracker.check_cancelled()
        voice_id = sc.get("voice", "adam")
        base_rate_num = int(str(style_cfg["default_rate"]).replace("%", "").replace("+", "") or 6)
        sfx_val = sc.get("sfx", "none")
        if "rate" in sc and sc["rate"]:
            rate = sc["rate"]
        elif idx == 1:
            rate = f"+{min(18, base_rate_num + 6)}%"
        elif sfx_val in ("boom", "sub_drop"):
            rate = f"+{max(0, base_rate_num - 3)}%"
        else:
            rate = f"+{min(15, base_rate_num + 2)}%"
        pitch = sc.get("pitch") or ("+2Hz" if idx == 1 else ("-2Hz" if sfx_val in ("boom", "sub_drop") else "+0Hz"))
        audio_name = f"doc_scene_{idx}.mp3"
        audio_path = os.path.join(temp_dir, audio_name)

        print(f"  • Cảnh {idx} ({sc['type'].upper()}): \"{sc['text'][:45]}...\" [{voice_id}]")
        try:
            tts_mod.text_to_speech(sc["text"], audio_path, voice=voice_id, rate=rate, pitch=pitch)
        except Exception as e:
            print(f"    ⚠️ TTS lỗi ({e}), chuyển sang gTTS: ")
            tts_mod.synthesize_gtts(sc["text"], audio_path)

        dur = get_audio_duration_seconds(audio_path)
        # Thêm 0.15s nhịp thở nhẹ giữa các cảnh
        scene_dur = dur + 0.15

        scenes_data.append({
            "index": idx,
            "type": sc["type"],
            "path": sc["path"],
            "text": sc["text"],
            "badge": sc.get("badge", ""),
            "search_query_en": sc.get("search_query_en", ""),
            "sfx": sc.get("sfx", "none"),
            "audio_path": audio_path,
            "duration": scene_dur,
            "start": current_time,
            "end": current_time + scene_dur
        })
        audio_files.append(audio_path)
        current_time += scene_dur

    total_duration = current_time
    print(f"✅ Tổng thời lượng dẫn chuyện: {total_duration:.2f} giây ({len(scenes)} phân cảnh)")
    if tracker:
        tracker.finish_step("tts")
        tracker.start_step("composite", f"Đang dựng {len(scenes)} phân cảnh (Hình ảnh Ken Burns & Video tư liệu 60 FPS)...", 55)

    # 2. RENDER TỪNG PHÂN CẢNH VIDEO DỌC 9:16 (SPLIT-SCREEN / KEN BURNS + MOTION STYLE)
    print(f"\n🎥 [BƯỚC 2/4]: Đang dựng từng phân cảnh (Style: {style_cfg['name']})...")
    scene_videos = []
    used_scene_imgs = set()
    used_img_urls = set()
    used_img_hashes = set()

    import random
    bottom_sat_video = None
    if style_cfg.get("use_split_screen", True):
        try:
            sat_root = os.path.join(ASSETS_DIR, "satisfying")
            sat_candidates = []
            if os.path.isdir(sat_root):
                for r_dir, _, f_names in os.walk(sat_root):
                    for fn in f_names:
                        if fn.lower().endswith(".mp4") and not fn.startswith("sample_"):
                            fp = os.path.join(r_dir, fn)
                            if os.path.getsize(fp) > 500_000:
                                sat_candidates.append(fp)
            if sat_candidates:
                bottom_sat_video = random.choice(sat_candidates)
                print(f"  🎮 [SPLIT-SCREEN BOTTOM]: {os.path.basename(bottom_sat_video)}")
        except Exception:
            bottom_sat_video = None

    local_ill_dir = os.path.join(ASSETS_DIR, "illustrations")
    local_ill_pool = []
    if os.path.isdir(local_ill_dir):
        local_ill_pool = [
            os.path.join(local_ill_dir, fn)
            for fn in sorted(os.listdir(local_ill_dir))
            if fn.lower().endswith((".jpg", ".jpeg", ".png"))
        ]

    # 2B. TÁCH CẢNH DÀI THÀNH MULTI-SHOT (NHỊP CẮT NHANH 2.5-4.5s) & DỰNG 6 CHUYỂN ĐỘNG CAMERA
    render_shots = []
    for sc in scenes_data:
        idx = sc["index"]
        stype = sc["type"]
        spath = sc.get("path", "")
        abs_check = spath if (spath and os.path.isabs(spath)) else (os.path.join(BASE_DIR, spath) if spath else "")
        en_q = (sc.get("search_query_en") or "").strip()

        def _build_diverse_shot_queries(tag: str) -> list:
            """Sinh bộ từ khóa khác góc nhìn cho từng shot con (a/b/c) để tránh lặp ảnh."""
            scene_text = (sc.get("text") or "").strip()
            badge_text = (sc.get("badge") or "").strip()
            if tag == "a":
                qs = [q for q in [en_q, f"{story_title} {badge_text}".strip(), scene_text[:80]] if q]
                return qs
            elif tag == "b":
                # Shot B: lấy bối cảnh / vật chứng / góc nhìn khác từ lời dẫn cảnh thay vì lặp lại y hệt en_q
                qs = []
                if scene_text:
                    try:
                        from real_image_search import generate_wikimedia_visual_queries
                        qs.extend(generate_wikimedia_visual_queries(scene_text, context=f"{story_title} - {badge_text}"))
                    except Exception:
                        pass
                if en_q:
                    qs.extend([f"{en_q} location", f"{en_q} crowd"])
                if badge_text:
                    qs.append(f"{story_title} {badge_text}".strip())
                return [q for q in qs if q]
            else:
                # Shot C: góc rộng / tư liệu báo chí / địa danh liên quan
                qs = []
                if en_q:
                    qs.extend([f"{en_q} building", f"{en_q} newspaper"])
                if story_title:
                    qs.append(f"{story_title} {badge_text}".strip())
                return [q for q in qs if q]

        def _fetch_or_fallback_image(tag: str, extra_queries: list, reuse_path: str = "") -> tuple:
            fetched_img = ""
            if extra_queries:
                try:
                    from real_image_search import search_and_download_real_image
                    scene_out_img = os.path.join(temp_dir, f"scene_web_{idx}_{tag}.jpg")
                    scene_ctx = f"{story_title} ({sc.get('badge', '')}): {sc.get('text', '')[:160]}"
                    fetched_img = search_and_download_real_image(
                        queries=extra_queries,
                        category="general_life",
                        output_path=scene_out_img,
                        target_width=1040,
                        target_height=820,
                        fallback_to_category=False,
                        exclude_urls=used_img_urls,
                        exclude_hashes=used_img_hashes,
                        topic_context=scene_ctx,
                    )
                except Exception:
                    fetched_img = ""
            if fetched_img and os.path.exists(fetched_img) and os.path.abspath(fetched_img) not in used_scene_imgs:
                used_scene_imgs.add(os.path.abspath(fetched_img))
                return fetched_img, "image"
            if reuse_path and os.path.exists(reuse_path):
                return reuse_path, "image"
            fallback_img = os.path.join(temp_dir, f"scene_slide_{idx}_{tag}.jpg")
            create_cinematic_scene_slide(sc.get("badge", ""), story_title, sc.get("text", ""), fallback_img)
            return fallback_img, "image"

        if abs_check and os.path.exists(abs_check):
            used_scene_imgs.add(os.path.abspath(abs_check))
            spath = abs_check
            ext = os.path.splitext(spath)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
                stype = "image"
            elif ext in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"):
                stype = "video"
        else:
            q_list_a = _build_diverse_shot_queries("a")
            if stype == "video" and en_q:
                try:
                    from real_image_search import search_and_download_real_video
                    fetched_vid = search_and_download_real_video(
                        queries=[en_q, story_title],
                        output_dir=temp_dir,
                        prefix=f"scene_vid_{idx}",
                        exclude_urls=used_img_urls,
                    )
                except Exception:
                    fetched_vid = ""
                if fetched_vid and os.path.exists(fetched_vid):
                    spath = fetched_vid
                    stype = "video"
                else:
                    spath, stype = _fetch_or_fallback_image("a", q_list_a)
            else:
                spath, stype = _fetch_or_fallback_image("a", q_list_a)

        sdur = sc["duration"]
        # Nhịp cắt bất đối xứng (2.2s - 3.8s/shot) theo skill short-form-video & beat-sync-editing
        if stype == "image" and sdur >= 7.5:
            dur_a = round(sdur * 0.31, 2)
            dur_b = round(sdur * 0.37, 2)
            dur_c = round(sdur - dur_a - dur_b, 2)
            q_list_b = _build_diverse_shot_queries("b")
            spath_b, _ = _fetch_or_fallback_image("b", q_list_b, reuse_path=spath)
            q_list_c = _build_diverse_shot_queries("c")
            spath_c, _ = _fetch_or_fallback_image("c", q_list_c, reuse_path=spath_b)
            render_shots.append({"scene_idx": idx, "type": "image", "path": spath, "dur": dur_a, "badge": sc.get("badge", ""), "sfx": sc.get("sfx", "none"), "show_badge": True})
            render_shots.append({"scene_idx": idx, "type": "image", "path": spath_b, "dur": dur_b, "badge": "", "sfx": "none", "show_badge": False})
            render_shots.append({"scene_idx": idx, "type": "image", "path": spath_c, "dur": dur_c, "badge": "", "sfx": "none", "show_badge": False})
        elif stype == "image" and sdur >= 4.2:
            dur_a = round(sdur * 0.43, 2)
            dur_b = round(sdur - dur_a, 2)
            q_list_b = _build_diverse_shot_queries("b")
            spath_b, _ = _fetch_or_fallback_image("b", q_list_b, reuse_path=spath)
            render_shots.append({"scene_idx": idx, "type": "image", "path": spath, "dur": dur_a, "badge": sc.get("badge", ""), "sfx": sc.get("sfx", "none"), "show_badge": True})
            render_shots.append({"scene_idx": idx, "type": "image", "path": spath_b, "dur": dur_b, "badge": "", "sfx": "none", "show_badge": False})
        else:
            render_shots.append({"scene_idx": idx, "type": "video" if stype == "video" else "image", "path": spath, "dur": sdur, "badge": sc.get("badge", ""), "sfx": sc.get("sfx", "none"), "show_badge": True})

    total_shots = len(render_shots)
    for shot_i, shot in enumerate(render_shots, 1):
        default_tracker.check_cancelled()
        stype = shot["type"]
        spath = shot["path"]
        sdur = shot["dur"]
        # Pad khớp chính xác với duration của xfade tiếp theo (0.18s khi đổi cảnh, 0.04s khi hard-cut trong cảnh)
        next_is_new_scene = (shot_i < total_shots) and (render_shots[shot_i]["scene_idx"] != shot["scene_idx"])
        xfade_pad = (0.18 if next_is_new_scene else 0.04) if shot_i < total_shots else 0.0
        render_dur = sdur + xfade_pad
        sc_out = os.path.join(temp_dir, f"rendered_shot_{shot_i}.mp4")

        top_y = 0 if bottom_sat_video else 80
        fg_h = 960 if bottom_sat_video else 1420
        if stype == "video":
            vf_fg = f"[0:v]fps=60,scale=1080:{fg_h}:force_original_aspect_ratio=increase,crop=1080:{fg_h}:(iw-ow)/2:(ih-oh)*0.25,eq=contrast=1.08:saturation=1.12[fg_raw]"
            in_args = ["-stream_loop", "-1", "-i", spath]
        else:
            n_frames = max(60, int(render_dur * 60))
            cam_mode = (shot_i - 1) % 6
            # Neo vùng lấy nét y ở 0.22 (1/3 trên) để không bao giờ cắt/khuất mặt nhân vật khi zoom
            if cam_mode == 0:
                zp_expr = "z='if(lt(on,14),1.16-0.01*on,min(zoom+0.0005,1.10))':d=1:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*0.22'"
            elif cam_mode == 1:
                zp_expr = f"z='1.12':d=1:x='(iw-iw/zoom)*on/{n_frames}':y='(ih-ih/zoom)*0.22'"
            elif cam_mode == 2:
                zp_expr = "z='if(eq(on,1),1.14,max(zoom-0.0008,1.02))':d=1:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*0.22'"
            elif cam_mode == 3:
                zp_expr = f"z='1.12':d=1:x='(iw-iw/zoom)*(1-on/{n_frames})':y='(ih-ih/zoom)*0.22'"
            elif cam_mode == 4:
                zp_expr = "z='min(zoom+0.0009,1.14)':d=1:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*0.22'"
            else:
                zp_expr = f"z='1.13':d=1:x='(iw-iw/zoom)*on/{n_frames}':y='(ih-ih/zoom)*(0.12+0.25*on/{n_frames})'"
            vf_fg = f"[0:v]scale=1360:-1:force_original_aspect_ratio=increase,zoompan={zp_expr}:s=1080x{fg_h}:fps=60,eq=contrast=1.08:saturation=1.12[fg_raw]"
            in_args = ["-loop", "1", "-framerate", "60", "-i", spath]

        # Impact flash nhẹ ở đầu shot 1 hoặc cảnh có boom/sub_drop
        if shot_i == 1 or shot.get("sfx") in ("boom", "sub_drop"):
            vf_fg += ";[fg_raw]eq=brightness='if(lt(t,0.16),0.18*(1-t/0.16),0)'[fg]"
        else:
            vf_fg += ";[fg_raw]copy[fg]"

        badge_idx = None
        if shot.get("show_badge") and shot.get("badge"):
            badge_png = os.path.join(temp_dir, f"shot_badge_{shot_i}.png")
            create_scene_badge_pill(shot["badge"], badge_png)
            badge_idx = 1
            in_args.extend(["-i", badge_png])

        if bottom_sat_video:
            sat_seek = (shot_i - 1) * 3.5
            sat_idx = 2 if badge_idx is not None else 1
            in_args.extend(["-stream_loop", "-1", "-ss", f"{sat_seek:.1f}", "-i", bottom_sat_video])
            vf = (
                f"color=c=#080b12:s=1080x1920:r=60[bg];"
                f"{vf_fg};"
                f"[{sat_idx}:v]fps=60,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bot];"
                f"[bg][fg]overlay=x=0:y=0[v1];"
                f"[v1][bot]overlay=x=0:y=960"
            )
        else:
            scrim_png = os.path.join(temp_dir, "cinematic_scrim.png")
            if not os.path.exists(scrim_png):
                create_cinematic_scrim_overlay(scrim_png)
            scrim_idx = 2 if badge_idx is not None else 1
            in_args.extend(["-i", scrim_png])
            vf = (
                f"color=c=#080b12:s=1080x1920:r=60[bg];"
                f"{vf_fg};"
                f"[bg][fg]overlay=x=0:y={top_y}[v1];"
                f"[v1][{scrim_idx}:v]overlay=x=0:y=0"
            )

        if badge_idx is not None:
            badge_y = 890 if bottom_sat_video else 1180
            vf += f"[v_pre_b];[v_pre_b][{badge_idx}:v]overlay=x='if(lt(t,0.22),-320+360*(t/0.22),40)':y={badge_y}[outv]"
        else:
            vf += "[outv]"

        cmd = [
            ffmpeg_exe, "-y",
            *in_args,
            "-filter_complex", vf,
            "-map", "[outv]",
            "-t", f"{render_dur:.2f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            sc_out
        ]

        default_tracker.run_cancellable_subprocess(cmd, timeout=120)
        if os.path.exists(sc_out) and os.path.getsize(sc_out) > 0:
            print(f"  ✓ Đã dựng Shot {shot_i}/{total_shots} (Cảnh {shot['scene_idx']} - {stype.upper()}): {sdur:.2f}s")
            scene_videos.append((sc_out, sdur, shot["scene_idx"]))
        else:
            print(f"  ❌ Lỗi khi dựng shot {shot_i}")

    # Nối shot: Hard cut bên trong cùng cảnh, chuyển cảnh nhanh 0.18s khi sang cảnh mới
    assembled_video = os.path.join(temp_dir, "assembled_scenes.mp4")
    if len(scene_videos) == 1:
        shutil.copyfile(scene_videos[0][0], assembled_video)
    elif len(scene_videos) > 1:
        xf_inputs = []
        for sv_path, _, _ in scene_videos:
            xf_inputs.extend(["-i", sv_path])
        xf_filters = []
        prev_label = "[0:v]"
        acc_dur = 0.0
        for i in range(len(scene_videos) - 1):
            acc_dur += scene_videos[i][1]
            offset_t = max(0.0, acc_dur)
            out_label = "[v_xf]" if i == len(scene_videos) - 2 else f"[xf_{i}]"
            same_scene = (scene_videos[i][2] == scene_videos[i + 1][2])
            if same_scene:
                xf_filters.append(f"{prev_label}[{i+1}:v]xfade=transition=fade:duration=0.04:offset={offset_t:.3f}{out_label}")
            else:
                tr_name = ["smoothleft", "wipeleft", "fade"][i % 3]
                xf_filters.append(f"{prev_label}[{i+1}:v]xfade=transition={tr_name}:duration=0.18:offset={offset_t:.3f}{out_label}")
            prev_label = out_label
        cmd_xf = [
            ffmpeg_exe, "-y",
            *xf_inputs,
            "-filter_complex", ";".join(xf_filters),
            "-map", "[v_xf]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            assembled_video
        ]
        res_xf = default_tracker.run_cancellable_subprocess(cmd_xf, text=True, timeout=180)
        if res_xf.returncode != 0 or not os.path.exists(assembled_video):
            concat_txt = os.path.join(temp_dir, "scenes_concat.txt")
            with open(concat_txt, "w", encoding="utf-8") as f:
                for sv_path, _, _ in scene_videos:
                    safe_v = os.path.abspath(sv_path).replace("\\", "/")
                    f.write(f"file '{safe_v}'\n")
            default_tracker.run_cancellable_subprocess([ffmpeg_exe, "-y", "-f", "concat", "-safe", "0", "-i", concat_txt, "-c", "copy", assembled_video], timeout=120)
    print("  ✓ Đã ghép nối toàn bộ các phân cảnh với hiệu ứng chuyển cảnh mượt xfade!")

    # 3. TẠO THẺ TIÊU ĐỀ HEADER & PHỤ ĐỀ KINETIC
    print("\n📝 [BƯỚC 3/4]: Tạo thẻ tiêu đề và file phụ đề động Kinetic...")
    header_img = os.path.join(temp_dir, "header_badge.png")
    create_header_badge_image(story_title, story_badge, header_img)

    ass_path = os.path.join(temp_dir, "subtitles.ass")
    build_ass_subtitle_file(
        scenes_data,
        ass_path,
        chunk_size=style_cfg["chunk_size"],
        font_size=style_cfg["font_size"],
        margin_v=style_cfg["margin_v"],
    )

    # 4. MIX AUDIO: NỐI GIỌNG ĐỌC + FOLEY SFX + NHẠC NỀN DUCKING
    print("\n🎵 [BƯỚC 4/4]: Xử lý Vocal Mastering, SFX chuyển cảnh & mix nhạc nền...")
    if tracker:
        tracker.finish_step("composite")
        tracker.start_step("audio", "Mastering giọng đọc, Foley SFX & Mix nhạc nền Ducking...", 75)
    audio_concat_txt = os.path.join(temp_dir, "audio_concat.txt")
    with open(audio_concat_txt, "w", encoding="utf-8") as f:
        for a in audio_files:
            safe_a = os.path.abspath(a).replace("\\", "/")
            f.write(f"file '{safe_a}'\n")

    combined_vocal = os.path.join(temp_dir, "combined_vocal.mp3")
    cmd_vocal = [
        ffmpeg_exe, "-y",
        "-f", "concat", "-safe", "0", "-i", audio_concat_txt,
        "-c", "copy",
        combined_vocal
    ]
    default_tracker.run_cancellable_subprocess(cmd_vocal, timeout=60)

    vocal_idx = 2
    audio_inputs_cmd = ["-i", combined_vocal]
    in_idx = 3

    foley_filters = []

    bgm_file = os.path.join(ASSETS_DIR, "bgm", "lofi_chill.mp3")
    if not os.path.exists(bgm_file):
        bgm_file = os.path.join(SFX_DIR, "lofi_bg.mp3")
    has_bgm = os.path.exists(bgm_file)

    if has_bgm:
        audio_inputs_cmd.extend(["-stream_loop", "-1", "-i", bgm_file])
        bgm_idx = in_idx
        in_idx += 1
        foley_filters.append(f"[{vocal_idx}:a]asplit=2[v_main][v_sc]")
        foley_filters.append(f"[{bgm_idx}:a]volume=0.20,atrim=0:{total_duration:.2f}[a_bgm_raw]")
        foley_filters.append("[a_bgm_raw][v_sc]sidechaincompress=threshold=0.02:ratio=4:attack=50:release=300[a_bgm]")
        vocal_mix_label = "[v_main]"
    else:
        vocal_mix_label = f"[{vocal_idx}:a]"

    sfx_streams = []
    for s_i, sc in enumerate(scenes_data):
        sfx_name = sc["sfx"]
        if (not sfx_name or sfx_name == "none") and s_i > 0:
            sfx_name = "whoosh"
        if sfx_name and sfx_name != "none":
            sfx_file = os.path.join(SFX_DIR, f"{sfx_name}.mp3")
            if os.path.exists(sfx_file):
                audio_inputs_cmd.extend(["-i", sfx_file])
                delay_ms = int(sc["start"] * 1000)
                sfx_stream_name = f"sfx_{in_idx}"
                vol = 0.25 if sfx_name == "whoosh" else 0.45
                foley_filters.append(f"[{in_idx}:a]adelay={delay_ms}:all=1,volume={vol}[{sfx_stream_name}]")
                sfx_streams.append(f"[{sfx_stream_name}]")
                in_idx += 1

    mix_inputs = [vocal_mix_label]
    if has_bgm:
        mix_inputs.append("[a_bgm]")
    mix_inputs.extend(sfx_streams)

    foley_filters.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=2[a_final]")

    bar_idx = in_idx
    audio_inputs_cmd.extend(["-f", "lavfi", "-i", "color=c=#e11d48:s=1080x6"])

    clean_ass = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")

    filter_complex_all = (
        f"[0:v]vignette=angle=PI/4[v_vig];"
        f"[v_vig][1:v]overlay=x=0:y=22[v_head];"
        f"[v_head]ass='{clean_ass}'[v_sub];"
        f"[v_sub][{bar_idx}:v]overlay=x='-1080+(1080*t/{total_duration:.2f})':y=1914:shortest=1[v_final];" +
        ";".join(foley_filters)
    )

    final_cmd = [
        ffmpeg_exe, "-y",
        "-i", assembled_video,
        "-i", header_img,
        *audio_inputs_cmd,
        "-filter_complex", filter_complex_all,
        "-map", "[v_final]",
        "-map", "[a_final]",
        "-r", "60",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "256k",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-t", f"{total_duration:.2f}",
        "-movflags", "+faststart",
        final_mp4
    ]

    print(f"\n🚀 FFmpeg đang xuất bản video thành phẩm ({total_duration:.2f}s, 60 FPS, CRF 18)...")
    if tracker:
        tracker.finish_step("audio")
        tracker.start_step("export", f"FFmpeg đang xuất bản video thành phẩm 60 FPS ({total_duration:.1f}s)...", 90)
    res_final = default_tracker.run_cancellable_subprocess(final_cmd, text=True, timeout=300)
    if res_final.returncode != 0:
        print(f"❌ Lỗi khi xuất video cuối cùng: {res_final.stderr[-600:]}")
        raise RuntimeError("FFmpeg xuất video thất bại!")

    # Dọn dẹp file tạm
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass

    # Tạo thumbnail ảnh bìa 9:16
    thumb_path = final_mp4.replace(".mp4", "_thumbnail.jpg")
    try:
        cmd_thumb = [
            ffmpeg_exe, "-y",
            "-ss", f"{total_duration * 0.45:.2f}",
            "-i", final_mp4,
            "-vframes", "1",
            "-q:v", "2",
            thumb_path
        ]
        subprocess.run(cmd_thumb, capture_output=True)
    except Exception:
        pass

    # Tạo caption file
    cap_path = final_mp4.replace(".mp4", "_caption.txt")
    caption_full = f"{story_title}\n\nCú lừa kinh điển biến 250 thành viên thành 250 tấm biển quảng cáo di động! 😂\n\n#truestory #lichsu #cangthang #fyp #xuhuong #chuyencothat\n"
    with open(cap_path, "w", encoding="utf-8") as f:
        f.write(caption_full)

    print("\n" + "═" * 65)
    print("🎉 HOÀN THÀNH XUẤT SẮC VIDEO VISUAL DOCUMENTARY!")
    print(f"📁 Video thành phẩm: {final_mp4}")
    print(f"⏱️ Thời lượng: {total_duration:.2f}s | Kích thước: {os.path.getsize(final_mp4) // 1024} KB")
    print("═" * 65)

    if tracker:
        tracker.finish_step("export")
        if hasattr(tracker, "finish_pipeline"):
            tracker.finish_pipeline(os.path.basename(final_mp4), caption_full, total_duration)
        elif hasattr(tracker, "set_success"):
            tracker.set_success(f"Hoàn thành video Visual Documentary ({total_duration:.1f}s, 60 FPS)!")

    return final_mp4
