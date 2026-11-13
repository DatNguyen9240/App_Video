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
            "Bạn là người viết Threads chuyên nghiệp, am hiểu sâu sắc văn hóa Gen Z, tâm lý giới trẻ Việt Nam "
            "và nghệ thuật kể chuyện đời thường.\n\n"
            "NHIỆM VỤ:\n"
            "Tạo kịch bản bài đăng Threads và chuỗi bình luận đối đáp chân thật 100%, "
            "như một người thật đang ngồi cà phê kể chuyện cho bạn thân nghe.\n\n"
            "NGUYÊN TẮC CỐT LÕI (THREADS HUMAN GEN Z WRITER):\n"
            "1. VIẾT NHƯ NGƯỜI THẬT NÓI CHUYỆN: Câu chữ trau chuốt, mềm mại, có nhịp điệu và giàu cảm xúc. "
            "Tuyệt đối không giống bài văn nghị luận hay AI giải thích.\n"
            "2. NGÔN NGỮ GEN Z ĐỜI THƯỜNG & TINH TẾ: Dùng từ ngữ tự nhiên của giới trẻ nhưng vừa đủ, "
            "TUYỆT ĐỐI KHÔNG gượng ép nhồi nhét từ lóng hay cố tỏ ra hài hước.\n"
            "3. CHI TIẾT ĐỜI THƯỜNG ĐẮC GIÁ: Luôn gắn với các tình huống cụ thể, quen thuộc (công sở, tình yêu, gia đình, bạn bè) "
            "khiến người đọc cảm thấy 'sao giống mình vậy'.\n"
            "4. CHUỖI BÌNH LUẬN SỐNG ĐỘNG: Bình luận là những người dùng thật vào góp chuyện — có người đồng cảm kể thêm chuyện éo le, "
            "có người bắt thóp chi tiết để cà khịa dí dỏm, có người reply_to tranh luận sôi nổi.\n"
            "5. ĐIỀU TUYỆT ĐỐI TRÁNH:\n"
            "- Không viết văn mẫu hay mở đầu sáo rỗng.\n"
            "- Không dùng quá nhiều emoji (tối đa 0-1 emoji tinh tế mỗi câu).\n"
            "- Không kết bài bằng đạo lý hay bài học nhân sinh ép uổng.\n"
            "- Không để lộ bất kỳ dấu vết nào của văn phong AI.\n\n"
            "TIÊU CHUẨN TỰ KIỂM DUYỆT:\n"
            "'Người thật có nói câu này ngoài đời không? Đọc lên có tự nhiên và lôi cuốn không? Có tạo cảm giác bạn bè đang kể chuyện cho nhau không?'"
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
        "hook_speed_rate": "+20%",
        "punchline_pre_pause_ms": 50,
        "punchline_post_pause_ms": 250,
        "comment_gap_ms": 180,
        "awkward_silence_duration_ms": 250,
        "bgm_volume_normal": 0.18,
        "bgm_volume_duck": 0.06,
        "enable_bgm_ducking": True,
        "enable_camera_shake": True,
        "enable_card_zoom": True
    },
    "voice_casting": {
        "default_author_voice": "adam",
        "default_commenter_female_voice": "vi-VN-HoaiMyNeural",
        "default_commenter_male_voice": "vi-VN-NamMinhNeural",
        "dynamic_casting": True,
        "female_voice_presets": [
            {"voice": "vi-VN-HoaiMyNeural", "rate": "+20%", "pitch": "+6Hz", "style": "cheerful"},
            {"voice": "ava", "rate": "+16%", "pitch": "+4Hz",  "style": "chat"},
            {"voice": "vi-VN-HoaiMyNeural", "rate": "+24%", "pitch": "+10Hz", "style": "excited"},
            {"voice": "emma", "rate": "+14%", "pitch": "+2Hz",  "style": "sad"}
        ],
        "male_voice_presets": [
            {"voice": "adam", "rate": "+14%", "pitch": "-4Hz", "style": "dramatic"},
            {"voice": "vi-VN-NamMinhNeural", "rate": "+20%", "pitch": "+4Hz",  "style": "cheerful"},
            {"voice": "brian", "rate": "+16%", "pitch": "-2Hz",  "style": "chat"},
            {"voice": "vi-VN-NamMinhNeural", "rate": "+22%", "pitch": "+2Hz",  "style": "angry"}
        ],
        "roles": {
            "author": {"voice": "adam", "rate": "+12%", "pitch": "-4Hz", "style": "narrative"},
            "commenter_1": {"voice": "vi-VN-HoaiMyNeural", "rate": "+20%", "pitch": "+6Hz", "style": "cheerful"},
            "commenter_2": {"voice": "vi-VN-NamMinhNeural", "rate": "+22%", "pitch": "+2Hz", "style": "chat"},
            "commenter_3": {"voice": "ava", "rate": "+16%", "pitch": "+4Hz", "style": "sarcastic"},
            "commenter_4": {"voice": "adam", "rate": "+14%", "pitch": "-4Hz", "style": "dramatic"},
            "commenter_5": {"voice": "vi-VN-HoaiMyNeural", "rate": "+18%", "pitch": "+3Hz", "style": "cheerful"},
            "commenter_6": {"voice": "brian", "rate": "+16%", "pitch": "-2Hz", "style": "chat"},
            "commenter_7": {"voice": "emma", "rate": "+14%", "pitch": "+2Hz", "style": "sad"},
            "commenter_8": {"voice": "vi-VN-NamMinhNeural", "rate": "+20%", "pitch": "+2Hz", "style": "chat"}
        },
        "emotion_style_map": {
            "funny":      {"style": "cheerful",    "rate_boost": "+8%",  "pitch_boost": "+4Hz"},
            "shocked":    {"style": "shouting",    "rate_boost": "+20%", "pitch_boost": "+6Hz"},
            "awkward":    {"style": "embarrassed", "rate_boost": "+8%",  "pitch_boost": "-2Hz"},
            "sarcastic":  {"style": "disgruntled", "rate_boost": "+14%", "pitch_boost": "+0Hz"},
            "insightful": {"style": "calm",        "rate_boost": "+10%", "pitch_boost": "+2Hz"},
            "neutral":    {"style": "",            "rate_boost": "+15%", "pitch_boost": "+2Hz"}
        }
    },
    "sfx_meme_registry": {
        "sub_drop": {"sfx_file": "sub_drop.mp3", "meme_file": "boom.gif", "emotion": "shocked", "camera_motion": "shake", "vol": 0.45},
        "scratch": {"sfx_file": "scratch.mp3", "meme_file": "awkward.gif", "emotion": "awkward", "camera_motion": "punch_in", "vol": 0.40},
        "pop": {"sfx_file": "pop.mp3", "meme_file": None, "emotion": "funny", "camera_motion": "none", "vol": 0.35},
        "ting": {"sfx_file": "ting.mp3", "meme_file": "ting.gif", "emotion": "insightful", "camera_motion": "none", "vol": 0.35},
        "whoosh": {"sfx_file": "whoosh.mp3", "meme_file": None, "emotion": "neutral", "camera_motion": "none", "vol": 0.35},
        "camera": {"sfx_file": "camera.mp3", "meme_file": None, "emotion": "neutral", "camera_motion": "none", "vol": 0.40},
        "boom": {"sfx_file": "sub_drop.mp3", "meme_file": "boom.gif", "emotion": "shocked", "camera_motion": "shake", "vol": 0.45},
        "laugh": {"sfx_file": "pop.mp3", "meme_file": "laugh.gif", "emotion": "funny", "camera_motion": "punch_in", "vol": 0.35},
        "bruh": {"sfx_file": "scratch.mp3", "meme_file": "bruh.gif", "emotion": "awkward", "camera_motion": "punch_in", "vol": 0.40},
        "shock": {"sfx_file": "sub_drop.mp3", "meme_file": "shock.gif", "emotion": "shocked", "camera_motion": "shake", "vol": 0.45},
        "huh": {"sfx_file": "huh.mp3", "meme_file": "huh.gif", "emotion": "awkward", "camera_motion": "punch_in", "vol": 0.35},
        "side_eye": {"sfx_file": "scratch.mp3", "meme_file": "side_eye.gif", "emotion": "sarcastic", "camera_motion": "punch_in", "vol": 0.40},
        "clown": {"sfx_file": "scratch.mp3", "meme_file": "clown.gif", "emotion": "funny", "camera_motion": "punch_in", "vol": 0.35},
        "math": {"sfx_file": "ting.mp3", "meme_file": "math.gif", "emotion": "shocked", "camera_motion": "none", "vol": 0.35},
        "applause": {"sfx_file": "ting.mp3", "meme_file": "applause.gif", "emotion": "funny", "camera_motion": "punch_in", "vol": 0.35},
        "dancing_cat": {"sfx_file": "pop.mp3", "meme_file": "dancing_cat.gif", "emotion": "funny", "camera_motion": "punch_in", "vol": 0.35},
        "none": {"sfx_file": None, "meme_file": None, "emotion": "neutral", "camera_motion": "none", "vol": 0.0}
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
        "slide_distance_px": 45,
        "enable_kinetic_subtitles": True,
        "enable_completion_progress_bar": True,
        "progress_bar_color": "#F43F5E",
        "progress_bar_height": 6,
        "enable_cinematic_vignette": True,
        "enable_spatial_audio_panning": True,
        "enable_tension_riser": True,
        "enable_meme_reaction_popups": False,
        "min_video_duration_s": 60.5,
        "enforce_min_duration_60s": True
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
    return {"voice": "vi-VN-HoaiMyNeural", "rate": "+18%", "pitch": "+4Hz"}


def get_sfx_info(sfx_key: str) -> dict:
    """Lấy thông tin file audio, meme sticker, hiệu ứng camera của SFX."""
    cfg = load_config()
    reg = cfg.get("sfx_meme_registry", {})
    return reg.get(sfx_key, reg.get("none", {}))

