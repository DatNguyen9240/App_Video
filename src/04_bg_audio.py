"""
MODULE 4: QUẢN LÝ VIDEO NỀN & HIỆU ỨNG ÂM THANH (BG & SFX Manager)
=====================================================================
Chức năng:
  - TẠO HIỆU ỨNG ĐƯỜNG HẦM VÔ CỰC (3D INFINITY TUNNEL / WORMHOLE)
    -> Hiệu ứng thôi miên cực cuốn hút, chuẩn xu hướng TikTok / Reels!
  - Cắt video nền Minecraft Parkour nếu người dùng thích dạng gameplay
  - Tạo nền màu Gradient chuyển động mượt mà
  - Ghép giọng đọc + Hiệu ứng âm thanh meme (laugh, boom, ting, bruh)
  - Trộn nhạc nền lofi nhẹ nhàng
"""

import math
import os
import random
import re
import shutil
import subprocess
import sys
from PIL import Image, ImageDraw

SRC_DIR      = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC_DIR)
import config as config_mod

BASE_DIR     = os.path.dirname(SRC_DIR)
ASSETS_DIR   = os.path.join(BASE_DIR, "assets")
BG_DIR       = os.path.join(ASSETS_DIR, "backgrounds")
SFX_DIR      = os.path.join(ASSETS_DIR, "sfx")
OUT_DIR      = os.path.join(BASE_DIR, "output")

INFINITY_BASE_FILE = os.path.join(BG_DIR, "infinity_base.mp4")

# BỘ BẢNG MÀU ĐƯỜNG HẦM VÔ CỰC (3D INFINITY TUNNEL)
INFINITY_THEMES = {
    "neon": {
        "name": "🌀 Cyberpunk Neon Tunnel (Xanh Cyan - Tím Dạ Quang)",
        "bg": (6, 5, 16),
        "center_rgb": (30, 240, 255),
        "edge_rgb": (230, 30, 250),
    },
    "dark": {
        "name": "🌑 Dark Mirror Room (Gương vô cực LED xám sang trọng)",
        "bg": (5, 5, 8),
        "center_rgb": (180, 190, 210),
        "edge_rgb": (60, 65, 85),
    },
    "gold": {
        "name": "✨ Golden Matrix (Đường hầm Vàng Kim quyền lực)",
        "bg": (12, 8, 4),
        "center_rgb": (255, 230, 120),
        "edge_rgb": (240, 120, 20),
    }
}


