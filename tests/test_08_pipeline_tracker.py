"""
Test Case Suite 08: Pipeline State Machine & Tracker (src/pipeline_tracker.py)
=============================================================================
Kiểm thử các thành phần:
  - Khởi tạo và reset tracker
  - Chuyển đổi trạng thái các bước (start_step, update_progress, finish_step)
  - Trạng thái tạm dừng chờ duyệt (pause_for_review)
  - Trạng thái hoàn thành (finish_pipeline)
  - Báo lỗi (set_error)
  - Serialization (get_state)
"""

import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

import _loader
tracker_mod = _loader.load_module("pipeline_tracker", os.path.join(SRC_DIR, "pipeline_tracker.py"))


class TestPipelineTracker(unittest.TestCase):

    def setUp(self):
        tracker_mod.reset()

    def test_initial_state(self):
        """Trạng thái ban đầu sau reset phải ở trạng thái khởi động."""
        state = tracker_mod.get_state()
        self.assertTrue(state["is_running"])
        self.assertEqual(state["progress_percent"], 2)
        self.assertEqual(state["current_step_id"], "init")
        self.assertTrue(isinstance(state["steps"], list))
        self.assertTrue(len(state["steps"]) > 0)

    def test_start_and_finish_step(self):
        """Bắt đầu và hoàn thành 1 bước pipeline."""
        tracker_mod.start_step("script", "Đang tạo kịch bản...", 15)
        state = tracker_mod.get_state()
        self.assertTrue(state["is_running"])
        self.assertEqual(state["progress_percent"], 15)
        step_script = next(s for s in state["steps"] if s["id"] == "script")
        self.assertEqual(step_script["status"], "running")

        tracker_mod.finish_step("script")
        state2 = tracker_mod.get_state()
        step_script2 = next(s for s in state2["steps"] if s["id"] == "script")
        self.assertEqual(step_script2["status"], "done")

    def test_pause_for_review(self):
        """Tạm dừng ở bước xem trước kịch bản/thẻ trước khi render."""
        tracker_mod.pause_for_review("Chờ người dùng duyệt thẻ...")
        state = tracker_mod.get_state()
        self.assertFalse(state["is_running"])
        self.assertEqual(state["current_step_id"], "ready_to_render")
        self.assertIn("Chờ người dùng duyệt thẻ", state["message"])

    def test_finish_pipeline(self):
        """Hoàn tất toàn bộ pipeline với thông tin file video xuất xưởng."""
        tracker_mod.finish_pipeline("test_final.mp4", "Caption video hấp dẫn", 45.5)
        state = tracker_mod.get_state()
        self.assertFalse(state["is_running"])
        self.assertEqual(state["progress_percent"], 100)
        self.assertEqual(state["video_filename"], "test_final.mp4")
        self.assertEqual(state["total_duration_sec"], 45.5)

    def test_set_success(self):
        """Kiểm tra alias set_success đảm bảo tương thích ngược không crash."""
        tracker_mod.set_success("Hoàn thành tài liệu thành công!", "doc_video.mp4", "Caption tài liệu", 30.0)
        state = tracker_mod.get_state()
        self.assertFalse(state["is_running"])
        self.assertEqual(state["progress_percent"], 100)
        self.assertEqual(state["video_filename"], "doc_video.mp4")
        self.assertEqual(state["total_duration_sec"], 30.0)

    def test_set_error(self):
        """Báo lỗi pipeline và lưu thông điệp chi tiết."""
        tracker_mod.set_error("Lỗi kết nối bộ xử lý hình ảnh")
        state = tracker_mod.get_state()
        self.assertFalse(state["is_running"])
        self.assertEqual(state["error"], "Lỗi kết nối bộ xử lý hình ảnh")
        self.assertIn("Lỗi kết nối", state["message"])


if __name__ == "__main__":
    unittest.main()
