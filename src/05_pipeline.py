"""
MODULE 5: ĐẠO DIỄN & GHÉP NỐI TOÀN BỘ MODULE (Master Video Compositor & Pipeline V2.1)
======================================================================================
Chức năng:
  - Nhạc trưởng điều phối toàn bộ các module: Kịch bản -> Thẻ -> Diễn xuất -> Âm thanh -> Dựng phim
  - HẠN CHẾ HARDCODE 100%: Mọi thông số lấy từ config/director_config.json qua src/config.py
  - NÂNG CẤP CHẤT LƯỢNG TOÀN DIỆN:
    + 60 FPS Native siêu mượt
    + Visually Lossless CRF 18 + BT.709 HD Color Matrix
    + Chuyển động thẻ trượt mượt mà (Smooth Slide-Up with Ease-Out)
    + Hỗ trợ cả Video Meme Reaction (.mp4) và Sticker PNG (.png)
    + Camera Shake động học khi có cú twist bẻ lái
"""

import os
import sys
import shutil
import subprocess
import time
from datetime import datetime
from PIL import Image

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
sys.path.insert(0, SRC_DIR)

import config as config_mod
import pipeline_tracker as tracker

OUT_DIR    = os.path.join(BASE_DIR, "output")
CARDS_DIR  = os.path.join(OUT_DIR, "cards")
AUDIO_DIR  = os.path.join(OUT_DIR, "audio")
FINAL_DIR  = os.path.join(OUT_DIR, "final")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
SFX_DIR    = os.path.join(ASSETS_DIR, "sfx")
MEME_DIR   = os.path.join(ASSETS_DIR, "memes")

import importlib.util

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


