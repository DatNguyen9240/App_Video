"""
MODULE 3: GIỌNG ĐỌC TTS + ĐO THỜI LƯỢNG (TTS Engine)
======================================================
Chức năng:
  - Chuyển text tiếng Việt → file MP3 bằng Microsoft Edge TTS (Nam Minh / Hoài My)
  - Tự động fallback sang Google TTS (gTTS) nếu Microsoft tạm thời chặn kết nối/rate-limit
  - Lọc ký tự XML/nháy đơn/nháy kép/emoji tránh lỗi SSML
  - Đo thời lượng audio chính xác (ms) để căn khớp timing với video/card

TEST ĐỘC LẬP: python src/03_tts.py
"""

import os
import re
import struct
import subprocess
import tempfile
import time

VOICE_VI_MALE   = "vi-VN-NamMinhNeural"    # Giọng nam tiếng Việt
VOICE_VI_FEMALE = "vi-VN-HoaiMyNeural"     # Giọng nữ tiếng Việt

def get_configured_voice_and_rate():
    """Đọc cấu hình giọng đọc và tốc độ từ .env hoặc môi trường."""
    voice = os.environ.get("TTS_VOICE", "").strip()
    rate = os.environ.get("TTS_RATE", "").strip()
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TTS_VOICE="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val: voice = val
                elif line.startswith("TTS_RATE="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val: rate = val
    return voice or VOICE_VI_MALE, rate or "+10%"

DEFAULT_VOICE, DEFAULT_RATE = get_configured_voice_and_rate()

OUT_DIR = r"d:\Dat\App_Video\output\audio"


def clean_for_tts(text: str) -> str:
    """Loại bỏ emoji, ký tự đặc biệt và dấu nháy/XML gây lỗi SSML của Edge TTS."""
    # Giữ lại chữ tiếng Việt và ký tự cơ bản, loại bỏ emoji ngoài BMP
    cleaned = re.sub(r"[^\u0000-\u2BFF]+", " ", text)
    # Xóa các dấu nháy đơn, nháy kép, dấu XML làm vỡ giao thức SSML của Edge TTS
    for ch in ["'", '"', "‘", "’", "“", "”", "`", "<", ">", "&", "\\", "/", "^"]:
        cleaned = cleaned.replace(ch, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def fallback_gtts(text: str, output_path: str):
    """Fallback sang Google TTS (gTTS) nếu Microsoft Edge TTS gặp sự cố kết nối."""
    from gtts import gTTS
    tts = gTTS(text=text, lang="vi", slow=False)
    tts.save(output_path)


def text_to_speech(text: str, output_path: str,
                   voice: str = DEFAULT_VOICE,
                   rate: str = "+10%",
                   max_retries: int = 3) -> str:
    """
    Chuyển text → file MP3 dùng edge-tts CLI qua file trung gian UTF-8.
    Nếu Microsoft Edge TTS bị rate-limit sau max_retries, tự động chuyển sang gTTS.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cleaned = clean_for_tts(text)

    # Ghi text vào file tạm định dạng UTF-8 để Windows CLI không làm vỡ dấu tiếng Việt
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as tf:
        tf.write(cleaned)
        temp_txt_path = tf.name

    try:
        for attempt in range(1, max_retries + 1):
            cmd = [
                "edge-tts",
                "--voice", voice,
                "--rate", rate,
                "--file", temp_txt_path,
                "--write-media", output_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path

            wait = attempt * 2.5
            print(f"     [!] Edge TTS bận (lần {attempt}/{max_retries}), đợi {wait:.1f}s...")
            time.sleep(wait)

        # Nếu sau max_retries vẫn lỗi -> Dùng fallback gTTS an toàn tuyệt đối
        print("     ⚠️ Đang chuyển sang chế độ dự phòng Google TTS để không gián đoạn...")
        fallback_gtts(cleaned, output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path

        raise RuntimeError(f"Cả Edge TTS và fallback đều thất bại cho: {output_path}")
    finally:
        if os.path.exists(temp_txt_path):
            try:
                os.remove(temp_txt_path)
            except OSError:
                pass


def get_mp3_duration_ms(filepath: str) -> float:
    """
    Đọc thời lượng file MP3 (milli-giây) từ header frame hoặc FFmpeg.
    """
    try:
        total_frames = 0
        sample_rate = 44100
        with open(filepath, "rb") as f:
            data = f.read()

        i = 0
        while i < len(data) - 4:
            if data[i:i+3] == b"ID3":
                id3_size = (
                    (data[i+6] & 0x7F) << 21 |
                    (data[i+7] & 0x7F) << 14 |
                    (data[i+8] & 0x7F) << 7  |
                    (data[i+9] & 0x7F)
                )
                i += 10 + id3_size
                continue

            if data[i] == 0xFF and (data[i+1] & 0xE0) == 0xE0:
                header = struct.unpack(">I", data[i:i+4])[0]
                layer = (header >> 17) & 0x3
                bitrate_idx = (header >> 12) & 0xF
                sr_idx = (header >> 10) & 0x3
                padding = (header >> 9) & 0x1

                sr_table = {0: 44100, 1: 48000, 2: 32000}
                br_table = {
                    1: [0,32,40,48,56,64,80,96,112,128,160,192,224,256,320,0],
                    2: [0,32,48,56,64,80,96,112,128,160,192,224,256,320,384,0],
                }

                if layer not in br_table or sr_idx not in sr_table:
                    i += 1
                    continue

                bitrate = br_table.get(layer, br_table[1])[bitrate_idx] * 1000
                sample_rate = sr_table[sr_idx]

                if bitrate == 0:
                    i += 1
                    continue

                frame_size = 144 * bitrate // sample_rate + padding
                total_frames += 1
                i += frame_size
            else:
                i += 1

        if total_frames > 0:
            return total_frames * 1152 / sample_rate * 1000
    except Exception:
        pass

    # Fallback đo thời lượng bằng FFmpeg
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        res = subprocess.run([exe, "-i", filepath], capture_output=True, text=True)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
        if m:
            h, mn, s = m.groups()
            return (int(h) * 3600 + int(mn) * 60 + float(s)) * 1000
    except Exception:
        pass

    return 3000.0  # Mặc định 3s an toàn


def generate_all_audio(post, out_dir: str = OUT_DIR, voice: str = DEFAULT_VOICE) -> dict:
    """
    Sinh file audio cho bài đăng + từng comment và đo thời lượng.
    Giãn cách hợp lý (0.8s) để không bị máy chủ TTS chặn.
    """
    os.makedirs(out_dir, exist_ok=True)
    result = {"post": None, "comments": []}

    print(f"  Đang tạo audio bài đăng...")
    post_path = os.path.join(out_dir, "post.mp3")
    text_to_speech(post.content, post_path, voice=voice)
    dur = get_mp3_duration_ms(post_path)
    result["post"] = {"path": post_path, "duration_ms": dur}
    print(f"     → {dur/1000:.2f}s")
    time.sleep(0.8)

    for i, comment in enumerate(post.comments, 1):
        print(f"  Đang tạo audio comment {i}...")
        cmt_path = os.path.join(out_dir, f"comment_{i}.mp3")
        text_to_speech(comment.content, cmt_path, voice=voice)
        dur = get_mp3_duration_ms(cmt_path)
        result["comments"].append({"path": cmt_path, "duration_ms": dur})
        print(f"     → {dur/1000:.2f}s")
        time.sleep(0.8)

    return result


if __name__ == "__main__":
    import importlib.util
    spec = importlib.util.spec_from_file_location("content", r"d:\Dat\App_Video\src\01_content.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    SAMPLE_POST = mod.SAMPLE_POST

    print("=" * 55)
    print("  MODULE 3: TTS ENGINE — Đang tạo giọng đọc tiếng Việt...")
    print("=" * 55)
    print(f"  Giọng: {DEFAULT_VOICE}\n")

    timing = generate_all_audio(SAMPLE_POST)

    print("\n📊 THỜI LƯỢNG (ms / s):")
    print(f"  Post       : {timing['post']['duration_ms']/1000:.2f}s")
    total = timing["post"]["duration_ms"]
    for i, c in enumerate(timing["comments"], 1):
        print(f"  Comment {i}  : {c['duration_ms']/1000:.2f}s")
        total += c["duration_ms"]
    print(f"  ─────────────────────────")
    print(f"  TỔNG CỘNG  : {total/1000:.2f}s")

    print(f"\n✅ Module 3 OK — Thư mục audio: {OUT_DIR}")
