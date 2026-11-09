"""
MODULE 1B: SINH BÀI ĐĂNG TỰ ĐỘNG BẰNG GOOGLE GEMINI AI
======================================================
Chức năng:
  - Sử dụng Gemini API (gemini-2.5-flash trên v1beta) để sáng tạo câu chuyện hài Threads
  - Tự động gán SFX (tiếng cười, vine boom, bruh) cho từng comment
  - Tự động đọc và lưu API key vào file .env
  - Trả về cấu trúc dữ liệu chuẩn để chuyển thẳng sang Pipeline làm video

CÁCH DÙNG: python src/01_ai_content.py
"""

import os
import sys
import json
import random
import re
from typing import Literal
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
OUT_DIR  = os.path.join(BASE_DIR, "output")

sys.path.insert(0, os.path.join(BASE_DIR, "src"))
import importlib.util
spec = importlib.util.spec_from_file_location("content_base", os.path.join(BASE_DIR, "src", "01_content.py"))
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


# ─── ĐỊNH NGHĨA SCHEMA JSON CHO GEMINI ────────────────────────────────────────

class CommentSchema(BaseModel):
    username: str = Field(description="Tên người dùng hài hước tiếng Việt hoặc Gen Z, ví dụ: hr_ngapnguoi, dev_khocnhe, an.tran99")
    content: str = Field(description="Bình luận tiếng Việt ngắn gọn, dí dỏm, bẻ lái hoặc châm chọc hài hước")
    avatar_color: str = Field(default="#3b82f6", description="Mã màu hex (vd: #3b82f6, #ef4444, #10b981, #f59e0b, #ec4899)")
    likes: int = Field(default=4500, description="Lượt thích ước tính từ 1500 đến 25000")
    sfx: Literal["laugh", "boom", "bruh", "ting"] = Field(
        description="Hiệu ứng âm thanh phù hợp: 'laugh' cho câu buồn cười, 'boom' cho câu bẻ lái sốc, 'bruh' cho câu cạn lời bất lực, 'ting' cho câu thông minh"
    )

class PostSchema(BaseModel):
    username: str = Field(description="Tên tác giả bài viết, ví dụ: genz.tamsu, chuyen.congso, thanhmanh.dev")
    content: str = Field(description="Câu chuyện Threads ngắn gọn 2-3 câu bằng tiếng Việt, hài hước đời thường, kết thúc có cú twist bất ngờ")
    avatar_color: str = Field(default="#8b5cf6", description="Mã màu hex cho avatar tác giả (vd #8b5cf6)")
    is_verified: bool = Field(default=True, description="True nếu có tích xanh")
    likes: int = Field(default=35000, description="Lượt thích ước tính từ 15000 đến 85000")
    video_title: str = Field(default="", description="Tiêu đề video ngắn gọn, giật gân hài hước, ví dụ: 'Bá khí dữ chưa bà con 😂'")
    video_caption: str = Field(default="", description="Đoạn caption đăng TikTok/Reels cuốn hút kèm câu hỏi kêu gọi bình luận")
    hashtags: str = Field(default="", description="Chuỗi 5-7 hashtag viral, ví dụ: '#fyp #xuhuong #haihuoc #chuyencongso #threads #minecraftparkour'")
    comments: list[CommentSchema] = Field(description="Danh sách chính xác 3 bình luận tiếng Việt hài hước nối tiếp câu chuyện")


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

