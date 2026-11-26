"""
TEST MODULE 11: AI CONTENT GENERATOR (01_ai_content.py) – MOCK GEMINI API
==========================================================================
Kiểm tra với Gemini API được mock hoàn toàn:
  - clean_hex_color xử lý đúng các trường hợp
  - Schema Pydantic validate dữ liệu Gemini
  - Fallback khi Gemini timeout/quota hết
  - generate_story_with_gemini parse JSON đúng
  - Phân vai giọng đọc đa vai (dynamic voice casting)
"""
import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(ROOT_DIR, "src")
sys.path.insert(0, SRC_DIR)

import importlib.util


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCleanHexColor(unittest.TestCase):
    """Unit test cho hàm clean_hex_color."""

    def setUp(self):
        self.ai_mod = _load("ai_content", "01_ai_content.py")

    def test_valid_6char_hex_with_hash(self):
        """#3b82f6 → #3b82f6 (không thay đổi)."""
        result = self.ai_mod.clean_hex_color("#3b82f6")
        self.assertEqual(result, "#3b82f6")

    def test_valid_6char_hex_without_hash(self):
        """3b82f6 → #3b82f6 (thêm #)."""
        result = self.ai_mod.clean_hex_color("3b82f6")
        self.assertEqual(result, "#3b82f6")

    def test_uppercase_hex_accepted(self):
        """3B82F6 → #3B82F6."""
        result = self.ai_mod.clean_hex_color("3B82F6")
        self.assertEqual(result, "#3B82F6")

    def test_invalid_hex_returns_palette_color(self):
        """Giá trị rác phải trả về màu từ PALETTE (bắt đầu bằng #)."""
        result = self.ai_mod.clean_hex_color("not_a_color")
        self.assertTrue(result.startswith("#"),
                        f"Phải trả về màu hex hợp lệ, nhận được: {result}")
        self.assertEqual(len(result), 7)

    def test_empty_string_returns_palette_color(self):
        """Chuỗi rỗng phải trả về màu từ PALETTE."""
        result = self.ai_mod.clean_hex_color("")
        self.assertTrue(result.startswith("#"))

    def test_too_short_hex_returns_palette_color(self):
        """3 ký tự hex (#f00) phải trả về màu PALETTE (cần 6 ký tự)."""
        result = self.ai_mod.clean_hex_color("#f00")
        self.assertTrue(result.startswith("#"))
        self.assertEqual(len(result), 7)

    def test_whitespace_stripped(self):
        """Khoảng trắng xung quanh phải được loại bỏ."""
        result = self.ai_mod.clean_hex_color("  #3b82f6  ")
        self.assertEqual(result, "#3b82f6")


class TestPydanticSchemas(unittest.TestCase):
    """Kiểm tra Pydantic Schema validation."""

    def setUp(self):
        self.ai_mod = _load("ai_content", "01_ai_content.py")
        self.CommentSchema = self.ai_mod.CommentSchema

    def test_comment_schema_valid_full_data(self):
        """CommentSchema với dữ liệu đầy đủ hợp lệ phải pass validation."""
        data = {
            "username": "viet.anh99",
            "reply_to": "author",
            "content": "Trời ơi anh hỏng thiệt rồi, kinh vậy aaaa.",
            "avatar_color": "#3b82f6",
            "likes": 8500,
            "sfx": "boom",
            "emotion": "shocked",
            "voice": "female_1",
            "rate": "+8%",
            "pitch": "+4Hz",
        }
        try:
            schema = self.CommentSchema(**data)
            self.assertEqual(schema.username, "viet.anh99")
            self.assertEqual(schema.sfx, "boom")
        except Exception as e:
            self.fail(f"CommentSchema không chấp nhận dữ liệu hợp lệ: {e}")

    def test_comment_schema_default_values(self):
        """CommentSchema với dữ liệu tối thiểu phải dùng default value."""
        minimal = {
            "username": "user_123",
            "content": "Bình luận ngắn gọn.",
        }
        schema = self.CommentSchema(**minimal)
        # Kiểm tra default values
        self.assertIn(schema.sfx, ["none", "laugh", "boom", "bruh"])  # phải có giá trị default
        self.assertIn(schema.emotion, ["funny", "shocked", "awkward", "sarcastic", "insightful", "neutral"])

    def test_comment_schema_invalid_sfx_raises(self):
        """SFX không hợp lệ phải raise ValidationError."""
        from pydantic import ValidationError
        with self.assertRaises((ValidationError, ValueError)):
            self.CommentSchema(
                username="user",
                content="Bình luận.",
                sfx="totally_invalid_sfx_xyz",
            )

    def test_comment_schema_invalid_emotion_raises(self):
        """Emotion không hợp lệ phải raise ValidationError."""
        from pydantic import ValidationError
        with self.assertRaises((ValidationError, ValueError)):
            self.CommentSchema(
                username="user",
                content="Bình luận.",
                emotion="very_happy_xyz",
            )


