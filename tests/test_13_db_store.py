"""
TEST MODULE 13: SQLITE DB STORE & MULTI-KEY ROTATION (db_store.py)
==================================================================
Kiểm tra:
  - Thêm / liệt kê / che key / bật-tắt / xóa GEMINI_API_KEY trong SQLite
  - Cơ chế tự động xoay vòng key khi 1 key bị lỗi quota (error_count tăng)
  - Fallback về GEMINI_API_KEY trong `.env` khi DB rỗng
  - Lưu & đọc studio_settings từ DB kèm fallback về `.env`
"""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT_DIR, "src")
sys.path.insert(0, SRC_DIR)

import db_store


class TestDbStoreGeminiKeys(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_studio.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_add_and_list_masked_keys(self):
        added = db_store.add_gemini_key("AIzaSyTestKey1234567890ABCD", label="Key chính", db_path=self.db_path)
        self.assertEqual(added["label"], "Key chính")
        self.assertNotIn("1234567890", added["masked_key"])
        self.assertTrue(added["masked_key"].startswith("AIzaSy"))
        self.assertTrue(added["masked_key"].endswith("ABCD"))

        items = db_store.list_gemini_keys(db_path=self.db_path)
        self.assertEqual(len(items), 1)

    def test_rotation_prioritizes_healthy_keys_over_errored_keys(self):
        k1 = "AIzaSyFirstKey00000000001111"
        k2 = "AIzaSySecondKey0000000002222"
        db_store.add_gemini_key(k1, label="K1", db_path=self.db_path)
        db_store.add_gemini_key(k2, label="K2", db_path=self.db_path)

        # Ban đầu k1 đứng trước k2
        active = db_store.get_active_gemini_keys(db_path=self.db_path)
        self.assertEqual(active[0], k1)

        # Khi k1 báo lỗi 429/quota, k2 phải tự động lên đầu danh sách
        db_store.report_key_error(k1, "429 RESOURCE_EXHAUSTED", db_path=self.db_path)
        active_after = db_store.get_active_gemini_keys(db_path=self.db_path)
        self.assertEqual(active_after[0], k2)

        # Khi k1 hồi phục, error_count reset về 0
        db_store.report_key_success(k1, db_path=self.db_path)
        active_recovered = db_store.get_active_gemini_keys(db_path=self.db_path)
        self.assertEqual(active_recovered[0], k1)

    def test_toggle_and_delete_key(self):
        added = db_store.add_gemini_key("AIzaSyToggleKey123456789", db_path=self.db_path)
        self.assertTrue(db_store.toggle_gemini_key(added["id"], False, db_path=self.db_path))
        keys = db_store.list_gemini_keys(db_path=self.db_path)
        self.assertFalse(keys[0]["is_active"])

        self.assertTrue(db_store.delete_gemini_key(added["id"], db_path=self.db_path))
        self.assertEqual(len(db_store.list_gemini_keys(db_path=self.db_path)), 0)

    def test_fallback_to_env_when_db_empty(self):
        with patch.object(db_store, "_read_env_vars", return_value={"GEMINI_API_KEY": "env-fallback-key-999"}):
            keys = db_store.get_active_gemini_keys(db_path=self.db_path)
            self.assertEqual(keys, ["env-fallback-key-999"])


class TestDbStoreStudioSettings(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_settings.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_override_and_reset_fallback_to_env(self):
        with patch.object(db_store, "_read_env_vars", return_value={"PLATFORM": "facebook", "BGM_VOLUME": "40"}):
            # Trước khi lưu DB -> dùng .env
            initial = db_store.get_studio_settings(db_path=self.db_path)
            self.assertEqual(initial["PLATFORM"], "facebook")
            self.assertEqual(initial["BGM_VOLUME"], "40")

            # Lưu DB -> ghi đè .env
            db_store.save_studio_settings({"PLATFORM": "tiktok", "BGM_VOLUME": "15"}, db_path=self.db_path)
            overridden = db_store.get_studio_settings(db_path=self.db_path)
            self.assertEqual(overridden["PLATFORM"], "tiktok")
            self.assertEqual(overridden["BGM_VOLUME"], "15")

            # Reset DB -> quay về .env
            reverted = db_store.reset_studio_settings(db_path=self.db_path)
            self.assertEqual(reverted["PLATFORM"], "facebook")
            self.assertEqual(reverted["BGM_VOLUME"], "40")


if __name__ == "__main__":
    unittest.main(verbosity=2)
