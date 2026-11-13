"""
MODULE 1B: SINH BÀI ĐĂNG TỰ ĐỘNG BẰNG GOOGLE GEMINI AI (ĐẠO DIỄN CẢM XÚC)
==========================================================================
Chức năng:
  - Sử dụng Gemini API (gemini-2.5-flash) để sáng tạo kịch bản Threads chuẩn đồ thị cảm xúc
  - Tự động gán vai diễn, giọng đọc đa vai (Nam/Nữ), nhịp thở (pauses), và hiệu ứng SFX/Camera
  - Không hardcode: Tải toàn bộ prompt, vai diễn và tham số từ src/config.py
  - Hỗ trợ progress callback để cập nhật trực tiếp lên Tab Giám Sát Quy Trình
"""

import os
import sys
import json
import random
import re
from typing import Literal, Optional, Callable
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(BASE_DIR, "src")
ENV_PATH = os.path.join(BASE_DIR, ".env")
OUT_DIR  = os.path.join(BASE_DIR, "output")

sys.path.insert(0, SRC_DIR)
import config as config_mod
import importlib.util

spec = importlib.util.spec_from_file_location("content_base", os.path.join(SRC_DIR, "01_content.py"))
content_base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(content_base)

Post = content_base.Post
Comment = content_base.Comment

PALETTE = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"]


def clean_hex_color(c: str) -> str:
    """Đảm bảo chuỗi màu luôn là mã hex 6 ký tự hợp lệ."""
    c = str(c).strip()
    if re.match(r"^#?[0-9a-fA-F]{6}$", c):
        return c if c.startswith("#") else f"#{c}"
    return random.choice(PALETTE)


# ─── ĐỊNH NGHĨA SCHEMA JSON CHO GEMINI (CÓ METADATA ĐẠO DIỄN) ────────────────

class CommentSchema(BaseModel):
    username: str = Field(description="Tên người dùng hài hước tiếng Việt hoặc Gen Z, ví dụ: hr_ngapnguoi, dev_khocnhe, linh.cute03, tuan_it_98")
    reply_to: str = Field(default="", description="Tên username mà bình luận này đang trả lời (có thể là tác giả HOẶC username của bình luận trước để tạo hội thoại cãi nhau/đối đáp)")
    content: str = Field(description="Bình luận tiếng Việt CỰC KỲ NGẮN GỌN (chỉ 1 câu đắt giá, tối đa 20 từ), sắc sảo, dí dỏm, bẻ lái hoặc châm chọc hài hước")
    avatar_color: str = Field(default="#3b82f6", description="Mã màu hex (vd: #3b82f6, #ef4444, #10b981, #f59e0b)")
    likes: int = Field(default=4500, description="Lượt thích ước tính từ 1500 đến 25000")
    sfx: Literal["none", "sub_drop", "scratch", "ting", "boom", "laugh", "bruh", "pop"] = Field(
        default="none",
        description="Hiệu ứng âm thanh điểm nhấn. QUAN TRỌNG: 80% bình luận phải để 'none'! Toàn video chỉ tối đa 1-2 comment có SFX để tạo điểm nhấn bất ngờ (sub_drop cho bẻ lái sốc, scratch cho cạn lời/quay xe, ting cho điểm sáng đắt giá). Tuyệt đối không gắn SFX cho mọi comment!"
    )
    emotion: Literal["funny", "shocked", "awkward", "sarcastic", "insightful", "neutral"] = Field(
        default="funny", description="Sắc thái cảm xúc của câu nói"
    )
    pause_before_ms: int = Field(default=180, description="Khoảng lặng tự nhiên trước khi nói (120-250ms)")
    pause_after_ms: int = Field(default=220, description="Khoảng lặng tự nhiên sau khi dứt câu (150-300ms)")
    camera_motion: Literal["none", "punch_in", "shake"] = Field(
        default="none", description="Hiệu ứng chuyển động hình ảnh ('punch_in' phóng to nhẹ, 'shake' rung lắc khi sốc)"
    )


