"""
MODULE 5: GHÉP VIDEO HOÀN CHỈNH (Video Compositor & Pipeline)
==============================================================
Chức năng:
  - Đồng bộ giọng đọc tiếng Việt + Âm thanh Meme (Laugh, Boom, Ting, Bruh)
  - Hiện ảnh Meme Sticker (😂, 💥, 💀) đúng lúc âm thanh vang lên
  - Ghép thẻ bài đăng Threads & đổi thẻ bình luận mượt mà
  - Cắt video nền Minecraft Parkour đúng độ dài
  - Xuất video hoàn chỉnh chuẩn 9:16 (1080x1920)
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)

OUT_DIR    = os.path.join(BASE_DIR, "output")
CARDS_DIR  = os.path.join(OUT_DIR, "cards")
AUDIO_DIR  = os.path.join(OUT_DIR, "audio")
FINAL_DIR  = os.path.join(OUT_DIR, "final")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
SFX_DIR    = os.path.join(ASSETS_DIR, "sfx")
MEME_DIR   = os.path.join(ASSETS_DIR, "memes")

POST_Y    = 240   # Tọa độ Y thẻ bài đăng (phía trên)
COMMENT_Y = 720   # Tọa độ Y thẻ bình luận (bên dưới bài đăng)

# Map SFX sang ảnh sticker tương ứng
SFX_TO_MEME = {
    "laugh": "laugh.png",
    "boom":  "boom.png",
    "bruh":  "skull.png",
    "ting":  "shock.png"
}


def load_module(name: str, filename: str):
    path = os.path.join(SRC_DIR, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


content_mod = load_module("content", "01_content.py")
card_mod    = load_module("card_gen", "02_card_gen.py")
tts_mod     = load_module("tts", "03_tts.py")
bg_mod      = load_module("bg_audio", "04_bg_audio.py")


def build_timeline_with_sfx_and_memes(post, timing: dict) -> tuple[list[dict], list[str], float]:
    """
    Tính mốc thời gian hiển thị từng thẻ và danh sách audio cần nối (kèm SFX & Meme sticker):
    - Khi đọc xong comment có tag 'laugh' -> phát laugh.mp3 -> hiện sticker 😂 -> giữ card.
    - Khi đọc xong comment có tag 'boom' -> phát boom.mp3 -> hiện sticker 💥 -> giữ card.
    """
    timeline = []
    audio_sequence = []
    cursor = 0.2
    gap = 0.25

    # 1. Thẻ bài đăng
    post_dur = timing["post"]["duration_ms"] / 1000.0
    audio_sequence.append(timing["post"]["path"])

    comment_cursor = cursor + post_dur + gap

    # 2. Các thẻ bình luận
    # Tính tọa độ Y của thẻ Comment dựa trên chiều cao thực tế của thẻ Post
    post_path = os.path.join(CARDS_DIR, "post.png")
    actual_post_h = 320
    if os.path.exists(post_path):
        try:
            from PIL import Image
            with Image.open(post_path) as p_img:
                actual_post_h = p_img.height
        except Exception:
            pass

    actual_comment_y = POST_Y + actual_post_h + 24

    for i, c in enumerate(post.comments, 1):
        c_dur = timing["comments"][i - 1]["duration_ms"] / 1000.0
        c_audio_path = timing["comments"][i - 1]["path"]
        audio_sequence.append(c_audio_path)

        # Kiểm tra SFX
        sfx_dur = 0.0
        sfx_name = getattr(c, "sfx", "none")
        sfx_file = os.path.join(SFX_DIR, f"{sfx_name}.mp3") if sfx_name != "none" else None

        if sfx_file and os.path.exists(sfx_file):
            sfx_dur = min(bg_mod.get_media_duration(sfx_file), 1.5)
            audio_sequence.append(sfx_file)

        c_start = comment_cursor
        c_end = c_start + c_dur + sfx_dur + gap

        # Thẻ bình luận
        timeline.append({
            "type": "comment",
            "index": i,
            "start": c_start,
            "end": c_end,
            "x": "(W-w)/2",
            "y": actual_comment_y,
            "width": 960,
            "image": os.path.join(CARDS_DIR, f"comment_{i}.png"),
        })

        # Nếu có SFX -> hiện Meme sticker pop-up ngay khi dứt câu đọc
        if sfx_name in SFX_TO_MEME and sfx_dur > 0:
            meme_file = os.path.join(MEME_DIR, SFX_TO_MEME[sfx_name])
            if os.path.exists(meme_file):
                timeline.append({
                    "type": "meme_sticker",
                    "start": c_start + c_dur,
                    "end": c_end,
                    "x": "820",               # Nổi bật ở góc phải dưới thẻ comment
                    "y": str(actual_comment_y + 140),
                    "width": 190,
                    "image": meme_file,
                })

        comment_cursor = c_end

    total_duration = comment_cursor + 0.6  # đệm nhẹ cuối video

    # Thẻ Post ghim từ đầu đến hết video
    timeline.insert(0, {
        "type": "post",
        "start": cursor,
        "end": total_duration,
        "x": "(W-w)/2",
        "y": POST_Y,
        "width": 960,
        "image": os.path.join(CARDS_DIR, "post.png"),
    })

    return timeline, audio_sequence, total_duration


def compose_video(bg_video: str, audio_path: str, timeline: list[dict],
                  total_duration: float, output_path: str):
    """Overlay thẻ bài viết, bình luận & sticker meme lên video nền theo timeline."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    exe = bg_mod.get_ffmpeg_exe()

    inputs = ["-i", bg_video, "-i", audio_path]
    for item in timeline:
        inputs.extend(["-i", item["image"]])

    filter_chains = []
    prev_stream = "[0:v]"

    for idx, item in enumerate(timeline):
        img_idx = idx + 2
        layer_label = f"[layer{idx}]"
        out_stream = f"[v{idx}]"

        w = item.get("width", 980)
        filter_chains.append(f"[{img_idx}:v]scale={w}:-1:flags=lanczos{layer_label}")

        x_pos = item.get("x", "(W-w)/2")
        y_pos = str(item["y"])
        start_t = f"{item['start']:.2f}"
        end_t = f"{item['end']:.2f}"
        filter_chains.append(
            f"{prev_stream}{layer_label}overlay=x={x_pos}:y={y_pos}:enable='between(t,{start_t},{end_t})'{out_stream}"
        )
        prev_stream = out_stream

    filter_complex = ";".join(filter_chains)

    cmd = [
        exe, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", prev_stream,
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-t", f"{total_duration:.2f}",
        "-movflags", "+faststart",
        output_path
    ]

    print("  ⏳ Đang ghép video hoàn chỉnh (Cards + Meme Stickers + SFX)...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg composite lỗi:\n{res.stderr[-1000:]}")
    print(f"  [✓] Đã tạo thành công: {output_path}")


