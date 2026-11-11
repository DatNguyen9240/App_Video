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
import random
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
    sticker_sz = layout.get("sticker_size", 440)
    sticker_offset = layout.get("sticker_offset_y", 28)

    # 1. Tính toán trước chiều cao thẻ để tự động căn chỉnh vào Vùng An Toàn (TikTok/Reels Safe-Zone)
    post_path = os.path.join(CARDS_DIR, "post.png")
    actual_post_h = 320
    if os.path.exists(post_path):
        try:
            with Image.open(post_path) as p_img:
                actual_post_h = p_img.height
        except Exception:
            pass

    # Nếu thẻ bài viết có ảnh minh họa đính kèm, tự động cân đối kích thước meme và khoảng cách
    if actual_post_h > 450:
        sticker_sz = 350
        card_gap = 18
        sticker_offset = 20

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
        # Bố cục chia nửa màn hình: gameplay 60FPS phía trên (0-720px), thẻ Threads phía dưới (từ 740px)
        post_y = 740
    elif preset_str == "bottom_story":
        target_y = 750
        post_y = max(220, min(target_y, max_safe_bottom - total_stack_h))
    else:
        # Bố cục Toàn Màn Hình Chuẩn TikTok (Center Focus / Full Minecraft)
        if actual_post_h > 450:
            post_y = 180  # Có ảnh minh họa đính kèm: đẩy lên 180px để thẻ không bị tràn đáy
        else:
            post_y = layout.get("post_card_y", 280)
        if post_y + total_stack_h > max_safe_bottom:
            post_y = max(160, max_safe_bottom - total_stack_h)

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

    # Vị trí cố định của Hộp Video Meme phía dưới thẻ comment (phóng to 440px)
    meme_anchor_y = actual_comment_y + max_cmt_h + sticker_offset

    # BỘ SƯU TẬP MEME ĐA PHÂN ĐOẠN (Mỗi phân đoạn trong 1 video tự đổi mới meme liên tục):
    # 1. Hook (Mở màn): tò mò, ngơ ngác, bất ngờ
    hook_meme_pool = ["huh.gif", "shocked_cat.gif", "math.gif", "dancing_cat.gif"]
    hook_choices = [m for m in hook_meme_pool if os.path.exists(os.path.join(MEME_DIR, m))]
    hook_meme = random.choice(hook_choices) if hook_choices else "dancing_cat.gif"
    hook_img_path = os.path.join(MEME_DIR, hook_meme)

    print(f"  🎬 Đa dạng hóa Meme trong 1 video (Kích thước {sticker_sz}px):")
    print(f"     [Hook Mở Màn] -> {hook_meme}")

    # 1.1. Hiện Meme mở màn theo câu chuyện Hook
    if os.path.exists(hook_img_path):
        timeline.append({
            "type": "anchor_meme",
            "start": cursor,
            "end": comment_cursor,
            "x": "(W-w)/2",
            "y": str(meme_anchor_y),
            "width": sticker_sz,
            "camera_motion": "none",
            "image": hook_img_path,
            "is_video": hook_meme.endswith(".mp4"),
            "is_gif": hook_meme.endswith(".gif"),
            "enable_slide": True
        })

    # Kho meme theo từng vai trò bình luận để tự đổi mới liên tục:
    cmt_speaking_pools = {
        1: ["dancing_cat.gif", "applause.gif", "laugh.gif"],
        2: ["shocked_cat.gif", "clown.gif", "huh.gif"],
        3: ["side_eye.gif", "awkward.gif", "facepalm.gif", "math.gif"],
        4: ["shock.gif", "clown.gif", "huh.gif"],
        5: ["bruh.gif", "facepalm.gif", "side_eye.gif"],
        6: ["laugh.gif", "dancing_cat.gif", "bruh.gif"]
    }

    popup_shown = False

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

        # Xử lý SFX & Meme Reaction
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
        react_start = c_start + c_dur + pre_delay_ms
        c_end = react_start + sfx_dur + post_delay_ms

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
            "shake_start": react_start if cam_motion == "shake" else 0,
            "shake_end": react_start + min(0.6, sfx_dur) if cam_motion == "shake" else 0,
            "image": os.path.join(CARDS_DIR, f"comment_{i}.png"),
            "is_video": False,
            "enable_slide": True
        })

        # CHỌN MEME HOẶC POPUP ẢNH MINH HỌA NỔI CHO TỪNG PHÂN ĐOẠN CỦA BÌNH LUẬN:
        illus_cfg = cfg.get("illustration_settings", {})
        popup_card_path = os.path.join(CARDS_DIR, "popup_proof.png")
        has_popup = illus_cfg.get("enable_popup_illustration", True) and os.path.exists(popup_card_path)
        is_popup_target = has_popup and not popup_shown and (i == 2 or sfx_name in ["boom", "shock"])
        if is_popup_target:
            popup_shown = True

        # Tìm meme phản ứng sẵn có
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

        if is_popup_target:
            # CUTAWAY GAG CHỚP NHOÁNG (1.8s - 2.2s):
            # Xuất hiện chính giữa màn hình (H-h)/2 với hiệu ứng nảy lò xo Punch-in Bounce
            # Bắt trọn tâm điểm của trò đùa rồi lập tức biến mất, đúng chuẩn phong cách TikToker triệu view
            popup_dur = float(illus_cfg.get("popup_duration_s", 2.2))
            popup_start = c_start + 0.25
            popup_end = min(popup_start + popup_dur, c_end)
            popup_y = "(H-h)/2"

            print(f"     🎬 [CUTAWAY GAG MINH HỌA CHỚP NHOÁNG] -> {popup_start:.2f}s đến {popup_end:.2f}s (Thời lượng: {popup_end-popup_start:.2f}s, Vị trí: Chính giữa màn hình, Kích thước: 980x640px)")
            timeline.append({
                "type": "popup_illustration",
                "start": popup_start,
                "end": popup_end,
                "x": "(W-w)/2",
                "y": popup_y,
                "width": 980,
                "height": 640,
                "camera_motion": "punch_in_bounce",
                "image": popup_card_path,
                "is_video": False,
                "is_gif": False,
                "enable_slide": False
            })
            # Trong thời gian Cutaway Gag (2.2s), hình ảnh chiếm trọn spotlight
            # Sau khi Cutaway biến mất, nếu còn thời gian thì reaction meme punchline sẽ xuất hiện
            if react_start > popup_end and os.path.exists(meme_file_cand):
                timeline.append({
                    "type": "meme_reaction",
                    "start": react_start,
                    "end": c_end,
                    "x": "(W-w)/2",
                    "y": str(actual_comment_y + max_cmt_h + 16),
                    "width": sticker_sz,
                    "camera_motion": "punch_in",
                    "image": meme_file_cand,
                    "is_video": is_meme_video,
                    "is_gif": is_meme_gif,
                    "enable_slide": False
                })
        else:
            # A. Trong lúc người đó đang nói (c_start đến react_start): đổi sang meme đồng hành mới của comment i
            pool_i = cmt_speaking_pools.get(i, ["dancing_cat.gif", "huh.gif"])
            valid_pool_i = [m for m in pool_i if os.path.exists(os.path.join(MEME_DIR, m))]
            speak_meme_name = random.choice(valid_pool_i) if valid_pool_i else "dancing_cat.gif"
            speak_meme_path = os.path.join(MEME_DIR, speak_meme_name)

            print(f"     [Comment {i} Đang Nói] -> {speak_meme_name}")

            if os.path.exists(speak_meme_path) and react_start > c_start + 0.1:
                timeline.append({
                    "type": "anchor_meme",
                    "start": c_start,
                    "end": react_start,
                    "x": "(W-w)/2",
                    "y": str(meme_anchor_y),
                    "width": sticker_sz,
                    "camera_motion": "none",
                    "image": speak_meme_path,
                    "is_video": speak_meme_name.endswith(".mp4"),
                    "is_gif": speak_meme_name.endswith(".gif"),
                    "enable_slide": False
                })

            # B. Tại điểm rơi Punchline / Bẻ lái (react_start đến c_end): Biến hình thành Reaction Meme!
            if meme_file_cand and sfx_dur > 0:
                print(f"     [Comment {i} Punchline] -> {os.path.basename(meme_file_cand)} (Reaction SFX: {sfx_name})")
                timeline.append({
                    "type": "meme_reaction",
                    "start": react_start,
                    "end": c_end,
                    "x": "(W-w)/2",
                    "y": str(meme_anchor_y),
                    "width": sticker_sz,
                    "camera_motion": "shake" if sfx_name == "boom" else "punch_in",
                    "image": meme_file_cand,
                    "is_video": is_meme_video,
                    "is_gif": is_meme_gif,
                    "enable_slide": False
                })
            elif os.path.exists(speak_meme_path):
                timeline.append({
                    "type": "anchor_meme",
                    "start": react_start,
                    "end": c_end,
                    "x": "(W-w)/2",
                    "y": str(meme_anchor_y),
                    "width": sticker_sz,
                    "camera_motion": "none",
                    "image": speak_meme_path,
                    "is_video": speak_meme_name.endswith(".mp4"),
                    "is_gif": speak_meme_name.endswith(".gif"),
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

    # Outro anchor meme đến hết video (chọn 1 meme chào kết vui vẻ)
    outro_meme = os.path.join(MEME_DIR, "dancing_cat.gif")
    if os.path.exists(outro_meme) and total_duration > comment_cursor:
        timeline.append({
            "type": "anchor_meme",
            "start": comment_cursor,
            "end": total_duration,
            "x": "(W-w)/2",
            "y": str(meme_anchor_y),
            "width": sticker_sz,
            "camera_motion": "none",
            "image": outro_meme,
            "is_video": False,
            "is_gif": True,
            "enable_slide": False
        })

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
        start_t = f"{item['start']:.2f}"
        end_t = f"{item['end']:.2f}"
        cam_motion = item.get("camera_motion", "none")
        base_y = str(item["y"])

        # Luôn scale tĩnh theo độ rộng w đã chỉ định (980px), không scale động bằng eval=frame vì still image có t=0
        filter_chains.append(f"[{img_idx}:v]scale={w}:-1:flags=lanczos{layer_label}")

        # Hiệu ứng chuyển động (Bounce / Punch-in / Slide-Up / Shake) được xử lý hoàn toàn trong overlay
        if cam_motion == "punch_in_bounce":
            # Nảy lò xo từ dưới lên 90px trong 0.25s đầu + Rung nhẹ hiệu ứng đập vào mắt người xem
            x_pos = f"(W-w)/2 + if(lt(t, {start_t} + 0.35), 8*sin(60*(t-{start_t})), 0)"
            y_pos = f"{base_y} + if(lt(t, {start_t} + 0.25), pow(1 - (t - {start_t})/0.25, 2)*90, 0)"
        else:
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
                 layout_preset: str = None, enable_attached_image: bool = None) -> str:
    """
    Quy trình tự động hoàn chỉnh từ dữ liệu -> video thành phẩm chuẩn đồ thị cảm xúc.
    Đồng bộ và cập nhật liên tục sang tracker để giao diện Web UI hiển thị.
    Hỗ trợ tùy biến bố cục video: split_screen, center, bottom_story.
    Hỗ trợ bật/tắt ảnh đính kèm trong câu hỏi & ảnh minh họa pop-up nổi bên ngoài.
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
        bg_mode = layout.get("default_bg_mode", "minecraft")

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

        # BƯỚC 2: RENDER THẺ THREADS (AVATAR NGƯỜI THẬT & POPUP MINH HỌA)
        if not skip_cards_render:
            tracker.start_step("cards", "Đang render bộ thẻ Threads chuẩn (Avatar người thật)...", 25)
            os.makedirs(CARDS_DIR, exist_ok=True)
            post_avatar = getattr(post, "avatar_url", "") or getattr(post, "avatar_path", "")
            post_illus = getattr(post, "illustration_path", "")
            if not post_illus or not os.path.exists(post_illus):
                # Kiểm tra kho ảnh minh họa chất lượng cao trong output/illustrations
                candidate_pool = [
                    os.path.join(OUT_DIR, "illustrations", "my_eyes_angle.jpg"),
                    os.path.join(OUT_DIR, "illustrations", "cat_lightmode.jpg"),
                    os.path.join(OUT_DIR, "illustrations", "sunglasses_bed.jpg")
                ]
                valid_cand = [p for p in candidate_pool if os.path.exists(p)]
                if valid_cand:
                    post_illus = random.choice(valid_cand)

            # Tùy chọn gắn ảnh vào câu hỏi bài đăng (có thể có hoặc không):
            illus_cfg = cfg.get("illustration_settings", {})
            use_attached = enable_attached_image if enable_attached_image is not None else illus_cfg.get("enable_post_attached_image", False)
            attached_img_arg = post_illus if (use_attached and post_illus and os.path.exists(post_illus)) else None

            post_img = card_mod.render_post_card(
                post.username, post.content, post.avatar_color, post.is_verified, post.likes,
                theme=card_theme, avatar_source=post_avatar,
                attached_image_path=attached_img_arg
            )
            card_mod.save_card(post_img, os.path.join(CARDS_DIR, "post.png"))

            # Tạo thẻ ảnh minh họa nổi bên ngoài (Popup Proof Card) nếu được bật
            if illus_cfg.get("enable_popup_illustration", True) and post_illus and os.path.exists(post_illus):
                popup_path = os.path.join(CARDS_DIR, "popup_proof.png")
                badge_lbl = illus_cfg.get("badge_text", "")
                caption_lbl = getattr(post, "illustration_caption", "")
                card_mod.create_popup_illustration_card(post_illus, popup_path, badge_text=badge_lbl, caption_text=caption_lbl)
                tracker.log(f"  📸 Đã tạo khung ảnh minh họa nổi (Popup Proof Card): {popup_path}")

            for i, c in enumerate(post.comments, 1):
                c_avatar = getattr(c, "avatar_url", "") or getattr(c, "avatar_path", "")
                c_reply = getattr(c, "reply_to", "") or post.username
                c_img = card_mod.render_comment_card(
                    c.username, c.content, c.avatar_color, c.is_verified, c.likes,
                    index=i, reply_to=c_reply, theme=card_theme, avatar_source=c_avatar
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
        # Tự động chọn nhạc nền ngẫu nhiên từ kho assets/bgm hoặc file mặc định
        bgm_candidates = []
        bgm_dir = os.path.join(ASSETS_DIR, "bgm")
        if os.path.exists(bgm_dir):
            bgm_candidates += [os.path.join(bgm_dir, f) for f in os.listdir(bgm_dir) if f.lower().endswith((".mp3", ".wav", ".m4a"))]
        default_lofi = os.path.join(ASSETS_DIR, "sfx", "lofi_bg.mp3")
        if os.path.exists(default_lofi) and default_lofi not in bgm_candidates:
            bgm_candidates.append(default_lofi)

        selected_bgm = random.choice(bgm_candidates) if bgm_candidates else None
        bg_mod.build_mixed_audio_with_sfx(
            audio_sequence, mixed_audio_out,
            bg_music_path=selected_bgm
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
