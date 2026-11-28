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

    def test_mix_reup_audio_preserves_full_duration_across_crossfades(self):
        from pydub import AudioSegment
        with tempfile.TemporaryDirectory() as tmp:
            seg1 = os.path.join(tmp, "s1.wav")
            seg2 = os.path.join(tmp, "s2.wav")
            AudioSegment.silent(duration=1500).export(seg1, format="wav")
            AudioSegment.silent(duration=1500).export(seg2, format="wav")
            segs = [
                {"id": 1, "start_ms": 1000, "end_ms": 2500, "tts_path": seg1},
                {"id": 2, "start_ms": 7000, "end_ms": 8500, "tts_path": seg2},
            ]
            out_wav = os.path.join(tmp, "mixed.wav")
            reup_mod.mix_reup_audio(segs, "", out_wav, video_duration_ms=10000)
            mixed = AudioSegment.from_wav(out_wav)
            self.assertEqual(len(mixed), 10000)

    def test_voice_preview_endpoint_returns_cached_mp3(self):
        os.environ["APP_VIDEO_DISABLE_BG_WORKERS"] = "1"
        import app_ui
        client = app_ui.app.test_client()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(app_ui, "AUDIO_DIR", tmp):
                def _fake_tts(text, output_path, voice="nam_hai", rate="+15%"):
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(b"ID3" + b"\x00" * 2048)
                    return output_path

                with patch.object(app_ui.tts_mod, "text_to_speech", side_effect=_fake_tts) as mock_tts:
                    r1 = client.get("/api/voice_preview?voice=nam_hai&rate=%2B15%25")
                    self.assertEqual(r1.status_code, 200)
                    r1.close()
                    self.assertEqual(mock_tts.call_count, 1)
                    # Lần 2 phải dùng cache, không gọi lại TTS
                    r2 = client.get("/api/voice_preview?voice=nam_hai&rate=%2B15%25")
                    self.assertEqual(r2.status_code, 200)
                    r2.close()
                    self.assertEqual(mock_tts.call_count, 1)

    def test_manual_cookie_normalization_and_flask_endpoints(self):
        import http.cookiejar
        import app_ui

        os.environ["APP_VIDEO_DISABLE_BG_WORKERS"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = os.path.join(tmp, "reup_cookies.txt")
            # 1) Raw header format -> Netscape loadable by MozillaCookieJar
            ns_txt, meta = reup_mod.normalize_cookie_text_to_netscape(
                "Cookie: ttwid=abc123; sessionid=sec999; odin_tt=xyz",
                target_url="https://www.douyin.com/user/demo",
            )
            self.assertEqual(meta["format"], "header")
            self.assertEqual(meta["cookie_count"], 3)
            self.assertIn(".douyin.com", meta["domains"])
            with open(cookie_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(ns_txt)
            jar = http.cookiejar.MozillaCookieJar(cookie_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            self.assertEqual({c.name for c in jar}, {"ttwid", "sessionid", "odin_tt"})

            # 2) JSON Cookie-Editor export format
            ns_json, meta_json = reup_mod.normalize_cookie_text_to_netscape(
                '[{"name":"SESSDATA","value":"bili_val","domain":".bilibili.com","path":"/","secure":true}]',
                platform_source="bilibili",
            )
            self.assertEqual(meta_json["format"], "json")
            self.assertEqual(meta_json["domains"], [".bilibili.com"])
            self.assertIn("SESSDATA\tbili_val", ns_json)

            # 3) Flask GET/POST/DELETE /api/reup/cookies + /api/reup/crawl custom_cookies passthrough + UI HTML hooks
            client = app_ui.app.test_client()
            with patch.object(app_ui, "load_mod", return_value=reup_mod):
                with patch.object(reup_mod, "REUP_MANUAL_COOKIES_PATH", cookie_file):
                    if os.path.exists(cookie_file):
                        os.remove(cookie_file)

                    # Invalid empty input -> HTTP 400
                    bad_res = client.post("/api/reup/cookies", json={"cookies_text": "not_a_valid_cookie"})
                    self.assertEqual(bad_res.status_code, 400)

                    # Save header cookies -> 200 without leaking raw secret value in API response
                    save_res = client.post(
                        "/api/reup/cookies",
                        json={"cookies_text": "ttwid=secret_tok_value; s_v_web_id=web123", "platform": "douyin"},
                    )
                    self.assertEqual(save_res.status_code, 200)
                    save_json = save_res.get_json()
                    self.assertTrue(save_json["has_cookies"])
                    self.assertEqual(save_json["cookie_count"], 2)
                    self.assertNotIn("secret_tok_value", json.dumps(save_json))

                    get_res = client.get("/api/reup/cookies").get_json()
                    self.assertTrue(get_res["has_cookies"])
                    self.assertEqual(get_res["cookie_count"], 2)
                    self.assertNotIn("secret_tok_value", json.dumps(get_res))

                    # _build_ytdlp_auth_and_net_args uses saved manual cookies file without DPAPI Chrome dependency
                    args = reup_mod._build_ytdlp_auth_and_net_args("https://www.douyin.com/user/123", browser_cookies="manual")
                    self.assertIn("--cookies", args)
                    self.assertIn(cookie_file, args)

                    # /api/reup/crawl passes custom_cookies cleanly
                    with patch.object(reup_mod, "crawl_video_links", return_value=[]) as mock_crawl:
                        cr_res = client.post(
                            "/api/reup/crawl",
                            json={
                                "query": "https://www.douyin.com/user/demo",
                                "platform": "douyin",
                                "browser_cookies": "manual",
                                "custom_cookies": "ttwid=direct_val",
                            },
                        )
                        self.assertEqual(cr_res.status_code, 200)
                        self.assertEqual(mock_crawl.call_args.kwargs.get("custom_cookies"), "ttwid=direct_val")

                    del_res = client.delete("/api/reup/cookies").get_json()
                    self.assertFalse(del_res["has_cookies"])

        html = app_ui.app.test_client().get("/").get_data(as_text=True)
        for token in ("reup.customCookiesText", "saveReupCookies", "Nhập Cookie trực tiếp", "/api/reup/cookies"):
            self.assertIn(token, html)


if __name__ == "__main__":
    unittest.main()