class PostSchema(BaseModel):
    username: str = Field(description="Tên tác giả bài viết, ví dụ: genz.tamsu, chuyen.congso, dev_nghiep")
    content: str = Field(description="Câu chuyện Threads CỰC KỲ NGẮN GỌN (chỉ từ 1 đến 2 câu ngắn, tối đa 30-35 từ). Bắt đầu bằng Hook giật mình tò mò, nêu ngay mâu thuẫn cốt lõi, không dài dòng lan man")
    avatar_color: str = Field(default="#8b5cf6", description="Mã màu hex cho avatar tác giả")
    is_verified: bool = Field(default=True, description="True nếu có tích xanh")
    likes: int = Field(default=35000, description="Lượt thích ước tính từ 15000 đến 85000")
    video_title: str = Field(default="", description="Tiêu đề video ngắn gọn, giật gân hài hước")
    video_caption: str = Field(default="", description="Đoạn caption đăng TikTok/Reels cuốn hút kèm câu hỏi kêu gọi bình luận")
    hashtags: str = Field(default="", description="Chuỗi 5-7 hashtag viral")
    illustration_prompt: str = Field(
        default="",
        description="Mô tả hình ảnh bằng tiếng Anh chi tiết (10-15 từ) để AI vẽ ảnh minh họa hài hước chuẩn xác tình huống/trò đùa của bài, ví dụ: 'funny man in bed wearing sunglasses at midnight blinded by bright light funny meme'"
    )
    illustration_caption: str = Field(
        default="",
        description="Câu chú thích hài hước tiếng Việt ngắn (dưới 10 từ) gắn lên ảnh minh họa để người xem hiểu ngay trò đùa, ví dụ: 'Đèn ngủ lúc 2h sáng: Sáng như sân Mỹ Đình!'"
    )
    emotional_arc: str = Field(default="Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời", description="Tóm tắt đường cong cảm xúc của video")
    hook_energy: Literal["high", "intense", "curious"] = Field(default="high", description="Mức năng lượng mở màn")
    comments: list[CommentSchema] = Field(
        description="Danh sách từ 3 đến 6 bình luận tiếng Việt tạo thành chuỗi drama/hội thoại cuốn hút, đối đáp qua lại hoặc plot twist liên hoàn"
    )


# ─── QUẢN LÝ GEMINI API KEY ──────────────────────────────────────────────────