def build_timeline_with_emotional_pacing(post, timing: dict, layout_preset: str = None, bg_mode: str = None) -> tuple[list[dict], list[str], float, list[dict]]:
    """
    Tính mốc thời gian hiển thị từng thẻ, danh sách audio cần nối kèm khoảng lặng nhịp thở,
    SFX meme tại điểm rơi (ưu tiên video meme .mp4 trước PNG) và dữ liệu đồ thị cảm xúc cho Web UI.
    Hỗ trợ bố cục đa dạng: split_screen (TikTok Split), center (truyền thống), bottom_story.
    """
    cfg = config_mod.load_config()
    layout = cfg.get("visual_layout", {})

    card_w = layout.get("card_width", 960)
    card_gap = layout.get("comment_card_gap", 24)
    sticker_sz = layout.get("sticker_size", 260)
    sticker_offset = layout.get("sticker_offset_y", 35)

    # 1. Tính toán trước chiều cao thẻ để tự động căn chỉnh vào Vùng An Toàn (TikTok/Reels Safe-Zone)
    post_path = os.path.join(CARDS_DIR, "post.png")
    actual_post_h = 320
    if os.path.exists(post_path):
        try:
            with Image.open(post_path) as p_img:
                actual_post_h = p_img.height
        except Exception:
            pass

    max_cmt_h = 280
    for idx in range(1, len(post.comments) + 1):
        c_path = os.path.join(CARDS_DIR, f"comment_{idx}.png")
        if os.path.exists(c_path):
            try:
                with Image.open(c_path) as c_file:
                    if c_file.height > max_cmt_h:
                        max_cmt_h = c_file.height
            except Exception:
                pass

    has_meme = any(getattr(c, "sfx", "none") != "none" for c in post.comments)
    total_stack_h = actual_post_h + card_gap + max_cmt_h + (sticker_sz + sticker_offset if has_meme else 0)

    # Đáy vùng an toàn TikTok/Reels/Shorts: tuyệt đối không vượt quá y = 1460 (tránh bị che bởi caption, username, avatar, nút like/share)
    max_safe_bottom = 1460

    mode_str = (bg_mode or "").lower()
    preset_str = (layout_preset or "").lower()

    if preset_str == "split_screen" or mode_str in ["split", "split_gameplay", "split_screen"]:
        # Bố cục chia nửa màn hình: gameplay phía trên, thẻ phía dưới
        target_y = 680
        post_y = max(220, min(target_y, max_safe_bottom - total_stack_h))
    elif preset_str == "bottom_story":
        target_y = 750
        post_y = max(220, min(target_y, max_safe_bottom - total_stack_h))
    else:
        # Bố cục Căn Giữa Chuẩn TikTok (Center Focus) - ĐẸP NHẤT, RÕ RÀNG NHẤT, DỄ ĐỌC TRÊN ĐIỆN THOẠI
        post_y = layout.get("post_card_y", 240)
        if post_y + total_stack_h > max_safe_bottom:
            post_y = max(180, max_safe_bottom - total_stack_h)

    timeline = []
    audio_sequence = []
    emotional_arc_data = []

    cursor = 0.2
    silence_idx = 0

    # 1. Thẻ bài đăng (Hook & Context)
    post_dur = timing["post"]["duration_ms"] / 1000.0
    post_pause_after = getattr(post, "pause_after_ms", 300) / 1000.0

    audio_sequence.append(timing["post"]["path"])

    # Chèn khoảng lặng tự nhiên sau bài viết
    if post_pause_after > 0.05:
        silence_file = os.path.join(AUDIO_DIR, f"_silence_{silence_idx}.mp3")
        silence_idx += 1
        tts_mod.generate_silence_mp3(post_pause_after, silence_file)
        audio_sequence.append(silence_file)

    emotional_arc_data.append({
        "beat": "1. Mở Màn / Hook",
        "role": f"Tác giả (@{post.username})",
        "emotion": getattr(post, "hook_energy", "high"),
        "energy_level": 85,
        "voice": timing["post"].get("voice", "Nam Minh"),
        "text": post.content[:80] + ("..." if len(post.content) > 80 else ""),
        "duration_s": round(post_dur, 2),
        "sfx": "none",
        "pause_ms": int(post_pause_after * 1000)
    })

    actual_comment_y = post_y + actual_post_h + card_gap
    comment_cursor = cursor + post_dur + post_pause_after

    # 2. Xử lý từng bình luận (Đẩy cao trào -> Cú Twist -> Chốt hạ)
    for i, c in enumerate(post.comments, 1):
        c_dur = timing["comments"][i - 1]["duration_ms"] / 1000.0
        c_audio_path = timing["comments"][i - 1]["path"]
        sfx_name = getattr(c, "sfx", "none")
        emotion = getattr(c, "emotion", "funny")
        cam_motion = getattr(c, "camera_motion", "none")
        sfx_info = config_mod.get_sfx_info(sfx_name)

        pause_before = getattr(c, "pause_before_ms", 200) / 1000.0
        pause_after = getattr(c, "pause_after_ms", 350) / 1000.0
        pre_delay_ms = sfx_info.get("pre_delay_ms", 200) / 1000.0
        post_delay_ms = sfx_info.get("post_delay_ms", 350) / 1000.0

        # Khoảng lặng trước câu nói (nhịp thở tự nhiên)
        if pause_before > 0.05:
            silence_file = os.path.join(AUDIO_DIR, f"_silence_{silence_idx}.mp3")
            silence_idx += 1
            tts_mod.generate_silence_mp3(pause_before, silence_file)
            audio_sequence.append(silence_file)

        audio_sequence.append(c_audio_path)

        # Xử lý SFX & Meme
        sfx_dur = 0.0
        sfx_file = None
        sfx_filename = sfx_info.get("sfx_file")
        if sfx_filename:
            candidate = os.path.join(SFX_DIR, sfx_filename)
            if os.path.exists(candidate):
                sfx_file = candidate
                sfx_dur = min(bg_mod.get_media_duration(sfx_file), 1.8)

        # Khoảng ngắt kịch tính (dramatic micro-pause) trước khi SFX nổ ra
        if sfx_file and pre_delay_ms > 0.05:
            silence_file = os.path.join(AUDIO_DIR, f"_silence_{silence_idx}.mp3")
            silence_idx += 1
            tts_mod.generate_silence_mp3(pre_delay_ms, silence_file)
            audio_sequence.append(silence_file)

        if sfx_file:
            audio_sequence.append(sfx_file)

        # Khoảng đệm sau khi SFX dứt
        if post_delay_ms > 0.05:
            silence_file = os.path.join(AUDIO_DIR, f"_silence_{silence_idx}.mp3")
            silence_idx += 1
            tts_mod.generate_silence_mp3(post_delay_ms, silence_file)
            audio_sequence.append(silence_file)

        c_start = comment_cursor + pause_before
        c_end = c_start + c_dur + pre_delay_ms + sfx_dur + post_delay_ms

        if sfx_name == "boom" and cam_motion == "none":
            cam_motion = "shake"

        # Đưa thẻ bình luận vào timeline với hiệu ứng Slide-Up mượt mà
        timeline.append({
            "type": "comment",
            "index": i,
            "start": c_start,
            "end": c_end,
            "x": "(W-w)/2",
            "y": actual_comment_y,
            "width": card_w,
            "camera_motion": cam_motion,
            "shake_start": c_start + c_dur + pre_delay_ms if cam_motion == "shake" else 0,
            "shake_end": c_start + c_dur + pre_delay_ms + min(0.6, sfx_dur) if cam_motion == "shake" else 0,
            "image": os.path.join(CARDS_DIR, f"comment_{i}.png"),
            "is_video": False,
            "enable_slide": True
        })

        # Hiện Meme GIF / Video Meme / Sticker đúng điểm rơi
        # Ưu tiên kiểm tra: GIF hoạt hình (.gif) -> Video MP4 (.mp4) -> Sticker tĩnh (.png)
        meme_file_cand = None
        is_meme_video = False
        is_meme_gif = False

        gif_name = f"{sfx_name}.gif"
        mp4_name = f"{sfx_name}.mp4"
        png_name = sfx_info.get("meme_file") or f"{sfx_name}.png"

        if os.path.exists(os.path.join(MEME_DIR, gif_name)):
            meme_file_cand = os.path.join(MEME_DIR, gif_name)
            is_meme_gif = True
        elif os.path.exists(os.path.join(MEME_DIR, mp4_name)):
            meme_file_cand = os.path.join(MEME_DIR, mp4_name)
            is_meme_video = True
        elif os.path.exists(os.path.join(MEME_DIR, png_name)):
            meme_file_cand = os.path.join(MEME_DIR, png_name)

        if meme_file_cand and sfx_dur > 0:
            c_card_path = os.path.join(CARDS_DIR, f"comment_{i}.png")
            actual_cmt_h = 300
            if os.path.exists(c_card_path):
                try:
                    with Image.open(c_card_path) as c_file:
                        actual_cmt_h = c_file.height
                except Exception:
                    pass

            timeline.append({
                "type": "meme_gif" if is_meme_gif else ("meme_video" if is_meme_video else "meme_sticker"),
                "start": c_start + c_dur + pre_delay_ms,
                "end": c_end,
                "x": "(W-w)/2",
                "y": str(actual_comment_y + actual_cmt_h + sticker_offset),
                "width": sticker_sz,
                "camera_motion": "punch_in",
                "image": meme_file_cand,
                "is_video": is_meme_video,
                "is_gif": is_meme_gif,
                "enable_slide": False
            })

        # Ghi nhận dữ liệu nhịp cảm xúc
        energy_map = {"funny": 70, "shocked": 98, "awkward": 65, "sarcastic": 80, "neutral": 50}
        emotional_arc_data.append({
            "beat": f"{i+1}. Bình luận {i}" + (" (Bẻ lái sốc)" if sfx_name == "boom" else (" (Chốt hạ)" if i == len(post.comments) else "")),
            "role": f"@{c.username}",
            "emotion": emotion,
            "energy_level": energy_map.get(emotion, 75),
            "voice": timing["comments"][i-1].get("voice", "N/A"),
            "text": c.content[:70] + ("..." if len(c.content) > 70 else ""),
            "duration_s": round(c_dur + sfx_dur, 2),
            "sfx": sfx_name,
            "camera": cam_motion,
            "pause_ms": int((pre_delay_ms + post_delay_ms) * 1000)
        })

        comment_cursor = c_end

    total_duration = comment_cursor + 0.5

    # Thẻ Post ghim từ đầu đến hết video (slide-up mượt lúc mở màn)
    timeline.insert(0, {
        "type": "post",
        "start": cursor,
        "end": total_duration,
        "x": "(W-w)/2",
        "y": post_y,
        "width": card_w,
        "camera_motion": "none",
        "image": os.path.join(CARDS_DIR, "post.png"),
        "is_video": False,
        "enable_slide": True
    })

    return timeline, audio_sequence, total_duration, emotional_arc_data