def get_ffmpeg_exe() -> str:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def run_ffmpeg(*args) -> subprocess.CompletedProcess:
    exe = get_ffmpeg_exe()
    cmd = [exe, "-y"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Lỗi FFmpeg:\n{result.stderr[-1000:]}")
    return result


def get_media_duration(filepath: str) -> float:
    exe = get_ffmpeg_exe()
    result = subprocess.run([exe, "-i", filepath], capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if match:
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def ensure_infinity_base_loop(theme: str = "neon") -> str:
    """
    Sinh video loop gốc 3D Infinity Tunnel chuẩn Native 1080x1920 @ 60 FPS siêu mượt.
    Lưu vào assets/backgrounds/infinity_base_{theme}.mp4 để tái sử dụng tức thì.
    """
    os.makedirs(BG_DIR, exist_ok=True)
    exe = get_ffmpeg_exe()

    target_file = os.path.join(BG_DIR, f"infinity_base_{theme}.mp4")
    if os.path.exists(target_file) and os.path.getsize(target_file) > 10000:
        return target_file

    cfg_main = config_mod.load_config()
    layout = cfg_main.get("visual_layout", {})
    W = layout.get("canvas_width", 1080)
    H = layout.get("canvas_height", 1920)
    fps = layout.get("fps", 60)

    cx, cy = W // 2, H // 2
    num_rings = 20
    loop_duration = 1.5
    total_frames = int(fps * loop_duration)

    cfg = INFINITY_THEMES.get(theme, INFINITY_THEMES["neon"])
    bg_color = cfg["bg"]
    c_r, c_g, c_b = cfg["center_rgb"]
    e_r, e_g, e_b = cfg["edge_rgb"]

    ffmpeg_cmd = [
        exe, "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{W}x{H}",
        "-pix_fmt", "rgb24",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        target_file
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    for f_idx in range(total_frames):
        phase = f_idx / total_frames
        img = Image.new("RGB", (W, H), bg_color)
        draw = ImageDraw.Draw(img)

        for i in range(num_rings):
            z = (i / num_rings + phase) % 1.0
            if z < 0.03:
                continue

            d = 1.0 - z
            scale = (1.0 / (d * 3.2 + 0.08)) ** 1.35

            rw = int(48 * scale)
            rh = int(76 * scale)
            if rw > W * 1.8:
                continue

            # Tính màu gradient theo độ sâu z (tâm sáng rực -> rìa chuyển màu neon)
            t_col = 1.0 - z
            r_col = int(c_r * (1 - t_col) + e_r * t_col)
            g_col = int(c_g * (1 - t_col) + e_g * t_col)
            b_col = int(c_b * (1 - t_col) + e_b * t_col)

            width = max(3, int(14 * (1 - d) ** 0.8))
            bbox = [cx - rw, cy - rh, cx + rw, cy + rh]
            draw.rounded_rectangle(bbox, radius=int(rw * 0.28), outline=(r_col, g_col, b_col), width=width)

        proc.stdin.write(img.tobytes())

    proc.stdin.close()
    proc.wait()
    return target_file


def generate_infinity_background(duration_sec: float, output_path: str, theme: str = "neon") -> str:
    """
    Tạo video nền Hiệu ứng Infinity Tunnel (Đường hầm vô cực) với độ dài tùy ý.
    Tốc độ render siêu tốc (<0.5 giây), chuẩn Full HD 1080x1920!
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    base_file = ensure_infinity_base_loop(theme)

    print(f"  🌀 Đang tạo nền Infinity Tunnel ({theme}) vô cực ({duration_sec:.1f}s)...")
    run_ffmpeg(
        "-stream_loop", "-1",
        "-i", base_file,
        "-t", f"{duration_sec:.2f}",
        "-c", "copy",
        output_path
    )
    print(f"  [✓] Nền Infinity Tunnel sẵn sàng: {output_path}")
    return output_path


def cut_background_video(duration_sec: float, output_path: str, bg_video_path: str = None) -> str:
    """Cắt ngẫu nhiên 1 đoạn Minecraft Parkour chuẩn 9:16 (1080x1920) tắt tiếng."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if bg_video_path is None:
        videos = [f for f in os.listdir(BG_DIR) if f.lower().endswith((".mp4", ".mkv", ".mov")) and "base" not in f]
        if videos:
            bg_video_path = os.path.join(BG_DIR, random.choice(videos))

    if bg_video_path and os.path.exists(bg_video_path):
        print(f"  🎬 Dùng video Minecraft: {os.path.basename(bg_video_path)}")
        total_duration = get_media_duration(bg_video_path)
        max_start = max(0.0, total_duration - duration_sec - 1.0)
        start_sec = random.uniform(0.0, max_start) if max_start > 0 else 0.0

        print(f"     Cắt từ {start_sec:.1f}s đến {start_sec + duration_sec:.1f}s ({duration_sec:.1f}s)")
        run_ffmpeg(
            "-ss", str(start_sec),
            "-t", str(duration_sec),
            "-i", bg_video_path,
            "-vf", "crop=ih*9/16:ih:(iw-ow)/2:0,scale=1080:1920:flags=lanczos,eq=brightness=-0.04:contrast=1.05",
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            output_path
        )
    else:
        generate_infinity_background(duration_sec, output_path)

    print(f"  [✓] Video nền sẵn sàng: {output_path}")
    return output_path


def generate_split_screen_background(duration_sec: float, output_path: str) -> str:
    """
    Tạo video nền TikTok Split-Screen chuẩn viral:
    - Nửa trên (0 đến 900px): Gameplay Minecraft/Parkour sắc nét 60 FPS.
    - Đường chia đôi: Thanh LED dạ quang cyan/indigo cao cấp (4px).
    - Nửa dưới (900 đến 1920px): Nền tối sang trọng (#090d16) để hiển thị thẻ Threads rõ nét.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    exe = get_ffmpeg_exe()

    videos = [f for f in os.listdir(BG_DIR) if f.lower().endswith((".mp4", ".mkv", ".mov")) and "base" not in f]
    bg_video_path = os.path.join(BG_DIR, random.choice(videos)) if videos else None

    if bg_video_path and os.path.exists(bg_video_path):
        total_duration = get_media_duration(bg_video_path)
        max_start = max(0.0, total_duration - duration_sec - 1.0)
        start_sec = random.uniform(0.0, max_start) if max_start > 0 else 0.0

        print(f"  🔥 Tạo nền TikTok Split-Screen từ gameplay (từ {start_sec:.1f}s, dài {duration_sec:.1f}s)...")
        cmd = [
            exe, "-y",
            "-ss", str(start_sec),
            "-t", f"{duration_sec:.2f}",
            "-i", bg_video_path,
            "-f", "lavfi",
            "-i", "color=c=0x0a0e17:s=1080x1920:r=60",
            "-filter_complex",
            "[0:v]scale=1080:720:force_original_aspect_ratio=increase,crop=1080:720[top];"
            "[1:v][top]overlay=0:0[bg];"
            "[bg]drawbox=y=716:color=0x6366f1@0.95:width=1080:height=4:t=fill[out]",
            "-map", "[out]",
            "-t", f"{duration_sec:.2f}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            output_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return output_path

    # Fallback nếu không có video gameplay
    return generate_infinity_background(duration_sec, output_path, "neon")


def generate_gradient_background(duration_sec: float, output_path: str, theme: str = "cyber") -> str:
    """
    Tạo video nền Gradient màu tĩnh cao cấp (Dark Cyberpunk hoặc Cozy Lofi).
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    exe = get_ffmpeg_exe()
    color_hex = "0x090d16" if theme == "cyber" else "0x160e0a"

    cmd = [
        exe, "-y",
        "-f", "lavfi",
        "-i", f"color=c={color_hex}:s=1080x1920:r=60",
        "-t", f"{duration_sec:.2f}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        output_path
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    return output_path


def prepare_background(duration_sec: float, output_path: str, mode: str = "infinity", theme: str = "neon") -> str:
    """
    Chuẩn bị nền theo chế độ đa dạng:
    - split_gameplay: TikTok Split-Screen (Nửa trên Gameplay 60FPS, Nửa dưới Thẻ Threads)
    - infinity_neon / infinity: 3D Infinity Cyberpunk Neon
    - infinity_dark: 3D Infinity Dark Luxury
    - infinity_gold: 3D Infinity Golden Matrix
    - minecraft / video: Gameplay toàn màn hình 9:16
    - gradient_cyber: Nền tối Neon tĩnh cao cấp
    - gradient_lofi: Nền cà phê Lofi ấm cúng
    """
    m = (mode or "minecraft").lower()
    if m in ["minecraft", "video", "parkour", "gameplay", "full_gameplay"]:
        return cut_background_video(duration_sec, output_path)
    elif m in ["split", "split_gameplay", "split_screen"]:
        return generate_split_screen_background(duration_sec, output_path)
    elif m in ["gradient_cyber", "cyber"]:
        return generate_gradient_background(duration_sec, output_path, "cyber")
    elif m in ["gradient_lofi", "lofi"]:
        return generate_gradient_background(duration_sec, output_path, "lofi")
    elif m in ["infinity_dark", "dark"]:
        return generate_infinity_background(duration_sec, output_path, "dark")
    elif m in ["infinity_gold", "gold"]:
        return generate_infinity_background(duration_sec, output_path, "gold")
    elif m in ["infinity_neon", "infinity", "tunnel", "wormhole"]:
        return generate_infinity_background(duration_sec, output_path, "neon")
    elif m in ["minecraft", "video", "parkour"]:
        return cut_background_video(duration_sec, output_path)
    else:
        return generate_infinity_background(duration_sec, output_path, theme)


def build_mixed_audio_with_sfx(audio_sequence: list[str], output_path: str, bg_music_path: str = None) -> str:
    """
    Nối tuần tự các file âm thanh (giọng đọc + hiệu ứng âm thanh meme SFX)
    và trộn nhạc nền lofi nhẹ. Đảm bảo an toàn 100% trước file hỏng.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    audio_dir = os.path.join(OUT_DIR, "audio")
    voice_sfx_merged = os.path.join(audio_dir, "_voice_sfx_merged.mp3")
    exe = get_ffmpeg_exe()

    # Kiểm định từng file âm thanh trước khi đưa vào concat
    valid_sequence = []
    for path in audio_sequence:
        if path and os.path.exists(path) and os.path.getsize(path) > 100:
            chk = subprocess.run([exe, "-i", path, "-f", "null", "-"], capture_output=True)
            if chk.returncode == 0:
                valid_sequence.append(path)
            else:
                print(f"  ⚠️ File audio {os.path.basename(path)} không hợp lệ, đang tự động sửa...")
                # Thử tự động chuẩn hóa bằng FFmpeg
                fix_path = path + ".fix.mp3"
                fix_res = subprocess.run([exe, "-y", "-i", path, "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", fix_path], capture_output=True)
                if fix_res.returncode == 0 and os.path.exists(fix_path):
                    shutil.move(fix_path, path)
                    valid_sequence.append(path)

    if not valid_sequence:
        # Nếu không có audio nào, tạo audio im lặng 3s
        run_ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "3", voice_sfx_merged)
        valid_sequence = [voice_sfx_merged]

    cmd = []
    filter_parts = []
    for i, path in enumerate(valid_sequence):
        cmd.extend(["-i", path])
        filter_parts.append(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}]")

    concat_inputs = "".join(f"[a{i}]" for i in range(len(valid_sequence)))
    filter_parts.append(f"{concat_inputs}concat=n={len(valid_sequence)}:v=0:a=1[out]")

    run_ffmpeg(
        *cmd,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]",
        "-c:a", "libmp3lame",
        "-q:a", "2",
        voice_sfx_merged
    )

    if bg_music_path and os.path.exists(bg_music_path):
        cfg = config_mod.load_config()
        pacing = cfg.get("emotional_pacing", {})
        bg_vol = pacing.get("bgm_volume_normal", 0.16)
        duck_enabled = pacing.get("enable_bgm_ducking", True)
        print(f"  🎵 Trộn nhạc nền TikTok (volume={bg_vol}, ducking={duck_enabled}): {os.path.basename(bg_music_path)}")

        if duck_enabled:
            # Dynamic ducking qua sidechaincompress:
            # Nhạc nền tự hạ khi có giọng nói, tự động trồi lên khi dừng câu hoặc có khoảng lặng
            filter_str = (
                f"[1:a]volume={bg_vol}[bg];"
                f"[0:a]asplit=2[v_main][v_side];"
                f"[bg][v_side]sidechaincompress=threshold=0.03:ratio=4:attack=50:release=350[ducked_bg];"
                f"[v_main][ducked_bg]amix=inputs=2:duration=first:dropout_transition=2[out]"
            )
        else:
            filter_str = f"[1:a]volume={bg_vol}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[out]"

        run_ffmpeg(
            "-i", voice_sfx_merged,
            "-stream_loop", "-1",
            "-i", bg_music_path,
            "-filter_complex", filter_str,
            "-map", "[out]",
            "-c:a", "libmp3lame",
            "-q:a", "2",
            output_path
        )
    else:
        shutil.copy(voice_sfx_merged, output_path)

    print(f"  [✓] Audio tổng (đã có SFX meme): {output_path}")
    return output_path


if __name__ == "__main__":
    out_test = os.path.join(OUT_DIR, "test_infinity_out.mp4")
    generate_infinity_background(5.0, out_test, "neon")
    print(f"Test infinity background OK: {out_test}")
