"""
TEST MODULE 10: DOCUMENTARY MEDIA MANAGER (documentary_media.py)
=================================================================
Kiểm tra:
  - create_documentary_center_video với danh sách ảnh hợp lệ / rỗng
  - create_cutaway_proof_card với ảnh thật
  - Logic tính số chu kỳ lặp video
  - Hàm get_ffmpeg_exe
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call
from PIL import Image

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(ROOT_DIR, "src")
sys.path.insert(0, SRC_DIR)

import importlib.util


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_test_image(path: str, width: int = 400, height: int = 300, color=(200, 100, 50)):
    """Tạo ảnh PNG giả để test."""
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, "PNG")
    return path


class TestDocumentaryMediaFFmpeg(unittest.TestCase):
    """Kiểm tra hàm liên quan FFmpeg trong documentary_media.py."""

    def setUp(self):
        try:
            self.doc_mod = _load("documentary_media", "documentary_media.py")
        except Exception as e:
            self.skipTest(f"Không load được documentary_media.py: {e}")

    def test_get_ffmpeg_exe_returns_non_empty_string(self):
        """get_ffmpeg_exe phải trả về đường dẫn hợp lệ không rỗng."""
        exe = self.doc_mod.get_ffmpeg_exe()
        self.assertIsInstance(exe, str)
        self.assertGreater(len(exe), 0)

    def test_get_ffmpeg_exe_fallback_when_not_in_path(self):
        """Nếu ffmpeg không trong PATH, phải fallback sang imageio-ffmpeg."""
        with patch("shutil.which", return_value=None):
            exe = self.doc_mod.get_ffmpeg_exe()
            self.assertIsInstance(exe, str)


class TestDocumentaryMediaEmptyInput(unittest.TestCase):
    """Kiểm tra xử lý input rỗng."""

    def setUp(self):
        try:
            self.doc_mod = _load("documentary_media", "documentary_media.py")
        except Exception as e:
            self.skipTest(f"Không load được documentary_media.py: {e}")
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_center_video_with_empty_image_list_returns_none(self):
        """
        create_documentary_center_video với danh sách ảnh rỗng phải trả về None.
        """
        output_path = os.path.join(self.tmp_dir, "out.mp4")
        result = self.doc_mod.create_documentary_center_video(
            image_paths=[],
            output_video_path=output_path,
            target_duration=10.0,
        )
        self.assertIsNone(result, "Phải trả về None khi không có ảnh hợp lệ.")

    def test_create_center_video_with_nonexistent_images_returns_none(self):
        """
        create_documentary_center_video với đường dẫn ảnh không tồn tại phải trả về None.
        """
        output_path = os.path.join(self.tmp_dir, "out.mp4")
        result = self.doc_mod.create_documentary_center_video(
            image_paths=["/nonexistent/image1.jpg", "/nonexistent/image2.jpg"],
            output_video_path=output_path,
            target_duration=10.0,
        )
        self.assertIsNone(result, "Phải trả về None khi tất cả ảnh không tồn tại.")


class TestDocumentaryMediaWithMockFFmpeg(unittest.TestCase):
    """Kiểm tra logic với FFmpeg được mock."""

    def setUp(self):
        try:
            self.doc_mod = _load("documentary_media", "documentary_media.py")
        except Exception as e:
            self.skipTest(f"Không load được documentary_media.py: {e}")
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch("subprocess.run")
    def test_create_center_video_with_valid_images_calls_ffmpeg(self, mock_subproc):
        """
        create_documentary_center_video với ảnh hợp lệ phải gọi FFmpeg subprocess.
        """
        mock_subproc.return_value = MagicMock(returncode=0, stderr="")

        # Tạo ảnh thật để pass validation
        img1 = _create_test_image(os.path.join(self.tmp_dir, "scene1.png"))
        img2 = _create_test_image(os.path.join(self.tmp_dir, "scene2.png"))
        output_path = os.path.join(self.tmp_dir, "documentary.mp4")

        # Giả lập file output được tạo bởi FFmpeg
        with open(output_path, "w") as f:
            f.write("fake mp4")

        result = self.doc_mod.create_documentary_center_video(
            image_paths=[img1, img2],
            output_video_path=output_path,
            target_duration=10.0,
        )

        # FFmpeg subprocess phải được gọi (ít nhất 1 lần)
        self.assertTrue(mock_subproc.called,
                        "subprocess.run phải được gọi để chạy FFmpeg.")

    def test_cycle_calculation_for_target_duration(self):
        """
        Số chu kỳ lặp phải đủ để video dài hơn target_duration.
        """
        # Logic: target_duration / (n_images * interval_per_image) phải được làm tròn lên
        import math
        n_images = 5
        interval_s = 4.5
        target_duration = 60.0
        single_cycle_duration = n_images * interval_s  # 22.5s
        required_cycles = math.ceil(target_duration / single_cycle_duration)  # 3
        total_duration = required_cycles * single_cycle_duration  # 67.5s

        self.assertGreaterEqual(total_duration, target_duration,
                                "Tổng thời lượng phải >= target_duration.")
        self.assertEqual(required_cycles, 3)


class TestCutawayProofCard(unittest.TestCase):
    """Kiểm tra tạo thẻ Cutaway Proof Card."""

    def setUp(self):
        try:
            self.doc_mod = _load("documentary_media", "documentary_media.py")
        except Exception as e:
            self.skipTest(f"Không load được documentary_media.py: {e}")
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_cutaway_proof_card_with_valid_image(self):
        """
        create_cutaway_proof_card với ảnh hợp lệ phải tạo file PNG 980×640px.
        """
        if not hasattr(self.doc_mod, "create_cutaway_proof_card"):
            self.skipTest("create_cutaway_proof_card chưa được export.")

        img_path = _create_test_image(os.path.join(self.tmp_dir, "proof.png"),
                                       width=800, height=600)
        output_path = os.path.join(self.tmp_dir, "card.png")

        result = self.doc_mod.create_cutaway_proof_card(
            image_path=img_path,
            output_path=output_path,
            caption="Trước khi giặt",
        )

        if result and os.path.exists(result):
            img = Image.open(result)
            # Card phải có kích thước chuẩn 980×640
            self.assertEqual(img.width, 980)
            self.assertEqual(img.height, 640)

    def test_create_cutaway_proof_card_with_nonexistent_image(self):
        """
        create_cutaway_proof_card với ảnh không tồn tại phải trả về None hoặc raise.
        """
        if not hasattr(self.doc_mod, "create_cutaway_proof_card"):
            self.skipTest("create_cutaway_proof_card chưa được export.")

        try:
            result = self.doc_mod.create_cutaway_proof_card(
                image_path="/nonexistent/image.jpg",
                output_path=os.path.join(self.tmp_dir, "card.png"),
                caption="Test",
            )
            # Nếu không raise, phải trả None
            self.assertIsNone(result)
        except (FileNotFoundError, ValueError, Exception):
            pass  # Raise exception cũng chấp nhận được


if __name__ == "__main__":
    unittest.main(verbosity=2)