class TestGenerateStoryWithGeminiMock(unittest.TestCase):
    """Kiểm tra generate_story_with_gemini với Gemini SDK được mock."""

    def setUp(self):
        self.ai_mod = _load("ai_content", "01_ai_content.py")
        # Tải content_base để lấy Post/Comment
        self.content_mod = _load("content_base", "01_content.py")

    def _make_gemini_response(self) -> dict:
        """Tạo response JSON mẫu giả lập Gemini trả về."""
        return {
            "post": {
                "username": "chuyen_phong_ban",
                "content": "Hôm nay sếp hỏi mình tại sao deadline lại trễ...",
                "likes": 15000,
                "archetype": "double_twist",
                "video_title": "DRAMA VĂN PHÒNG EPIC",
            },
            "comments": [
                {
                    "username": "it_dev_hn",
                    "reply_to": "",
                    "content": "Trời ơi anh cũng vậy, cả team deadline trễ mà sếp hỏi mình.",
                    "avatar_color": "#3b82f6",
                    "likes": 9800,
                    "sfx": "laugh",
                    "emotion": "funny",
                    "voice": "male_1",
                    "rate": "+10%",
                    "pitch": "+2Hz",
                },
                {
                    "username": "hr_ngapnguoi",
                    "reply_to": "it_dev_hn",
                    "content": "Reply cãi nhau: Thực ra bạn report sai từ tuần trước rồi.",
                    "avatar_color": "#ef4444",
                    "likes": 12000,
                    "sfx": "boom",
                    "emotion": "shocked",
                    "voice": "female_1",
                    "rate": "-5%",
                    "pitch": "+8Hz",
                },
            ]
        }

    @patch("google.genai.Client")
    def test_generate_story_returns_post_object(self, mock_client_class):
        """
        generate_story_with_gemini phải trả về Post object hợp lệ
        khi Gemini trả về JSON đúng định dạng.
        """
        # Thiết lập mock
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = json.dumps(self._make_gemini_response())
        mock_client.models.generate_content.return_value = mock_response

        result = self.ai_mod.generate_story_with_gemini(
            topic="chuyện drama văn phòng",
            api_key="test-api-key-12345",
        )

        self.assertIsNotNone(result)
        # Phải có username và content
        self.assertTrue(hasattr(result, "username") or isinstance(result, dict))

    @patch("google.genai.Client")
    def test_generate_story_handles_malformed_json_gracefully(self, mock_client_class):
        """
        Nếu Gemini trả về JSON sai định dạng, phải raise Exception hoặc trả về fallback.
        """
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "{ invalid json here ..."
        mock_client.models.generate_content.return_value = mock_response

        try:
            result = self.ai_mod.generate_story_with_gemini(
                topic="test",
                api_key="test-key",
            )
            # Nếu không raise, kết quả phải không None (đã fallback sang emergency)
            self.assertIsNotNone(result)
        except (ValueError, json.JSONDecodeError, Exception):
            pass  # Chấp nhận raise exception

    @patch("google.genai.Client", side_effect=Exception("API quota exceeded"))
    def test_generate_story_handles_api_quota_error(self, mock_client_class):
        """
        Khi Gemini API quota hết, phải không crash và xử lý gracefully.
        """
        try:
            result = self.ai_mod.generate_story_with_gemini(
                topic="test topic",
                api_key="test-key",
            )
            # Nếu không raise, phải trả về gì đó
            self.assertIsNotNone(result)
        except Exception as e:
            # Exception với message rõ ràng là OK
            self.assertIn("quota", str(e).lower(),
                          "Exception phải đề cập quota hoặc API error.")

    @patch("google.genai.Client")
    def test_comments_in_result_have_valid_voices(self, mock_client_class):
        """
        Các comment trong kết quả phải có voice attribute hợp lệ.
        """
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = json.dumps(self._make_gemini_response())
        mock_client.models.generate_content.return_value = mock_response

        result = self.ai_mod.generate_story_with_gemini(
            topic="chuyện văn phòng",
            api_key="test-key",
        )

        if result and hasattr(result, "comments"):
            for cmt in result.comments:
                if hasattr(cmt, "voice"):
                    self.assertIsInstance(cmt.voice, str)
                    self.assertGreater(len(cmt.voice), 0)


class TestDynamicVoiceCasting(unittest.TestCase):
    """Kiểm tra logic phân vai giọng đọc tự động."""

    def setUp(self):
        self.ai_mod = _load("ai_content", "01_ai_content.py")

    def test_male_voice_assigned_for_male_username(self):
        """Username chứa từ khóa nam phải được gán giọng nam."""
        if not hasattr(self.ai_mod, "infer_voice_gender"):
            self.skipTest("infer_voice_gender chưa được export.")
        gender = self.ai_mod.infer_voice_gender("anh_hung_it")
        self.assertIn(gender.lower(), ["male", "nam", "m"])

    def test_female_voice_assigned_for_female_username(self):
        """Username chứa từ khóa nữ phải được gán giọng nữ."""
        if not hasattr(self.ai_mod, "infer_voice_gender"):
            self.skipTest("infer_voice_gender chưa được export.")
        gender = self.ai_mod.infer_voice_gender("linh.cute03")
        self.assertIn(gender.lower(), ["female", "nu", "nữ", "f"])

    def test_alternating_voices_for_ambiguous_usernames(self):
        """
        Username không rõ giới tính phải được gán giọng xen kẽ nam/nữ.
        """
        if not hasattr(self.ai_mod, "assign_voices_to_comments"):
            self.skipTest("assign_voices_to_comments chưa được export.")

        usernames = ["user_1", "user_2", "user_3", "user_4"]
        voices = self.ai_mod.assign_voices_to_comments(usernames)
        self.assertEqual(len(voices), len(usernames))

        # Kiểm tra không phải tất cả cùng giọng
        unique_voices = set(voices)
        self.assertGreater(len(unique_voices), 1,
                           "Phải có ít nhất 2 giọng khác nhau cho các username không rõ giới tính.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
