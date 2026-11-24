"""
Test Case Suite 04: Background Video & Audio SFX System (src/04_bg_audio.py)
=============================================================================
Kiểm thử các thành phần:
  - FFmpeg executable resolver
  - Infinity themes definition & structure
  - Media duration probing
  - Satisfying video category selection
  - Background video preparation fallback
"""

import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

import _loader
bg_audio_mod = _loader.load_module("04_bg_audio", os.path.join(SRC_DIR, "04_bg_audio.py"))


class TestBgAudio(unittest.TestCase):

    def test_ffmpeg_exe_available(self):
        """FFmpeg phải có thể tìm thấy hoặc resolve được đường dẫn hợp lệ."""
        exe = bg_audio_mod.get_ffmpeg_exe()
        self.assertIsNotNone(exe)
        self.assertTrue(len(exe) > 0)
        self.assertTrue("ffmpeg" in exe.lower())

    def test_infinity_themes_structure(self):
        """Bảng màu đường hầm vô cực phải có đầy đủ các cấu hình neon, dark, gold."""
        themes = bg_audio_mod.INFINITY_THEMES
        self.assertIn("neon", themes)
        self.assertIn("dark", themes)
        self.assertIn("gold", themes)
        for name, cfg in themes.items():
            self.assertIn("name", cfg)
            self.assertIn("bg", cfg)
            self.assertIn("center_rgb", cfg)
            self.assertIn("edge_rgb", cfg)
            self.assertEqual(len(cfg["bg"]), 3)
            self.assertEqual(len(cfg["center_rgb"]), 3)
            self.assertEqual(len(cfg["edge_rgb"]), 3)

    def test_satisfying_video_category(self):
        """Kiểm tra tìm kiếm video nền satisfying theo category."""
        # Test fallback / default video lookup
        path = bg_audio_mod.get_satisfying_video_path("puzzle")
        if path:
            self.assertTrue(os.path.exists(path))

    def test_media_duration_nonexistent(self):
        """File không tồn tại phải trả về duration 0.0 thay vì ném unhandled exception."""
        dur = bg_audio_mod.get_media_duration("non_existent_file_xyz123.mp4")
        self.assertEqual(dur, 0.0)


if __name__ == "__main__":
    unittest.main()
