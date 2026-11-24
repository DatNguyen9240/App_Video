"""
TEST MODULE 02: CARD GENERATION & RENDERING ENGINE (02_card_gen.py)
"""
import os
import sys
import unittest
from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import importlib.util
spec = importlib.util.spec_from_file_location("card_mod", os.path.join(SRC_DIR, "02_card_gen.py"))
card_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(card_mod)


class TestCardGen(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(BASE_DIR, "output", "test_output")
        os.makedirs(self.test_dir, exist_ok=True)

    def test_render_threads_post_card(self):
        img = card_mod.render_post_card(
            username="lan_anh.hr",
            content="Công ty thông báo thưởng Tết bằng cổ phiếu nhưng kèm điều kiện gắn bó thêm 3 năm nữa 😭",
            avatar_color="#6366f1",
            is_verified=True,
            likes=42000,
            theme="light",
            platform="threads"
        )
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size[0], 960)
        self.assertGreater(img.size[1], 200)

    def test_render_facebook_post_card(self):
        img = card_mod.render_post_card(
            username="Hội Công Sở Drama",
            content="Góc nhờ tư vấn: Sếp yêu cầu tăng ca không lương vào tối thứ 7 để thể hiện tinh thần cống hiến...",
            avatar_color="#3b82f6",
            is_verified=True,
            likes=18500,
            theme="light",
            platform="facebook"
        )
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size[0], 960)

    def test_render_instagram_post_card(self):
        img = card_mod.render_post_card(
            username="coffeewithme",
            content="Một buổi chiều bình yên bên ly latte thơm lừng giữa lòng Hà Nội mùa thu ✨",
            avatar_color="#ec4899",
            is_verified=False,
            likes=9300,
            theme="dark",
            platform="instagram"
        )
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size[0], 960)

    def test_render_comment_cards_all_platforms(self):
        for plat in ["threads", "facebook", "instagram"]:
            c_img = card_mod.render_comment_card(
                username="minh.triet99",
                content=f"Đối đáp kịch tính trên nền tảng {plat} nè bạn ơi 😂",
                avatar_color="#10b981",
                is_verified=False,
                likes=5400,
                index=1,
                reply_to="lan_anh.hr",
                theme="light",
                platform=plat
            )
            self.assertIsInstance(c_img, Image.Image)
            self.assertEqual(c_img.size[0], 960)

    def test_render_outro_cta_card(self):
        out_path = os.path.join(self.test_dir, "test_outro.png")
        card_mod.render_outro_cta_card(
            question_text="Còn bạn thì sao? Nếu ở vị trí đó bạn sẽ xử lý thế nào?",
            output_path=out_path,
            platform="threads"
        )
        self.assertTrue(os.path.exists(out_path))
        self.assertGreater(os.path.getsize(out_path), 5000)

    def test_save_card(self):
        img = Image.new("RGBA", (200, 200), (255, 0, 0, 255))
        out_p = os.path.join(self.test_dir, "test_save.png")
        card_mod.save_card(img, out_p)
        self.assertTrue(os.path.exists(out_p))


if __name__ == "__main__":
    unittest.main()
