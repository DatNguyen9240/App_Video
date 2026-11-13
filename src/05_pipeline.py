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
import satisfying_manager

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

    # TÙY CHỌN BỐ CỤC SANDWICH ĐỈNH CAO TIKTOK:
    # 1. Thẻ bài viết ở trên (140px)
    # 2. VIDEO THỎA MÃN (Ghép hình puzzle, Ăn uống mukbang, ASMR cắt xà phòng) Ở CHÍNH GIỮA (560px)
    # 3. Thẻ bình luận đối đáp ở dưới (1050px)
    enable_center_sat = layout.get("enable_center_satisfying_video", True)
    sat_cat = layout.get("center_satisfying_category", "random")
    if "puzzle" in mode_str:
        sat_cat = "puzzle"
        enable_center_sat = True
    elif "food" in mode_str or "mukbang" in mode_str:
        sat_cat = "food"
        enable_center_sat = True
    elif "asmr" in mode_str:
        sat_cat = "asmr"
        enable_center_sat = True
    elif "gameplay" in mode_str or "parkour" in mode_str:
        sat_cat = "gameplay"
        enable_center_sat = True
    elif "hypnotic" in mode_str or "spiral" in mode_str or "geometry" in mode_str:
        sat_cat = "hypnotic"
        enable_center_sat = True
    elif "sand" in mode_str or "kinetic" in mode_str:
        sat_cat = "kinetic_sand"
        enable_center_sat = True
    elif preset_str in ["sandwich", "center_satisfying"]:
        enable_center_sat = True

    center_vid_file = None
    if enable_center_sat:
        post_y = 140
        center_video_w = layout.get("center_satisfying_width", 960)
        center_video_h = layout.get("center_satisfying_height", 560)
        center_video_y = post_y + actual_post_h + 20
        actual_comment_y = center_video_y + center_video_h + 24
        center_vid_file = bg_mod.get_satisfying_video_path(sat_cat)
        print(f"  ✨ [BỐ CỤC SANDWICH TIKTOK]: Video thỏa mãn ({sat_cat}) ở chính giữa 960x{center_video_h}px (y={center_video_y})!")
    elif preset_str == "split_screen" or mode_str in ["split", "split_gameplay", "split_screen"]:
        # Bố cục chia nửa màn hình: gameplay 60FPS phía trên (0-720px), thẻ Threads phía dưới (từ 740px)
        post_y = 740
        actual_comment_y = post_y + actual_post_h + card_gap
    elif preset_str == "bottom_story":
        target_y = 750
        post_y = max(220, min(target_y, max_safe_bottom - total_stack_h))
        actual_comment_y = post_y + actual_post_h + card_gap
    else:
        # Bố cục Toàn Màn Hình Chuẩn TikTok (Center Focus / Full Minecraft)
        if actual_post_h > 450:
            post_y = 180  # Có ảnh minh họa đính kèm: đẩy lên 180px để thẻ không bị tràn đáy
        else:
            post_y = layout.get("post_card_y", 280)
        if post_y + total_stack_h > max_safe_bottom:
            post_y = max(160, max_safe_bottom - total_stack_h)
        actual_comment_y = post_y + actual_post_h + card_gap

    timeline = []
    audio_sequence = []
    emotional_arc_data = []
    overlay_sfx = []

    cursor = 0.2
    silence_idx = 0

    whoosh_sfx = os.path.join(SFX_DIR, "whoosh.mp3")
    pop_sfx = os.path.join(SFX_DIR, "pop.mp3")
    camera_sfx = os.path.join(SFX_DIR, "camera.mp3")
    enable_foley = layout.get("enable_foley_sfx", True)

    # Đồng bộ audio_sequence với cursor khởi đầu (0.2s)
    silence_file = os.path.join(AUDIO_DIR, f"_silence_{silence_idx}.mp3")
    silence_idx += 1
    tts_mod.generate_silence_mp3(cursor, silence_file)
    audio_sequence.append(silence_file)

    # Foley Whoosh khi thẻ bài viết chính lướt vào
    if enable_foley and os.path.exists(whoosh_sfx):
        overlay_sfx.append({"time": cursor, "file": whoosh_sfx, "vol": 0.35})

    # 1. Thẻ bài đăng (Hook & Context)
    post_dur = timing["post"]["duration_ms"] / 1000.0
    post_pause_after = min(0.30, getattr(post, "pause_after_ms", 250) / 1000.0)

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

    if not enable_center_sat:
        actual_comment_y = post_y + actual_post_h + card_gap
    comment_cursor = cursor + post_dur + post_pause_after

    # Vị trí cố định của Hộp Video Meme phía dưới thẻ comment (phóng to 440px)
    meme_anchor_y = actual_comment_y + max_cmt_h + sticker_offset
    enable_anchor_meme = layout.get("enable_continuous_anchor_meme", False)
    punchline_center = layout.get("punchline_meme_popup_center", True)
    punchline_sz = layout.get("punchline_meme_popup_size", 780)

    # BỘ SƯU TẬP MEME ĐA PHÂN ĐOẠN:
    # 1. Hook (Mở màn): tò mò, ngơ ngác, bất ngờ
    hook_meme_pool = ["huh.gif", "shocked_cat.gif", "math.gif", "dancing_cat.gif"]
    hook_choices = [m for m in hook_meme_pool if os.path.exists(os.path.join(MEME_DIR, m))]
    hook_meme = random.choice(hook_choices) if hook_choices else "dancing_cat.gif"
    hook_img_path = os.path.join(MEME_DIR, hook_meme)

    if enable_anchor_meme:
        print(f"  🎬 Hiển thị Meme đồng hành liên tục (Kích thước {sticker_sz}px):")
        print(f"     [Hook Mở Màn] -> {hook_meme}")
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
    else:
        print(f"  ✨ Chế độ Tinh Gọn & Thỏa Mãn Thị Giác: Tắt meme chạy nền liên tục, nhường toàn bộ spotlight cho video thỏa mãn người xem!")

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

        # Khoảng lặng tự nhiên giữa các câu nói (nhịp thở người thật 120ms - 220ms)
        pause_before = min(0.22, max(0.12, getattr(c, "pause_before_ms", 180) / 1000.0))
        pause_after = min(0.28, max(0.15, getattr(c, "pause_after_ms", 220) / 1000.0))

        # Khoảng lặng trước câu nói (nhịp thở tự nhiên)
        if pause_before > 0.05:
            silence_file = os.path.join(AUDIO_DIR, f"_silence_{silence_idx}.mp3")
            silence_idx += 1
            tts_mod.generate_silence_mp3(pause_before, silence_file)
            audio_sequence.append(silence_file)

        audio_sequence.append(c_audio_path)

        c_start = comment_cursor + pause_before
        react_start = c_start + c_dur

        # XỬ LÝ SFX ĐIỂM RƠI: LỒNG ĐÈ (OVERLAY) CHUYÊN NGHIỆP, KHÔNG NỐI TUẦN TỰ GÂY CHẾT NHỊP!
        sfx_file = None
        sfx_filename = sfx_info.get("sfx_file")
        if sfx_filename and sfx_name != "none":
            candidate = os.path.join(SFX_DIR, sfx_filename)
            if os.path.exists(candidate):
                sfx_file = candidate
                sfx_vol = sfx_info.get("vol", 0.40)
                # Đánh trúng điểm rơi punchline ngay khi câu nói vừa dứt (-0.05s)
                overlay_sfx.append({
                    "time": max(0.0, react_start - 0.05),
                    "file": sfx_file,
                    "vol": sfx_vol
                })
                print(f"     🔊 [SFX OVERLAY PUNCHLINE]: {sfx_name} (vol={sfx_vol}) tại t={react_start:.2f}s")

                # Tension Riser Swell (Gió rít căng thẳng tăng dần 1.1s trước khi cú bẻ lái nổ tung)
                enable_riser = layout.get("enable_tension_riser", True)
                riser_path = os.path.join(SFX_DIR, "riser.mp3")
                if enable_riser and os.path.exists(riser_path) and sfx_name in ["sub_drop", "boom"]:
                    riser_time = max(0.0, react_start - 1.2)
                    overlay_sfx.append({
                        "time": riser_time,
                        "file": riser_path,
                        "vol": 0.32
                    })
                    print(f"     🔥 [TENSION RISER SWELL]: Gió rít căng thẳng tăng dần tại t={riser_time:.2f}s trước cú Twist!")

        # Foley Pop giòn tan khi thẻ bình luận trượt lên
        if enable_foley and os.path.exists(pop_sfx):
            overlay_sfx.append({"time": c_start, "file": pop_sfx, "vol": 0.35})

        # Thời lượng phản ứng visual (meme popup hoặc dừng nhẹ)
        has_meme_sticker = (sfx_name != "none" and sfx_info.get("meme_file"))
        if has_meme_sticker:
            extra_react_time = 0.85 # Snappy 0.85s reaction, vừa kịp xem visual, không gây dead air!
        else:
            extra_react_time = pause_after

        c_end = react_start + extra_react_time

        # Đồng bộ audio_sequence với c_end
        silence_file = os.path.join(AUDIO_DIR, f"_silence_{silence_idx}.mp3")
        silence_idx += 1
        tts_mod.generate_silence_mp3(extra_react_time, silence_file)
        audio_sequence.append(silence_file)

        if sfx_name in ["boom", "sub_drop"] and cam_motion == "none":
            cam_motion = "shake"

        # Hiệu ứng chuyển động thông minh theo vai trò (Spring Pop, Conversational Slide Left/Right)
        cmt_reply_to = getattr(c, "reply_to", "")
        global_trans_style = layout.get("transition_style", "dynamic_conversational")
        if global_trans_style == "spring_pop":
            cmt_transition = "spring_pop"
        elif global_trans_style == "slide_up":
            cmt_transition = "slide_up"
        else:  # "dynamic_conversational"
            if sfx_name in ["boom", "sub_drop"]:
                cmt_transition = "spring_pop"
            elif cmt_reply_to and cmt_reply_to != post.username:
                cmt_transition = "slide_right"
            elif i % 2 == 1:
                cmt_transition = "spring_pop"
            else:
                cmt_transition = "slide_left"

        # Đưa thẻ bình luận vào timeline với hiệu ứng chuyển cảnh sống động
        timeline.append({
            "type": "comment",
            "index": i,
            "start": c_start,
            "end": c_end,
            "x": "(W-w)/2",
            "y": actual_comment_y,
            "width": card_w,
            "camera_motion": cam_motion,
            "transition": cmt_transition,
            "shake_start": react_start if cam_motion == "shake" else 0,
            "shake_end": react_start + min(0.6, extra_react_time) if cam_motion == "shake" else 0,
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

            # Foley Camera Shutter khi ảnh minh họa nảy ra giữa màn hình
            if enable_foley:
                if os.path.exists(camera_sfx):
                    overlay_sfx.append({"time": popup_start, "file": camera_sfx, "vol": 0.5})
                elif os.path.exists(pop_sfx):
                    overlay_sfx.append({"time": popup_start, "file": pop_sfx, "vol": 0.4})
            # Trong thời gian Cutaway Gag (2.2s), hình ảnh chiếm trọn spotlight
            # Sau khi Cutaway biến mất, nếu còn thời gian thì reaction meme punchline sẽ xuất hiện
            if react_start > popup_end and meme_file_cand and os.path.exists(meme_file_cand):
                m_x = "(W-w)/2"
                m_y = "(H-h)/2" if punchline_center else str(actual_comment_y + max_cmt_h + 16)
                m_w = punchline_sz if punchline_center else sticker_sz
                m_motion = "shake" if sfx_name == "boom" else "punch_in_bounce"

                timeline.append({
                    "type": "meme_reaction",
                    "start": react_start,
                    "end": c_end,
                    "x": m_x,
                    "y": m_y,
                    "width": m_w,
                    "camera_motion": m_motion,
                    "image": meme_file_cand,
                    "is_video": is_meme_video,
                    "is_gif": is_meme_gif,
                    "enable_slide": False
                })
        else:
            # A. Trong lúc người đó đang nói (c_start đến react_start): chỉ hiện nếu bật enable_anchor_meme
            pool_i = cmt_speaking_pools.get(i, ["dancing_cat.gif", "huh.gif"])
            valid_pool_i = [m for m in pool_i if os.path.exists(os.path.join(MEME_DIR, m))]
            speak_meme_name = random.choice(valid_pool_i) if valid_pool_i else "dancing_cat.gif"
            speak_meme_path = os.path.join(MEME_DIR, speak_meme_name)

            if enable_anchor_meme and os.path.exists(speak_meme_path) and react_start > c_start + 0.1:
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

            # B. Tại điểm rơi Punchline / Bẻ lái (react_start đến c_end):
            # TỰ ĐỘNG ĐÈ RA GIỮA MÀN HÌNH VÀ PHÓNG TO NẢY LÒ XO (780px PUNCH-IN BOUNCE)!
            if meme_file_cand and extra_react_time >= 0.5:
                m_x = "(W-w)/2"
                m_y = "(H-h)/2" if punchline_center else str(meme_anchor_y)
                m_w = punchline_sz if punchline_center else sticker_sz
                m_motion = "shake" if sfx_name in ["boom", "sub_drop"] else "punch_in_bounce"

                print(f"     🎬 [PUNCHLINE REACTION ĐÈ RA GIỮA PHÓNG TO {m_w}px] -> {os.path.basename(meme_file_cand)} (SFX: {sfx_name})")
                timeline.append({
                    "type": "meme_reaction",
                    "start": react_start,
                    "end": c_end,
                    "x": m_x,
                    "y": m_y,
                    "width": m_w,
                    "camera_motion": m_motion,
                    "image": meme_file_cand,
                    "is_video": is_meme_video,
                    "is_gif": is_meme_gif,
                    "enable_slide": False
                })
            elif enable_anchor_meme and os.path.exists(speak_meme_path):
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
            "beat": f"{i+1}. Bình luận {i}" + (" (Bẻ lái sốc)" if sfx_name in ["boom", "sub_drop"] else (" (Chốt hạ)" if i == len(post.comments) else "")),
            "role": f"@{c.username}",
            "emotion": emotion,
            "energy_level": energy_map.get(emotion, 75),
            "voice": timing["comments"][i-1].get("voice", "N/A"),
            "text": c.content[:70] + ("..." if len(c.content) > 70 else ""),
            "duration_s": round(c_dur + extra_react_time, 2),
            "sfx": sfx_name,
            "camera": cam_motion,
            "pause_ms": int((pause_before + extra_react_time) * 1000)
        })

        comment_cursor = c_end

    # 3. Thẻ Outro Call-To-Action (Kêu gọi bình luận & thả tim tương tác)
    enable_outro = layout.get("enable_outro_cta", True)
    outro_card_path = os.path.join(CARDS_DIR, "outro_cta.png")
    if enable_outro and os.path.exists(outro_card_path):
        # Tạo giọng đọc kêu gọi tương tác (TTS Outro) để câu cuối có giọng nói lôi cuốn thay vì im lặng
        cta_q = getattr(post, "cta_question", "") or "Còn bạn thì sao? Hãy thả tim và để lại bình luận câu chuyện của bạn nhé!"
        for emo in ["👇", "❤️", "😱", "😂", "💀", "🔥", "✨"]:
            cta_q = cta_q.replace(emo, "").strip()

        outro_dur = float(layout.get("outro_cta_duration_s", 2.6))
        outro_voice_file = os.path.join(AUDIO_DIR, "outro_cta.mp3")
        try:
            # Dùng giọng nữ ngọt ngào hoặc giọng chính để kêu gọi hành động
            tts_mod.text_to_speech(cta_q, outro_voice_file, voice="vi-VN-HoaiMyNeural", rate="+18%")
            actual_outro_dur = bg_mod.get_media_duration(outro_voice_file)
            if actual_outro_dur > 0.5:
                outro_dur = max(outro_dur, actual_outro_dur + 0.3)
                audio_sequence.append(outro_voice_file)
            else:
                raise ValueError("Outro voice duration too short")
        except Exception:
            silence_file = os.path.join(AUDIO_DIR, f"_silence_{silence_idx}.mp3")
            silence_idx += 1
            tts_mod.generate_silence_mp3(outro_dur, silence_file)
            audio_sequence.append(silence_file)

        outro_start = comment_cursor
        outro_end = comment_cursor + outro_dur
        timeline.append({
            "type": "outro_cta",
            "start": outro_start,
            "end": outro_end,
            "x": "(W-w)/2",
            "y": str(actual_comment_y),
            "width": card_w,
            "camera_motion": "punch_in_bounce",
            "image": outro_card_path,
            "is_video": False,
            "is_gif": False,
            "enable_slide": True
        })
        if enable_foley and os.path.exists(whoosh_sfx):
            overlay_sfx.append({"time": outro_start, "file": whoosh_sfx, "vol": 0.45})

        # Icon sticker / Reaction GIF sống động cho câu cuối (Outro Celebration Reaction)
        outro_stickers = [m for m in ["applause.gif", "dancing_cat.gif", "ting.gif"] if os.path.exists(os.path.join(MEME_DIR, m))]
        if outro_stickers:
            outro_stk = os.path.join(MEME_DIR, outro_stickers[0])
            timeline.append({
                "type": "outro_sticker",
                "start": outro_start + 0.15,
                "end": outro_end,
                "x": "(W-w)/2",
                "y": str(max(200, actual_comment_y - 200)),
                "width": 240,
                "camera_motion": "punch_in_bounce",
                "image": outro_stk,
                "is_video": False,
                "is_gif": True,
                "enable_slide": False
            })

        emotional_arc_data.append({
            "beat": "Kết Thúc / Outro Call-To-Action",
            "role": "Studio CTA",
            "emotion": "interactive",
            "energy_level": 90,
            "voice": "vi-VN-HoaiMyNeural",
            "text": cta_q,
            "duration_s": outro_dur,
            "sfx": "whoosh",
            "camera": "punch_in_bounce",
            "pause_ms": 0
        })
        comment_cursor = outro_end

    total_duration = comment_cursor + 0.3

    # Outro anchor meme đến hết video (chỉ hiện nếu bật enable_continuous_anchor_meme)
    outro_meme = os.path.join(MEME_DIR, "dancing_cat.gif")
    if enable_anchor_meme and os.path.exists(outro_meme) and total_duration > comment_cursor - 0.2:
        timeline.append({
            "type": "anchor_meme",
            "start": comment_cursor - 0.2,
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

    # Chèn video thỏa mãn người xem ở chính giữa (Ghép hình, Ăn uống/Mukbang, ASMR)
    if enable_center_sat and center_vid_file and os.path.exists(center_vid_file):
        timeline.insert(0, {
            "type": "center_satisfying_video",
            "start": cursor,
            "end": total_duration,
            "x": "(W-w)/2",
            "y": str(center_video_y),
            "width": center_video_w,
            "height": center_video_h,
            "camera_motion": "none",
            "image": center_vid_file,
            "is_video": True,
            "is_gif": False,
            "enable_slide": True
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

    return timeline, audio_sequence, overlay_sfx, total_duration, emotional_arc_data


def generate_kinetic_subtitles_ass(post, timing: dict, timeline: list[dict], output_ass_path: str):
    """
    Tạo phụ đề động Kinetic Subtitles phong cách Shorts/TikTok (Hormozi word-chunk pop-in):
    - Font Arial đậm nét, viền đen dày 4.5px, nổi bật trên mọi nền video.
    - Màu vàng neon (&H0000FFFF) hoặc trắng (&H00FFFFFF) bắt mắt.
    - Chia nhỏ câu thành từng cụm 3-4 từ nảy nhịp (Word-chunk pop-in) theo đúng nhịp nói,
      không hiện cả khối dài gây rối mắt, tối đa hóa tỉ lệ giữ chân người xem (Retention).
    """
    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Kinetic,Arial,56,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4.2,2,2,40,40,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []

    def format_ass_time(sec: float) -> str:
        sec = max(0.0, sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def chunk_text_by_words(text: str, start_sec: float, end_sec: float, words_per_chunk: int = 4):
        clean_t = text.strip().replace("\n", " ")
        words = clean_t.split()
        if not words:
            return
        total_words = len(words)
        dur = max(0.5, end_sec - start_sec)

        chunks = []
        for i in range(0, total_words, words_per_chunk):
            chunks.append(" ".join(words[i:i + words_per_chunk]))

        chunk_dur = dur / len(chunks)
        for idx, chunk in enumerate(chunks):
            c_start = start_sec + idx * chunk_dur
            c_end = min(end_sec, c_start + chunk_dur)
            dialogues.append(
                f"Dialogue: 0,{format_ass_time(c_start)},{format_ass_time(c_end)},Kinetic,,0,0,0,,{{\\fscx106\\fscy106\\t(0,70,\\fscx100\\fscy100)}}{chunk.upper()}"
            )

    # Lấy các đoạn nói từ timeline
    for item in timeline:
        itype = item.get("type")
        if itype == "comment":
            idx = item.get("index", 1) - 1
            if idx < len(post.comments):
                cmt = post.comments[idx]
                chunk_text_by_words(cmt.content, item["start"], item["end"] - 0.35, words_per_chunk=4)
        elif itype == "post":
            post_dur = timing.get("post", {}).get("duration_ms", 3000) / 1000.0
            chunk_text_by_words(post.content[:100], item["start"], item["start"] + post_dur, words_per_chunk=4)

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header + "\n".join(dialogues) + "\n")


def compose_video(bg_video: str, audio_path: str, timeline: list[dict],
                  total_duration: float, output_path: str, ass_subtitle_path: str = None):
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

    # Input mask bo tròn góc cho Center Satisfying Video
    mask_file = os.path.join(ASSETS_DIR, "satisfying", "rounded_mask_960x560.png")
    has_rounded_mask = os.path.exists(mask_file)
    mask_input_idx = len(timeline) + 2 if has_rounded_mask else None
    if has_rounded_mask:
        inputs.extend(["-i", mask_file])

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
        itype = item.get("type", "")
        h_val = item.get("height")

        # Scale tĩnh theo kích thước chỉ định: nếu có height thì crop vừa vặn, ngược lại giữ aspect ratio
        if itype == "center_satisfying_video" and h_val and mask_input_idx is not None:
            # Bo góc 26px mềm mại cho video ở giữa
            filter_chains.append(
                f"[{img_idx}:v]scale={w}:{h_val}:force_original_aspect_ratio=increase,crop={w}:{h_val},format=yuva420p[c_raw{idx}];"
                f"[{mask_input_idx}:v]scale={w}:{h_val}[m_scale{idx}];"
                f"[c_raw{idx}][m_scale{idx}]alphamerge{layer_label}"
            )
        elif h_val:
            filter_chains.append(f"[{img_idx}:v]scale={w}:{h_val}:force_original_aspect_ratio=increase,crop={w}:{h_val}{layer_label}")
        else:
            filter_chains.append(f"[{img_idx}:v]scale={w}:-1:flags=lanczos{layer_label}")

        # Tính toán hiệu ứng chuyển cảnh đa dạng (Spring Pop, Conversational Slide Left/Right, Punch-in Bounce, Shake)
        trans_type = item.get("transition", "slide_up" if (enable_slide and item.get("enable_slide")) else "none")
        if cam_motion == "punch_in_bounce":
            trans_type = "punch_in_bounce"

        base_x = item.get("x", "(W-w)/2")
        x_pos, y_pos = satisfying_manager.build_transition_expression(
            trans_type=trans_type,
            base_x=base_x,
            base_y=base_y,
            start_t=item["start"],
            dur=slide_dur,
            dist=slide_dist,
            cam_motion=cam_motion,
            shake_start=item.get("shake_start", 0),
            shake_end=item.get("shake_end", 0)
        )

        filter_chains.append(
            f"{prev_stream}{layer_label}overlay=x='{x_pos}':y='{y_pos}':enable='between(t,{start_t},{end_t})':format=auto{out_stream}"
        )
        prev_stream = out_stream

    # 1. Hiệu ứng Vignette Điện Ảnh (Làm tối nhẹ 4 góc để hướng sự chú ý tuyệt đối vào nội dung giữa màn hình)
    enable_vignette = layout.get("enable_cinematic_vignette", True)
    if enable_vignette:
        out_vig = "[v_vignette]"
        filter_chains.append(f"{prev_stream}vignette=PI/4:eval=init{out_vig}")
        prev_stream = out_vig

    # 2. Phụ đề động Kinetic Subtitles (nảy chữ theo nhịp nói kiểu Shorts/TikTok)
    if ass_subtitle_path and os.path.exists(ass_subtitle_path):
        clean_ass = os.path.abspath(ass_subtitle_path).replace("\\", "/").replace(":", "\\:")
        out_sub_stream = "[v_subbed]"
        filter_chains.append(f"{prev_stream}ass='{clean_ass}'{out_sub_stream}")
        prev_stream = out_sub_stream

    # 3. Thanh tiến trình video (Completion Progress Bar) chạy ngầm ở đáy màn hình
    enable_prog_bar = layout.get("enable_completion_progress_bar", True)
    if enable_prog_bar:
        bar_h = int(layout.get("progress_bar_height", 6))
        bar_color = layout.get("progress_bar_color", "#F43F5E")
        bar_y = 1920 - bar_h
        out_bar_bg = "[v_bar_bg]"
        out_bar_fill = "[v_bar_fill]"
        # Dải ray nền đen mờ 40%
        filter_chains.append(f"{prev_stream}drawbox=x=0:y={bar_y}:w=1080:h={bar_h}:color=black@0.4:t=fill{out_bar_bg}")
        # Dải thanh tiến trình gradient hồng tím chạy từ 0 đến 100% video
        filter_chains.append(f"{out_bar_bg}drawbox=x=0:y={bar_y}:w='1080*(t/{max(0.1, total_duration):.3f})':h={bar_h}:color={bar_color}@0.95:t=fill{out_bar_fill}")
        prev_stream = out_bar_fill

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

            # Tạo thẻ Outro Call-To-Action nếu được bật
            if layout.get("enable_outro_cta", True):
                outro_card_path = os.path.join(CARDS_DIR, "outro_cta.png")
                cta_q = getattr(post, "cta_question", "") or "Còn bạn thì sao? Đã từng gặp tình huống éo le tương tự chưa?"
                card_mod.render_outro_cta_card(question_text=cta_q, output_path=outro_card_path)
                tracker.log(f"  📢 Đã tạo thẻ Outro Call-To-Action: {outro_card_path}")

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
        timeline, audio_sequence, overlay_sfx, total_duration, emotional_arc_data = build_timeline_with_emotional_pacing(post, timing, layout_preset=layout_preset, bg_mode=bg_mode)
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
            bg_music_path=selected_bgm,
            overlay_sfx=overlay_sfx
        )
        tracker.finish_step("audio")

        # BƯỚC 5: DỰNG PHIM ĐA LỚP & CAMERA MOTION (60 FPS + SLIDE-UP + LOSSLESS CRF 18)
        tracker.start_step("composite", "FFmpeg dựng phim 60 FPS, hiệu ứng trượt thẻ Slide-Up & Camera Shake...", 85)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_video = os.path.join(FINAL_DIR, f"final_meme_pro_{timestamp}.mp4")

        ass_sub_path = None
        if layout.get("enable_kinetic_subtitles", True):
            ass_sub_path = os.path.join(OUT_DIR, "subtitles.ass")
            generate_kinetic_subtitles_ass(post, timing, timeline, ass_sub_path)
            tracker.log(f"  ✨ Đã tạo phụ đề động Kinetic Subtitles: {ass_sub_path}")

        compose_video(bg_video_out, mixed_audio_out, timeline, total_duration, final_video, ass_subtitle_path=ass_sub_path)
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

        # Tự động render ảnh bìa Thumbnail tỉ lệ 9:16 chuẩn viral (Auto Clickbait Cover Maker)
        if layout.get("enable_auto_thumbnail", True):
            thumb_bg_path = os.path.join(FINAL_DIR, "_thumb_frame.jpg")
            thumb_out_path = final_video.replace(".mp4", "_thumbnail.jpg")
            exe = bg_mod.get_ffmpeg_exe()
            subprocess.run([
                exe, "-y", "-ss", "1.5", "-i", final_video,
                "-vframes", "1", "-q:v", "2", thumb_bg_path
            ], capture_output=True)
            if os.path.exists(thumb_bg_path):
                card_mod.render_video_thumbnail(post, thumb_bg_path, thumb_out_path)
                tracker.log(f"  🖼️ Đã tạo ảnh bìa Thumbnail chuẩn tỉ lệ 9:16: {thumb_out_path}")

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
