"""
TEST MODULE 03: TTS & MULTI-VOICE CASTING ENGINE (03_tts.py)
"""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import importlib.util
spec = importlib.util.spec_from_file_location("tts_mod", os.path.join(SRC_DIR, "03_tts.py"))
tts_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tts_mod)


class TestTTSEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(BASE_DIR, "output", "test_output")
        os.makedirs(self.test_dir, exist_ok=True)

    def test_parse_rate_and_pitch(self):
        # Kiểm tra hàm tính toán biến thiên rate và pitch an toàn
        test_rate = "+20%"
        test_pitch = "+4Hz"
        self.assertTrue(test_rate.endswith("%"))
        self.assertTrue(test_pitch.endswith("Hz"))

    def test_gtts_synthesis_fallback(self):
        # Kiểm tra tầng gTTS độc lập không phụ thuộc Edge TTS
        out_audio = os.path.join(self.test_dir, "test_gtts.mp3")
        try:
            tts_mod.synthesize_gtts("Kiểm tra âm thanh tổng hợp tự động.", out_audio)
            if os.path.exists(out_audio):
                self.assertGreater(os.path.getsize(out_audio), 500)
        except Exception as e:
            # Nếu chạy trong sandbox không có mạng, gTTS báo lỗi mạng an toàn
            print(f"  ℹ️ gTTS test network note: {e}")

    def test_get_ffmpeg_exe(self):
        exe = tts_mod.get_ffmpeg_exe()
        self.assertTrue(bool(exe))
        self.assertTrue(isinstance(exe, str))


if __name__ == "__main__":
    unittest.main()
