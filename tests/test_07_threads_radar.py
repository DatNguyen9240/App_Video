"""
Test Case Suite 07: Threads Radar & Context Intelligence (src/threads_radar.py)
==============================================================================
Kiểm thử các thành phần:
  - Phân loại ngữ cảnh chủ đề (detect_topic_context)
  - Sinh kịch bản cứu nguy khẩn cấp khi offline (build_emergency_story)
  - Cấu trúc danh sách chủ đề thịnh hành (get_daily_trending_topics)
"""

import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

import _loader
radar_mod = _loader.load_module("threads_radar", os.path.join(SRC_DIR, "threads_radar.py"))


class TestThreadsRadar(unittest.TestCase):

    def test_detect_topic_context(self):
        """Phát hiện chính xác nhóm bối cảnh từ tiêu đề/nội dung."""
        ctx_office = radar_mod.detect_topic_context("Họp công ty cuối năm bị sếp mắng deadline")
        self.assertEqual(ctx_office, "cong_so")

        ctx_student = radar_mod.detect_topic_context("Thi tốt nghiệp đại học rớt môn điểm danh")
        self.assertEqual(ctx_student, "sinh_vien")

        ctx_gen = radar_mod.detect_topic_context("Chuyện đi dạo phố mua đồ ăn vặt")
        self.assertEqual(ctx_gen, "general")

    def test_build_emergency_story(self):
        """Tạo cấu trúc bài viết khẩn cấp hợp lệ khi mất mạng hoặc không có API key."""
        story = radar_mod.build_emergency_story("Chuyện dở khóc dở cười")
        self.assertIn("username", story)
        self.assertIn("content", story)
        self.assertIn("comments", story)
        self.assertTrue(len(story["comments"]) >= 5)
        for c in story["comments"]:
            self.assertIn("username", c)
            self.assertIn("content", c)
            self.assertIn("reply_to", c)

    def test_get_daily_trending_topics_structure(self):
        """Cấu trúc trending topics trả về phải có ngày cập nhật và danh sách chủ đề."""
        data = radar_mod.get_daily_trending_topics(force_refresh=False)
        self.assertIn("date", data)
        self.assertIn("topics", data)
        self.assertTrue(isinstance(data["topics"], list))
        self.assertTrue(len(data["topics"]) > 0)


if __name__ == "__main__":
    unittest.main()
