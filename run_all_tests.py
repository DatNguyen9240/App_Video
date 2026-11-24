"""
Master Test Suite Runner - Threads Video Automation & Documentary Studio
========================================================================
Chạy tự động toàn bộ 9 bộ testcase chuyên nghiệp bao phủ toàn diện hệ thống:
  01: Data Models & Serialization (01_content.py)
  02: Platform Card Renderers & Popup Proofs (02_card_gen.py)
  03: Text-to-Speech & Voice Engine (03_tts.py)
  04: Background Video & Audio SFX System (04_bg_audio.py)
  05: Visual Documentary Story Builder (visual_story_builder.py)
  06: Real Image Search & Verification (real_image_search.py)
  07: Threads Radar Context Intelligence (threads_radar.py)
  08: Pipeline State Machine & Tracker (pipeline_tracker.py)
  09: Web Studio Flask API Endpoints (app_ui.py)
"""

import os
import sys
import time
import unittest


def run_master_test_suite():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tests_dir = os.path.join(base_dir, "tests")

    print("\n" + "═" * 70)
    print("🧪 MASTER TEST RUNNER: THREADS VIDEO AUTOMATION STUDIO")
    print(f"📂 Thư mục kiểm thử: {tests_dir}")
    print("═" * 70)

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=tests_dir, pattern="test_*.py")

    start_time = time.time()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed = time.time() - start_time

    print("\n" + "═" * 70)
    print(f"⏱️ Tổng thời gian chạy: {elapsed:.2f}s")
    print(f"📊 Tổng số test cases đã chạy: {result.testsRun}")
    print(f"✅ Thành công: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"❌ Thất bại (Failures): {len(result.failures)}")
    print(f"💥 Lỗi (Errors): {len(result.errors)}")
    print("═" * 70)

    if result.wasSuccessful():
        print("🎉 TẤT CẢ TEST CASES ĐÃ VƯỢT QUA 100%! HỆ THỐNG HOÀN TOÀN KHỎE MẠNH!")
        return 0
    else:
        print("⚠️ CÓ TEST CASE BỊ LỖI, VUI LÒNG KIỂM TRA CHI TIẾT Ở TRÊN.")
        return 1


if __name__ == "__main__":
    sys.exit(run_master_test_suite())
