"""
Test Case Suite 05: Visual Documentary Story Builder (src/visual_story_builder.py)
=================================================================================
Kiểm thử các thành phần:
  - Header Badge Card Generator (create_header_badge_image)
  - Cinematic Fallback Slide Generator (create_cinematic_scene_slide)
  - ASS Subtitle Generator (build_ass_subtitle_file)
  - Default Scenes Structure & Integrity
"""

import os
import sys
import tempfile
import unittest
from PIL import Image

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

import _loader
builder_mod = _loader.load_module("visual_story_builder", os.path.join(SRC_DIR, "visual_story_builder.py"))


class TestVisualStoryBuilder(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_header_badge_image(self):
        """Tạo thẻ header badge documentary chuẩn 1080x220."""
        out_png = os.path.join(self.temp_dir, "header_badge_test.png")
        result = builder_mod.create_header_badge_image(
            title="VICTOR LUSTIG BÁN THÁP EIFFEL 2 LẦN",
            subtitle="SIÊU LỪA ĐẢO THẾ KỶ • NƯỚC PHÁP",
            output_path=out_png
        )
        self.assertTrue(os.path.exists(out_png))
        self.assertEqual(result, out_png)
        with Image.open(out_png) as img:
            self.assertEqual(img.size, (1080, 220))

    def test_create_cinematic_scene_slide(self):
        """Tạo slide điện ảnh thay thế chất lượng cao 1020x620 khi thiếu ảnh/video."""
        out_jpg = os.path.join(self.temp_dir, "slide_test.jpg")
        result = builder_mod.create_cinematic_scene_slide(
            badge="PARIS NĂM 1925",
            title="CÚ LỪA BÁN THÁP EIFFEL",
            text="Năm 1925, Victor Lustig giả mạo Thứ trưởng Pháp bí mật bán đấu giá 7000 tấn sắt tháp Eiffel.",
            output_path=out_jpg,
            width=1020,
            height=620
        )
        self.assertTrue(os.path.exists(out_jpg))
        self.assertEqual(result, out_jpg)
        with Image.open(out_jpg) as img:
            self.assertEqual(img.size, (1020, 620))

    def test_build_ass_subtitle_file(self):
        """Tạo file phụ đề ASS động với định dạng Dialogue và thời gian chuẩn xác."""
        out_ass = os.path.join(self.temp_dir, "subtitles_test.ass")
        scenes_data = [
            {
                "index": 1,
                "text": "Năm 1925 tại Paris hoa lệ...",
                "start": 0.0,
                "end": 4.5
            },
            {
                "index": 2,
                "text": "Victor Lustig đã nghĩ ra một phi vụ không tưởng.",
                "start": 4.5,
                "end": 9.2
            }
        ]
        res = builder_mod.build_ass_subtitle_file(scenes_data, out_ass)
        self.assertTrue(os.path.exists(out_ass))
        self.assertEqual(res, out_ass)
        with open(out_ass, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Script Info]", content)
        self.assertIn("[V4+ Styles]", content)
        self.assertIn("[Events]", content)
        self.assertIn("Dialogue: 0,0:00:00.00,", content)
        self.assertIn("Năm 1925 tại Paris", content)
        self.assertIn("hoa lệ...", content)

    def test_default_trojan_scenes_integrity(self):
        """Mẫu Trojan scenes phải có đủ 6 phân cảnh với cấu trúc tiêu chuẩn."""
        scenes = builder_mod.DEFAULT_TROJAN_SCENES
        self.assertEqual(len(scenes), 6)
        for i, sc in enumerate(scenes, 1):
            self.assertEqual(sc["index"], i)
            self.assertIn(sc["type"], ["video", "image"])
            self.assertTrue(len(sc["badge"]) > 0)
            self.assertTrue(len(sc["text"]) > 0)
            self.assertIn("sfx", sc)


if __name__ == "__main__":
    unittest.main()
