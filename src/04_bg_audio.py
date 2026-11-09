"""
MODULE 4: QUẢN LÝ VIDEO NỀN & HIỆU ỨNG ÂM THANH (BG & SFX Manager)
=====================================================================
Chức năng:
  - TẠO NỀN MÀU GRADIENT ĐỘNG (Không cần video Minecraft, siêu nhẹ, đẹp mắt)
  - Cắt video nền Minecraft Parkour nếu người dùng thích dạng gameplay
  - Ghép giọng đọc + Hiệu ứng âm thanh meme (laugh, boom, ting, bruh)
  - Trộn nhạc nền lofi nhẹ nhàng
"""

import os
import random
import re
import shutil
import subprocess

ASSETS_DIR   = r"d:\Dat\App_Video\assets"
BG_DIR       = os.path.join(ASSETS_DIR, "backgrounds")
SFX_DIR      = os.path.join(ASSETS_DIR, "sfx")
OUT_DIR      = r"d:\Dat\App_Video\output"

# BỘ BẢNG MÀU GRADIENT ĐỘNG CHUẨN XU HƯỚNG TIKTOK / SHORTS
GRADIENT_PALETTES = {
    "neon": {
        "name": "🌌 Tím Neon Dạ Quang (Cyberpunk)",
        "c0": "0x581c87", "c1": "0x0f172a", "type": "radial", "speed": "0.015"
    },
    "sunset": {
        "name": "🌅 Hoàng Hôn Cam Đào (Sunset Vibe)",
        "c0": "0x831843", "c1": "0x7c2d12", "type": "linear", "speed": "0.012"
    },
    "dark": {
        "name": "🌑 Xám Than Tối Sang Trọng (Aesthetic Dark)",
        "c0": "0x27272a", "c1": "0x09090b", "type": "radial", "speed": "0.008"
    },
    "ocean": {
        "name": "🌊 Xanh Thẳm Đại Dương (Deep Ocean)",
        "c0": "0x064e3b", "c1": "0x0f172a", "type": "linear", "speed": "0.015"
    },
    "midnight": {
        "name": "✨ Đêm Huyền Bí (Midnight Blue)",
        "c0": "0x1e1b4b", "c1": "0x030712", "type": "radial", "speed": "0.01"
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


def generate_gradient_background(duration_sec: float, output_path: str, palette_key: str = "random") -> str:
    """
    Tạo video nền màu Gradient chuyển sắc động tuyệt đẹp (1080x1920 9:16).
    Không cần file video ngoài, render siêu nhanh (1-2s), dung lượng siêu nhẹ!
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if palette_key == "random" or palette_key not in GRADIENT_PALETTES:
        palette_key = random.choice(list(GRADIENT_PALETTES.keys()))

    p = GRADIENT_PALETTES[palette_key]
    print(f"  🎨 Tạo nền màu Gradient: {p['name']} ({duration_sec:.1f}s)...")

    # Sử dụng FFmpeg filter gradients để tạo luồng màu chuyển động mượt mà
    filter_expr = f"gradients=s=1080x1920:d={duration_sec:.1f}:c0={p['c0']}:c1={p['c1']}:type={p['type']}:speed={p['speed']}"

    run_ffmpeg(
        "-f", "lavfi",
        "-i", filter_expr,
        "-t", f"{duration_sec:.2f}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        output_path
    )
    print(f"  [✓] Nền màu Gradient sẵn sàng: {output_path}")
    return output_path


def cut_background_video(duration_sec: float, output_path: str, bg_video_path: str = None) -> str:
    """Cắt ngẫu nhiên 1 đoạn Minecraft Parkour chuẩn 9:16 (1080x1920) tắt tiếng."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if bg_video_path is None:
        videos = [f for f in os.listdir(BG_DIR) if f.lower().endswith((".mp4", ".mkv", ".mov"))]
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
        # Tự động tạo gradient thay vì video lỗi
        generate_gradient_background(duration_sec, output_path)

    print(f"  [✓] Video nền sẵn sàng: {output_path}")
    return output_path


def prepare_background(duration_sec: float, output_path: str, mode: str = "gradient", palette: str = "random") -> str:
    """
    Hàm chuẩn bị background thông minh:
    - mode = "gradient" (hoặc "color"): Tạo nền màu chuyển sắc sang trọng.
    - mode = "minecraft" (hoặc "video"): Dùng video gameplay trong assets/backgrounds/.
    """
    if mode in ["gradient", "color"]:
        return generate_gradient_background(duration_sec, output_path, palette)
    else:
        return cut_background_video(duration_sec, output_path)


def build_mixed_audio_with_sfx(audio_sequence: list[str], output_path: str, bg_music_path: str = None) -> str:
    """
    Nối tuần tự các file âm thanh (giọng đọc + hiệu ứng âm thanh meme SFX)
    và trộn nhạc nền lofi nhẹ.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    audio_dir = os.path.join(OUT_DIR, "audio")
    voice_sfx_merged = os.path.join(audio_dir, "_voice_sfx_merged.mp3")

    cmd = []
    filter_parts = []
    for i, path in enumerate(audio_sequence):
        cmd.extend(["-i", path])
        filter_parts.append(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}]")

    concat_inputs = "".join(f"[a{i}]" for i in range(len(audio_sequence)))
    filter_parts.append(f"{concat_inputs}concat=n={len(audio_sequence)}:v=0:a=1[out]")

    run_ffmpeg(
        *cmd,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]",
        "-c:a", "libmp3lame",
        "-q:a", "2",
        voice_sfx_merged
    )

    # Trộn nhạc nền lofi nếu có
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
    out_test = os.path.join(OUT_DIR, "test_color_bg.mp4")
    generate_gradient_background(5.0, out_test, "neon")
    print(f"Test gradient background OK: {out_test}")
