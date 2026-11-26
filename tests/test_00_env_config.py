"""
TEST MODULE 00: ENVIRONMENT & CONFIGURATION VALIDATION
======================================================
Kiểm tra:
  - Biến môi trường Python Engine (.env gốc)
  - Tính hợp lệ của config/director_config.json
  - Parity giữa .env và .env.example
  - Validation kiểu dữ liệu và range của từng config field
"""
import os
import sys
import json
import re
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(ROOT_DIR, "src")
ENV_PATH = os.path.join(ROOT_DIR, ".env")
ENV_EXAMPLE_PATH = os.path.join(ROOT_DIR, ".env.example")
CONFIG_PATH = os.path.join(ROOT_DIR, "config", "director_config.json")

sys.path.insert(0, SRC_DIR)


def _parse_env_file(path: str) -> dict:
    """Parse .env file và trả về dict key-value (bỏ qua comment và dòng rỗng)."""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    return result


class TestEnvFile(unittest.TestCase):
    """Kiểm tra file .env của Python Video Engine."""

    def setUp(self):
        self.env = _parse_env_file(ENV_PATH)

    def test_env_file_exists(self):
        """File .env phải tồn tại tại thư mục gốc."""
        self.assertTrue(os.path.exists(ENV_PATH), f".env không tìm thấy tại: {ENV_PATH}")

    def test_gemini_api_key_present_and_non_empty(self):
        """GEMINI_API_KEY phải tồn tại và không được rỗng."""
        key = self.env.get("GEMINI_API_KEY", "")
        self.assertTrue(len(key) > 0, "GEMINI_API_KEY không được rỗng.")

    def test_platform_is_valid_value(self):
        """PLATFORM phải là một trong các giá trị được hỗ trợ."""
        valid_platforms = {"threads", "documentary", "true_story"}
        platform = self.env.get("PLATFORM", "threads")
        self.assertIn(platform, valid_platforms,
                      f"PLATFORM '{platform}' không hợp lệ. Phải là: {valid_platforms}")

    def test_bg_mode_is_valid_value(self):
        """BG_MODE phải là một trong các giá trị video nền hợp lệ."""
        valid_bg_modes = {
            "minecraft", "gta5", "subway", "asmr", "split_gameplay",
            "gradient", "infinity_tunnel", "random_gameplay"
        }
        bg_mode = self.env.get("BG_MODE", "minecraft")
        self.assertIn(bg_mode, valid_bg_modes,
                      f"BG_MODE '{bg_mode}' không hợp lệ.")

    def test_bgm_volume_is_numeric_and_in_range(self):
        """BGM_VOLUME phải là số nguyên trong khoảng [0, 100]."""
        raw = self.env.get("BGM_VOLUME", "25")
        try:
            vol = int(raw)
        except ValueError:
            self.fail(f"BGM_VOLUME '{raw}' không phải số nguyên hợp lệ.")
        self.assertGreaterEqual(vol, 0, "BGM_VOLUME không được âm.")
        self.assertLessEqual(vol, 100, "BGM_VOLUME không được vượt quá 100.")

    def test_tts_rate_format(self):
        """TTS_RATE phải có định dạng như '+0%', '+15%', '-8%'."""
        rate = self.env.get("TTS_RATE", "+0%")
        self.assertRegex(rate, r'^[+-]\d+%$',
                         f"TTS_RATE '{rate}' không đúng định dạng (vd: +0%, +15%, -8%).")


class TestEnvExampleParity(unittest.TestCase):
    """Kiểm tra .env.example có đầy đủ các biến cần thiết."""

    REQUIRED_PYTHON_VARS = [
        "GEMINI_API_KEY",
        "PLATFORM",
        "LAYOUT_PRESET",
        "BG_MODE",
        "CARD_THEME",
        "TTS_VOICE",
        "TTS_RATE",
        "BGM_CHOICE",
        "BGM_VOLUME",
    ]

    def test_env_example_exists(self):
        """File .env.example phải tồn tại."""
        self.assertTrue(os.path.exists(ENV_EXAMPLE_PATH),
                        ".env.example không tìm thấy.")

    def test_env_example_contains_required_python_vars(self):
        """
        .env.example phải chứa tất cả các biến bắt buộc của Python Engine.
        Dù file nằm chung với config NestJS, các biến Python phải có mặt.
        """
        content = ""
        if os.path.exists(ENV_EXAMPLE_PATH):
            with open(ENV_EXAMPLE_PATH, encoding="utf-8") as f:
                content = f.read()
        for var in self.REQUIRED_PYTHON_VARS:
            self.assertIn(var, content,
                          f"Biến '{var}' bị thiếu trong .env.example.")


