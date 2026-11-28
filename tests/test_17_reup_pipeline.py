"""
Test Case Suite 17: Reup & Crawl Pipeline (src/reup_pipeline.py & app_ui.py)
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, SRC_DIR)

import _loader

reup_mod = _loader.load_module("reup_pipeline", os.path.join(SRC_DIR, "reup_pipeline.py"))


class TestReupPipeline(unittest.TestCase):
    def test_crawl_video_links_parses_ytdlp_json(self):
        fake_stdout = json.dumps({
            "extractor_key": "YoutubeTab",
            "uploader": "DemoChannel",
            "entries": [
                {
                    "id": "abc123",
                    "title": "Test Short Video",
                    "url": "abc123",
                    "duration": 42,
                    "thumbnails": [{"url": "https://img.example.com/1.jpg"}],
                    "view_count": 1200,
                }
            ],
        })
        mock_proc = MagicMock(returncode=0, stdout=fake_stdout, stderr="")
        with patch("subprocess.run", return_value=mock_proc):
            items = reup_mod.crawl_video_links("https://www.youtube.com/@demo/shorts", limit=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(items[0]["duration_str"], "0:42")
        self.assertEqual(items[0]["platform"], "YouTube")

    def test_write_reup_ass_subtitles_creates_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = os.path.join(tmp, "test.ass")
            segs = [
                {"start_ms": 0, "end_ms": 2500, "vi_text": "Xin chào các bạn đến với câu chuyện hôm nay"},
                {"start_ms": 2700, "end_ms": 5000, "vi_text": "Cú lật kèo cực gắt ở phút cuối"},
            ]
            out = reup_mod.write_reup_ass_subtitles(segs, ass_path, video_title="Bí ẩn lịch sử")
            self.assertTrue(os.path.exists(out))
            content = open(out, "r", encoding="utf-8").read()
            self.assertIn("Dialogue: 1,", content)
            self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.50", content)

    def test_flask_reup_endpoints_validation_and_crawl(self):
        os.environ["APP_VIDEO_DISABLE_BG_WORKERS"] = "1"
        import app_ui
        client = app_ui.app.test_client()

        resp_empty = client.post("/api/reup/crawl", json={"query": ""})
        self.assertEqual(resp_empty.status_code, 400)

        with patch.object(reup_mod, "crawl_video_links", return_value=[{"id": "1", "title": "V1", "url": "https://youtu.be/1"}]):
            with patch.object(app_ui, "load_mod", return_value=reup_mod):
                resp_ok = client.post("/api/reup/crawl", json={"query": "test", "limit": 5})
                self.assertEqual(resp_ok.status_code, 200)
                data = resp_ok.get_json()
                self.assertTrue(data["success"])
                self.assertEqual(len(data["items"]), 1)

        resp_run_empty = client.post("/api/reup/run", json={"urls": []})
        self.assertEqual(resp_run_empty.status_code, 400)

    def test_normalize_douyin_and_bilibili_urls(self):
        share_text = "5.89 Copy打开抖音，看看【作者】的作品！ https://v.douyin.com/iRNBxyz/ 复制此链接"
        self.assertEqual(reup_mod.normalize_video_url(share_text), "https://v.douyin.com/iRNBxyz/")
        modal_url = "https://www.douyin.com/jingxuan?modal_id=7647094786759865807"
        self.assertEqual(reup_mod.normalize_video_url(modal_url), "https://www.douyin.com/video/7647094786759865807")

    def test_crawl_bilibili_search_parses_response(self):
        mock_spi = MagicMock(status_code=200)
        mock_spi.json.return_value = {"data": {"b_3": "test_b3", "b_4": "test_b4"}}
        mock_search = MagicMock(status_code=200)
        mock_search.json.return_value = {
            "data": {
                "result": [
                    {
                        "bvid": "BV1pWhre4EBc",
                        "title": "<em class=\"keyword\">Cat</em> Funny Moment &amp; Dog",
                        "pic": "//i0.hdslb.com/bfs/archive/test.jpg",
                        "duration": "1:0",
                        "author": "BiliUser",
                        "play": 9500,
                    }
                ]
            }
        }
        with patch.object(reup_mod, "_translate_search_keyword_to_zh", return_value="钓鱼"):
            with patch("requests.Session") as mock_sess_cls:
                sess = mock_sess_cls.return_value
                sess.headers = {}
                sess.get.side_effect = [mock_spi, mock_search]
                items = reup_mod.crawl_video_links("cần câu", limit=5, platform_source="bilibili")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["platform"], "Bilibili")
        self.assertEqual(items[0]["url"], "https://www.bilibili.com/video/BV1pWhre4EBc")
        self.assertEqual(items[0]["title"], "Cat Funny Moment & Dog")
        self.assertEqual(items[0]["duration_str"], "1:00")
        self.assertEqual(items[0]["thumbnail"], "https://i0.hdslb.com/bfs/archive/test.jpg")

    def test_run_reup_pipeline_updates_tracker_correctly(self):
        import pipeline_tracker as tracker
        tracker.reset()
        with tempfile.TemporaryDirectory() as tmp:
            fake_src = os.path.join(tmp, "source.mp4")
            open(fake_src, "wb").write(b"fake")
            with patch.object(reup_mod, "download_video_from_url", return_value=fake_src), \
                 patch.object(reup_mod, "probe_video_info", return_value={"width": 1080, "height": 1920, "duration": 5.0}), \
                 patch.object(reup_mod, "extract_and_translate_segments", return_value={
                     "video_title": "Test",
                     "video_caption": "Cap",
                     "hashtags": "#reup",
                     "segments": [{"id": 1, "start_ms": 0, "end_ms": 2000, "vi_text": "Xin chào"}],
                 }), \
                 patch.object(reup_mod, "synthesize_reup_tts", return_value=[
                     {"id": 1, "start_ms": 0, "end_ms": 2000, "duration_ms": 2000, "vi_text": "Xin chào", "tts_path": fake_src}
                 ]), \
                 patch.object(reup_mod, "mix_reup_audio", return_value=os.path.join(tmp, "mix.wav")), \
                 patch.object(reup_mod, "render_reup_video", return_value=os.path.join(tmp, "out.mp4")):
                res = reup_mod.run_reup_pipeline("https://www.bilibili.com/video/BV1test", output_filename="unit_reup.mp4")
        self.assertEqual(res["video_filename"], "unit_reup.mp4")
        st = tracker.get_state()
        self.assertEqual(st["progress_percent"], 96)


if __name__ == "__main__":
    unittest.main()
