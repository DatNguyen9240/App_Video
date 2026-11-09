"""
MODULE 1: QUẢN LÝ NỘI DUNG (Content Parser)
============================================
Chức năng:
  - Định nghĩa cấu trúc dữ liệu bài đăng Threads (Post + Comment)
  - Load/save từ file JSON để chỉnh sửa tay
  - Tạo kịch bản mẫu để test

TEST ĐỘC LẬP: python src/01_content.py
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Literal


# ─── Kiểu dữ liệu ─────────────────────────────────────────────────────────────

@dataclass
class Comment:
    username: str
    content: str
    avatar_color: str           # Màu avatar hex, vd: "#f43f5e"
    is_verified: bool = False
    likes: int = 0
    # Âm thanh hiệu ứng phát sau khi đọc xong comment này
    sfx: Literal["laugh", "boom", "ting", "bruh", "none"] = "none"


@dataclass
class Post:
    username: str
    content: str
    avatar_color: str
    is_verified: bool = False
    likes: int = 0
    video_title: str = ""
    video_caption: str = ""
    hashtags: str = ""
    comments: list[Comment] = field(default_factory=list)


# ─── Load / Save ───────────────────────────────────────────────────────────────

def load_from_json(filepath: str) -> Post:
    """Load bài đăng từ file JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    comments = [Comment(**c) for c in data.get("comments", [])]
    post_data = {k: v for k, v in data.items() if k != "comments"}
    return Post(**post_data, comments=comments)


def save_to_json(post: Post, filepath: str):
    """Lưu bài đăng ra file JSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(asdict(post), f, ensure_ascii=False, indent=2)
    print(f"  [✓] Lưu JSON: {filepath}")


# ─── Dữ liệu mẫu ──────────────────────────────────────────────────────────────

SAMPLE_POST = Post(
    username="nguyenvan.hai",
    content="Hôm nay sếp hỏi mình: 'Em làm gì cả ngày vậy?' rồi mình chiếu màn hình ra. Sếp nhìn 10 giây rồi đi không nói gì 💀",
    avatar_color="#6366f1",
    is_verified=False,
    likes=24700,
    comments=[
        Comment(
            username="user_binhluan1",
            content="Chắc sếp cũng vậy nên không dám nói 😭",
            avatar_color="#f43f5e",
            likes=8420,
            sfx="laugh",
        ),
        Comment(
            username="thanhmanh.dev",
            content="Mình làm IT, sếp hỏi câu đó mình chiếu Stack Overflow ra. Sếp gật đầu rồi đi 🫡",
            avatar_color="#10b981",
            is_verified=True,
            likes=5100,
            sfx="boom",
        ),
        Comment(
            username="an_tran99",
            content="10 giây không phải để hiểu đâu, là để xử lý cú sốc 😂",
            avatar_color="#f59e0b",
            likes=3200,
            sfx="laugh",
        ),
    ],
)


# ─── Main test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  MODULE 1: CONTENT PARSER")
    print("=" * 55)

    print(f"\n📌 BÀI ĐĂNG:")
    print(f"   @{SAMPLE_POST.username}")
    print(f"   {SAMPLE_POST.content}")
    print(f"   ❤️  {SAMPLE_POST.likes:,} likes")

    print(f"\n💬 BÌNH LUẬN ({len(SAMPLE_POST.comments)} cái):")
    for i, c in enumerate(SAMPLE_POST.comments, 1):
        sfx_tag = f"  ← [{c.sfx.upper()}]" if c.sfx != "none" else ""
        print(f"\n   [{i}] @{c.username}")
        print(f"       {c.content}")
        print(f"       ❤️ {c.likes:,}{sfx_tag}")

    out_path = r"d:\Dat\App_Video\output\sample_post.json"
    save_to_json(SAMPLE_POST, out_path)
    print(f"\n✅ Module 1 OK — chỉnh sửa nội dung tại: {out_path}")
