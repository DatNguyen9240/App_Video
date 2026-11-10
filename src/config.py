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
        "temperature": 0.88,
        "system_instruction": (
            "Bạn là Đạo diễn kiêm Nhà sáng tạo nội dung hàng đầu cho video ngắn (TikTok, Reels, Shorts) tại Việt Nam. "
            "Phong cách dí dỏm, châm biếm sâu cay, bắt trend Gen Z và dân văn phòng cực nhạy.\n"
            "Xây dựng kịch bản có 'ĐỒ THỊ CẢM XÚC' lôi cuốn người xem như người thật:\n"
            "1. HOOK: Tò mò, kích thích người xem không lướt qua.\n"
            "2. BÌNH LUẬN 1: Tung hứng đồng cảm hài hước.\n"
            "3. BÌNH LUẬN 2: Cú bẻ lái sốc (punchline) không thể đoán trước.\n"
            "4. BÌNH LUẬN 3: Chốt hạ cạn lời hoặc để lại dư vị tranh cãi."
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
        "bgm_volume_normal": 0.08,
        "bgm_volume_duck": 0.03,
        "enable_bgm_ducking": True,
        "enable_camera_shake": True,
        "enable_card_zoom": True
    },
    "voice_casting": {
        "default_author_voice": "vi-VN-NamMinhNeural",
        "default_commenter_female_voice": "vi-VN-HoaiMyNeural",
        "default_commenter_male_voice": "vi-VN-NamMinhNeural",
        "roles": {
            "author": {"voice": "vi-VN-NamMinhNeural", "rate": "+8%", "pitch": "+0Hz"},
            "commenter_1": {"voice": "vi-VN-HoaiMyNeural", "rate": "+10%", "pitch": "+4Hz"},
            "commenter_2": {"voice": "vi-VN-NamMinhNeural", "rate": "+12%", "pitch": "-4Hz"},
            "commenter_3": {"voice": "vi-VN-HoaiMyNeural", "rate": "+5%", "pitch": "-2Hz"}
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
        "post_card_y": 240,
        "comment_card_gap": 24,
        "sticker_size": 260,
        "sticker_offset_y": 35,
        "default_bg_mode": "infinity",
        "default_card_theme": "light",
        "fps": 60,
        "video_crf": 18,
        "video_preset": "medium",
        "color_space": "bt709",
        "enable_slide_up_animation": True,
        "slide_duration_s": 0.22,
        "slide_distance_px": 45
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

