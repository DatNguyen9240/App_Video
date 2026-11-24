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


if __name__ == "__main__":
    unittest.main()
