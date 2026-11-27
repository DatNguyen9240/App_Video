import os
import sys
import time
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import _loader
content_mod = _loader._load("01_content.py")
import pipeline_tracker as tracker
import visual_story_builder as vsb
import app_ui


class TestDocumentaryE2EAndCancel(unittest.TestCase):
    def setUp(self):
        tracker.reset()
        tracker.pause_for_review("Sẵn sàng test")
        self.client = app_ui.app.test_client()

    def test_01_one_click_documentary_ignores_stale_post_and_passes_new_title_and_scenes(self):
        """1-Click với chủ đề mới phải gọi Gemini thay vì dùng lại 2 cảnh cũ / tiêu đề cũ từ sample_post.json."""
        captured = {}

        new_scenes = [
            {
                "index": i,
                "type": "image",
                "path": "",
                "badge": f"CẢNH #{i}",
                "search_query_en": f"Pablo Escobar Avianca Flight 203 scene {i}",
                "text": f"Lời dẫn phân cảnh {i} về vụ Avianca 203.",
                "sfx": "tension_riser",
            }
            for i in range(1, 6)
        ]
        mock_post = content_mod.Post(
            username="director",
            content="Vụ Pablo Escobar đánh bom chuyến bay Avianca 203 năm 1989.",
            platform="documentary",
            story_mode="true_story",
            video_title="VỤ ĐÁNH BOM AVIANCA 203",
            story_badge="HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT",
            documentary_scenes=new_scenes,
            voice_id="nam_hai",
        )

        class FakeAIMod:
            called = False

            @staticmethod
            def generate_story_with_gemini(**kwargs):
                FakeAIMod.called = True
                return mock_post

        def fake_render(**kwargs):
            captured.update(kwargs)
            tracker.finish_pipeline("fake_doc.mp4", "caption", 48.0)
            return "output/final/fake_doc.mp4"

        orig_load_mod = app_ui.load_mod

        def fake_load_mod(name, filename):
            if filename == "01_ai_content.py":
                return FakeAIMod
            return orig_load_mod(name, filename)

        with patch.object(app_ui, "load_mod", side_effect=fake_load_mod), \
             patch.object(app_ui.visual_builder_mod, "render_visual_documentary_video", side_effect=fake_render), \
             patch.object(app_ui, "_render_all_cards_helper", return_value=[]):
            res = self.client.post("/api/create_video_one_click", json={
                "topic": "Vụ Pablo Escobar đánh bom chuyến bay Avianca 203",
                "story_mode": "true_story",
                "voice": "nam_hai",
                "settings": {
                    "platform": "documentary",
                    "motion_style": "tiktok_split",
                    "card_theme": "dark",
                },
                "post": {
                    "video_title": "CHUYỆN DRAMA VĂN PHÒNG",
                    "story_badge": "OLD BADGE",
                    "content": "Sếp giao việc gấp cuối tuần.",
                    "documentary_scenes": [
                        {"index": 1, "type": "video", "text": "Cảnh cũ 1"},
                        {"index": 2, "type": "image", "text": "Cảnh cũ 2"},
                    ],
                },
            })
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json()["success"])

            deadline = time.time() + 5.0
            while time.time() < deadline:
                st = tracker.get_state()
                if not st["is_running"] and st["current_step_id"] in ("done", "error"):
                    break
                time.sleep(0.05)

        st = tracker.get_state()
        self.assertIsNone(st["error"], f"Unexpected pipeline error: {st['error']}")
        self.assertTrue(FakeAIMod.called, "1-Click phải gọi Gemini khi bài cũ không khớp chủ đề mới")
        self.assertEqual(captured.get("story_title"), "VỤ ĐÁNH BOM AVIANCA 203")
        self.assertEqual(len(captured.get("scenes", [])), 5)
        self.assertEqual(captured.get("motion_style"), "tiktok_split")

    def test_02_render_video_documentary_passes_motion_style_without_name_error(self):
        """POST /api/render_video ở chế độ documentary phải truyền đúng motion_style mà không bị NameError."""
        captured = {}

        def fake_render(**kwargs):
            captured.update(kwargs)
            tracker.finish_pipeline("fake_manual_doc.mp4", "caption", 35.0)
            return "output/final/fake_manual_doc.mp4"

        with patch.object(app_ui.visual_builder_mod, "render_visual_documentary_video", side_effect=fake_render):
            res = self.client.post("/api/render_video", json={
                "settings": {
                    "platform": "documentary",
                    "motion_style": "explainer_doc",
                },
                "post": {
                    "platform": "documentary",
                    "video_title": "HỒ SƠ AVIANCA 203",
                    "story_badge": "TƯ LIỆU LỊCH SỬ",
                    "documentary_scenes": [
                        {"index": 1, "type": "image", "text": "Cảnh 1", "search_query_en": "Avianca 203"},
                        {"index": 2, "type": "image", "text": "Cảnh 2", "search_query_en": "Pablo Escobar"},
                    ],
                },
            })
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json()["success"])

            deadline = time.time() + 5.0
            while time.time() < deadline:
                st = tracker.get_state()
                if not st["is_running"] and st["current_step_id"] in ("done", "error"):
                    break
                time.sleep(0.05)

        st = tracker.get_state()
        self.assertIsNone(st["error"], f"Unexpected error: {st['error']}")
        self.assertEqual(captured.get("motion_style"), "explainer_doc")
        self.assertEqual(captured.get("story_title"), "HỒ SƠ AVIANCA 203")

    def test_03_visual_story_builder_preserves_search_query_en_and_guards_image_extension(self):
        """render_visual_documentary_video phải giữ search_query_en, ép .jpg thành image, và tạo TTS mới."""
        captured_queries = []

        with tempfile.TemporaryDirectory() as tmpdir:
            img1 = os.path.join(tmpdir, "scene1.jpg")
            img2 = os.path.join(tmpdir, "scene2.jpg")
            Image.new("RGB", (1040, 820), (30, 60, 120)).save(img1)
            Image.new("RGB", (1040, 820), (120, 40, 40)).save(img2)

            def fake_tts(text, out_path, **kwargs):
                # Tạo file mp3 hợp lệ ~1.2s bằng FFmpeg lavfi
                exe = vsb.get_ffmpeg_exe()
                import subprocess
                subprocess.run(
                    [exe, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
                     "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", out_path],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                )

            def fake_search_image(queries, **kwargs):
                captured_queries.append(list(queries))
                out_p = kwargs.get("output_path")
                Image.new("RGB", (1040, 820), (40, 120, 60)).save(out_p)
                return out_p

            out_name = "test_e2e_doc_verify.mp4"
            out_file = os.path.join(vsb.FINAL_DIR, out_name)
            if os.path.exists(out_file):
                os.remove(out_file)

            scenes = [
                {
                    "index": 1,
                    "type": "video",  # cố tình gán 'video' cho file .jpg để test extension guard
                    "path": img1,
                    "badge": "CẢNH 1",
                    "text": "Cảnh một kiểm tra ép kiểu ảnh.",
                    "search_query_en": "Avianca Flight 203 wreckage 1989",
                    "sfx": "none",
                },
                {
                    "index": 2,
                    "type": "image",
                    "path": "",
                    "badge": "CẢNH 2",
                    "text": "Cảnh hai kiểm tra tìm ảnh tiếng Anh.",
                    "search_query_en": "Pablo Escobar mugshot Colombia 1989",
                    "sfx": "none",
                },
            ]

            with patch.object(vsb.tts_mod, "text_to_speech", side_effect=fake_tts), \
                 patch("real_image_search.search_and_download_real_image", side_effect=fake_search_image):
                res_path = vsb.render_visual_documentary_video(
                    story_title="VỤ ĐÁNH BOM AVIANCA 203",
                    story_badge="HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT",
                    scenes=scenes,
                    output_filename=out_name,
                    tracker=tracker,
                    motion_style="explainer_doc",
                )

            try:
                self.assertTrue(os.path.exists(res_path))
                self.assertGreater(os.path.getsize(res_path), 50_000)
                self.assertTrue(len(captured_queries) >= 1)
                self.assertEqual(captured_queries[0][0], "Pablo Escobar mugshot Colombia 1989")
            finally:
                for p in (res_path, res_path.replace(".mp4", "_thumbnail.jpg"), res_path.replace(".mp4", "_caption.txt")):
                    if os.path.exists(p):
                        os.remove(p)

    def test_04_cancel_pipeline_kills_subprocess_fast_and_updates_state(self):
        """POST /api/cancel_pipeline phải tiêu diệt tiến trình con ngay (<2s) và đặt trạng thái cancelled."""
        tracker.reset()
        tracker.start_step("composite", "Đang dựng FFmpeg...", 55)

        t0 = time.monotonic()
        timer = threading.Timer(
            0.25,
            lambda: self.client.post("/api/cancel_pipeline", json={"reason": "Test ngắt"}),
        )
        timer.start()

        with self.assertRaises(tracker.PipelineCancelledError):
            tracker.run_cancellable_subprocess([sys.executable, "-c", "import time; time.sleep(15)"])

        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.5, f"Ngắt tiến trình quá chậm: {elapsed:.2f}s")
        st = tracker.get_state()
        self.assertFalse(st["is_running"])
        self.assertEqual(st["current_step_id"], "cancelled")
        self.assertIsNone(st["error"])
        comp_step = [s for s in st["steps"] if s["id"] == "composite"][0]
        self.assertEqual(comp_step["status"], "cancelled")

    def test_05_header_badge_handles_long_subtitle_and_title(self):
        """create_header_badge_image phải xuất ảnh RGBA 1080x220 hợp lệ dù tiêu đề & badge dài."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_png = os.path.join(tmpdir, "badge.png")
            vsb.create_header_badge_image(
                "VỤ ÁN PABLO ESCOBAR ĐÁNH BOM CHUYẾN BAY AVIANCA 203 NĂM 1989 TẠI COLOMBIA",
                "HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT",
                out_png,
            )
            self.assertTrue(os.path.exists(out_png))
            with Image.open(out_png) as im:
                self.assertEqual(im.size, (1080, 220))


if __name__ == "__main__":
    unittest.main()
