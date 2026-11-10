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
radar_mod = load_mod("threads_radar", "threads_radar.py")
ai_mod = load_mod("ai_content", "01_ai_content.py")

Post = content_mod.Post
Comment = content_mod.Comment


def choose_or_create_post():
    print("\n" + "=" * 55)
    print("🔥 CHỌN CHỦ ĐỀ TRENDING THREADS HÔM NAY ĐỂ LÀM VIDEO")
    print("=" * 55)
    
    daily_res = radar_mod.get_daily_trending_topics()
    topics = daily_res.get("topics", [])
    
    for i, t in enumerate(topics, 1):
        print(f"  [{i}] {t}")
    print(f"  [{len(topics) + 1}] ✍️ Tự nhập chủ đề / nội dung của riêng bạn")
    print("=" * 55)

    choice = input(f"Chọn số [1-{len(topics) + 1}] (Mặc định: 1): ").strip()
    if not choice:
        choice = "1"

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(topics):
            selected_topic = topics[idx]
            print(f"\n[✓] Đang quét tin và tạo kịch bản cho chủ đề: '{selected_topic}'...")
            return radar_mod.crawl_trending_threads_story(selected_topic)
    except ValueError:
        pass

    # Tự nhập tay
    print("\n--- TỰ NHẬP NỘI DUNG HOẶC CHỦ ĐỀ ---")
    custom_topic = input("Nhập chủ đề bạn muốn (hoặc bỏ trống để AI tự tạo): ").strip()
    if custom_topic:
        return radar_mod.crawl_trending_threads_story(custom_topic)
    else:
        return radar_mod.crawl_trending_threads_story()


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

