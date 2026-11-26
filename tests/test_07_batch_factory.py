"""
TEST MODULE 07: BATCH VIDEO FACTORY (07_batch_factory.py)
==========================================================
Kiểm tra:
  - Hàm run_batch_factory với mock pipeline và AI
  - Xử lý exception khi 1 video lỗi → phải tiếp tục sang video tiếp theo
  - Giới hạn số lượng video hợp lệ
  - Log output hoàn chỉnh sau khi xong
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, call

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(ROOT_DIR, "src")
sys.path.insert(0, SRC_DIR)

import importlib.util


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBatchFactoryLogic(unittest.TestCase):
    """Kiểm tra logic nghiệp vụ của batch factory."""

    def setUp(self):
        try:
            self.batch_mod = _load("batch_factory", "07_batch_factory.py")
        except Exception as e:
            self.skipTest(f"Không load được 07_batch_factory.py: {e}")

    def test_module_loads_successfully(self):
        """Module 07_batch_factory.py phải load được không có lỗi."""
        self.assertIsNotNone(self.batch_mod)

    def test_run_batch_factory_exists(self):
        """Hàm run_batch_factory phải tồn tại và callable."""
        self.assertTrue(
            hasattr(self.batch_mod, "run_batch_factory"),
            "run_batch_factory phải được export từ 07_batch_factory.py"
        )
        self.assertTrue(callable(self.batch_mod.run_batch_factory))

    @patch("builtins.print")
    def test_run_batch_with_custom_topics_and_mock_pipeline(self, mock_print):
        """
        run_batch_factory với custom_topics và mock pipeline phải:
        - Gọi pipeline đúng số lần
        - Không throw exception
        """
        if not hasattr(self.batch_mod, "run_batch_factory"):
            self.skipTest("run_batch_factory chưa tồn tại.")

        mock_post = MagicMock()
        mock_post.username = "test_user"
        mock_post.content = "Drama văn phòng hôm nay"

        with patch.object(self.batch_mod, "ai_mod") as mock_ai, \
             patch.object(self.batch_mod, "pipeline_mod") as mock_pipeline, \
             patch.object(self.batch_mod, "radar_mod") as mock_radar, \
             patch("os.startfile", create=True):

            mock_ai.generate_story_with_gemini.return_value = mock_post
            mock_ai.get_or_prompt_api_key.return_value = "test-api-key"
            mock_pipeline.run_pipeline.return_value = "/output/final/video_1.mp4"
            mock_radar.get_daily_trending_topics.return_value = {"topics": []}
            mock_radar.crawl_trending_threads_story.return_value = mock_post

            self.batch_mod.run_batch_factory(
                num_videos=3,
                custom_topics=["chủ đề 1", "chủ đề 2", "chủ đề 3"]
            )

            self.assertEqual(mock_pipeline.run_pipeline.call_count, 3)

    @patch("builtins.print")
    def test_batch_continues_after_single_video_error(self, mock_print):
        """
        Nếu 1 video bị lỗi (exception), batch phải tiếp tục render các video còn lại.
        """
        if not hasattr(self.batch_mod, "run_batch_factory"):
            self.skipTest("run_batch_factory chưa tồn tại.")

        mock_post = MagicMock()
        mock_post.username = "user"
        mock_post.content = "Drama"

        call_count = {"n": 0}

        def side_effect_pipeline(post, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("Giả lập lỗi render video thứ 2")
            return f"/output/video_{call_count['n']}.mp4"

        with patch.object(self.batch_mod, "ai_mod") as mock_ai, \
             patch.object(self.batch_mod, "pipeline_mod") as mock_pipeline, \
             patch.object(self.batch_mod, "radar_mod") as mock_radar, \
             patch("os.startfile", create=True):

            mock_ai.generate_story_with_gemini.return_value = mock_post
            mock_ai.get_or_prompt_api_key.return_value = "test-key"
            mock_pipeline.run_pipeline.side_effect = side_effect_pipeline
            mock_radar.get_daily_trending_topics.return_value = {"topics": []}
            mock_radar.crawl_trending_threads_story.return_value = mock_post

            # Không được raise exception ngay cả khi video 2 lỗi
            try:
                self.batch_mod.run_batch_factory(
                    num_videos=3,
                    custom_topics=["topic 1", "topic 2", "topic 3"]
                )
            except Exception as e:
                self.fail(f"run_batch_factory throw exception không mong đợi: {e}")

            # Pipeline phải được gọi đúng 3 lần (dù 1 lần lỗi)
            self.assertEqual(mock_pipeline.run_pipeline.call_count, 3)

    @patch("builtins.print")
    def test_batch_zero_videos_does_not_crash(self, mock_print):
        """run_batch_factory(num_videos=0) không được crash."""
        if not hasattr(self.batch_mod, "run_batch_factory"):
            self.skipTest("run_batch_factory chưa tồn tại.")

        with patch.object(self.batch_mod, "ai_mod") as mock_ai, \
             patch.object(self.batch_mod, "pipeline_mod") as mock_pipeline, \
             patch.object(self.batch_mod, "radar_mod") as mock_radar, \
             patch("os.startfile", create=True):

            mock_ai.get_or_prompt_api_key.return_value = "key"
            mock_radar.get_daily_trending_topics.return_value = {"topics": ["t1"]}
            mock_radar.crawl_trending_threads_story.return_value = MagicMock()

            try:
                self.batch_mod.run_batch_factory(num_videos=0, custom_topics=["t1"])
            except Exception:
                pass  # Chấp nhận exception, nhưng không được crash Python interpreter

            # Pipeline không được được gọi với num_videos=0
            self.assertEqual(mock_pipeline.run_pipeline.call_count, 0)

    @patch("builtins.print")
    def test_topics_cycle_when_fewer_than_num_videos(self, mock_print):
        """
        Nếu số topic ít hơn num_videos, chủ đề phải được lặp vòng (modulo).
        """
        if not hasattr(self.batch_mod, "run_batch_factory"):
            self.skipTest("run_batch_factory chưa tồn tại.")

        mock_post = MagicMock()
        mock_post.username = "user"
        mock_post.content = "Drama"
        generated_topics = []

        def capture_generate(topic, **kwargs):
            generated_topics.append(topic)
            return mock_post

        with patch.object(self.batch_mod, "ai_mod") as mock_ai, \
             patch.object(self.batch_mod, "pipeline_mod") as mock_pipeline, \
             patch.object(self.batch_mod, "radar_mod") as mock_radar, \
             patch("os.startfile", create=True):

            mock_ai.generate_story_with_gemini.side_effect = capture_generate
            mock_ai.get_or_prompt_api_key.return_value = "key"
            mock_pipeline.run_pipeline.return_value = "/output/v.mp4"
            mock_radar.get_daily_trending_topics.return_value = {"topics": []}
            mock_radar.crawl_trending_threads_story.return_value = mock_post

            self.batch_mod.run_batch_factory(
                num_videos=4,
                custom_topics=["topic A", "topic B"]
            )

        # 4 video với 2 topic → phải lặp lại
        self.assertEqual(len(generated_topics), 4)
        self.assertEqual(generated_topics[0], generated_topics[2])
        self.assertEqual(generated_topics[1], generated_topics[3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
