import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import db_store
import proxy_manager
import app_ui


class TestProxyStoreAndRotation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test_proxy.db")

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp.cleanup()
        except PermissionError:
            pass

    def test_normalize_proxy_formats(self):
        self.assertEqual(db_store.normalize_proxy_url("1.2.3.4:8080"), "http://1.2.3.4:8080")
        self.assertEqual(db_store.normalize_proxy_url("1.2.3.4:8080:alice:secret"), "http://alice:secret@1.2.3.4:8080")
        self.assertEqual(db_store.normalize_proxy_url("alice:secret@1.2.3.4:8080"), "http://alice:secret@1.2.3.4:8080")
        self.assertEqual(db_store.normalize_proxy_url("https://p.example.com:443"), "https://p.example.com:443")

    def test_mask_proxy_hides_password(self):
        self.assertEqual(db_store.mask_proxy("http://alice:secret@1.2.3.4:8080"), "http://alice:***@1.2.3.4:8080")

    def test_add_toggle_delete_and_rotate_proxies(self):
        p1 = db_store.add_proxy("10.0.0.1:8080:u1:p1", label="US-1", db_path=self.db_path)
        p2 = db_store.add_proxy("10.0.0.2:8080:u2:p2", label="US-2", db_path=self.db_path)
        self.assertEqual(len(db_store.list_proxies(db_path=self.db_path)), 2)

        got1 = proxy_manager.get_next_proxy(db_path=self.db_path)
        got2 = proxy_manager.get_next_proxy(db_path=self.db_path)
        self.assertNotEqual(got1, got2)

        db_store.toggle_proxy(p1["id"], False, db_path=self.db_path)
        active = db_store.get_active_proxies(db_path=self.db_path)
        self.assertEqual(len(active), 1)
        self.assertIn("10.0.0.2:8080", active[0])

        self.assertTrue(db_store.delete_proxy(p2["id"], db_path=self.db_path))
        self.assertTrue(db_store.delete_proxy(p1["id"], db_path=self.db_path))
        self.assertEqual(len(db_store.list_proxies(db_path=self.db_path)), 0)

    def test_api_proxies_crud(self):
        orig_load_mod = app_ui.load_mod
        with patch.object(db_store, "DB_PATH", self.db_path), \
             patch.object(app_ui, "load_mod", side_effect=lambda name, fn: db_store if fn == "db_store.py" else orig_load_mod(name, fn)):
            client = app_ui.app.test_client()
            res = client.post("/api/proxies", json={
                "label": "Node",
                "proxy_url": "127.0.0.1:8888\n127.0.0.1:9999:user:pass",
            })
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["count"], 2)

            res_get = client.get("/api/proxies")
            items = res_get.get_json()["proxies"]
            self.assertEqual(len(items), 2)

            res_del = client.delete(f"/api/proxies/{items[0]['id']}")
            self.assertTrue(res_del.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
