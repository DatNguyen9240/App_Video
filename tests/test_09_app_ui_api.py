"""
Test Case Suite 09: Web Studio Flask API Endpoints (app_ui.py)
=============================================================
Kiểm thử các REST API endpoints của giao diện Web Studio:
  - /api/pipeline_status
  - /api/settings (GET / POST)
  - /api/latest_video
  - /api/trending_topics
  - /api/documentary_media (Streaming & Security check)
  - /api/reset_tracker
"""

import os
import sys
import json
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import app_ui


class TestAppUiApi(unittest.TestCase):

    def setUp(self):
        app_ui.app.config["TESTING"] = True
        self.client = app_ui.app.test_client()

    def test_api_pipeline_status(self):
        """Endpoint /api/pipeline_status trả về JSON trạng thái hợp lệ."""
        res = self.client.get("/api/pipeline_status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("steps", data)
        self.assertIn("current_step_id", data)

    def test_api_settings_get_and_post(self):
        """Endpoint /api/settings đọc và cập nhật cài đặt."""
        # GET settings
        res = self.client.get("/api/settings")
        self.assertEqual(res.status_code, 200)
        settings = res.get_json()
        self.assertIsInstance(settings, dict)

        # POST settings update
        new_payload = {"platform": "documentary", "author_voice": "adam"}
        res2 = self.client.post(
            "/api/settings",
            data=json.dumps(new_payload),
            content_type="application/json"
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertTrue(data2.get("success", False))

    def test_api_latest_video(self):
        """Endpoint /api/latest_video không được trả về file tạm hoặc crash ngoài context."""
        res = self.client.get("/api/latest_video")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("exists", data)
        if data.get("exists"):
            self.assertIn("filename", data)
            if data.get("filename"):
                self.assertFalse(data["filename"].startswith("_"))

    def test_api_trending_topics(self):
        """Endpoint /api/trending_topics trả về danh sách các chủ đề thịnh hành."""
        res = self.client.get("/api/trending_topics")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("topics", data)

    def test_api_documentary_media_security(self):
        """Bảo mật: Ngăn chặn triệt để path traversal qua /api/documentary_media."""
        # Thử tấn công path traversal
        res = self.client.get("/api/documentary_media?path=../../../../etc/passwd")
        self.assertIn(res.status_code, [403, 404])

        res2 = self.client.get("/api/documentary_media?path=..\\..\\windows\\system32\\cmd.exe")
        self.assertIn(res2.status_code, [403, 404])

    def test_api_reset_tracker(self):
        """Endpoint /api/reset_tracker đưa pipeline về trạng thái sẵn sàng."""
        res = self.client.post("/api/reset_tracker")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success", False))


if __name__ == "__main__":
    unittest.main()