def load_gemini_api_key() -> str:
    """Đọc API Key từ môi trường hoặc file .env."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return ""


def save_gemini_api_key(key: str):
    """Lưu API Key vào file .env."""
    key = key.strip()
    lines = []
    found = False
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("GEMINI_API_KEY="):
                    lines.append(f"GEMINI_API_KEY={key}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"GEMINI_API_KEY={key}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"[✓] Đã lưu API key vào: {ENV_PATH}")


def get_or_prompt_api_key() -> str:
    """Lấy API key, nếu chưa có thì hỏi người dùng nhập."""
    key = load_gemini_api_key()
    if key:
        return key

    print("\n" + "=" * 60)
    print("🔑 CHƯA TÌM THẤY GEMINI API KEY")
    print("=" * 60)
    print("Bạn có thể lấy API Key hoàn toàn MIỄN PHÍ tại:")
    print("👉 https://aistudio.google.com/app/apikey")
    print("=" * 60)
    new_key = input("Nhập Gemini API Key của bạn: ").strip()
    if not new_key:
        raise ValueError("API Key không được để trống!")
    save_gemini_api_key(new_key)
    return new_key


# ─── GỌI GEMINI ĐỂ SINH NỘI DUNG ──────────────────────────────────────────────

def generate_story_with_gemini(
    topic: str = "",
    duration_mode: str = None,
    target_comment_count: Optional[int] = None,
    api_key: str = None,
    progress_cb: Optional[Callable[[str, int], None]] = None
) -> Post:
    """
    Sử dụng Gemini API với cấu hình đạo diễn cảm xúc để tạo kịch bản Threads hoàn chỉnh.
    Hỗ trợ linh hoạt số lượng bình luận (4-8+ câu) và đa dạng hóa đồ thị drama.
    Tự động phân vai giọng đọc (Adam, Brian, Nam Minh, Hoài My...) và thiết lập khoảng lặng.
    """
    if progress_cb:
        progress_cb("Khởi tạo kết nối Gemini AI...", 10)

    if not api_key:
        api_key = get_or_prompt_api_key()

    cfg = config_mod.load_config()
    ai_cfg = cfg.get("ai_director", {})
    voice_cfg = cfg.get("voice_casting", {})

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})

    if not topic:
        # Ưu tiên lấy từ danh sách trend Threads được fetch mỗi ngày
        try:
            radar_spec = importlib.util.spec_from_file_location("threads_radar", os.path.join(SRC_DIR, "threads_radar.py"))
            radar_mod = importlib.util.module_from_spec(radar_spec)
            radar_spec.loader.exec_module(radar_mod)
            daily_res = radar_mod.get_daily_trending_topics()
            if daily_res.get("topics"):
                topic = random.choice(daily_res["topics"])
        except Exception:
            pass

    if not topic:
        topic = "câu chuyện thịnh hành và gây sốt nhất hôm nay trên mạng xã hội Threads"

    system_instruction = ai_cfg.get("system_instruction", "")
    model_primary = ai_cfg.get("model", "gemini-3.5-flash")
    model_fallback = ai_cfg.get("fallback_model", "gemini-3.5-flash-lite")
    temperature = float(ai_cfg.get("temperature", 0.92))
    dur_mode = duration_mode or ai_cfg.get("duration_mode", "auto")

    if target_comment_count and int(target_comment_count) > 0:
        c_count = int(target_comment_count)
        cmt_guide = (
            f"SỐ LƯỢNG BÌNH LUẬN: BẮT BUỘC TẠO CHÍNH XÁC {c_count} BÌNH LUẬN (từ C1 đến C{c_count}). Tuyệt đối không được ít hơn hay nhiều hơn!\n"
            f"- C1: Reaction mở đầu châm ngòi (reply_to username tác giả, sfx='none').\n"
            f"- C2 đến C{max(2, c_count - 2)}: Đối đáp, tranh cãi, bóc phốt hoặc kể thêm tình huống éo le (hãy dùng reply_to để các comment đối đáp lẫn nhau sống động).\n"
            f"- C{max(2, c_count - 1)}: Cú bẻ lái sốc 180° hoặc bóc mẽ đau điếng (gắn sfx='sub_drop').\n"
            f"- C{c_count}: Câu chốt hạ cạn lời / dí dỏm (sfx='scratch' hoặc 'none')."
        )
    elif dur_mode == "fast_30s":
        cmt_guide = (
            "SỐ LƯỢNG BÌNH LUẬN: ĐÚNG 3 BÌNH LUẬN (Thời lượng ngắn 25-30s):\n"
            "- C1: Đồng cảm / reaction hài hước (reply_to username tác giả, sfx='none').\n"
            "- C2: BẺ LÁI 180° sốc hoặc bóc phốt đau điếng (sfx='sub_drop').\n"
            "- C3: Chốt hạ cạn lời dí dỏm (sfx='none' hoặc 'scratch')."
        )
    elif dur_mode == "deep_60s":
        cmt_guide = (
            "SỐ LƯỢNG BÌNH LUẬN: TỪ 5 ĐẾN 6 BÌNH LUẬN (Thời lượng drama 45-60s):\n"
            "- C1: Reaction mở đầu châm ngòi drama (reply_to tác giả, sfx='none').\n"
            "- C2: Tranh cãi hoặc phản bác gay gắt (reply_to username C1, sfx='none').\n"
            "- C3: Tác giả hoặc người trong cuộc vào bóc thêm chi tiết éo le (sfx='none').\n"
            "- C4: Bạn bè hoặc người ngoài bắt thóp chi tiết buồn cười (sfx='none').\n"
            "- C5: Cú bẻ lái sốc lần 2 đảo lộn sự thật (sfx='sub_drop').\n"
            "- C6: Chốt hạ cạn lời / đúc kết dí dỏm (sfx='scratch' hoặc 'none')."
        )
    elif dur_mode == "viral_story_90s":
        cmt_guide = (
            "SỐ LƯỢNG BÌNH LUẬN: TỪ 6 ĐẾN 8 BÌNH LUẬN (Thời lượng viral dài 70-90s):\n"
            "- Tạo một chuỗi drama hoàn chỉnh: khẩu chiến nhiều phe, bóc phốt liên hoàn, các cú twist dồn dập khiến người xem không thể rời mắt.\n"
            "- QUY TẮC SFX: Tối đa 2 bình luận có sfx (chỉ gắn ở điểm twist đỉnh cao), các bình luận còn lại để sfx='none'."
        )
    else:  # "auto"
        cmt_guide = (
            "SỐ LƯỢNG BÌNH LUẬN: TỪ 5 ĐẾN 7 BÌNH LUẬN (KHÔNG ĐƯỢC GIỚI HẠN Ở 4 BÌNH LUẬN! Hãy tạo chuỗi hội thoại đa tầng nhiều người đối đáp):\n"
            "- C1: Reaction mở đầu đồng cảm / ngạc nhiên (reply tác giả).\n"
            "- C2: Tranh cãi, phản biện hoặc kể thêm trải nghiệm éo le của bản thân (reply C1).\n"
            "- C3: Bắt thóp chi tiết sơ hở để cà khịa (reply C2 hoặc tác giả).\n"
            "- C4: Người thứ 4 nhảy vào tiếp lời tạo không khí rôm rả (reply C1 hoặc C3).\n"
            "- C5: Cú bẻ lái bất ngờ 180° đảo ngược tình huống (sfx='sub_drop').\n"
            "- C6/C7: Chốt hạ cạn lời, meme đối đáp hài hước.\n"
            "- QUY TẮC SFX: 80% bình luận để sfx='none'. Chỉ 1-2 comment bẻ lái bất ngờ nhất mới gắn sfx='sub_drop' hoặc 'scratch'."
        )

    prompt = (
        f"Bạn là người viết Threads chuyên nghiệp (SKILL: THREADS HUMAN GEN Z WRITER).\n"
        f"Hãy tạo 1 kịch bản bài đăng Threads và các bình luận bằng tiếng Việt thật tự nhiên, trau chuốt như người thật đang chia sẻ trải nghiệm với bạn bè.\n"
        f"Chủ đề: '{topic}'\n"
        f"Chế độ kịch bản: {dur_mode}\n\n"
        f"QUY TẮC CỐT LÕI (PHẢI GIỐNG 100% NGƯỜI THẬT KỂ CHUYỆN):\n"
        f"- BÀI VIẾT CHÍNH: Ngắn gọn (1-2 câu, tối đa 30-35 từ). Mở đầu bằng một tình huống rất đời, rất cụ thể, câu chữ trau chuốt, nhịp điệu mềm mại, khiến người đọc cảm thấy 'sao giống mình vậy' và muốn dừng lại đọc tiếp.\n"
        f"{cmt_guide}\n"
        f"- CÁC BÌNH LUẬN: Mỗi bình luận chỉ 1 câu ngắn gọn (dưới 20 từ), mang góc nhìn chân thật của bạn bè mạng xã hội: người đồng cảm góp thêm tình huống dở khóc dở cười, người bắt thóp chi tiết để cà khịa dí dỏm, người tranh luận phản biện qua lại.\n"
        f"- NGÔN NGỮ GENZ TINH TẾ: Dùng từ ngữ tự nhiên của giới trẻ nhưng VỪA ĐỦ, tuyệt đối KHÔNG nhồi nhét tiếng lóng gượng ép, KHÔNG cố tỏ ra hài hước rẻ tiền.\n"
        f"- EMOJI VỪA PHẢI: Tối đa 0-1 emoji mỗi câu khi thật sự cần thiết (tránh spam emoji).\n"
        f"- USERNAME: Tên username tiếng Việt đời thường tự nhiên (ví dụ: 'linh.nguyen99', 'minh.triet01', 'hoang_anh.hr', 'be_meo_ngu_ngay').\n"
        f"- REPLY_TO: Ghi rõ username đang trả lời để tạo sự đối đáp sống động.\n"
        f"- TUYỆT ĐỐI TRÁNH: Không viết như AI giải thích, không mở đầu sáo rỗng, không kết bài bằng đạo lý rập khuôn.\n\n"
        f"TIÊU CHUẨN KIỂM DUYỆT:\n"
        f"Trước khi trả kết quả, tự hỏi: 'Người thật có nói câu này ngoài đời không? Đọc lên có tự nhiên và truyền cảm không?'\n\n"
        f"VÍ DỤ TONE THAM KHẢO (Đầy đủ 6 bình luận đối đáp nhiều tầng):\n"
        f"Bài: 'Chồng tui bảo mua đèn ngủ thông minh cho lãng mạn, giờ đêm trở mình đập muỗi một cái là phòng sáng rực như sân bóng 😭'\n"
        f"- C1: 'Rồi lúc hai vợ chồng cãi nhau cái đèn có chớp tắt báo động không bà? 😂' (reply tác giả)\n"
        f"- C2: 'Nhà tui mua đèn cảm biến giọng nói nè, đêm ổng ngáy một tiếng là nó tự đổi bảy màu lộng lẫy' (reply C1)\n"
        f"- C3: 'Ủa rồi lỡ nói mớ gọi tên nyc thì đèn có hú còi cứu hỏa không?' (reply C2)\n"
        f"- C4: 'Cười xỉu, tính năng tự động hóa hôn nhân phiên bản cảm lạnh dữ dằn 💀' (reply C1)\n"
        f"- C5: 'Review xin nhẹ link shop đi bà, tui mua tặng con bạn thân sắp cưới cho sáng mắt ra' (reply tác giả)\n"
        f"- C6: 'Ác vừa thôi bà, cưới xong block nhau nghỉ chơi luôn không chừng' (reply C5)\n"
    )

    if progress_cb:
        progress_cb(f"Gemini AI ({model_primary}) đang sáng tạo kịch bản...", 30)

    models_to_try = [model_primary, "gemini-flash-latest"]
    data = None
    last_err = None

    import time
    for m in models_to_try:
        if data:
            break
        for attempt in range(1, 4):
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=PostSchema,
                        temperature=temperature,
                    )
                )
                data = json.loads(response.text)
                break
            except Exception as e:
                last_err = e
                err_str = str(e)
                is_transient = any(k in err_str for k in ["503", "429", "UNAVAILABLE", "high demand", "temporarily", "RESOURCE_EXHAUSTED", "overloaded"])
                if is_transient and attempt < 3:
                    wait_s = attempt * 2.0
                    if progress_cb:
                        progress_cb(f"Máy chủ AI ({m}) tải cao (503). Đang tự động thử lại sau {wait_s:.0f}s...", 35)
                    time.sleep(wait_s)
                else:
                    break

    if not data:
        raise RuntimeError(f"Lỗi khi gọi Gemini API: {last_err}")

    if progress_cb:
        progress_cb("Phân bổ vai diễn & nhịp điệu cảm xúc...", 70)

    # Phân bổ vai diễn giọng đọc từ cấu hình (Dynamic Multi-Voice Casting)
    roles = voice_cfg.get("roles", {})
    author_role = roles.get("author", {})
    female_presets = voice_cfg.get("female_voice_presets", [
        {"voice": "vi-VN-HoaiMyNeural", "rate": "+20%", "pitch": "+6Hz", "style": "cheerful"},
        {"voice": "ava", "rate": "+16%", "pitch": "+4Hz",  "style": "chat"},
        {"voice": "vi-VN-HoaiMyNeural", "rate": "+24%", "pitch": "+10Hz", "style": "excited"},
        {"voice": "emma", "rate": "+14%", "pitch": "+2Hz",  "style": "sad"}
    ])
    male_presets = voice_cfg.get("male_voice_presets", [
        {"voice": "adam", "rate": "+14%", "pitch": "-4Hz", "style": "dramatic"},
        {"voice": "vi-VN-NamMinhNeural", "rate": "+20%", "pitch": "+4Hz",  "style": "cheerful"},
        {"voice": "brian", "rate": "+16%", "pitch": "-2Hz",  "style": "chat"},
        {"voice": "vi-VN-NamMinhNeural", "rate": "+22%", "pitch": "+2Hz",  "style": "angry"}
    ])

    female_keywords = {"linh", "trang", "mai", "be", "hoa", "ngoc", "lan", "anh", "girl", "cute", "thao", "vy", "my", "phuong", "giang", "nhung", "quynh", "huyen", "nga"}
    male_keywords = {"nam", "tuan", "duc", "boy", "dev", "minh", "hoang", "khoa", "huy", "kien", "dat", "quan", "hung", "long", "thanh", "hai", "son", "dung"}

    comments = []
    raw_comments = data.get("comments", [])
    for idx, c in enumerate(raw_comments, 1):
        u_name = c.get("username", f"user_{idx}").lower()
        role_key = f"commenter_{idx}"
        role_info = roles.get(role_key, {})

        # Tự động nhận diện giới tính theo username hoặc xen kẽ tự nhiên
        is_female = any(k in u_name for k in female_keywords)
        if not is_female and not any(k in u_name for k in male_keywords):
            is_female = (idx % 2 == 1)

        preset_list = female_presets if is_female else male_presets
        preset = preset_list[((idx - 1) // 2) % len(preset_list)]

        sfx_val = c.get("sfx", "laugh")
        camera_mot = c.get("camera_motion", "none")
        if sfx_val in ["boom", "shock"] and camera_mot == "none":
            camera_mot = "shake"
        elif sfx_val in ["laugh", "bruh", "applause"] and camera_mot == "none":
            camera_mot = "punch_in"

        emotion_val = c.get("emotion", "funny")
        emotion_map = voice_cfg.get("emotion_style_map", {})
        emo_style_cfg = emotion_map.get(emotion_val, {})

        # Tự động tính khoảng lặng kịch tính (Breathing Room)
        default_pause_before = c.get("pause_before_ms", 250)
        default_pause_after = c.get("pause_after_ms", 350)
        if sfx_val in ["boom", "shock"]:
            # Cú twist bẻ lái cần suspense rõ rệt
            default_pause_before = max(default_pause_before, 650)
            default_pause_after = max(default_pause_after, 600)
        elif idx == len(raw_comments):
            # Câu chốt hạ cuối cùng có khoảng lặng kết
            default_pause_after = max(default_pause_after, 700)

        assigned_voice = c.get("voice_id") or role_info.get("voice") or preset.get("voice", "vi-VN-HoaiMyNeural" if is_female else "vi-VN-NamMinhNeural")
        assigned_rate = role_info.get("rate") or emo_style_cfg.get("rate_boost") or preset.get("rate", "+20%")
        assigned_pitch = role_info.get("pitch") or emo_style_cfg.get("pitch_boost") or preset.get("pitch", "+2Hz")

        comments.append(Comment(
            username=c.get("username", f"user_{idx}"),
            content=c.get("content", ""),
            reply_to=c.get("reply_to", "") or data.get("username", "author"),
            avatar_color=clean_hex_color(c.get("avatar_color", "")),
            likes=c.get("likes", random.randint(2000, 25000)),
            sfx=sfx_val,
            emotion=emotion_val,
            voice_id=assigned_voice,
            rate_offset=assigned_rate,
            pitch_offset=assigned_pitch,
            pause_before_ms=default_pause_before,
            pause_after_ms=default_pause_after,
            camera_motion=camera_mot
        ))

    # Tự động tìm kiếm & tải ảnh THẬT từ web (Bing Images / Web photography) - KHÔNG dùng AI tự sinh méo mó & watermark
    illus_prompt = data.get("illustration_prompt", "")
    illus_path = ""
    if illus_prompt:
        try:
            import importlib.util
            real_img_spec = importlib.util.spec_from_file_location("real_image_search", os.path.join(SRC_DIR, "real_image_search.py"))
            real_img_mod = importlib.util.module_from_spec(real_img_spec)
            real_img_spec.loader.exec_module(real_img_mod)

            illus_dir = os.path.join(OUT_DIR, "illustrations")
            os.makedirs(illus_dir, exist_ok=True)
            illus_filename = f"real_web_{int(time.time())}_{random.randint(100, 999)}.jpg"
            target_file = os.path.join(illus_dir, illus_filename)

            found_img = real_img_mod.search_and_download_real_image(
                query=illus_prompt,
                output_path=target_file,
                target_width=940,
                target_height=620
            )
            if found_img and os.path.exists(found_img):
                illus_path = found_img
                print(f"  [✓] Đã tải thành công ảnh thật từ mạng (940x620, không watermark): {illus_path}")
        except Exception as e:
            print(f"  ⚠️ Không tải được ảnh thật trực tuyến ({e}), tự động chọn ảnh thật từ kho dự phòng.")

    # Fallback tự động chọn ảnh thật từ kho dự phòng (Zero AI watermark & sắc nét)
    if not illus_path or not os.path.exists(illus_path):
        fallback_pool = [
            os.path.join(OUT_DIR, "illustrations", "sunglasses_bed.jpg"),
            os.path.join(OUT_DIR, "illustrations", "my_eyes_angle.jpg"),
            os.path.join(OUT_DIR, "illustrations", "cat_lightmode.jpg")
        ]
        valid_fallbacks = [p for p in fallback_pool if os.path.exists(p)]
        if valid_fallbacks:
            illus_path = random.choice(valid_fallbacks)
            print(f"  [✓] Đã chọn ảnh chụp thật từ kho dự phòng: {os.path.basename(illus_path)}")

    post = Post(
        username=data.get("username", "threads.user"),
        content=data.get("content", ""),
        avatar_color=clean_hex_color(data.get("avatar_color", "")),
        is_verified=data.get("is_verified", True),
        likes=data.get("likes", random.randint(25000, 75000)),
        video_title=data.get("video_title", ""),
        video_caption=data.get("video_caption", ""),
        hashtags=data.get("hashtags", ""),
        illustration_prompt=illus_prompt,
        illustration_path=illus_path,
        illustration_caption=data.get("illustration_caption", ""),
        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
        hook_energy=data.get("hook_energy", "high"),
        voice_id=author_role.get("voice", voice_cfg.get("default_author_voice", "adam")),
        rate_offset=author_role.get("rate", "+18%"),
        pitch_offset=author_role.get("pitch", "+4Hz"),
        pause_before_ms=150,
        pause_after_ms=300,
        camera_motion="none",
        comments=comments
    )

    # Lưu kết quả ra file JSON mẫu
    out_json = os.path.join(OUT_DIR, "sample_post.json")
    content_base.save_to_json(post, out_json)

    if progress_cb:
        progress_cb("Hoàn thành kịch bản cảm xúc!", 100)

    print("\n" + "=" * 60)
    print("✨ ĐẠO DIỄN GEMINI ĐÃ SÁNG TẠO XONG KỊCH BẢN CẢM XÚC!")
    print("=" * 60)
    print(f"📊 Đồ thị cảm xúc: {post.emotional_arc}")
    print(f"📌 @{post.username} [{post.voice_id}]:")
    print(f"   {post.content}")
    print("\n💬 Các bình luận phân vai:")
    for i, c in enumerate(post.comments, 1):
        print(f"   [{i}] @{c.username} ({c.emotion.upper()}) [{c.voice_id} | SFX: {c.sfx.upper()} | Pause: {c.pause_after_ms}ms]:")
        print(f"       {c.content}")

    return post


if __name__ == "__main__":
    ai_post = generate_story_with_gemini()
