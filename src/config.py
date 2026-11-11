"""
CENTRALIZED CONFIGURATION MODULE (DIRECTOR CONFIG)
===================================================
Chức năng:
  - Tải & lưu cấu hình toàn bộ hệ thống từ file JSON: config/director_config.json
  - Tuyệt đối loại bỏ HARDCODE: Tất cả prompt, voice mapping, sfx mapping,
    pacing, khoảng lặng, visual layout đều được định nghĩa tập trung tại đây.
  - Hỗ trợ auto-reload và ghi đè an toàn.
"""

import os
import json
from copy import deepcopy

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "director_config.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Cấu hình mặc định phòng hờ file bị xóa
DEFAULT_CONFIG = {
    "project_name": "Threads Video Automation Studio",
    "version": "2.0.0",
    "ai_director": {
        "model": "gemini-3.5-flash",
        "fallback_model": "gemini-3.5-flash-lite",
        "temperature": 0.92,
        "duration_mode": "auto",  # "auto" (3-6 cmt), "fast_30s" (3 cmt), "deep_60s" (4-5 cmt), "viral_story_90s" (6-7 cmt)
        "min_comments": 3,
        "max_comments": 6,
        "drama_archetypes": [
            "double_twist",     # Cú lật kèo kép: twist ở cmt 2, cú twist sốc hơn ở cmt cuối
            "thread_war",       # Khẩu chiến qua lại: C1 cãi C2, tác giả thanh minh, cmt cuối bóc phốt cả hội
            "chain_exposure",   # Bóc phốt liên hoàn: Mỗi cmt bóc thêm 1 chi tiết éo le dở khóc dở cười
            "confession_roast"  # Hội đồng mạng xâu xé: Tác giả kể chuyện khó đỡ, dân mạng thi nhau cà khịa
        ],
        "system_instruction": (
            "Mày là đạo diễn nội dung viral đỉnh nhất TikTok Việt Nam, chuyên tạo kịch bản video "
            "mô phỏng drama THẬT trên Threads/Twitter. Mày hiểu rõ GenZ Việt nói chuyện như thế nào.\n\n"
            "PHONG CÁCH VIẾT BẮT BUỘC:\n"
            "- Dùng ngôn ngữ GenZ Việt THẬT: 'ủa', 'wtf', 'slay', 'real', 'fr fr', 'no cap', "
            "'vl', 'chs', 'lol', 'bruh', 'era nào rồi', 'tui uninstall cuộc đời', "
            "'same bro', 'đỉnh nóc kịch trần', 'rồi sao nữa...', '???', 'alo 115', "
            "'skill issue', 'it's giving', 'sến', 'xỉu', 'tui đây nè'\n"
            "- Bình luận PHẢI chaotic tự nhiên: viết thường hết HOẶC viết hoa la hét, "
            "dùng '...' '????' nhiều, reaction emoji (💀🤡😭☠️🔥)\n"
            "- Self-deprecating humor: GenZ thích tự chê bản thân ('tui đây nè', 'skill issue', "
            "'thôi tui xin', 'cho tui xin 1 vé đi tu')\n"
            "- Toxic nhẹ kiểu bạn bè: bóc mẽ nhau hài hước ('ê mày ơi', 'found the 🤡', "
            "'ai hỏi đâu nhưng flex hoài')\n"
            "- TUYỆT ĐỐI KHÔNG viết formal, KHÔNG dùng ngôn ngữ báo chí, "
            "KHÔNG viết như người lớn, KHÔNG viết đúng ngữ pháp quá chỉn chu\n\n"
            "CÁC ĐỒ THỊ CẢM XÚC SÁNG TẠO (CHỌN 1 ARCHETYPE PHÙ HỢP NHẤT VỚI CHỦ ĐỀ):\n"
            "1. DOUBLE TWIST (CÚ LẬT KÈO KÉP): Hook -> C1 đồng cảm -> C2 twist sốc lần 1 -> C3 đẩy cao trào -> Cmt cuối twist lần 2 lật ngược toàn bộ sự thật!\n"
            "2. THREAD WAR (KHẨU CHIẾN QUA LẠI): Hook -> C1 nêu ý kiến -> C2 reply_to C1 cãi gay gắt -> C3 tác giả phân trần -> C4 thánh phán bóc mẽ cả hội cạn lời.\n"
            "3. CHAIN EXPOSURE (BÓC PHỐT LIÊN HOÀN): Mỗi bình luận bóc thêm một chi tiết éo le của tác giả, càng về sau tình huống càng bung bét hài hước.\n"
            "4. CONFESSION & ROAST: Tác giả kể một bí mật/sai lầm ngớ ngẩn, các bình luận bên dưới thi nhau cà khịa hoặc hiến kế bá đạo."
        )
    },
    "audio_mastering": {
        "enable_vocal_chain": True,
        "target_lufs": -14,
        "vocal_highpass_hz": 80,
        "vocal_presence_boost_db": 2.5,
        "enable_compression": True,
        "audio_bitrate": "256k"
    },
    "emotional_pacing": {
        "hook_speed_rate": "+10%",
        "punchline_pre_pause_ms": 350,
        "punchline_post_pause_ms": 500,
        "comment_gap_ms": 280,
        "awkward_silence_duration_ms": 400,
        "bgm_volume_normal": 0.16,
        "bgm_volume_duck": 0.05,
        "enable_bgm_ducking": True,
        "enable_camera_shake": True,
        "enable_card_zoom": True
    },
    "voice_casting": {
        "default_author_voice": "vi-VN-NamMinhNeural",
        "default_commenter_female_voice": "vi-VN-HoaiMyNeural",
        "default_commenter_male_voice": "vi-VN-NamMinhNeural",
        "dynamic_casting": True,
        "female_voice_presets": [
            {"voice": "vi-VN-HoaiMyNeural", "rate": "+18%", "pitch": "+10Hz", "style": "cheerful"},
            {"voice": "vi-VN-HoaiMyNeural", "rate": "+12%", "pitch": "+4Hz",  "style": "chat"},
            {"voice": "vi-VN-HoaiMyNeural", "rate": "+24%", "pitch": "+14Hz", "style": "excited"},
            {"voice": "vi-VN-HoaiMyNeural", "rate": "-6%",  "pitch": "-6Hz",  "style": "sad"}
        ],
        "male_voice_presets": [
            {"voice": "vi-VN-NamMinhNeural", "rate": "+25%", "pitch": "-10Hz", "style": "angry"},
            {"voice": "vi-VN-NamMinhNeural", "rate": "+16%", "pitch": "+6Hz",  "style": "cheerful"},
            {"voice": "vi-VN-NamMinhNeural", "rate": "+22%", "pitch": "+0Hz",  "style": "chat"},
            {"voice": "vi-VN-NamMinhNeural", "rate": "-8%",  "pitch": "-8Hz",  "style": "sad"}
        ],
        "roles": {
            "author": {"voice": "vi-VN-NamMinhNeural", "rate": "+5%", "pitch": "+0Hz", "style": ""},
            "commenter_1": {"voice": "vi-VN-HoaiMyNeural", "rate": "+18%", "pitch": "+10Hz", "style": "cheerful"},
            "commenter_2": {"voice": "vi-VN-NamMinhNeural", "rate": "+25%", "pitch": "-10Hz", "style": "angry"},
            "commenter_3": {"voice": "vi-VN-HoaiMyNeural", "rate": "-8%", "pitch": "-6Hz", "style": "sad"}
        },
        "emotion_style_map": {
            "funny":      {"style": "cheerful",    "rate_boost": "+5%",  "pitch_boost": "+3Hz"},
            "shocked":    {"style": "shouting",    "rate_boost": "+15%", "pitch_boost": "+6Hz"},
            "awkward":    {"style": "embarrassed", "rate_boost": "-5%",  "pitch_boost": "-2Hz"},
            "sarcastic":  {"style": "disgruntled", "rate_boost": "+8%",  "pitch_boost": "-4Hz"},
            "insightful": {"style": "calm",        "rate_boost": "+0%",  "pitch_boost": "+0Hz"},
            "neutral":    {"style": "",            "rate_boost": "+0%",  "pitch_boost": "+0Hz"}
        }
    },
    "sfx_meme_registry": {
        "laugh": {"sfx_file": "laugh.mp3", "meme_file": "laugh.gif", "emotion": "funny", "camera_motion": "punch_in"},
        "boom": {"sfx_file": "boom.mp3", "meme_file": "boom.gif", "emotion": "shocked", "camera_motion": "shake"},
        "bruh": {"sfx_file": "bruh.mp3", "meme_file": "bruh.gif", "emotion": "awkward", "camera_motion": "punch_in"},
        "shock": {"sfx_file": "shock.mp3", "meme_file": "shock.gif", "emotion": "shocked", "camera_motion": "shake"},
        "huh": {"sfx_file": "huh.mp3", "meme_file": "huh.gif", "emotion": "awkward", "camera_motion": "punch_in"},
        "side_eye": {"sfx_file": "side_eye.mp3", "meme_file": "side_eye.gif", "emotion": "sarcastic", "camera_motion": "punch_in"},
        "clown": {"sfx_file": "clown.mp3", "meme_file": "clown.gif", "emotion": "funny", "camera_motion": "punch_in"},
        "math": {"sfx_file": "math.mp3", "meme_file": "math.gif", "emotion": "shocked", "camera_motion": "none"},
        "applause": {"sfx_file": "laugh.mp3", "meme_file": "applause.gif", "emotion": "funny", "camera_motion": "punch_in"},
        "dancing_cat": {"sfx_file": "laugh.mp3", "meme_file": "dancing_cat.gif", "emotion": "funny", "camera_motion": "punch_in"},
        "ting": {"sfx_file": "ting.mp3", "meme_file": "ting.gif", "emotion": "insightful", "camera_motion": "none"},
        "none": {"sfx_file": None, "meme_file": None, "emotion": "neutral", "camera_motion": "none"}
    },
    "visual_layout": {
        "canvas_width": 1080,
        "canvas_height": 1920,
        "card_width": 960,
        "post_card_y": 280,
        "comment_card_gap": 24,
        "sticker_size": 440,
        "sticker_offset_y": 28,
        "anchor_memes_pool": [
            "dancing_cat.gif",
            "shocked_cat.gif",
            "huh.gif",
            "clown.gif",
            "math.gif",
            "applause.gif"
        ],
        "default_bg_mode": "minecraft",
        "default_card_theme": "light",
        "fps": 60,
        "video_crf": 18,
        "video_preset": "medium",
        "color_space": "bt709",
        "enable_slide_up_animation": True,
        "slide_duration_s": 0.22,
        "slide_distance_px": 45
    },
    "illustration_settings": {
        "enable_post_attached_image": False,
        "enable_popup_illustration": True,
        "popup_trigger": "twist",
        "popup_duration_s": 2.2,
        "popup_animation": "punch_in_bounce",
        "badge_text": ""
    }
}

