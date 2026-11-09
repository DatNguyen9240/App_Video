"""
MODULE 6: TẠO KỊCH BẢN TỰ ĐỘNG THEO CHỦ ĐỀ (Story & Topic Generator)
=====================================================================
Chức năng:
  - Cung cấp sẵn kho chủ đề hài hước kiểu Threads/TikTok (Công sở, IT, Người yêu cũ, Sinh viên, v.v.)
  - Cho phép người dùng nhập chủ đề bất kỳ hoặc chọn mẫu có sẵn
  - Tự động gắn tag hiệu ứng âm thanh (SFX laugh, boom, bruh) cho từng câu punchline
  - Chuyển thẳng sang Pipeline để xuất video 1 nút bấm!

CÁCH DÙNG: python src/06_generator.py
"""

import os
import sys
import json
import random

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
sys.path.insert(0, SRC_DIR)

import importlib.util

def load_mod(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

content_mod = load_mod("content", "01_content.py")
pipeline_mod = load_mod("pipeline", "05_pipeline.py")

Post = content_mod.Post
Comment = content_mod.Comment

# KHO KỊCH BẢN MẪU VIRAL THREADS
VIRAL_PRESETS = [
    {
        "title": "Chuyện đi phỏng vấn xin việc",
        "post": Post(
            username="tuyendung.genz",
            content="Nhà tuyển dụng hỏi: 'Điểm yếu lớn nhất của em là gì?'. Mình lỡ miệng: 'Em hay nói thật ạ'. HR bảo: 'Tôi nghĩ đó không phải điểm yếu'. Mình đáp: 'Em kệ mẹ anh nghĩ gì' 💀",
            avatar_color="#8b5cf6",
            is_verified=True,
            likes=48200,
            comments=[
                Comment(username="hr_ngapnguoi", content="Sau đó HR mời em lên thẳng ghế Giám đốc ngồi đúng không? 😂", avatar_color="#ec4899", likes=12300, sfx="laugh"),
                Comment(username="nam_it98", content="Nói thật thì bị loại, nói dối thì trúng tuyển nhưng vào làm không nổi 😭", avatar_color="#3b82f6", likes=7800, sfx="boom"),
                Comment(username="diep.leee", content="HR sốc 3 ngày 3 đêm chưa hoàn hồn 🗿", avatar_color="#10b981", likes=4500, sfx="bruh")
            ]
        )
    },
    {
        "title": "Chuyện làm Lập trình viên (IT)",
        "post": Post(
            username="dev.cuocsong",
            content="Khách hàng bảo: 'Nút này bấm vào cho nó đổi 7 màu cầu vồng, nhưng màu đen trắng thôi nha em'. Mình tắt máy đi về luôn 🥲",
            avatar_color="#06b6d4",
            is_verified=False,
            likes=31500,
            comments=[
                Comment(username="frontend_khoc", content="Khách hàng luôn luôn đúng, trừ khi khách hàng bắt đầu sáng tạo 😂", avatar_color="#f59e0b", likes=9800, sfx="laugh"),
                Comment(username="senior_buonngu", content="Code 5 phút, giải thích cho khách 5 tiếng, cuối cùng khách bảo về giao diện cũ 🫡", avatar_color="#ef4444", likes=6200, sfx="boom"),
                Comment(username="tester_hienlanh", content="Và thế là bug thành feature mới luôn 💀", avatar_color="#84cc16", likes=3100, sfx="bruh")
            ]
        )
    },
    {
        "title": "Chuyện người yêu cũ",
        "post": Post(
            username="tamsu.demmuon",
            content="Người yêu cũ nhắn tin lúc 2 giờ sáng: 'Anh còn nhớ em không?'. Mình rep: 'Nhớ chứ, 500k tiền lẩu năm ngoái chuyển khoản lại em ơi' 💸",
            avatar_color="#f43f5e",
            is_verified=True,
            likes=59000,
            comments=[
                Comment(username="bich_ngoc", content="Người ta tìm về kỷ niệm, mình tìm về tiền gốc và lãi 😂", avatar_color="#a855f7", likes=15400, sfx="laugh"),
                Comment(username="hoang.tran", content="Tin nhắn đã xem và chuyển khoản trong 30 giây 🗿", avatar_color="#0ea5e9", likes=9200, sfx="boom"),
                Comment(username="linh_dan", content="Thế là dứt tình dứt nợ luôn, quá tỉnh táo! 👏", avatar_color="#14b8a6", likes=5600, sfx="ting")
            ]
        )
    },
    {
        "title": "Chuyện ở trọ sinh viên",
        "post": Post(
            username="sinhvien.thantho",
            content="Chủ trọ bảo: 'Phòng này thoáng mát lắm con'. Vào ở mới biết thoáng là vì trần nhà lủng 3 lỗ, trời mưa khỏi cần tắm vòi sen 🌧️",
            avatar_color="#eab308",
            is_verified=False,
            likes=22700,
            comments=[
                Comment(username="truc_k58", content="Phòng view triệu đô ngắm trăng sao trực tiếp luôn bạn 😂", avatar_color="#d946ef", likes=6700, sfx="laugh"),
                Comment(username="huy_bkit", content="Tháng sau chủ nhà tăng giá vì có thêm tiện ích 'bể bơi trong nhà' 💀", avatar_color="#f97316", likes=4900, sfx="boom"),
                Comment(username="minh_anh", content="Đúng là công nghệ thông gió tự nhiên đỉnh cao 😭", avatar_color="#64748b", likes=2800, sfx="bruh")
            ]
        )
    }
]


def choose_or_create_post():
    print("\n" + "=" * 55)
    print("🔥 CHỌN KỊCH BẢN HÀI HƯỚC ĐỂ LÀM VIDEO")
    print("=" * 55)
    for i, p in enumerate(VIRAL_PRESETS, 1):
        print(f"  [{i}] {p['title']}")
    print(f"  [{len(VIRAL_PRESETS) + 1}] ✍️ Tự nhập bài đăng & bình luận của riêng bạn")
    print("=" * 55)

    choice = input(f"Chọn số [1-{len(VIRAL_PRESETS) + 1}] (Mặc định: 1): ").strip()
    if not choice:
        choice = "1"

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(VIRAL_PRESETS):
            selected = VIRAL_PRESETS[idx]["post"]
            print(f"\n[✓] Đã chọn: {VIRAL_PRESETS[idx]['title']}")
            return selected
    except ValueError:
        pass

    # Tự nhập tay
    print("\n--- TỰ NHẬP NỘI DUNG ---")
    uname = input("Username tác giả (vd: nam.nguyen): ").strip() or "user_an_danh"
    content = input("Nội dung bài đăng: ").strip()
    if not content:
        content = "Hôm nay đi làm sếp bảo em hãy xem công ty như nhà của mình. Thế là mình lên giường ngủ một giấc tới chiều."

    comments = []
    print("\nNhập 2-3 bình luận (Để trống để dừng):")
    for i in range(1, 4):
        c_text = input(f"Bình luận {i}: ").strip()
        if not c_text:
            break
        sfx_pick = input("Hiệu ứng [1: Cười (laugh), 2: Boom (boom), 3: Bruh (bruh)] (Mặc định: 1): ").strip()
        sfx_map = {"1": "laugh", "2": "boom", "3": "bruh"}
        sfx = sfx_map.get(sfx_pick, "laugh")
        comments.append(Comment(
            username=f"user_{i}",
            content=c_text,
            avatar_color=random.choice(["#3b82f6", "#ef4444", "#10b981", "#f59e0b"]),
            likes=random.randint(1500, 18000),
            sfx=sfx
        ))

    return Post(
        username=uname,
        content=content,
        avatar_color="#6366f1",
        is_verified=True,
        likes=random.randint(20000, 80000),
        comments=comments
    )


if __name__ == "__main__":
    post = choose_or_create_post()
    # Lưu ra sample_post.json
    out_json = os.path.join(OUT_DIR, "sample_post.json")
    content_mod.save_to_json(post, out_json)

    run_now = input("\n👉 Bạn có muốn RENDER VIDEO NGAY BÂY GIỜ không? [y/N]: ").strip().lower()
    if run_now in ["y", "yes"]:
        pipeline_mod.run_pipeline(post)
    else:
        print(f"\n[✓] Kịch bản đã được lưu tại: {out_json}")
        print("    Bạn có thể chạy `python main.py` bất cứ lúc nào để render!")

