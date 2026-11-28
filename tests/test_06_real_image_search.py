"""
Test Case Suite 06: Real Image Search & Verification (src/real_image_search.py)
=============================================================================
Kiểm thử các thành phần:
  - Chuẩn hóa và làm sạch từ khóa tìm kiếm (clean_search_term, normalize_query_terms)
  - Sinh từ khóa trực quan cho Wikimedia (generate_wikimedia_visual_queries)
  - Phân biệt ảnh đồ họa/logo phẳng với ảnh chụp thật (is_logo_or_graphic)
"""

import os
import sys
import unittest
from PIL import Image

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

import _loader
img_search_mod = _loader.load_module("real_image_search", os.path.join(SRC_DIR, "real_image_search.py"))


class TestRealImageSearch(unittest.TestCase):

    def test_clean_search_term(self):
        """Làm sạch từ khóa, loại bỏ ký tự lạ và dấu ngoặc kép."""
        raw = '  "Victor Lustig: Eiffel Tower (1925) - scam!"  '
        cleaned = img_search_mod.clean_search_term(raw)
        self.assertNotIn('"', cleaned)
        self.assertNotIn("!", cleaned)
        self.assertTrue(len(cleaned) > 0)
        self.assertIn("victor lustig", cleaned)

    def test_normalize_query_terms(self):
        """Sinh ra danh sách các biến thể từ khóa hợp lệ để tra cứu."""
        terms = img_search_mod.normalize_query_terms("Exit Deutschland trojan t-shirt")
        self.assertTrue(isinstance(terms, list))
        self.assertTrue(len(terms) >= 1)
        self.assertIn("exit deutschland trojan t-shirt", terms)

    def test_generate_wikimedia_visual_queries(self):
        """Sinh các truy vấn Wikimedia sát với thực tế lịch sử."""
        queries = img_search_mod.generate_wikimedia_visual_queries("Victor Lustig tháp Eiffel")
        self.assertTrue(isinstance(queries, list))
        self.assertTrue(len(queries) >= 1)

    def test_is_logo_or_graphic_solid_color(self):
        """Phát hiện ảnh đơn sắc (solid color) hoặc đồ họa phẳng để loại bỏ."""
        # Tạo ảnh đơn sắc 100x100 màu trắng
        im_solid = Image.new("RGB", (100, 100), (255, 255, 255))
        is_graphic = img_search_mod.is_logo_or_graphic(im_solid)
        self.assertTrue(is_graphic)

    def test_search_bing_images_async_fallback_parsing(self):
        """Bing async endpoint parsing extracts valid image URLs and filters blacklist."""
        from unittest.mock import patch, MagicMock
        fake_html = (
            '&quot;murl&quot;:&quot;https://example.com/real-photo.jpg&quot; '
            '&quot;murl&quot;:&quot;https://www.shutterstock.com/watermarked.jpg&quot; '
            '&quot;murl&quot;:&quot;https://shopee.vn/review-item.jpg&quot;'
        ).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = fake_html
        with patch.object(img_search_mod, "_urlopen", return_value=mock_resp):
            urls_strict = img_search_mod.search_bing_images("car wash", limit=5, allow_ecommerce=False)
            self.assertEqual(urls_strict, ["https://example.com/real-photo.jpg"])
            urls_ecom = img_search_mod.search_bing_images("cọ rửa xe", limit=5, allow_ecommerce=True)
            self.assertEqual(
                urls_ecom,
                ["https://example.com/real-photo.jpg", "https://shopee.vn/review-item.jpg"],
            )

    def test_search_openverse_images_parsing(self):
        """Openverse API parser filters blacklisted/svg URLs and keeps real photos."""
        import json
        from unittest.mock import patch, MagicMock
        payload = json.dumps({
            "results": [
                {"url": "https://live.staticflickr.com/123/photo.jpg"},
                {"url": "https://example.com/icon.svg"},
                {"url": "https://img.freepik.com/vector.jpg"},
            ]
        }).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = payload
        with patch.object(img_search_mod, "_urlopen", return_value=mock_resp):
            urls = img_search_mod.search_openverse_images("office sofa", limit=5)
            self.assertEqual(urls, ["https://live.staticflickr.com/123/photo.jpg"])


if __name__ == "__main__":
    unittest.main()