_cached_config = None


def load_config(force_reload: bool = False) -> dict:
    """Tải cấu hình từ JSON file với cơ chế fallback và caching."""
    global _cached_config
    if _cached_config is not None and not force_reload:
        return _cached_config

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge sâu với mặc định để không bị thiếu trường
                merged = deepcopy(DEFAULT_CONFIG)
                for k, v in data.items():
                    if isinstance(v, dict) and k in merged:
                        merged[k].update(v)
                    else:
                        merged[k] = v
                _cached_config = merged
                return _cached_config
        except Exception as e:
            print(f"[!] Lỗi đọc director_config.json: {e}. Sử dụng cấu hình mặc định.")

    _cached_config = deepcopy(DEFAULT_CONFIG)
    return _cached_config


def save_config(new_config: dict) -> bool:
    """Lưu cấu hình mới vào file JSON."""
    global _cached_config
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(new_config, f, ensure_ascii=False, indent=2)
        _cached_config = deepcopy(new_config)
        return True
    except Exception as e:
        print(f"[!] Lỗi lưu director_config.json: {e}")
        return False


def get_voice_for_role(role_name: str) -> dict:
    """Lấy cấu hình giọng đọc, tốc độ và cao độ cho từng vai."""
    cfg = load_config()
    roles = cfg.get("voice_casting", {}).get("roles", {})
    if role_name in roles:
        return roles[role_name]
    # Fallback mặc định
    return {"voice": "vi-VN-NamMinhNeural", "rate": "+10%", "pitch": "+0Hz"}


def get_sfx_info(sfx_key: str) -> dict:
    """Lấy thông tin file audio, meme sticker, hiệu ứng camera của SFX."""
    cfg = load_config()
    reg = cfg.get("sfx_meme_registry", {})
    return reg.get(sfx_key, reg.get("none", {}))