def get_default_bg_mode() -> str:
    """Đọc chế độ nền mặc định từ .env: 'infinity' (đường hầm vô cực), 'minecraft' (gameplay)."""
    mode = os.environ.get("BG_MODE", "").strip().lower()
    env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("BG_MODE="):
                    val = line.split("=", 1)[1].strip().lower()
                    if val: mode = val
    return mode or "infinity"


def run_pipeline(post=None, bg_mode: str = None) -> str:
    """Chạy quy trình tự động hoàn chỉnh từ dữ liệu -> render video."""
    if post is None:
        post = content_mod.SAMPLE_POST

    if bg_mode is None:
        bg_mode = get_default_bg_mode()

    print("\n" + "=" * 58)
    print("🎬 BẮT ĐẦU QUY TRÌNH TẠO VIDEO (CÓ MEME STICKER & SFX)")
    print("=" * 58)

    # 1. Render ảnh thẻ
    print("\n[Bước 1/4] 🎨 Render ảnh thẻ Threads...")
    post_img = card_mod.render_post_card(
        post.username, post.content, post.avatar_color, post.is_verified, post.likes
    )
    card_mod.save_card(post_img, os.path.join(CARDS_DIR, "post.png"))

    for i, c in enumerate(post.comments, 1):
        c_img = card_mod.render_comment_card(
            c.username, c.content, c.avatar_color, c.is_verified, c.likes,
            index=i, reply_to=post.username
        )
        card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))

    # 2. Tạo giọng đọc TTS
    print("\n[Bước 2/4] 🎙️ Tạo giọng đọc tiếng Việt bằng Edge TTS...")
    timing = tts_mod.generate_all_audio(post, out_dir=AUDIO_DIR)

    # 3. Tính timeline & Xử lý SFX meme
    print("\n[Bước 3/4] 🎮 Đồng bộ timeline kèm SFX & Meme Pop-up & Video nền...")
    timeline, audio_sequence, total_duration = build_timeline_with_sfx_and_memes(post, timing)

    bg_video_out = os.path.join(FINAL_DIR, "_bg_temp.mp4")
    bg_mod.prepare_background(total_duration, bg_video_out, mode=bg_mode)

    mixed_audio_out = os.path.join(AUDIO_DIR, "_audio_final.mp3")
    bg_music_sample = os.path.join(ASSETS_DIR, "sfx", "lofi_bg.mp3")
    bg_mod.build_mixed_audio_with_sfx(
        audio_sequence, mixed_audio_out,
        bg_music_path=bg_music_sample if os.path.exists(bg_music_sample) else None
    )

    # 4. Ghép video hoàn chỉnh
    print("\n[Bước 4/4] 🚀 Render video hoàn chỉnh (1080x1920)...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_video = os.path.join(FINAL_DIR, f"final_meme_pro_{timestamp}.mp4")
    compose_video(bg_video_out, mixed_audio_out, timeline, total_duration, final_video)

    # Xuất file caption & hashtags đăng TikTok / Shorts
    caption_txt_path = final_video.replace(".mp4", "_caption.txt")
    title_text = getattr(post, "video_title", "") or f"Chuyện Threads hài hước"
    caption_text = getattr(post, "video_caption", "") or post.content[:120]
    hashtag_text = getattr(post, "hashtags", "") or "#xuhuong #haihuoc #threads #fyp #minecraftparkour"

    with open(caption_txt_path, "w", encoding="utf-8") as f:
        f.write(f"🎬 TIÊU ĐỀ VIDEO:\n{title_text}\n\n")
        f.write(f"📝 CAPTION ĐĂNG TIKTOK / SHORTS / REELS:\n{caption_text}\n\n")
        f.write(f"🏷️ HASHTAGS:\n{hashtag_text}\n")

    print("\n" + "=" * 58)
    print(f"🎉 HOÀN THÀNH XUẤT SẮC!")
    print(f"📍 File video: {final_video}")
    print(f"📝 File caption: {caption_txt_path}")
    print(f"⏱️ Thời lượng: {total_duration:.1f} giây")
    print("=" * 58)
    return final_video


if __name__ == "__main__":
    run_pipeline()