class TestDirectorConfig(unittest.TestCase):
    """Kiểm tra file config/director_config.json."""

    def setUp(self):
        self.assertTrue(os.path.exists(CONFIG_PATH),
                        f"director_config.json không tìm thấy tại: {CONFIG_PATH}")
        with open(CONFIG_PATH, encoding="utf-8") as f:
            self.cfg = json.load(f)

    def test_config_file_is_valid_json(self):
        """config/director_config.json phải parse được thành công."""
        self.assertIsInstance(self.cfg, dict)

    def test_top_level_sections_exist(self):
        """Các section bắt buộc phải có mặt trong config."""
        required_sections = ["ai_director", "audio_mastering", "emotional_pacing"]
        for sec in required_sections:
            self.assertIn(sec, self.cfg, f"Section '{sec}' bị thiếu trong director_config.json.")

    def test_ai_director_duration_mode_valid(self):
        """duration_mode phải là một trong 4 giá trị hợp lệ."""
        valid_modes = {"auto", "fast_30s", "deep_60s", "viral_story_90s"}
        mode = self.cfg.get("ai_director", {}).get("duration_mode", "auto")
        self.assertIn(mode, valid_modes,
                      f"duration_mode '{mode}' không hợp lệ.")

    def test_ai_director_comment_range_sane(self):
        """min_comments phải nhỏ hơn hoặc bằng max_comments."""
        ai = self.cfg.get("ai_director", {})
        min_c = ai.get("min_comments", 3)
        max_c = ai.get("max_comments", 6)
        self.assertGreaterEqual(min_c, 1, "min_comments phải >= 1.")
        self.assertLessEqual(min_c, max_c, "min_comments phải <= max_comments.")
        self.assertLessEqual(max_c, 10, "max_comments không nên vượt quá 10.")

    def test_ai_director_temperature_in_range(self):
        """temperature của Gemini phải nằm trong [0.0, 2.0]."""
        temp = self.cfg.get("ai_director", {}).get("temperature", 0.92)
        self.assertGreaterEqual(temp, 0.0, "temperature phải >= 0.0.")
        self.assertLessEqual(temp, 2.0, "temperature phải <= 2.0.")

    def test_ai_director_drama_archetypes_valid(self):
        """drama_archetypes chỉ được chứa 4 giá trị hợp lệ."""
        valid = {"double_twist", "thread_war", "chain_exposure", "confession_roast"}
        archetypes = self.cfg.get("ai_director", {}).get("drama_archetypes", [])
        self.assertIsInstance(archetypes, list, "drama_archetypes phải là mảng.")
        for a in archetypes:
            self.assertIn(a, valid, f"Archetype '{a}' không hợp lệ.")

    def test_audio_mastering_lufs_target_sane(self):
        """target_lufs cho vocal mastering phải âm và không quá thấp."""
        lufs = self.cfg.get("audio_mastering", {}).get("target_lufs", -14)
        self.assertLess(lufs, 0, "target_lufs phải là giá trị âm.")
        self.assertGreater(lufs, -40, "target_lufs quá thấp (< -40 LUFS) sẽ làm audio quá nhỏ.")

    def test_emotional_pacing_bgm_volume_in_range(self):
        """bgm_volume_normal và bgm_volume_duck phải trong [0.0, 1.0]."""
        pacing = self.cfg.get("emotional_pacing", {})
        vol_normal = pacing.get("bgm_volume_normal", 0.45)
        vol_duck = pacing.get("bgm_volume_duck", 0.20)
        self.assertGreaterEqual(vol_normal, 0.0)
        self.assertLessEqual(vol_normal, 1.0)
        self.assertGreaterEqual(vol_duck, 0.0)
        self.assertLessEqual(vol_duck, vol_normal,
                             "bgm_volume_duck phải <= bgm_volume_normal.")

    def test_illustration_popup_duration_sane(self):
        """popup_duration_s của Cutaway Gag phải trong [1.0, 5.0] giây."""
        illus = self.cfg.get("illustration_settings", {})
        duration = illus.get("popup_duration_s", 2.2)
        self.assertGreaterEqual(duration, 1.0,
                                "popup_duration_s quá ngắn (< 1.0s).")
        self.assertLessEqual(duration, 5.0,
                             "popup_duration_s quá dài (> 5.0s) sẽ block nội dung.")


class TestConfigModule(unittest.TestCase):
    """Kiểm tra src/config.py load và trả về config đúng."""

    def setUp(self):
        import config as config_mod
        self.config_mod = config_mod

    def test_load_config_returns_dict(self):
        """load_config() phải trả về dict."""
        cfg = self.config_mod.load_config()
        self.assertIsInstance(cfg, dict)

    def test_load_config_has_required_sections(self):
        """Config load từ file phải có đầy đủ các section."""
        cfg = self.config_mod.load_config()
        for section in ["ai_director", "audio_mastering", "emotional_pacing"]:
            self.assertIn(section, cfg)

    def test_get_config_nested_value(self):
        """get_config() phải trả về giá trị nested đúng."""
        cfg = self.config_mod.load_config()
        ai_cfg = cfg.get("ai_director", {})
        self.assertIn("model", ai_cfg)
        self.assertIsInstance(ai_cfg["model"], str)

    def test_config_fallback_to_default_when_missing(self):
        """Nếu key không tồn tại, config phải có cơ chế fallback về DEFAULT_CONFIG."""
        # DEFAULT_CONFIG luôn có key này
        self.assertIn("project_name", self.config_mod.DEFAULT_CONFIG)
        self.assertIsInstance(self.config_mod.DEFAULT_CONFIG["project_name"], str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
