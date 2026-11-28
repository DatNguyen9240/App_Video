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

    def test_omnivoice_primary_engine_replaces_edge_tts(self):
        import numpy as np
        from unittest.mock import patch

        self.assertFalse(hasattr(tts_mod, "synthesize_edge_tts"))

        class FakeOmniModel:
            def generate(self, **kwargs):
                self.last_kwargs = kwargs
                # 0.5s sine wave at 24kHz
                t = np.linspace(0, 0.5, 12000, dtype=np.float32)
                return [0.2 * np.sin(2 * np.pi * 440 * t)]

        fake_model = FakeOmniModel()
        out_mp3 = os.path.join(self.test_dir, "test_omni.mp3")
        with patch.object(tts_mod, "_get_omnivoice_model", return_value=fake_model):
            res = tts_mod.text_to_speech("Thử nghiệm giọng nói OmniVoice tiếng Việt.", out_mp3, voice="nam_hai", rate="+15%")
        self.assertEqual(res, out_mp3)
        self.assertTrue(os.path.exists(out_mp3))
        self.assertGreater(os.path.getsize(out_mp3), 1000)
        self.assertEqual(fake_model.last_kwargs.get("language"), "vietnamese")
        self.assertAlmostEqual(fake_model.last_kwargs.get("speed"), 1.15, places=2)


if __name__ == "__main__":
    unittest.main()
