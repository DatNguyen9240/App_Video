"""
MODULE 7: NHÀ MÁY SẢN XUẤT VIDEO HÀNG LOẠT (Batch Video Factory)
================================================================
Chức năng:
  - Tự động tạo 3, 5 hoặc 10 video cùng lúc với các chủ đề khác nhau
  - Gemini AI tự động sáng tạo kịch bản không trùng lặp
  - Mỗi video được cắt từ một đoạn ngẫu nhiên khác nhau của video Minecraft
  - Xuất sẵn file video (.mp4) và caption kèm hashtag (.txt) sẵn sàng lên lịch đăng cả tuần!

CÁCH DÙNG: python src/07_batch_factory.py
"""

import os
import sys
import time

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
sys.path.insert(0, SRC_DIR)

import importlib.util

def load_mod(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

import config as config_mod

ai_mod = load_mod("ai_content", "01_ai_content.py")
pipeline_mod = load_mod("pipeline", "05_pipeline.py")
radar_mod = load_mod("threads_radar", "threads_radar.py")


def run_batch_factory(num_videos: int = 3, custom_topics: list[str] = None):
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║     🏭 NHÀ MÁY SẢN XUẤT VIDEO TỰ ĐỘNG HÀNG LOẠT        ║")
    print("╚" + "═" * 58 + "╝")
    print(f"\nSố lượng video cần sản xuất: {num_videos}")

    api_key = ai_mod.get_or_prompt_api_key()
    if not custom_topics:
        daily_res = radar_mod.get_daily_trending_topics()
        topics = daily_res.get("topics", [])
    else:
        topics = custom_topics

    if not topics:
        topics = [radar_mod.crawl_trending_threads_story().content[:40]]

    completed_videos = []

    for i in range(1, num_videos + 1):
        topic = topics[(i - 1) % len(topics)]
        print("\n" + "─" * 60)
        print(f"🚀 ĐANG SẢN XUẤT VIDEO [{i}/{num_videos}] — Chủ đề: '{topic}'")
        print("─" * 60)

        try:
            # 1. Gọi Gemini sinh kịch bản
            post = ai_mod.generate_story_with_gemini(topic, api_key=api_key)

            # 2. Render video hoàn chỉnh
            video_path = pipeline_mod.run_pipeline(post)
            completed_videos.append(video_path)

            print(f"\n[✓] Hoàn thành video {i}/{num_videos}: {os.path.basename(video_path)}")

            # Nghỉ ngắn giữa các video
            if i < num_videos:
                print("⏳ Nghỉ 2 giây trước khi sang video tiếp theo...")
                time.sleep(2.0)

        except Exception as e:
            print(f"\n❌ Lỗi khi xử lý video {i}: {e}")
            continue

    print("\n" + "═" * 60)
    print("🎉 TỔNG KẾT: ĐÃ HOÀN THÀNH TẤT CẢ VIDEO TRONG LÔ SẢN XUẤT!")
    print("═" * 60)
    for idx, v in enumerate(completed_videos, 1):
        print(f"  [{idx}] {os.path.basename(v)}")
        cap_file = v.replace(".mp4", "_caption.txt")
        if os.path.exists(cap_file):
            print(f"      📝 Đã có caption & hashtag: {os.path.basename(cap_file)}")

    print(f"\n📂 Thư mục chứa video: {os.path.join(BASE_DIR, 'output', 'final')}")
    if os.name == "nt":
        os.startfile(os.path.join(BASE_DIR, "output", "final"))


if __name__ == "__main__":
    count_input = input("Nhập số lượng video bạn muốn tạo tự động [Mặc định: 3]: ").strip()
    try:
        count = int(count_input) if count_input else 3
    except ValueError:
        count = 3

    run_batch_factory(count)

