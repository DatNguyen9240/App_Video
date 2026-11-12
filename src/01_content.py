"""
MODULE 1: QUẢN LÝ NỘI DUNG & ĐẠO DIỄN CẢM XÚC (Content & Emotion Model)
=====================================================================
Chức năng:
  - Định nghĩa cấu trúc dữ liệu bài đăng Threads và Đồ thị Cảm xúc (Emotional Arc)
  - Hỗ trợ metadata đạo diễn: vai diễn, cảm xúc, tốc độ/cao độ, khoảng lặng (pauses), hiệu ứng camera
  - Load/Save an toàn 100% (không lỗi khi thiếu trường mới)
"""

import json
import os
from dataclasses import dataclass, field, asdict, fields
from typing import Literal

# ─── Kiểu dữ liệu ─────────────────────────────────────────────────────────────

@dataclass
class Comment:
    username: str
    content: str
    reply_to: str = ""                     # Username mà comment này trả lời (tác giả hoặc commenter khác)
    avatar_color: str = "#8b5cf6"
    avatar_path: str = ""                  # Đường dẫn ảnh thật hoặc URL avatar
    avatar_url: str = ""                   # URL ảnh đại diện trên mạng
    is_verified: bool = False
    likes: int = 0
    # Âm thanh hiệu ứng
    sfx: str = "none"
    # Metadata đạo diễn cảm xúc (Human-like pacing)
    emotion: str = "neutral"               # funny, shocked, awkward, sarcastic, insightful, neutral
    voice_id: str = ""                     # Nếu để trống sẽ tự lấy theo vai diễn trong config
    rate_offset: str = ""                  # Điều chỉnh tốc độ riêng (ví dụ: "+10%", "-5%")
    pitch_offset: str = ""                 # Điều chỉnh cao độ riêng (ví dụ: "+4Hz", "-4Hz")
    pause_before_ms: int = 180             # Khoảng lặng trước khi đọc câu (nhịp thở)
    pause_after_ms: int = 220              # Khoảng lặng sau khi đọc xong câu
    camera_motion: str = "none"            # none, punch_in, shake


@dataclass
class Post:
    username: str
    content: str
    avatar_color: str = "#6366f1"
    avatar_path: str = ""                  # Đường dẫn ảnh thật hoặc URL avatar tác giả
    avatar_url: str = ""                   # URL ảnh đại diện trên mạng
    is_verified: bool = False
    likes: int = 0
    video_title: str = ""
    video_caption: str = ""
    hashtags: str = ""
    illustration_prompt: str = ""          # Prompt tiếng Anh ngắn gọn để tạo ảnh minh họa
    illustration_path: str = ""            # Đường dẫn ảnh minh họa đính kèm
    illustration_caption: str = ""         # Câu chú thích ngắn (dưới 10 từ) gắn lên ảnh để hiểu trò đùa
    # Metadata đạo diễn cảm xúc
    emotional_arc: str = "curiosity -> tension -> fake_relief -> punchline"
    hook_energy: str = "high"
    voice_id: str = ""
    rate_offset: str = "+18%"
    pitch_offset: str = "+4Hz"
    pause_before_ms: int = 150
    pause_after_ms: int = 300
    camera_motion: str = "none"
    comments: list[Comment] = field(default_factory=list)


# ─── Load / Save An Toàn ───────────────────────────────────────────────────────

def _filter_dataclass_kwargs(cls, data: dict):
    valid_keys = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in valid_keys}


def load_from_json(filepath: str) -> Post:
    """Load bài đăng từ file JSON với cơ chế lọc thuộc tính an toàn."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_comments = data.get("comments", [])
    comments = []
    for c in raw_comments:
        c_kwargs = _filter_dataclass_kwargs(Comment, c)
        comments.append(Comment(**c_kwargs))

    post_kwargs = _filter_dataclass_kwargs(Post, {k: v for k, v in data.items() if k != "comments"})
    return Post(**post_kwargs, comments=comments)


def save_to_json(post: Post, filepath: str):
    """Lưu bài đăng ra file JSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(asdict(post), f, ensure_ascii=False, indent=2)
    print(f"  [✓] Lưu JSON: {filepath}")


# Hỗ trợ hàm tương thích ngược
save_post_to_json = save_to_json
load_post_from_json = load_from_json


if __name__ == "__main__":
    out_sample = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "sample_post.json")
    if os.path.exists(out_sample):
        loaded = load_from_json(out_sample)
        print(f"✅ Đọc kịch bản hiện tại: @{loaded.username} ({len(loaded.comments)} comments)")
        print(f"   Nội dung: {loaded.content[:100]}...")
    else:
        print("ℹ️ Chưa có file kịch bản. Hãy dùng AI Sáng Tạo hoặc Threads Radar để sinh kịch bản động.")
