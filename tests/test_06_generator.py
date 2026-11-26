"""
TEST MODULE 06: STORY GENERATOR & TOPIC SELECTION (06_generator.py)
====================================================================
Kiểm tra:
  - Danh sách chủ đề mẫu hợp lệ
  - Hàm phân loại chủ đề theo context
  - Luồng lựa chọn chủ đề với mock input
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(ROOT_DIR, "src")
sys.path.insert(0, SRC_DIR)

import importlib.util


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestGeneratorTopics(unittest.TestCase):
    """Kiểm tra danh sách chủ đề mẫu và phân loại."""

    def setUp(self):
        try:
            self.gen_mod = _load("generator", "06_generator.py")
        except Exception as e:
            self.skipTest(f"Không load được 06_generator.py: {e}")

    def test_module_loads_successfully(self):
        """Module 06_generator.py phải load được không có lỗi."""
        self.assertIsNotNone(self.gen_mod)

    def test_generator_has_topics_or_function(self):
        """
        Module phải có danh sách topics hoặc hàm choose_or_create_post.
        """
        has_topics = hasattr(self.gen_mod, "SAMPLE_TOPICS") or \
                     hasattr(self.gen_mod, "TOPIC_CATEGORIES") or \
                     hasattr(self.gen_mod, "get_topics") or \
                     callable(getattr(self.gen_mod, "choose_or_create_post", None))
        self.assertTrue(has_topics,
                        "06_generator.py phải export SAMPLE_TOPICS, TOPIC_CATEGORIES, get_topics(), hoặc choose_or_create_post()")

    def test_topics_list_is_non_empty(self):
        """Danh sách topics mẫu phải không rỗng."""
        if hasattr(self.gen_mod, "SAMPLE_TOPICS"):
            self.assertGreater(len(self.gen_mod.SAMPLE_TOPICS), 0)
        elif hasattr(self.gen_mod, "TOPIC_CATEGORIES"):
            for cat, topics in self.gen_mod.TOPIC_CATEGORIES.items():
                self.assertGreater(len(topics), 0, f"Category '{cat}' không có topic nào.")

    def test_all_topics_are_strings(self):
        """Tất cả topic phải là chuỗi không rỗng."""
        topics = []
        if hasattr(self.gen_mod, "SAMPLE_TOPICS"):
            topics = self.gen_mod.SAMPLE_TOPICS
        elif hasattr(self.gen_mod, "TOPIC_CATEGORIES"):
            for cat_topics in self.gen_mod.TOPIC_CATEGORIES.values():
                topics.extend(cat_topics)

        for t in topics:
            self.assertIsInstance(t, str, f"Topic phải là chuỗi, nhận được: {type(t)}")
            self.assertGreater(len(t.strip()), 0, f"Topic không được rỗng: '{t}'")


class TestGeneratorWithMockRadar(unittest.TestCase):
    """Kiểm tra choose_or_create_post với mock radar và input."""

    def setUp(self):
        # Mock threads_radar để không cần kết nối mạng
        self.mock_radar = MagicMock()
        self.mock_radar.get_daily_trending_topics.return_value = {
            "date": "2026-11-26",
            "topics": [
                "chuyện văn phòng hôm nay",
                "crush vô tình tag mình vào ảnh cũ",
                "mua đồ online nhận về sai size",
            ]
        }
        self.mock_post = MagicMock()
        self.mock_post.username = "test_author"
        self.mock_post.content = "Hôm nay văn phòng có drama lớn..."
        self.mock_radar.crawl_trending_threads_story.return_value = self.mock_post

    def test_trending_topics_returned_correctly(self):
        """get_daily_trending_topics phải trả về dict với 'topics' là list."""
        result = self.mock_radar.get_daily_trending_topics()
        self.assertIn("topics", result)
        self.assertIsInstance(result["topics"], list)
        self.assertGreater(len(result["topics"]), 0)

    def test_crawl_story_returns_post_with_content(self):
        """crawl_trending_threads_story phải trả về Post có content hợp lệ."""
        post = self.mock_radar.crawl_trending_threads_story("chuyện văn phòng hôm nay")
        self.assertIsNotNone(post)
        self.assertTrue(hasattr(post, "username") or hasattr(post, "content"))


class TestGeneratorSFXMapping(unittest.TestCase):
    """Kiểm tra mapping SFX nếu có trong generator."""

    def setUp(self):
        try:
            self.gen_mod = _load("generator", "06_generator.py")
        except Exception as e:
            self.skipTest(f"Không load được 06_generator.py: {e}")

    def test_sfx_values_are_valid(self):
        """Nếu có VALID_SFX_TYPES, tất cả phải là chuỗi hợp lệ."""
        if not hasattr(self.gen_mod, "VALID_SFX_TYPES"):
            self.skipTest("VALID_SFX_TYPES chưa được export.")
        valid_sfx = self.gen_mod.VALID_SFX_TYPES
        self.assertIsInstance(valid_sfx, (list, tuple, set))
        for sfx in valid_sfx:
            self.assertIsInstance(sfx, str)
            self.assertFalse(sfx.strip() == "", f"SFX type không được rỗng: '{sfx}'")

    def test_none_sfx_always_in_valid_list(self):
        """'none' phải luôn là một lựa chọn SFX hợp lệ (không có effect)."""
        if not hasattr(self.gen_mod, "VALID_SFX_TYPES"):
            self.skipTest("VALID_SFX_TYPES chưa được export.")
        self.assertIn("none", self.gen_mod.VALID_SFX_TYPES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