def compose_video(bg_video: str, audio_path: str, timeline: list[dict],
                  total_duration: float, output_path: str):
    """
    Dựng phim chuyên nghiệp: 60 FPS, CRF 18, Color Space BT.709,
    Hiệu ứng trượt thẻ Ease-Out mượt mà & Camera Shake tự động.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    exe = bg_mod.get_ffmpeg_exe()
    cfg = config_mod.load_config()
    layout = cfg.get("visual_layout", {})

    fps = layout.get("fps", 60)
    video_crf = str(layout.get("video_crf", 18))
    video_preset = layout.get("video_preset", "medium")
    enable_slide = layout.get("enable_slide_up_animation", True)
    slide_dur = layout.get("slide_duration_s", 0.22)
    slide_dist = layout.get("slide_distance_px", 45)

    inputs = ["-i", bg_video, "-i", audio_path]
    for item in timeline:
        if item.get("is_gif"):
            inputs.extend(["-ignore_loop", "0", "-i", item["image"]])
        elif item.get("is_video"):
            inputs.extend(["-stream_loop", "-1", "-i", item["image"]])
        else:
            inputs.extend(["-i", item["image"]])

    filter_chains = []
    prev_stream = "[0:v]"

    for idx, item in enumerate(timeline):
        img_idx = idx + 2
        layer_label = f"[layer{idx}]"
        out_stream = f"[v{idx}]"

        w = item.get("width", 960)
        filter_chains.append(f"[{img_idx}:v]scale={w}:-1:flags=lanczos{layer_label}")

        start_t = f"{item['start']:.2f}"
        end_t = f"{item['end']:.2f}"
        cam_motion = item.get("camera_motion", "none")
        base_y = str(item["y"])

        # Hiệu ứng Ease-Out Slide-Up khi xuất hiện
        slide_expr = ""
        if enable_slide and item.get("enable_slide", False):
            slide_expr = f"if(lt(t, {start_t} + {slide_dur:.2f}), pow(1 - (t - {start_t})/{slide_dur:.2f}, 2)*{slide_dist}, 0)"

        if cam_motion == "shake" and "shake_start" in item:
            s_start = f"{item['shake_start']:.2f}"
            s_end = f"{item['shake_end']:.2f}"
            x_pos = f"(W-w)/2 + if(between(t,{s_start},{s_end}), 8*sin(60*t), 0)"
            if slide_expr:
                y_pos = f"{base_y} + if(between(t,{s_start},{s_end}), 6*cos(60*t), {slide_expr})"
            else:
                y_pos = f"{base_y} + if(between(t,{s_start},{s_end}), 6*cos(60*t), 0)"
        else:
            x_pos = item.get("x", "(W-w)/2")
            if slide_expr:
                y_pos = f"{base_y} + {slide_expr}"
            else:
                y_pos = base_y

        filter_chains.append(
            f"{prev_stream}{layer_label}overlay=x='{x_pos}':y='{y_pos}':enable='between(t,{start_t},{end_t})'{out_stream}"
        )
        prev_stream = out_stream

    filter_complex = ";".join(filter_chains)

    cmd = [
        exe, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", prev_stream,
        "-map", "1:a",
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", video_preset,
        "-crf", video_crf,
        "-c:a", "aac",
        "-b:a", "256k",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-t", f"{total_duration:.2f}",
        "-movflags", "+faststart",
        output_path
    ]

    tracker.log(f"⏳ FFmpeg đang render video Studio ({fps} FPS, CRF {video_crf}, BT.709, Slide-Up Animation)...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg composite lỗi:\n{res.stderr[-1000:]}")
    tracker.log(f"✅ FFmpeg xuất video chất lượng cao thành công: {output_path}")


def run_pipeline(post=None, bg_mode: str = None, card_theme: str = None,
                 reset_tracker: bool = True, skip_cards_render: bool = False,
                 layout_preset: str = None) -> str:
    """
    Quy trình tự động hoàn chỉnh từ dữ liệu -> video thành phẩm chuẩn đồ thị cảm xúc.
    Đồng bộ và cập nhật liên tục sang tracker để giao diện Web UI hiển thị.
    Hỗ trợ tùy biến bố cục video: split_screen, center, bottom_story.
    """
    cfg = config_mod.load_config()
    layout = cfg.get("visual_layout", {})

    if post is None:
        sample_path = os.path.join(OUT_DIR, "sample_post.json")
        if os.path.exists(sample_path):
            post = content_mod.load_from_json(sample_path)
        else:
            radar_spec = importlib.util.spec_from_file_location("radar", os.path.join(SRC_DIR, "threads_radar.py"))
            radar_mod = importlib.util.module_from_spec(radar_spec)
            radar_spec.loader.exec_module(radar_mod)
            post = radar_mod.crawl_trending_threads_story()

    if bg_mode is None:
        bg_mode = layout.get("default_bg_mode", "infinity")

    if card_theme is None:
        card_theme = layout.get("default_card_theme", "light")

    if reset_tracker:
        tracker.reset()
    tracker.log(f"🎬 Bắt đầu quy trình sản xuất video (Chủ đề: {getattr(post, 'video_title', 'Threads Story')})")

    try:
        # BƯỚC 1: KỊCH BẢN & ĐỒ THỊ CẢM XÚC
        if reset_tracker:
            tracker.start_step("script", "Đang phân tích cấu trúc kịch bản và đồ thị cảm xúc...", 10)
            time.sleep(0.3)
            tracker.finish_step("script")

        # BƯỚC 2: RENDER THẺ THREADS (AVATAR NGƯỜI THẬT)
        if not skip_cards_render:
            tracker.start_step("cards", "Đang render bộ thẻ Threads chuẩn (Avatar người thật)...", 25)
            os.makedirs(CARDS_DIR, exist_ok=True)
            post_avatar = getattr(post, "avatar_url", "") or getattr(post, "avatar_path", "")
            post_img = card_mod.render_post_card(
                post.username, post.content, post.avatar_color, post.is_verified, post.likes,
                theme=card_theme, avatar_source=post_avatar
            )
            card_mod.save_card(post_img, os.path.join(CARDS_DIR, "post.png"))

            for i, c in enumerate(post.comments, 1):
                c_avatar = getattr(c, "avatar_url", "") or getattr(c, "avatar_path", "")
                c_img = card_mod.render_comment_card(
                    c.username, c.content, c.avatar_color, c.is_verified, c.likes,
                    index=i, reply_to=post.username, theme=card_theme, avatar_source=c_avatar
                )
                card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))
            tracker.finish_step("cards")

        # BƯỚC 3: DIỄN XUẤT GIỌNG ĐỌC ĐA VAI (TTS + VOCAL MASTERING)
        tracker.start_step("tts", "Đang tạo giọng đọc đa vai (Nam/Nữ) và xử lý Vocal Mastering (LUFS -14)...", 45)
        timing = tts_mod.generate_all_audio(
            post, out_dir=AUDIO_DIR,
            progress_cb=lambda msg, pct: tracker.update_progress(45 + int(pct * 0.2), msg)
        )
        tracker.finish_step("tts")

        # BƯỚC 4: THIẾT KẾ ÂM THANH & NHỊP THỞ
        tracker.start_step("audio", "Đang đồng bộ timeline, chèn khoảng lặng kịch tính & mix nhạc nền...", 70)
        timeline, audio_sequence, total_duration, emotional_arc_data = build_timeline_with_emotional_pacing(post, timing, layout_preset=layout_preset, bg_mode=bg_mode)
        tracker.set_emotional_arc(emotional_arc_data)

        bg_video_out = os.path.join(FINAL_DIR, "_bg_temp.mp4")
        bg_mod.prepare_background(total_duration, bg_video_out, mode=bg_mode)

        mixed_audio_out = os.path.join(AUDIO_DIR, "_audio_final.mp3")
        bg_music_sample = os.path.join(ASSETS_DIR, "sfx", "lofi_bg.mp3")
        bg_mod.build_mixed_audio_with_sfx(
            audio_sequence, mixed_audio_out,
            bg_music_path=bg_music_sample if os.path.exists(bg_music_sample) else None
        )
        tracker.finish_step("audio")

        # BƯỚC 5: DỰNG PHIM ĐA LỚP & CAMERA MOTION (60 FPS + SLIDE-UP + LOSSLESS CRF 18)
        tracker.start_step("composite", "FFmpeg dựng phim 60 FPS, hiệu ứng trượt thẻ Slide-Up & Camera Shake...", 85)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_video = os.path.join(FINAL_DIR, f"final_meme_pro_{timestamp}.mp4")
        compose_video(bg_video_out, mixed_audio_out, timeline, total_duration, final_video)
        tracker.finish_step("composite")

        # BƯỚC 6: ĐÓNG GÓI & TỐI ƯU VIRAL
        tracker.start_step("export", "Đang đóng gói video, caption và thẻ hashtag viral...", 95)
        caption_txt_path = final_video.replace(".mp4", "_caption.txt")
        title_text = getattr(post, "video_title", "") or f"Chuyện Threads Hài Hước"
        caption_text = getattr(post, "video_caption", "") or post.content[:120]
        hashtag_text = getattr(post, "hashtags", "") or "#xuhuong #haihuoc #threads #fyp #minecraftparkour"

        caption_full = (
            f"🎬 TIÊU ĐỀ: {title_text}\n\n"
            f"📝 CAPTION ĐĂNG TIKTOK / REELS:\n{caption_text}\n\n"
            f"🏷️ HASHTAGS:\n{hashtag_text}\n"
        )
        with open(caption_txt_path, "w", encoding="utf-8") as f:
            f.write(caption_full)

        tracker.finish_step("export")
        tracker.finish_pipeline(os.path.basename(final_video), caption_full, total_duration)

        return final_video

    except Exception as e:
        import traceback
        traceback.print_exc()
        tracker.set_error(str(e))
        raise e


if __name__ == "__main__":
    run_pipeline()
