"""
TEST MODULE 01: DATA MODELS & CONTENT PIPELINE (01_content.py)
"""
import os
import sys
import json
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import importlib.util
spec = importlib.util.spec_from_file_location("content_mod", os.path.join(SRC_DIR, "01_content.py"))
content_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(content_mod)

Post = content_mod.Post
Comment = content_mod.Comment


class TestContentModels(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(BASE_DIR, "output", "test_output")
        os.makedirs(self.test_dir, exist_ok=True)

    def test_default_post_creation(self):
        post = Post(username="chuyen_cong_so", content="Chuyện công sở hôm nay...")
        self.assertEqual(post.username, "chuyen_cong_so")
        self.assertEqual(post.platform, "threads")
        self.assertEqual(post.story_mode, "everyday_life")
        self.assertTrue(isinstance(post.comments, list))
        self.assertTrue(isinstance(post.documentary_scenes, list))

    def test_documentary_post_creation(self):
        scenes = [
            {"index": 1, "type": "video", "badge": "SCENE 1", "text": "Đoạn dẫn 1", "sfx": "tension_riser"},
            {"index": 2, "type": "image", "badge": "SCENE 2", "text": "Đoạn dẫn 2", "sfx": "ting"}
        ]
        post = Post(
            username="doc_teller",
            content="Năm 1925 tại Paris hoa lệ...",
            platform="documentary",
            story_mode="true_story",
            video_title="KỲ ÁN BÁN THÁP EIFFEL",
            story_badge="HỒ SƠ KỲ THÚ • NƯỚC PHÁP",
            documentary_scenes=scenes
        )
        self.assertEqual(post.platform, "documentary")
        self.assertEqual(post.story_mode, "true_story")
        self.assertEqual(len(post.documentary_scenes), 2)
        self.assertEqual(post.documentary_scenes[0]["badge"], "SCENE 1")

    def test_comment_creation(self):
        cmt = Comment(
            username="viet.anh99",
            content="Bình luận đối đáp sắc sảo",
            reply_to="author",
            sfx="boom",
            emotion="shocked"
        )
        self.assertEqual(cmt.username, "viet.anh99")
        self.assertEqual(cmt.sfx, "boom")
        self.assertEqual(cmt.emotion, "shocked")

    def test_json_serialization_roundtrip(self):
        post = Post(
            username="test.user",
            content="Nội dung bài viết kiểm thử",
            likes=45000,
            video_title="BÀI VIẾT TEST",
            story_badge="BADGE TEST",
            platform="documentary",
            documentary_scenes=[{"index": 1, "badge": "TEST", "text": "Nội dung cảnh 1", "type": "image"}],
            comments=[Comment(username="c1", content="cmt 1")]
        )
        json_file = os.path.join(self.test_dir, "test_post.json")
        content_mod.save_post_to_json(post, json_file)
        self.assertTrue(os.path.exists(json_file))

        loaded = content_mod.load_post_from_json(json_file)
        self.assertEqual(loaded.username, "test.user")
        self.assertEqual(loaded.video_title, "BÀI VIẾT TEST")
        self.assertEqual(loaded.platform, "documentary")
        self.assertEqual(len(loaded.documentary_scenes), 1)
        self.assertEqual(len(loaded.comments), 1)

    def test_filter_dataclass_kwargs(self):
        extra_data = {
            "username": "clean.user",
            "content": "Valid text",
            "unknown_field_1": 123,
            "another_random_key": "ignore"
        }
        filtered = content_mod._filter_dataclass_kwargs(Post, extra_data)
        self.assertIn("username", filtered)
        self.assertIn("content", filtered)
        self.assertNotIn("unknown_field_1", filtered)
        self.assertNotIn("another_random_key", filtered)


if __name__ == "__main__":
    unittest.main()
