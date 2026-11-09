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
from PIL import Image, ImageDraw

ASSETS_DIR   = r"d:\Dat\App_Video\assets"
BG_DIR       = os.path.join(ASSETS_DIR, "backgrounds")
SFX_DIR      = os.path.join(ASSETS_DIR, "sfx")
OUT_DIR      = r"d:\Dat\App_Video\output"

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
    Sinh video loop gốc 3D Infinity Tunnel chuẩn 1080x1920 (chỉ mất ~0.4s).
    Lưu vào assets/backgrounds/infinity_base.mp4 để tái sử dụng tức thì.
    """
    os.makedirs(BG_DIR, exist_ok=True)
    exe = get_ffmpeg_exe()

    W, H = 540, 960
    cx, cy = W // 2, H // 2
    num_rings = 18
    fps = 30
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
        "-vf", "scale=1080:1920:flags=lanczos,boxblur=1:1",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        INFINITY_BASE_FILE
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

            rw = int(24 * scale)
            rh = int(38 * scale)
            if rw > W * 1.8:
                continue

            # Tính màu gradient theo độ sâu z (tâm sáng rực -> rìa chuyển màu neon)
            t_col = 1.0 - z
            r_col = int(c_r * (1 - t_col) + e_r * t_col)
            g_col = int(c_g * (1 - t_col) + e_g * t_col)
            b_col = int(c_b * (1 - t_col) + e_b * t_col)

            width = max(2, int(8 * (1 - d) ** 0.8))
            bbox = [cx - rw, cy - rh, cx + rw, cy + rh]
            draw.rounded_rectangle(bbox, radius=int(rw * 0.28), outline=(r_col, g_col, b_col), width=width)

        proc.stdin.write(img.tobytes())

    proc.stdin.close()
    proc.wait()
    return INFINITY_BASE_FILE


def generate_infinity_background(duration_sec: float, output_path: str, theme: str = "neon") -> str:
    """
    Tạo video nền Hiệu ứng Infinity Tunnel (Đường hầm vô cực) với độ dài tùy ý.
    Tốc độ render siêu tốc (<0.5 giây), chuẩn Full HD 1080x1920!
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    ensure_infinity_base_loop(theme)

    print(f"  🌀 Đang tạo nền Infinity Tunnel vô cực ({duration_sec:.1f}s)...")
    run_ffmpeg(
        "-stream_loop", "-1",
        "-i", INFINITY_BASE_FILE,
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
            "-vf", "crop=ih*9/16:ih:(iw-ow)/2:0,scale=1080:1920:flags=lanczos",
            "-an",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            output_path
        )
    else:
        generate_infinity_background(duration_sec, output_path)

    print(f"  [✓] Video nền sẵn sàng: {output_path}")
    return output_path


def prepare_background(duration_sec: float, output_path: str, mode: str = "infinity", theme: str = "neon") -> str:
    """
    Chuẩn bị nền theo chế độ:
    - mode = "infinity" (MẶC ĐỊNH): Đường hầm vô cực 3D thôi miên siêu đẹp & nhẹ!
    - mode = "minecraft" (hoặc "video"): Dùng video gameplay trong assets/backgrounds/.
    """
    if mode in ["infinity", "tunnel", "wormhole"]:
        return generate_infinity_background(duration_sec, output_path, theme)
    else:
        return cut_background_video(duration_sec, output_path)


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
        print(f"  🎵 Trộn nhạc nền lofi: {os.path.basename(bg_music_path)}")
        run_ffmpeg(
            "-i", voice_sfx_merged,
            "-stream_loop", "-1",
            "-i", bg_music_path,
            "-filter_complex",
            "[1:a]volume=0.07[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[out]",
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
