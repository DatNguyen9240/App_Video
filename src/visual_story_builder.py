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
tts_mod = _loader._load("03_tts.py")
bg_mod = _loader._load("04_bg_audio.py")
config_mod = _loader._load("config.py")


def get_ffmpeg_exe() -> str:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg and not sys_ffmpeg.lower().endswith((".cmd", ".bat")):
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return sys_ffmpeg or "ffmpeg"


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
    """Tạo thẻ tiêu đề thời sự / hồ sơ kỳ thú tinh gọn ở đầu video."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    font_title = None
    font_sub = None
    for f_path in [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]:
        if os.path.exists(f_path):
            try:
                font_title = ImageFont.truetype(f_path, 34)
                font_sub = ImageFont.truetype(f_path, 22)
                break
            except Exception:
                pass
    if not font_title:
        font_title = ImageFont.load_default()
        font_sub = font_title

    # Glassmorphism container
    box_w, box_h = 1000, 156
    bx, by = (width - box_w) // 2, 36
    draw.rounded_rectangle([(bx, by), (bx + box_w, by + box_h)], radius=24, fill=(15, 23, 42, 230), outline=(255, 255, 255, 70), width=2)

    # Pill badge đỏ tự co giãn theo độ dài chữ
    px, py = bx + 28, by + 18
    sub_bbox = draw.textbbox((0, 0), subtitle, font=font_sub)
    sub_tw = sub_bbox[2] - sub_bbox[0]
    pill_w = min(box_w - 56, max(180, sub_tw + 36))
    pill_h = 38
    draw.rounded_rectangle([(px, py), (px + pill_w, py + pill_h)], radius=12, fill=(225, 29, 72, 245))
    draw.text((px + 18, py + 6), subtitle, font=font_sub, fill=(255, 255, 255))

    # Title text (tự thu nhỏ nếu quá dài để không tràn khung)
    max_title_w = box_w - 56
    title_bbox = draw.textbbox((0, 0), title, font=font_title)
    if (title_bbox[2] - title_bbox[0]) > max_title_w:
        title = title[:42].rstrip() + "..."
    draw.text((bx + 28, by + 74), title, font=font_title, fill=(255, 255, 255))

    im.save(output_path, "PNG")
    return output_path


def create_cinematic_scene_slide(badge: str, title: str, text: str, output_path: str, width: int = 1020, height: int = 620) -> str:
    """Tạo slide hình ảnh điện ảnh dự phòng tự động khi một phân cảnh chưa có ảnh chụp từ web."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    im = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(im)

    # Gradient nền điện ảnh sâu có chiều sâu
    for y in range(height):
        ratio = y / height
        r = int(12 + ratio * 18)
        g = int(18 + ratio * 16)
        b = int(36 + ratio * 28)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Thanh accent dọc bên trái & viền kính mờ
    draw.rounded_rectangle([(12, 12), (width - 12, height - 12)], radius=24, outline=(148, 163, 184), width=2)
    draw.rounded_rectangle([(44, 48), (52, height - 48)], radius=4, fill=(225, 29, 72))

    font_badge = None
    font_text = None
    for f_path in [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]:
        if os.path.exists(f_path):
            try:
                font_badge = ImageFont.truetype(f_path, 24)
                font_text = ImageFont.truetype(f_path, 38)
                break
            except Exception:
                pass
    if not font_badge:
        font_badge = ImageFont.load_default()
        font_text = font_badge

    # Badge pill tự co giãn theo text
    badge_text = (badge.upper() if badge else "TƯ LIỆU CÓ THẬT")[:36]
    b_bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
    b_pw = min(width - 140, max(180, (b_bbox[2] - b_bbox[0]) + 40))
    draw.rounded_rectangle([(80, 56), (80 + b_pw, 106)], radius=14, fill=(225, 29, 72))
    draw.text((100, 68), badge_text, font=font_badge, fill=(255, 255, 255))

    # Text tóm tắt cảnh căn giữa theo chiều dọc, font lớn dễ đọc
    import textwrap
    lines = textwrap.wrap(text, width=34)[:5]
    line_h = 58
    total_text_h = len(lines) * line_h
    y_pos = max(145, 130 + (height - 170 - total_text_h) // 2)
    for line in lines:
        draw.text((84, y_pos), line, font=font_text, fill=(248, 250, 252))
        y_pos += line_h

    im.save(output_path, "JPEG", quality=92)
    return output_path


def build_ass_subtitle_file(scenes_data: List[Dict], output_ass: str) -> str:
    """Tạo file phụ đề ASS kiểu CapCut (cụm 4 từ, hiệu ứng pop-in mượt, viền đen dày)."""
    os.makedirs(os.path.dirname(output_ass), exist_ok=True)

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Segoe UI,60,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,4.5,2.5,2,60,60,835,1
Style: Highlight,Segoe UI,62,&H0000D7FF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,5.0,3.0,2,60,60,835,1

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

        # Chia câu thành cụm ngắn 4 từ kiểu CapCut để nhịp đọc nhanh, bắt mắt
        words = sc["text"].split()
        chunk_size = 4
        num_chunks = max(1, (len(words) + chunk_size - 1) // chunk_size)
        chunk_dur = (end_s - start_s) / num_chunks

        for c_idx in range(num_chunks):
            c_start = start_s + c_idx * chunk_dur
            c_end = min(end_s, c_start + chunk_dur)
            sub_words = words[c_idx * chunk_size : (c_idx + 1) * chunk_size]
            sub_text = " ".join(sub_words)
            has_num = any(ch.isdigit() for ch in sub_text)
            style_name = "Highlight" if (c_idx % 2 == 1 or "!" in sub_text or has_num) else "Default"
            pop_tag = r"{\fscx110\fscy110\t(0,110,\fscx100\fscy100)}"
            events.append(f"Dialogue: 0,{fmt(c_start)},{fmt(c_end)},{style_name},,0,0,0,,{pop_tag}{sub_text}")

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
    tracker = None
) -> str:
    """
    Quy trình dựng video Visual Documentary (Hình ảnh + Video tư liệu) hoàn chỉnh 60 FPS:
    1. Tạo audio TTS từng phân cảnh bằng giọng dẫn chuyện truyền cảm (Adam / Brian / Nam Minh).
    2. Render từng phân cảnh video (clip thật hoặc ảnh Ken Burns) với nền bokeh mờ 1080x1920.
    3. Ghép nối các phân cảnh thành luồng video liền mạch.
    4. Gắn thẻ tiêu đề Header ở trên và phụ đề Kinetic Subtitle ở dưới.
    5. Mix nhạc nền kịch tính ducking và hiệu ứng âm thanh chuyển cảnh Foley.
    """
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
        voice_id = sc.get("voice", "adam")
        rate = sc.get("rate", "+6%")
        pitch = sc.get("pitch", "+0Hz")
        audio_name = f"doc_scene_{idx}.mp3"
        audio_path = os.path.join(AUDIO_DIR, audio_name)

        print(f"  • Cảnh {idx} ({sc['type'].upper()}): \"{sc['text'][:45]}...\" [{voice_id}]")
        if not (os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000):
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
        tracker.start_step("composite", f"Đang dựng {len(scenes)} phân cảnh (Split-Screen Ken Burns + Satisfying 60 FPS)...", 55)

    # 2. RENDER TỪNG PHÂN CẢNH VIDEO DỌC 9:16 (SPLIT-SCREEN: TƯ LIỆU TRÊN + SATISFYING DƯỚI)
    print("\n🎥 [BƯỚC 2/4]: Đang dựng từng phân cảnh (Split-Screen Ken Burns + Satisfying 60 FPS)...")
    scene_videos = []
    used_scene_imgs = set()

    # Chọn 1 video satisfying thật (>500KB) để chạy xuyên suốt nửa dưới màn hình
    import random
    bottom_sat_video = None
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

    # Danh sách ảnh dự phòng cục bộ để xoay vòng nếu không tải được ảnh web
    local_ill_dir = os.path.join(ASSETS_DIR, "illustrations")
    local_ill_pool = []
    if os.path.isdir(local_ill_dir):
        local_ill_pool = [
            os.path.join(local_ill_dir, fn)
            for fn in sorted(os.listdir(local_ill_dir))
            if fn.lower().endswith((".jpg", ".jpeg", ".png"))
        ]

    for sc in scenes_data:
        idx = sc["index"]
        stype = sc["type"]
        spath = sc.get("path", "")
        abs_check = spath if (spath and os.path.isabs(spath)) else (os.path.join(BASE_DIR, spath) if spath else "")
        if abs_check and os.path.exists(abs_check):
            used_scene_imgs.add(os.path.abspath(abs_check))
        if not abs_check or not os.path.exists(abs_check):
            # Thử tự động tìm ảnh thật theo từ khóa của phân cảnh (lưu riêng từng cảnh để không bị ghi đè)
            fetched_img = ""
            q_list = [q for q in [sc.get("search_query_en", ""), sc.get("text", "")[:50], story_title] if q]
            if q_list:
                try:
                    from real_image_search import search_and_download_real_image
                    scene_out_img = os.path.join(temp_dir, f"scene_web_{idx}.jpg")
                    fetched_img = search_and_download_real_image(
                        queries=q_list,
                        category="general_life",
                        output_path=scene_out_img,
                        target_width=1040,
                        target_height=820,
                        fallback_to_category=False
                    )
                except Exception:
                    fetched_img = ""
            if fetched_img and os.path.exists(fetched_img) and os.path.abspath(fetched_img) not in used_scene_imgs:
                spath = fetched_img
                stype = "image"
                used_scene_imgs.add(os.path.abspath(fetched_img))
            else:
                # Xoay vòng ảnh từ assets/illustrations hoặc tạo slide điện ảnh
                unused_local = [p for p in local_ill_pool if os.path.abspath(p) not in used_scene_imgs]
                if unused_local:
                    spath = unused_local[0]
                    stype = "image"
                    used_scene_imgs.add(os.path.abspath(spath))
                else:
                    fallback_img = os.path.join(temp_dir, f"scene_slide_{idx}.jpg")
                    create_cinematic_scene_slide(sc.get("badge", ""), story_title, sc.get("text", ""), fallback_img)
                    spath = fallback_img
                    stype = "image"
        else:
            spath = abs_check
            # Bảo vệ: nếu file thực tế là ảnh (.jpg/.png/.webp...) thì ép stype='image' để tránh FFmpeg treo với -stream_loop -1
            ext = os.path.splitext(spath)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
                stype = "image"
            elif ext in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"):
                stype = "video"
        sdur = sc["duration"]
        xfade_pad = 0.35 if idx < len(scenes_data) else 0.0
        render_dur = sdur + xfade_pad
        sc_out = os.path.join(temp_dir, f"rendered_scene_{idx}.mp4")

        # Khung trên: 1040x820 tại y=205 (ngay dưới header). Khung dưới (nếu có): 1040x840 tại y=1045.
        top_y = 205 if bottom_sat_video else 235
        if stype == "video":
            vf_fg = f"[0:v]fps=60,scale=1040:820:force_original_aspect_ratio=increase,crop=1040:820[fg]"
            in_args = ["-stream_loop", "-1", "-i", spath]
        else:
            zp_expr = (
                "z='min(zoom+0.0007,1.09)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                if (idx % 2 == 1)
                else "z='if(eq(on,1),1.09,max(zoom-0.0007,1.01))':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            )
            vf_fg = f"[0:v]scale=1240:-1:force_original_aspect_ratio=increase,zoompan={zp_expr}:s=1040x820:fps=60[fg]"
            in_args = ["-loop", "1", "-framerate", "60", "-i", spath]

        if bottom_sat_video:
            sat_seek = (idx - 1) * 4.5
            in_args.extend(["-stream_loop", "-1", "-ss", f"{sat_seek:.1f}", "-i", bottom_sat_video])
            vf = (
                f"[0:v]fps=60,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=35:5,eq=brightness=-0.22:saturation=0.7[bg];"
                f"{vf_fg};"
                f"[1:v]fps=60,scale=1040:840:force_original_aspect_ratio=increase,crop=1040:840[bot];"
                f"[bg][fg]overlay=x=(W-w)/2:y={top_y}[v1];"
                f"[v1][bot]overlay=x=(W-w)/2:y=1045[outv]"
            )
        else:
            vf = (
                f"[0:v]fps=60,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=35:5,eq=brightness=-0.22:saturation=0.7[bg];"
                f"{vf_fg};"
                f"[bg][fg]overlay=x=(W-w)/2:y={top_y}[outv]"
            )

        cmd = [
            ffmpeg_exe, "-y",
            *in_args,
            "-filter_complex", vf,
            "-map", "[outv]",
            "-t", f"{render_dur:.2f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            sc_out
        ]

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(sc_out):
            print(f"  ✓ Đã dựng Cảnh {idx} ({stype.upper()}): {sdur:.2f}s")
            scene_videos.append((sc_out, sdur))
        else:
            print(f"  ❌ Lỗi khi dựng cảnh {idx}")

    # Nối tất cả các video phân cảnh bằng xfade chuyển cảnh mượt (0.35s)
    assembled_video = os.path.join(temp_dir, "assembled_scenes.mp4")
    if len(scene_videos) == 1:
        shutil.copyfile(scene_videos[0][0], assembled_video)
    elif len(scene_videos) > 1:
        xfade_dur = 0.35
        transitions = ["fade", "wipeleft", "smoothleft", "dissolve", "slideright"]
        xf_inputs = []
        for sv_path, _ in scene_videos:
            xf_inputs.extend(["-i", sv_path])
        xf_filters = []
        prev_label = "[0:v]"
        acc_dur = 0.0
        for i in range(len(scene_videos) - 1):
            acc_dur += scene_videos[i][1]
            # Offset khớp đúng điểm kết thúc giọng đọc của cảnh i (vì mỗi cảnh đã pad +0.35s)
            offset_t = max(0.0, acc_dur)
            out_label = "[v_xf]" if i == len(scene_videos) - 2 else f"[xf_{i}]"
            tr_name = transitions[i % len(transitions)]
            xf_filters.append(f"{prev_label}[{i+1}:v]xfade=transition={tr_name}:duration={xfade_dur}:offset={offset_t:.3f}{out_label}")
            prev_label = out_label
        cmd_xf = [
            ffmpeg_exe, "-y",
            *xf_inputs,
            "-filter_complex", ";".join(xf_filters),
            "-map", "[v_xf]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            assembled_video
        ]
        res_xf = subprocess.run(cmd_xf, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_xf.returncode != 0 or not os.path.exists(assembled_video):
            # Fallback sang concat nếu xfade gặp lỗi
            concat_txt = os.path.join(temp_dir, "scenes_concat.txt")
            with open(concat_txt, "w", encoding="utf-8") as f:
                for sv_path, _ in scene_videos:
                    safe_v = os.path.abspath(sv_path).replace("\\", "/")
                    f.write(f"file '{safe_v}'\n")
            subprocess.run([ffmpeg_exe, "-y", "-f", "concat", "-safe", "0", "-i", concat_txt, "-c", "copy", assembled_video], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print("  ✓ Đã ghép nối toàn bộ các phân cảnh với hiệu ứng chuyển cảnh mượt xfade!")

    # 3. TẠO THẺ TIÊU ĐỀ HEADER & PHỤ ĐỀ KINETIC
    print("\n📝 [BƯỚC 3/4]: Tạo thẻ tiêu đề và file phụ đề động Kinetic...")
    header_img = os.path.join(temp_dir, "header_badge.png")
    create_header_badge_image(story_title, story_badge, header_img)

    ass_path = os.path.join(temp_dir, "subtitles.ass")
    build_ass_subtitle_file(scenes_data, ass_path)

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
    subprocess.run(cmd_vocal, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Foley SFX và BGM
    # Input 0: assembled_video (chỉ có video)
    # Input 1: header_img (chỉ có ảnh)
    # Input 2: combined_vocal (audio giọng đọc chính)
    vocal_idx = 2
    audio_inputs_cmd = ["-i", combined_vocal]
    in_idx = 3

    foley_filters = []

    # Nhạc nền lofi/chill/suspense
    bgm_file = os.path.join(ASSETS_DIR, "bgm", "lofi_chill.mp3")
    if not os.path.exists(bgm_file):
        bgm_file = os.path.join(SFX_DIR, "lofi_bg.mp3")
    has_bgm = os.path.exists(bgm_file)

    if has_bgm:
        audio_inputs_cmd.extend(["-stream_loop", "-1", "-i", bgm_file])
        bgm_idx = in_idx
        in_idx += 1
        # Sidechain ducking: nhạc nền tự nhỏ xuống khi giọng đọc vang lên
        foley_filters.append(f"[{vocal_idx}:a]asplit=2[v_main][v_sc]")
        foley_filters.append(f"[{bgm_idx}:a]volume=0.26,atrim=0:{total_duration:.2f}[a_bgm_raw]")
        foley_filters.append("[a_bgm_raw][v_sc]sidechaincompress=threshold=0.02:ratio=4:attack=50:release=300[a_bgm]")
        vocal_mix_label = "[v_main]"
    else:
        vocal_mix_label = f"[{vocal_idx}:a]"

    # Thêm SFX tại điểm bắt đầu từng cảnh (tự chèn whoosh nhẹ khi chuyển cảnh nếu chưa có SFX)
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

    # Thanh tiến trình đỏ (Progress Bar) chạy ngang đáy màn hình
    bar_idx = in_idx
    audio_inputs_cmd.extend(["-f", "lavfi", "-i", "color=c=#e11d48:s=1080x6"])

    # MASTER COMPOSITE TOÀN DIỆN BẰNG FFMPEG (Vignette + Header + CapCut ASS + Progress Bar)
    clean_ass = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")

    filter_complex_all = (
        f"[0:v]vignette=angle=PI/4[v_vig];"
        f"[v_vig][1:v]overlay=x=0:y=30[v_head];"
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
    res_final = subprocess.run(final_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_final.returncode != 0:
        err_tail = (res_final.stderr or "").strip()[-400:]
        print(f"❌ Lỗi khi xuất video cuối cùng: {err_tail}")
        raise RuntimeError(f"FFmpeg xuất video thất bại! {err_tail}")

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