def generate_story_with_gemini(topic: str = "", api_key: str = None) -> Post:
    """
    Sử dụng Gemini API (gemini-2.5-flash trên v1beta) để tạo 1 bài viết Threads + 3 comments hài hước theo chủ đề.
    """
    if not api_key:
        api_key = get_or_prompt_api_key()

    from google import genai
    from google.genai import types

    # Sử dụng api_version='v1beta' để hỗ trợ JSON Schema Mode đầy đủ
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})

    if not topic:
        topic = "tình huống hài hước đời sống ngẫu nhiên (công sở, người yêu cũ, trường học, gia đình, IT, giao thông)"

    system_instruction = (
        "Bạn là một nhà sáng tạo nội dung hàng đầu trên Threads và TikTok tại Việt Nam. "
        "Phong cách của bạn dí dỏm, châm biếm sâu cay, dùng từ ngữ tự nhiên đời thường của Gen Z và dân văn phòng Việt Nam. "
        "Quy tắc quan trọng:\n"
        "1. TOÀN BỘ NỘI DUNG BÀI ĐĂNG VÀ BÌNH LUẬN BẮT BUỘC BẰNG TIẾNG VIỆT.\n"
        "2. Bài đăng: Ngắn gọn (tầm 2-3 câu), kể 1 tình huống thực tế có cú lừa hoặc kết thúc cay đắng hài hước.\n"
        "3. Comment 1: Đùa cợt đồng cảm (sfx: 'laugh' hoặc 'ting').\n"
        "4. Comment 2: Bẻ lái bất ngờ, châm chọc đỉnh cao (sfx: 'boom').\n"
        "5. Comment 3: Câu chốt hạ cạn lời hoặc cười bất lực (sfx: 'bruh' hoặc 'laugh').\n"
        "6. avatar_color phải là mã màu hex như #3b82f6, #ef4444, #10b981, #f59e0b, #8b5cf6."
    )

    prompt = f"Hãy sáng tạo 1 bài Threads bằng tiếng Việt thật hài hước, dí dỏm về chủ đề: '{topic}'"

    print(f"\n🤖 Đang gửi yêu cầu tới Gemini AI (chủ đề: {topic})...")

    # Ưu tiên gemini-2.5-flash, fallback sang gemini-2.5-flash-lite
    models_to_try = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    data = None
    last_err = None

    for m in models_to_try:
        try:
            response = client.models.generate_content(
                model=m,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=PostSchema,
                    temperature=0.9,
                )
            )
            data = json.loads(response.text)
            break
        except Exception as e:
            last_err = e
            continue

    if not data:
        raise RuntimeError(f"Lỗi khi gọi Gemini API: {last_err}")

    # Chuyển đổi sang dataclass Post & Comment của hệ thống
    comments = []
    for c in data.get("comments", []):
        comments.append(Comment(
            username=c.get("username", "user"),
            content=c.get("content", ""),
            avatar_color=clean_hex_color(c.get("avatar_color", "")),
            likes=c.get("likes", random.randint(2000, 15000)),
            sfx=c.get("sfx", "laugh")
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
        comments=comments
    )

    # Lưu kết quả ra file JSON
    out_json = os.path.join(OUT_DIR, "sample_post.json")
    content_base.save_to_json(post, out_json)

    print("\n" + "=" * 55)
    print("✨ GEMINI ĐÃ SÁNG TẠO XONG KỊCH BẢN!")
    print("=" * 55)
    print(f"📌 @{post.username}:")
    print(f"   {post.content}")
    print(f"   ❤️ {post.likes:,} likes")
    print("\n💬 Các bình luận:")
    for i, c in enumerate(post.comments, 1):
        print(f"   [{i}] @{c.username} [{c.sfx.upper()}]:")
        print(f"       {c.content}")

    return post


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════╗")
    print("║      🤖 TẠO BÀI ĐĂNG HÀI HƯỚC BẰNG GEMINI AI        ║")
    print("╚══════════════════════════════════════════════════════╝")
    user_topic = input("\nNhập chủ đề bạn muốn (hoặc ấn Enter để AI tự nghĩ ngẫu nhiên): ").strip()
    ai_post = generate_story_with_gemini(user_topic)

    render_confirm = input("\n👉 Bạn có muốn RENDER VIDEO NGAY BÂY GIỜ không? [y/N]: ").strip().lower()
    if render_confirm in ["y", "yes"]:
        pipeline_spec = importlib.util.spec_from_file_location("p", os.path.join(BASE_DIR, "src", "05_pipeline.py"))
        p_mod = importlib.util.module_from_spec(pipeline_spec)
        pipeline_spec.loader.exec_module(p_mod)
        p_mod.run_pipeline(ai_post)
