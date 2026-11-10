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
    username: str = Field(description="Tên người dùng hài hước tiếng Việt hoặc Gen Z, ví dụ: hr_ngapnguoi, dev_khocnhe, an.tran99")
    reply_to: str = Field(default="", description="Tên username mà bình luận này đang trả lời (có thể là tên tác giả bài đăng HOẶC tên người comment trước đó)")
    content: str = Field(description="Bình luận tiếng Việt CỰC KỲ NGẮN GỌN (chỉ 1 câu đắt giá, tối đa 20 từ), sắc sảo, dí dỏm, bẻ lái hoặc châm chọc hài hước")
    avatar_color: str = Field(default="#3b82f6", description="Mã màu hex (vd: #3b82f6, #ef4444, #10b981, #f59e0b)")
    likes: int = Field(default=4500, description="Lượt thích ước tính từ 1500 đến 25000")
    sfx: Literal["laugh", "boom", "bruh", "ting", "none"] = Field(
        description="SFX phù hợp: 'laugh' cho câu buồn cười, 'boom' cho cú bẻ lái sốc, 'bruh' cho cạn lời, 'ting' cho thông minh"
    )
    emotion: Literal["funny", "shocked", "awkward", "sarcastic", "insightful", "neutral"] = Field(
        default="funny", description="Sắc thái cảm xúc của câu nói"
    )
    pause_before_ms: int = Field(default=250, description="Khoảng lặng trước khi nói tính bằng mili-giây (150-400ms)")
    pause_after_ms: int = Field(default=350, description="Khoảng lặng sau khi dứt câu (300-600ms)")
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
    emotional_arc: str = Field(default="Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời", description="Tóm tắt đường cong cảm xúc của video")
    hook_energy: Literal["high", "intense", "curious"] = Field(default="high", description="Mức năng lượng mở màn")
    comments: list[CommentSchema] = Field(description="Danh sách chính xác 3 bình luận tiếng Việt tạo thành nhịp điệu cảm xúc hoàn chỉnh")


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
    api_key: str = None,
    progress_cb: Optional[Callable[[str, int], None]] = None
) -> Post:
    """
    Sử dụng Gemini API với cấu hình đạo diễn cảm xúc để tạo kịch bản Threads hoàn chỉnh.
    Tự động phân vai giọng đọc (Multi-Voice Acting) và thiết lập khoảng lặng (Breathing Room).
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
    temperature = float(ai_cfg.get("temperature", 0.88))

    prompt = (
        f"Hãy đóng vai Đạo diễn và sáng tạo 1 bài Threads bằng tiếng Việt theo Đồ thị Cảm xúc hoàn chỉnh.\n"
        f"Chủ đề kịch bản: '{topic}'\n\n"
        f"QUY TẮC ĐỘ DÀI & ĐỐI THOẠI (BẮT BUỘC NGẮN GỌN CHO VIDEO TIKTOK DƯỚI 30 GIÂY):\n"
        f"- Bài viết chính: CỰC KỲ NGẮN GỌN (chỉ 1 - 2 câu ngắn, tối đa 30-35 từ). Bắt đầu bằng Hook giật mình tò mò, nêu ngay mâu thuẫn chính, không mở bài dài dòng.\n"
        f"- Bình luận 1: Rất ngắn (1 câu, dưới 20 từ), khơi mào tranh luận, thường trả lời tác giả (reply_to = username tác giả).\n"
        f"- Bình luận 2: Cú bẻ lái sốc (twist punchline) cực ngắn và đanh thép (1 câu, dưới 20 từ), gán sfx 'boom'. ĐẶC BIỆT: Có thể trả lời tác giả HOẶC trả lời/bóc mẽ Bình luận 1 (reply_to = username của Bình luận 1)!\n"
        f"- Bình luận 3: Chốt hạ cạn lời hoặc dở khóc dở cười (1 câu đắt giá, dưới 18 từ), gán sfx 'bruh' hoặc 'laugh'. Có thể trả lời tác giả hoặc người bình luận trước.\n"
        f"- Trường 'reply_to': Bắt buộc ghi rõ username mà bình luận đó đang trực tiếp tương tác (ví dụ trả lời tác giả hoặc người bình luận trước)."
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

    # Phân bổ vai diễn giọng đọc từ cấu hình (không hardcode)
    roles = voice_cfg.get("roles", {})
    author_role = roles.get("author", {})

    comments = []
    raw_comments = data.get("comments", [])
    for idx, c in enumerate(raw_comments, 1):
        role_key = f"commenter_{idx}"
        role_info = roles.get(role_key, {})

        # Tự động điều chỉnh nhịp nếu cú sốc (boom)
        sfx_val = c.get("sfx", "laugh")
        camera_mot = c.get("camera_motion", "none")
        if sfx_val == "boom" and camera_mot == "none":
            camera_mot = "shake"
        elif sfx_val in ["laugh", "bruh"] and camera_mot == "none":
            camera_mot = "punch_in"

        comments.append(Comment(
            username=c.get("username", f"user_{idx}"),
            content=c.get("content", ""),
            reply_to=c.get("reply_to", "") or data.get("username", "author"),
            avatar_color=clean_hex_color(c.get("avatar_color", "")),
            likes=c.get("likes", random.randint(2000, 15000)),
            sfx=sfx_val,
            emotion=c.get("emotion", "funny"),
            voice_id=role_info.get("voice", voice_cfg.get("default_commenter_female_voice", "vi-VN-HoaiMyNeural")),
            rate_offset=role_info.get("rate", "+10%"),
            pitch_offset=role_info.get("pitch", "+0Hz"),
            pause_before_ms=c.get("pause_before_ms", 250),
            pause_after_ms=c.get("pause_after_ms", 350),
            camera_motion=camera_mot
        ))

    post = Post(
        username=data.get("username", "threads.user"),
        content=data.get("content", ""),
        avatar_color=clean_hex_color(data.get("avatar_color", "")),
        is_verified=data.get("is_verified", True),
        likes=data.get("likes", random.randint(25000, 75000)),
        video_title=data.get("video_title", ""),
        video_caption=data.get("video_caption", ""),
        hashtags=data.get("hashtags", ""),
        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
        hook_energy=data.get("hook_energy", "high"),
        voice_id=author_role.get("voice", voice_cfg.get("default_author_voice", "vi-VN-NamMinhNeural")),
        rate_offset=author_role.get("rate", "+8%"),
        pitch_offset=author_role.get("pitch", "+0Hz"),
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
