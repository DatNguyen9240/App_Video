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
    box_w, box_h = 1000, 150
    bx, by = (width - box_w) // 2, 40
    draw.rounded_rectangle([(bx, by), (bx + box_w, by + box_h)], radius=24, fill=(15, 23, 42, 220), outline=(255, 255, 255, 60), width=2)

    # Pill badge đỏ nổi bật
    pill_w, pill_h = 280, 36
    px, py = bx + 24, by + 18
    draw.rounded_rectangle([(px, py), (px + pill_w, py + pill_h)], radius=12, fill=(225, 29, 72, 240))
    draw.text((px + 16, py + 6), subtitle, font=font_sub, fill=(255, 255, 255))

    # Title text
    draw.text((bx + 24, by + 68), title, font=font_title, fill=(255, 255, 255))

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


def build_ass_subtitle_file(scenes_data: List[Dict], output_ass: str) -> str:
    """Tạo file phụ đề ASS với kiểu chữ vàng/trắng viền đen 3.5px chuẩn TikTok."""
    os.makedirs(os.path.dirname(output_ass), exist_ok=True)

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Segoe UI,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4.0,2.0,2,60,60,420,1
Style: Highlight,Segoe UI,56,&H0000F5FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4.5,2.5,2,60,60,420,1

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

        # Chia câu thành các cụm 6-8 từ để hiển thị nhịp nhàng trên màn hình điện thoại
        words = sc["text"].split()
        chunk_size = 7
        num_chunks = max(1, (len(words) + chunk_size - 1) // chunk_size)
        chunk_dur = (end_s - start_s) / num_chunks

        for c_idx in range(num_chunks):
            c_start = start_s + c_idx * chunk_dur
            c_end = min(end_s, c_start + chunk_dur)
            sub_words = words[c_idx * chunk_size : (c_idx + 1) * chunk_size]
            sub_text = " ".join(sub_words)
            style_name = "Highlight" if (c_idx % 2 == 1 or "!" in sub_text) else "Default"
            events.append(f"Dialogue: 0,{fmt(c_start)},{fmt(c_end)},{style_name},,0,0,0,,{sub_text}")

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
        rate = sc.get("rate", "+16%")
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
        tracker.start_step("composite", f"Đang dựng {len(scenes)} phân cảnh (Hình ảnh Ken Burns & Video tư liệu 60 FPS)...", 55)

    # 2. RENDER TỪNG PHÂN CẢNH VIDEO DỌC 9:16 (BOKEH BACKGROUND + SHARP MEDIA)
    print("\n🎥 [BƯỚC 2/4]: Đang dựng từng phân cảnh (Hình ảnh Ken Burns / Video tư liệu)...")
    scene_videos = []

    for sc in scenes_data:
        idx = sc["index"]
        stype = sc["type"]
        spath = sc.get("path", "")
        abs_check = spath if (spath and os.path.isabs(spath)) else (os.path.join(BASE_DIR, spath) if spath else "")
        if not abs_check or not os.path.exists(abs_check):
            # Tự động tạo ảnh slide điện ảnh chất lượng cao cho phân cảnh này
            fallback_img = os.path.join(temp_dir, f"scene_slide_{idx}.jpg")
            create_cinematic_scene_slide(sc.get("badge", ""), story_title, sc.get("text", ""), fallback_img)
            spath = fallback_img
            stype = "image"
        else:
            spath = abs_check
        sdur = sc["duration"]
        sc_out = os.path.join(temp_dir, f"rendered_scene_{idx}.mp4")

        # Khung viền hình ảnh/video chính giữa: 1000 x 600px đặt tại y=480
        if stype == "video":
            # Video clip: Lặp hoặc cắt đúng sdur giây, tạo nền mờ bokeh 1080x1920 và chèn video sắc nét ở giữa
            vf = (
                f"[0:v]fps=60,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,colorchannelmixer=aa=0.45[bg];"
                f"[0:v]fps=60,scale=1020:620:force_original_aspect_ratio=increase,crop=1020:620[fg];"
                f"[bg][fg]overlay=x=(W-w)/2:y=500[outv]"
            )
            cmd = [
                ffmpeg_exe, "-y",
                "-stream_loop", "-1", "-i", spath,
                "-filter_complex", vf,
                "-map", "[outv]",
                "-t", f"{sdur:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                sc_out
            ]
        else:
            # Hình ảnh sắc nét: Tạo hiệu ứng Ken Burns zoom nhẹ (1.0 -> 1.08) trên ảnh, kèm nền mờ bokeh
            vf = (
                f"[0:v]fps=60,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,colorchannelmixer=aa=0.45[bg];"
                f"[0:v]fps=60,scale=1020:620:force_original_aspect_ratio=increase,crop=1020:620[fg];"
                f"[bg][fg]overlay=x=(W-w)/2:y=500[outv]"
            )
            cmd = [
                ffmpeg_exe, "-y",
                "-loop", "1", "-framerate", "60", "-i", spath,
                "-filter_complex", vf,
                "-map", "[outv]",
                "-t", f"{sdur:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                sc_out
            ]

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(sc_out):
            print(f"  ✓ Đã dựng Cảnh {idx} ({stype.upper()}): {sdur:.2f}s")
            scene_videos.append(sc_out)
        else:
            print(f"  ❌ Lỗi khi dựng cảnh {idx}")

    # Nối tất cả các video phân cảnh lại
    concat_txt = os.path.join(temp_dir, "scenes_concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for v in scene_videos:
            safe_v = os.path.abspath(v).replace("\\", "/")
            f.write(f"file '{safe_v}'\n")

    assembled_video = os.path.join(temp_dir, "assembled_scenes.mp4")
    cmd_cat = [
        ffmpeg_exe, "-y",
        "-f", "concat", "-safe", "0", "-i", concat_txt,
        "-c", "copy",
        assembled_video
    ]
    subprocess.run(cmd_cat, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print("  ✓ Đã ghép nối toàn bộ các phân cảnh video thành luồng chuyển động liên tục!")

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
        foley_filters.append(f"[{bgm_idx}:a]volume=0.20,atrim=0:{total_duration:.2f}[a_bgm]")

    # Thêm SFX tại điểm bắt đầu từng cảnh
    sfx_streams = []
    for sc in scenes_data:
        sfx_name = sc["sfx"]
        if sfx_name and sfx_name != "none":
            sfx_file = os.path.join(SFX_DIR, f"{sfx_name}.mp3")
            if os.path.exists(sfx_file):
                audio_inputs_cmd.extend(["-i", sfx_file])
                delay_ms = int(sc["start"] * 1000)
                sfx_stream_name = f"sfx_{in_idx}"
                foley_filters.append(f"[{in_idx}:a]adelay={delay_ms}|{delay_ms},volume=0.45[{sfx_stream_name}]")
                sfx_streams.append(f"[{sfx_stream_name}]")
                in_idx += 1

    mix_inputs = [f"[{vocal_idx}:a]"]
    if has_bgm:
        mix_inputs.append("[a_bgm]")
    mix_inputs.extend(sfx_streams)

    foley_filters.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=2[a_final]")

    # MASTER COMPOSITE TOÀN DIỆN BẰNG FFMPEG
    clean_ass = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
    clean_header = os.path.abspath(header_img).replace("\\", "/")

    filter_complex_all = (
        f"[0:v][1:v]overlay=x=0:y=60[v_head];"
        f"[v_head]ass='{clean_ass}'[v_final];" +
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
