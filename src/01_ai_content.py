"""
MODULE 1B: SINH BÀI ĐĂNG TỰ ĐỘNG BẰNG GOOGLE GEMINI AI (ĐẠO DIỄN CẢM XÚC)
==========================================================================
Chức năng:
  - Sử dụng Gemini API (gemini-2.5-flash) để sáng tạo kịch bản Threads chuẩn đồ thị cảm xúc
  - Tự động gán vai diễn, giọng đọc đa vai (Nam/Nữ), nhịp thở (pauses), và hiệu ứng SFX/Camera
  - Không hardcode: Tải toàn bộ prompt, vai diễn và tham số từ src/config.py
  - Hỗ trợ progress callback để cập nhật trực tiếp lên Tab Giám Sát Quy Trình
"""

import os
import sys
import json
import random
import re
from typing import Literal, Optional, Callable
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(BASE_DIR, "src")
ENV_PATH = os.path.join(BASE_DIR, ".env")
OUT_DIR  = os.path.join(BASE_DIR, "output")

sys.path.insert(0, SRC_DIR)
import config as config_mod
import importlib.util

spec = importlib.util.spec_from_file_location("content_base", os.path.join(SRC_DIR, "01_content.py"))
content_base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(content_base)

Post = content_base.Post
Comment = content_base.Comment

PALETTE = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#06b6d4"]


def clean_hex_color(c: str) -> str:
    """Đảm bảo chuỗi màu luôn là mã hex 6 ký tự hợp lệ."""
    c = str(c).strip()
    if re.match(r"^#?[0-9a-fA-F]{6}$", c):
        return c if c.startswith("#") else f"#{c}"
    return random.choice(PALETTE)


# ─── ĐỊNH NGHĨA SCHEMA JSON CHO GEMINI (CÓ METADATA ĐẠO DIỄN) ────────────────

class CommentSchema(BaseModel):
    username: str = Field(description="Tên người dùng hài hước tiếng Việt hoặc Gen Z, ví dụ: hr_ngapnguoi, dev_khocnhe, linh.cute03, tuan_it_98")
    reply_to: str = Field(default="", description="Tên username mà bình luận này đang trả lời (có thể là tác giả HOẶC username của bình luận trước để tạo hội thoại cãi nhau/đối đáp)")
    content: str = Field(description="Bình luận tiếng Việt tự nhiên (14-22 từ), tập trung 100% vào đúng tình huống của tác giả. Tuyệt đối không tự ý kể chuyện lan man sang đồ vật hoặc đề tài khác.")
    avatar_color: str = Field(default="#3b82f6", description="Mã màu hex (vd: #3b82f6, #ef4444, #10b981, #f59e0b)")
    likes: int = Field(default=4500, description="Lượt thích ước tính từ 1500 đến 25000")
    sfx: Literal["none", "laugh", "bruh", "boom", "sub_drop", "shock", "huh", "side_eye", "ting"] = Field(
        default="none",
        description="Hiệu ứng âm thanh và meme reaction điểm nhấn. Phân bổ 2-3 comment có SFX tương ứng cảm xúc ('laugh' cho cười, 'bruh'/'huh' cho cạn lời ngơ ngác, 'side_eye' cho liếc xéo cà khịa, 'boom'/'sub_drop' cho bẻ lái rung lắc, 'ting' cho phát hiện đắt giá)."
    )
    emotion: Literal["funny", "shocked", "awkward", "sarcastic", "insightful", "neutral"] = Field(
        default="funny", description="Sắc thái cảm xúc của câu nói"
    )
    pause_before_ms: int = Field(default=220, description="Khoảng lặng tự nhiên trước khi nói (180-300ms)")
    pause_after_ms: int = Field(default=280, description="Khoảng lặng tự nhiên sau khi dứt câu (200-350ms)")
    camera_motion: Literal["none", "punch_in", "shake"] = Field(
        default="none", description="Hiệu ứng chuyển động hình ảnh ('punch_in' phóng to nhẹ, 'shake' rung lắc khi sốc)"
    )


class PostSchema(BaseModel):
    username: str = Field(description="Tên tác giả bài viết, ví dụ: genz.tamsu, chuyen.congso, dev_nghiep")
    content: str = Field(description="Câu chuyện Threads mở đầu cuốn hút, có bối cảnh chi tiết và tình huống rõ ràng (từ 35 đến 50 từ, 2-3 câu nhịp điệu mềm mại, nói trong 12-15 giây). Bắt đầu bằng Hook giật mình tò mò, nêu ngay mâu thuẫn cốt lõi, không dài dòng lan man.")
    avatar_color: str = Field(default="#8b5cf6", description="Mã màu hex cho avatar tác giả")
    is_verified: bool = Field(default=True, description="True nếu có tích xanh")
    likes: int = Field(default=35000, description="Lượt thích ước tính từ 15000 đến 85000")
    video_title: str = Field(default="", description="Tiêu đề video ngắn gọn, giật gân hài hước")
    video_caption: str = Field(default="", description="Đoạn caption đăng TikTok/Reels cuốn hút kèm câu hỏi kêu gọi bình luận")
    hashtags: str = Field(default="", description="Chuỗi 5-7 hashtag viral")
    illustration_prompt: str = Field(
        default="",
        description="2-3 từ danh từ tiếng Anh ngắn gọn, cụ thể chỉ đích danh vật phẩm hoặc sự việc đời thực (ví dụ: 'car wash brush', 'air fryer', 'laptop desk', 'burnt food', 'parcel delivery box'). TUYỆT ĐỐI KHÔNG ghép 2 địa điểm/hành động khác nhau (không viết 'car wash brush, kitchen floor')."
    )
    illustration_vietnamese_query: str = Field(
        default="",
        description="2-3 từ danh từ tiếng Việt ngắn gọn chỉ đích danh vật phẩm (ví dụ: 'cọ rửa xe', 'nồi chiên không dầu', 'bàn làm việc', 'đơn hàng giao tới')."
    )
    illustration_category: Literal["couple_photo_fail", "cafe_lifestyle", "food_cooking", "office_work", "shopping_tech", "student_exam", "general_life"] = Field(
        default="general_life",
        description="Phân loại ngữ cảnh chính xác của câu chuyện"
    )
    has_visual_proof: bool = Field(
        default=True,
        description="LUÔN ĐẶT TRUE: Bắt buộc cung cấp ảnh minh họa bằng chứng thực tế khớp với đồ vật, tình huống hoặc sự việc trong câu chuyện để video trực quan sinh động."
    )
    visual_proof_placement: Literal["popup_at_twist", "attached_in_post"] = Field(
        default="popup_at_twist",
        description="Vị trí hiển thị ảnh: 'popup_at_twist' (bung ảnh nổi ở cú bẻ lái) hoặc 'attached_in_post' (gắn trực tiếp dưới bài viết tác giả)"
    )
    sentence_reveal_mode: Literal["progressive_line_reveal", "standard_full"] = Field(
        default="progressive_line_reveal",
        description="ĐẠO DIỄN NGHỆ THUẬT: Mặc định chọn 'progressive_line_reveal' để mở màn từng câu bài viết theo nhịp đọc của giọng nói, giúp video cuốn hút, giữ chân người xem từng giây!"
    )
    illustration_caption: str = Field(
        default="",
        description="Câu chú thích hài hước tiếng Việt ngắn (dưới 8 từ) làm điểm nhấn cho bức ảnh bằng chứng (ví dụ: 'Thành quả bạn trai chụp', 'Bữa tối định mệnh', 'Hóa đơn tháng này')"
    )
    satisfying_category: Literal["food", "gameplay", "asmr", "puzzle", "hypnotic", "kinetic_sand"] = Field(
        default="gameplay",
        description="VIDEO NỀN THỎA MÃN Ở GIỮA: 'food' (ẩm thực, nấu nướng, đồ ngọt, cafe), 'gameplay' (IT, cãi vã, hành động, tốc độ), 'asmr' (tâm sự, cảm xúc, đêm muộn), 'puzzle' (trinh thám, logic, bóc phốt, bắt quả tang), 'hypnotic' (kỳ bí, thôi miên), 'kinetic_sand' (vui nhộn, thư giãn)."
    )
    recommended_theme: Literal["light", "dark"] = Field(
        default="light",
        description="THEME SÁNG / TỐI: 'dark' nếu là chuyện đêm muộn (nửa đêm, 2h sáng), rình mò, IT trực đêm, bí mật ngầm; 'light' nếu là chuyện ban ngày, quán cafe, công sở, mua sắm vui vẻ."
    )
    story_context_type: Literal["office_workplace", "student_youth", "couple_family", "general_social"] = Field(
        default="general_social",
        description="BỐI CẢNH ĐỂ GÁN AVATAR: 'office_workplace' (công sở, sếp, đồng nghiệp), 'student_youth' (sinh viên, học đường, gen Z), 'couple_family' hoặc 'general_social'."
    )
    cta_question: str = Field(
        default="",
        description="CÂU HỎI KÊU GỌI BÌNH LUẬN VIRAL Ở OUTRO (2.4s cuối): Câu hỏi mở gợi đòn tranh luận riêng cho bài viết (dưới 15 từ, vd: 'Người yêu bạn có chụp ảnh có tâm không hay cũng out nét giống vậy? 😂', 'Gặp sếp này bạn có dám gửi link đối thủ như ông senior không?')"
    )
    pacing_tempo: Literal["normal", "slow_emotional", "fast_comedic"] = Field(
        default="normal",
        description="NHỊP ĐỘ ĐỌC: 'slow_emotional' (-3% đến +5%) cho tâm sự lắng đọng; 'fast_comedic' (+22% đến +26%) cho cãi vã, tấu hài nhanh."
    )
    emotional_arc: str = Field(default="Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời", description="Tóm tắt đường cong cảm xúc của video")
    hook_energy: Literal["high", "intense", "curious"] = Field(default="high", description="Mức năng lượng mở màn")
    comments: list[CommentSchema] = Field(
        description="Danh sách từ 7 đến 8 bình luận tiếng Việt tạo thành chuỗi drama/hội thoại cuốn hút dài trên 1 phút, tất cả cùng bàn luận, cà khịa, đối đáp xoay quanh tình huống của tác giả"
    )


# ─── QUẢN LÝ GEMINI API KEY ──────────────────────────────────────────────────

def load_gemini_api_key() -> str:
    """Đọc API Key từ môi trường hoặc file .env."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key

    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return ""


def save_gemini_api_key(key: str):
    """Lưu API Key vào file .env."""
    key = key.strip()
    lines = []
    found = False
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("GEMINI_API_KEY="):
                    lines.append(f"GEMINI_API_KEY={key}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"GEMINI_API_KEY={key}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"[✓] Đã lưu API key vào: {ENV_PATH}")


def get_or_prompt_api_key() -> str:
    """Lấy API key, nếu chưa có thì hỏi người dùng nhập."""
    key = load_gemini_api_key()
    if key:
        return key

    print("\n" + "=" * 60)
    print("🔑 CHƯA TÌM THẤY GEMINI API KEY")
    print("=" * 60)
    print("Bạn có thể lấy API Key hoàn toàn MIỄN PHÍ tại:")
    print("👉 https://aistudio.google.com/app/apikey")
    print("=" * 60)
    new_key = input("Nhập Gemini API Key của bạn: ").strip()
    if not new_key:
        raise ValueError("API Key không được để trống!")
    save_gemini_api_key(new_key)
    return new_key


# ─── GỌI GEMINI ĐỂ SINH NỘI DUNG ──────────────────────────────────────────────

def generate_story_with_gemini(
    topic: str = "",
    duration_mode: str = None,
    target_comment_count: Optional[int] = None,
    api_key: str = None,
    progress_cb: Optional[Callable[[str, int], None]] = None
) -> Post:
    """
    Sử dụng Gemini API với cấu hình đạo diễn cảm xúc để tạo kịch bản Threads hoàn chỉnh.
    Hỗ trợ linh hoạt số lượng bình luận (4-8+ câu) và đa dạng hóa đồ thị drama.
    Tự động phân vai giọng đọc (Adam, Brian, Nam Minh, Hoài My...) và thiết lập khoảng lặng.
    """
    if progress_cb:
        progress_cb("Khởi tạo kết nối Gemini AI...", 10)

    if not api_key:
        api_key = get_or_prompt_api_key()

    cfg = config_mod.load_config()
    ai_cfg = cfg.get("ai_director", {})
    voice_cfg = cfg.get("voice_casting", {})

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})

    if not topic:
        # Ưu tiên lấy từ danh sách trend Threads được fetch mỗi ngày
        try:
            radar_spec = importlib.util.spec_from_file_location("threads_radar", os.path.join(SRC_DIR, "threads_radar.py"))
            radar_mod = importlib.util.module_from_spec(radar_spec)
            radar_spec.loader.exec_module(radar_mod)
            daily_res = radar_mod.get_daily_trending_topics()
            if daily_res.get("topics"):
                topic = random.choice(daily_res["topics"])
        except Exception:
            pass

    if not topic:
        topic = "câu chuyện thịnh hành và gây sốt nhất hôm nay trên mạng xã hội Threads"

    system_instruction = ai_cfg.get("system_instruction", "")
    model_primary = ai_cfg.get("model", "gemini-3.5-flash")
    model_fallback = ai_cfg.get("fallback_model", "gemini-3.5-flash-lite")
    temperature = float(ai_cfg.get("temperature", 0.92))
    dur_mode = duration_mode or ai_cfg.get("duration_mode", "auto")

    if target_comment_count and int(target_comment_count) > 0:
        c_count = int(target_comment_count)
        cmt_guide = (
            f"SỐ LƯỢNG BÌNH LUẬN: BẮT BUỘC TẠO CHÍNH XÁC {c_count} BÌNH LUẬN (từ C1 đến C{c_count}). Tuyệt đối không được ít hơn hay nhiều hơn!\n"
            f"- TẤT CẢ BÌNH LUẬN PHẢI BÀN VỀ CÂU CHUYỆN CỦA TÁC GIẢ: Mọi người cùng xúm lại bàn tán, cà khịa, đối đáp xoay quanh đúng sự việc đó.\n"
            f"- C1: Reaction mở đầu đồng cảm / ngạc nhiên (reply_to username tác giả, sfx='none').\n"
            f"- C2 đến C{max(2, c_count - 2)}: Đối đáp, tranh cãi, bóc thóp sơ hở của nhau hoặc của tác giả (dùng reply_to để các comment đối đáp liên hoàn).\n"
            f"- C{max(2, c_count - 1)}: Cú bẻ lái sốc hoặc phát hiện đắt giá (sfx='sub_drop' hoặc 'none').\n"
            f"- C{c_count}: Câu chốt hạ cạn lời / dí dỏm (sfx='none')."
        )
    elif dur_mode == "fast_30s":
        cmt_guide = (
            "SỐ LƯỢNG BÌNH LUẬN: ĐÚNG 4 BÌNH LUẬN (Thời lượng ngắn 30-40s):\n"
            "- C1: Đồng cảm / reaction hài hước về câu chuyện tác giả (reply_to tác giả, sfx='none').\n"
            "- C2: Bắt thóp chi tiết hoặc trêu chọc tác giả (reply C1, sfx='none').\n"
            "- C3: Cú bẻ lái bất ngờ về sự việc (sfx='sub_drop' hoặc 'none').\n"
            "- C4: Chốt hạ cạn lời dí dỏm (sfx='none')."
        )
    else:  # "auto", "deep_60s", "viral_story_90s"
        cmt_guide = (
            "YÊU CẦU ĐỘ DÀI: VIDEO BẮT BUỘC DÀI TỐI THIỂU 1 PHÚT (TỪ 60S ĐẾN 75S - ĐẠT CHUẨN KIẾM TIỀN TIKTOK CREATOR REWARDS PROGRAM)!\n"
            "SỐ LƯỢNG BÌNH LUẬN: BẮT BUỘC ĐỦ TỪ 7 ĐẾN 8 BÌNH LUẬN (Tạo chuỗi đối đáp nhiều tầng sâu sắc, tuyệt đối không được dưới 7 bình luận):\n"
            "- NGUYÊN TẮC QUAN TRỌNG NHẤT: 100% BÌNH LUẬN PHẢI BÀN LUẬN, TRANH CÃI, CÀ KHỊA VỀ ĐÚNG SỰ VIỆC CỦA TÁC GIẢ. TUYỆT ĐỐI KHÔNG để mỗi người kể một câu chuyện cá nhân lan man sang đồ vật/chủ đề khác!\n"
            "- C1: Reaction mở đầu trêu chọc / ngạc nhiên (reply tác giả, sfx='none').\n"
            "- C2: Người thứ 2 vào chất vấn hoặc thắc mắc thêm chi tiết éo le (reply C1 hoặc tác giả, sfx='none').\n"
            "- C3: Bắt thóp chi tiết sơ hở để cà khịa (reply C2 hoặc tác giả, sfx='none').\n"
            "- C4: Tác giả hoặc người chứng kiến vào phân trần / đính chính tình tiết oái oăm (reply C3, sfx='none').\n"
            "- C5: Bình luận viên thứ 5 vào tiếp lời chọc cười, phân tích độ bất lực (reply C4, sfx='none').\n"
            "- C6: Cú BẺ LÁI BẤT NGỜ đảo ngược suy nghĩ của mọi người (sfx='sub_drop' hoặc 'none').\n"
            "- C7: Đúc kết cạn lời / nhận xét đỉnh cao (sfx='ting' hoặc 'none').\n"
            "- C8 (nếu có): Câu chốt hạ dí dỏm khiến người xem bật cười thả tim (sfx='none').\n"
            "- ÂM THANH SFX TINH TẾ: 90% bình luận để sfx='none'. Tuyệt đối KHÔNG spam âm thanh hoạt hình nhảm nhí!"
        )

    prompt = (
        f"Bạn là người viết Threads chuyên nghiệp (SKILL: THREADS HUMAN GEN Z WRITER).\n"
        f"Hãy tạo 1 kịch bản bài đăng Threads và các bình luận bằng tiếng Việt thật tự nhiên, trau chuốt như người thật đang chia sẻ trải nghiệm với bạn bè.\n"
        f"Chủ đề: '{topic}'\n"
        f"Chế độ kịch bản: {dur_mode}\n\n"
        f"QUY TẮC CỐT LÕI (PHẢI GIỐNG 100% NGƯỜI THẬT KỂ CHUYỆN - ĐẠT THỜI LƯỢNG ÍT NHẤT 1 PHÚT):\n"
        f"- BÀI VIẾT CHÍNH: Kể mở đầu một câu chuyện rất đời thực, cuốn hút và có bối cảnh chi tiết (khoảng 35-50 từ, 2-3 câu nhịp điệu mềm mại, nói trong 12-15 giây), nêu rõ vật phẩm/sự việc cụ thể khiến người đọc cảm thấy 'sao giống mình vậy' và muốn dừng lại xem hết.\n"
        f"{cmt_guide}\n"
        f"- TÍNH ĐỒNG NHẤT CỦA BÌNH LUẬN: Tất cả các comment phải xoay quanh câu chuyện và mâu thuẫn của bài viết chính. Đừng chuyển đề tài.\n"
        f"- CÁC BÌNH LUẬN: Mỗi bình luận từ 14-22 từ, mang góc nhìn chân thật của bạn bè mạng xã hội.\n"
        f"- NGÔN NGỮ GENZ TINH TẾ: Dùng từ ngữ tự nhiên của giới trẻ nhưng VỪA ĐỦ, tuyệt đối KHÔNG nhồi nhét tiếng lóng gượng ép, KHÔNG cố tỏ ra hài hước rẻ tiền.\n"
        f"- EMOJI VỪA PHẢI: Tối đa 0-1 emoji mỗi câu khi thật sự cần thiết (tránh spam emoji).\n"
        f"- USERNAME: Tên username tiếng Việt đời thường tự nhiên (ví dụ: 'linh.nguyen99', 'minh.triet01', 'hoang_anh.hr', 'tuan.oto').\n"
        f"- REPLY_TO: Ghi rõ username đang trả lời để tạo sự đối đáp sống động.\n"
        f"- HÌNH ẢNH MINH HỌA BẰNG CHỨNG (LUÔN BẬT has_visual_proof = true):\n"
        f"  + Luôn cung cấp từ khóa tìm ảnh chân thực (illustration_vietnamese_query và illustration_prompt) là 2-3 TỪ DANH TỪ CỤ THỂ chỉ đích danh vật phẩm hoặc bằng chứng đời thực (ví dụ: 'cọ rửa xe' / 'car wash brush', 'vết trầy xe' / 'car scratch', 'nồi chiên không dầu' / 'air fryer'). Tuyệt đối không ghép 2 địa điểm/hành động khác nhau.\n"
        f"  + ĐỒNG BỘ ĐỐI ĐÁP VÀ HÌNH ẢNH KHỚP NHAU 100%:\n"
        f"    Nếu có 1 bình luận hỏi xin ảnh/bằng chứng (ví dụ C1 hoặc C2: 'Có hình ảnh gì không bạn?'), thì bình luận TIẾP THEO (câu trả lời) BẮT BUỘC phải trực tiếp giới thiệu bằng chứng (ví dụ: 'Đây nè, tui chụp lại cái cọ này nè...', 'Nhìn cái vết trầy xót ruột chưa nè...'). Thẻ ảnh popup sẽ bung lên CHÍNH XÁC trong lúc câu trả lời này cất tiếng!\n"
        f"  + illustration_caption: Viết câu chú thích ngắn gọn (dưới 8 từ) mô tả đúng bằng chứng trong câu trả lời (ví dụ: 'Cận cảnh cây cọ rửa xe xoay', 'Chiếc xe sau khi cọ rửa').\n"
        f"  + Chọn visual_proof_placement = 'popup_at_twist' (bung ảnh nổi tại câu trả lời/cú bẻ lái) hoặc 'attached_in_post' (gắn trong bài).\n"
        f"  + Chọn illustration_category: couple_photo_fail, cafe_lifestyle, food_cooking, office_work, shopping_tech, student_exam, general_life.\n"
        f"- PHONG CÁCH HIỆN CÂU (SENTENCE REVEAL): Mặc định chọn sentence_reveal_mode = 'progressive_line_reveal' để từng dòng mở ra theo nhịp đọc của giọng nói, tạo cảm giác cuốn hút và giữ chân người xem!\n"
        f"- HIỆU ỨNG MEME, SFX & CAMERA MOTION: Phân bổ 2 đến 3 comment có SFX tương ứng cảm xúc để kích hoạt MEME REACTION (sfx='laugh' cho meme cười, sfx='bruh'/'huh' cho bất lực ngơ ngác, sfx='side_eye' cho liếc xéo bóc mẽ, sfx='sub_drop'/'boom' cho bẻ lái rung lắc camera, sfx='ting' cho mách nước đắt giá).\n"
        f"- KHỚP VIDEO NỀN Ở GIỮA (satisfying_category): Chọn 'food' nếu nói về ăn uống/bếp núc; 'gameplay' nếu là IT/công sở/gay cấn; 'asmr' nếu là tâm sự chia sẻ; 'puzzle' nếu là trinh thám/bóc phốt.\n"
        f"- GIAO DIỆN SÁNG/TỐI (recommended_theme): Chọn 'dark' nếu là chuyện đêm muộn/bí mật rình mò/IT trực đêm; 'light' nếu là chuyện ban ngày/cafe/mua sắm.\n"
        f"- CÂU HỎI OUTRO VIRAL (cta_question): Nghĩ 1 câu hỏi mở sắc sảo ở 2.4s cuối đánh trúng tim đen để kích thích người xem nhảy vào bình luận.\n"
        f"- TUYỆT ĐỐI TRÁNH: Không viết như AI giải thích, không mở đầu sáo rỗng, không kết bài bằng đạo lý rập khuôn.\n\n"
        f"TIÊU CHUẨN KIỂM DUYỆT:\n"
        f"Trước khi trả kết quả, tự hỏi: 'Người thật có nói câu này ngoài đời không? Tất cả bình luận có cùng bàn về tình huống của tác giả không? Thời lượng nói có đủ dài trên 1 phút (từ 7 đến 8 bình luận) không?'\n\n"
        f"VÍ DỤ TONE THAM KHẢO (Đồng nhất chủ đề, đủ 7 bình luận đối đáp liên hoàn, dài trên 1 phút):\n"
        f"Bài: 'Chồng tui bảo mua đèn ngủ thông minh cảm biến âm thanh cho lãng mạn. Giờ đêm trở mình đập muỗi một cái là phòng sáng rực như sân vận động Mỹ Đình, ổng ngáy một tiếng là đèn tự đổi bảy màu lộng lẫy 😭'\n"
        f"- C1: 'Rồi lúc hai vợ chồng cãi nhau cái đèn có chớp tắt báo động đỏ như vũ trường không bà? 😂' (reply tác giả, sfx='none')\n"
        f"- C2: 'Mà cái đèn đó cảm biến nhạy vậy thì tiếng ngáy của ổng chắc phải ngang ngửa còi xe container' (reply C1, sfx='none')\n"
        f"- C3: 'Ủa rồi lỡ nói mớ gọi tên nyc trong mơ thì đèn có hú còi cứu hỏa báo động không?' (reply C2, sfx='none')\n"
        f"- C4: 'Hôm qua ổng ho nhẹ một tiếng mà đèn nháy EDM liên tục làm cả nhà tưởng sắp có động đất' (reply C3, sfx='none')\n"
        f"- C5: 'Mua đèn tạo không gian chill ai dè rước thêm cái vũ trường mini về tra tấn tinh thần' (reply C4, sfx='none')\n"
        f"- C6: 'Sáng nay tui phát hiện ổng lén tháo pin giấu vào ngăn kéo rồi, bảo mệt tim quá chịu hết nổi' (reply C5, sfx='sub_drop')\n"
        f"- C7: 'Đèn thì thông minh mà người mua thì bất lực toàn tập luôn' (reply C6, sfx='none')\n"
    )

    if progress_cb:
        progress_cb(f"Gemini AI ({model_primary}) đang sáng tạo kịch bản...", 30)

    models_to_try = [model_primary, model_fallback, "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
    # Loại bỏ trùng lặp giữ nguyên thứ tự
    seen = set()
    models_to_try = [x for x in models_to_try if x and not (x in seen or seen.add(x))]
    data = None
    last_err = None

    import time
    for m in models_to_try:
        if data:
            break
        for attempt in range(1, 4):
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=PostSchema,
                        temperature=temperature,
                    )
                )
                data = json.loads(response.text)
                break
            except Exception as e:
                last_err = e
                err_str = str(e)
                is_transient = any(k in err_str for k in ["503", "429", "UNAVAILABLE", "high demand", "temporarily", "RESOURCE_EXHAUSTED", "overloaded"])
                if is_transient and attempt < 3:
                    wait_s = attempt * 2.0
                    if progress_cb:
                        progress_cb(f"Máy chủ AI ({m}) tải cao (503). Đang tự động thử lại sau {wait_s:.0f}s...", 35)
                    time.sleep(wait_s)
                else:
                    break

    if not data:
        raise RuntimeError(f"Lỗi khi gọi Gemini API: {last_err}")

    if progress_cb:
        progress_cb("Phân bổ vai diễn & nhịp điệu cảm xúc...", 70)

    # Phân bổ vai diễn giọng đọc từ cấu hình (Dynamic Multi-Voice Casting)
    roles = voice_cfg.get("roles", {})
    author_role = roles.get("author", {})
    female_presets = voice_cfg.get("female_voice_presets", [
        {"voice": "vi-VN-HoaiMyNeural", "rate": "+20%", "pitch": "+6Hz", "style": "cheerful"},
        {"voice": "ava", "rate": "+16%", "pitch": "+4Hz",  "style": "chat"},
        {"voice": "vi-VN-HoaiMyNeural", "rate": "+24%", "pitch": "+10Hz", "style": "excited"},
        {"voice": "emma", "rate": "+14%", "pitch": "+2Hz",  "style": "sad"}
    ])
    male_presets = voice_cfg.get("male_voice_presets", [
        {"voice": "adam", "rate": "+14%", "pitch": "-4Hz", "style": "dramatic"},
        {"voice": "vi-VN-NamMinhNeural", "rate": "+20%", "pitch": "+4Hz",  "style": "cheerful"},
        {"voice": "brian", "rate": "+16%", "pitch": "-2Hz",  "style": "chat"},
        {"voice": "vi-VN-NamMinhNeural", "rate": "+22%", "pitch": "+2Hz",  "style": "angry"}
    ])

    female_keywords = {"linh", "trang", "mai", "be", "hoa", "ngoc", "lan", "anh", "girl", "cute", "thao", "vy", "my", "phuong", "giang", "nhung", "quynh", "huyen", "nga"}
    male_keywords = {"nam", "tuan", "duc", "boy", "dev", "minh", "hoang", "khoa", "huy", "kien", "dat", "quan", "hung", "long", "thanh", "hai", "son", "dung"}

    # Phân nhóm kho avatar người thật theo bối cảnh câu chuyện
    ctx_type = data.get("story_context_type", "general_social")
    if ctx_type == "office_workplace":
        f_avatar_pool = ["female_office_1.jpg", "female_office_2.jpg", "female_office_3.jpg"]
        m_avatar_pool = ["male_office_1.jpg", "male_office_2.jpg", "male_office_3.jpg"]
    elif ctx_type == "student_youth":
        f_avatar_pool = ["female_student_1.jpg", "female_student_2.jpg", "female_student_3.jpg"]
        m_avatar_pool = ["male_student_1.jpg", "male_student_2.jpg", "male_student_3.jpg"]
    else:
        f_avatar_pool = ["female_office_1.jpg", "female_office_2.jpg", "female_student_1.jpg", "female_student_2.jpg", "female_student_3.jpg", "female_office_3.jpg"]
        m_avatar_pool = ["male_office_1.jpg", "male_office_2.jpg", "male_student_1.jpg", "male_student_2.jpg", "male_student_3.jpg", "male_office_3.jpg"]

    comments = []
    raw_comments = data.get("comments", [])
    author_username = data.get("username", "threads.user").strip().lower()
    author_is_female = any(k in author_username for k in female_keywords)
    author_avatar_default = f_avatar_pool[0] if author_is_female else m_avatar_pool[0]
    if author_is_female:
        author_voice_final = author_role.get("voice") if (author_role.get("voice") and author_role.get("voice") != "adam") else "vi-VN-HoaiMyNeural"
    else:
        author_voice_final = author_role.get("voice") or voice_cfg.get("default_author_voice", "adam")

    for idx, c in enumerate(raw_comments, 1):
        u_name = c.get("username", f"user_{idx}").strip().lower()
        role_key = f"commenter_{idx}"
        role_info = roles.get(role_key, {})

        # Tác giả tự trả lời trong phần bình luận: Giữ nguyên giới tính, avatar và giọng nói của tác giả!
        if u_name == author_username:
            is_female = author_is_female
            assigned_voice = author_voice_final
            c_avatar_file = author_avatar_default
        else:
            # Tự động nhận diện giới tính theo username hoặc xen kẽ tự nhiên
            is_female = any(k in u_name for k in female_keywords)
            if not is_female and not any(k in u_name for k in male_keywords):
                is_female = (idx % 2 == 1)

            preset_list = female_presets if is_female else male_presets
            preset = preset_list[((idx - 1) // 2) % len(preset_list)]
            assigned_voice = c.get("voice_id") or role_info.get("voice") or preset.get("voice", "vi-VN-HoaiMyNeural" if is_female else "vi-VN-NamMinhNeural")
            chosen_c_pool = f_avatar_pool if is_female else m_avatar_pool
            c_avatar_file = chosen_c_pool[(idx - 1) % len(chosen_c_pool)]

        raw_sfx = str(c.get("sfx", "none")).lower().strip()
        sfx_val = raw_sfx if raw_sfx in ["ting", "pop", "sub_drop"] else "none"
        camera_mot = c.get("camera_motion", "none")
        if sfx_val == "sub_drop" and camera_mot == "none":
            camera_mot = "shake"

        emotion_val = c.get("emotion", "funny")
        emotion_map = voice_cfg.get("emotion_style_map", {})
        emo_style_cfg = emotion_map.get(emotion_val, {})

        # Tự động tính khoảng lặng kịch tính (Breathing Room)
        default_pause_before = c.get("pause_before_ms", 250)
        default_pause_after = c.get("pause_after_ms", 350)
        if sfx_val in ["boom", "shock"]:
            # Cú twist bẻ lái cần suspense rõ rệt
            default_pause_before = max(default_pause_before, 650)
            default_pause_after = max(default_pause_after, 600)
        elif idx == len(raw_comments):
            # Câu chốt hạ cuối cùng có khoảng lặng kết
            default_pause_after = max(default_pause_after, 700)

        # Gán giọng nói và avatar (nếu không phải tác giả thì dùng phân vai động)
        if u_name != author_username:
            assigned_voice = c.get("voice_id") or role_info.get("voice") or preset.get("voice", "vi-VN-HoaiMyNeural" if is_female else "vi-VN-NamMinhNeural")
            chosen_c_pool = f_avatar_pool if is_female else m_avatar_pool
            c_avatar_file = chosen_c_pool[(idx - 1) % len(chosen_c_pool)]

        assigned_rate = role_info.get("rate") or emo_style_cfg.get("rate_boost") or preset.get("rate", "+20%")
        assigned_pitch = role_info.get("pitch") or emo_style_cfg.get("pitch_boost") or preset.get("pitch", "+2Hz")

        comments.append(Comment(
            username=c.get("username", f"user_{idx}"),
            content=c.get("content", ""),
            reply_to=c.get("reply_to", "") or data.get("username", "author"),
            avatar_color=clean_hex_color(c.get("avatar_color", "")),
            avatar_url=c_avatar_file,
            likes=c.get("likes", random.randint(2000, 25000)),
            sfx=sfx_val,
            emotion=emotion_val,
            voice_id=assigned_voice,
            rate_offset=assigned_rate,
            pitch_offset=assigned_pitch,
            pause_before_ms=default_pause_before,
            pause_after_ms=default_pause_after,
            camera_motion=camera_mot
        ))

    # Xử lý ảnh minh họa bằng chứng chuẩn ngữ cảnh
    has_proof = bool(data.get("has_visual_proof", True))
    placement = data.get("visual_proof_placement", "popup_at_twist")
    if placement == "none" or not placement:
        placement = "popup_at_twist"

    illus_prompt = data.get("illustration_prompt", "") or topic
    illus_vn = data.get("illustration_vietnamese_query", "") or topic
    illus_cat = data.get("illustration_category", "general_life")
    illus_path = ""

    candidate_queries = [q for q in [illus_vn, illus_prompt] if q]
    if candidate_queries:
        try:
            import importlib.util
            real_img_spec = importlib.util.spec_from_file_location("real_image_search", os.path.join(SRC_DIR, "real_image_search.py"))
            real_img_mod = importlib.util.module_from_spec(real_img_spec)
            real_img_spec.loader.exec_module(real_img_mod)

            illus_dir = os.path.join(OUT_DIR, "illustrations")
            os.makedirs(illus_dir, exist_ok=True)
            illus_filename = f"real_web_{int(time.time())}_{random.randint(100, 999)}.jpg"
            target_file = os.path.join(illus_dir, illus_filename)

            found_img = real_img_mod.search_and_download_real_image(
                queries=candidate_queries,
                category=illus_cat,
                output_path=target_file,
                target_width=940,
                target_height=620
            )
            if found_img and os.path.exists(found_img):
                illus_path = found_img
                print(f"  [✓] Đã tải thành công ảnh thật chuẩn ngữ cảnh (940x620): {illus_path}")
        except Exception as e:
            print(f"  ⚠️ Lỗi khi tìm ảnh thật trực tuyến ({e})")

    # Tuyệt đối không fallback sang ảnh chung cư/nhà hàng không liên quan
    if not illus_path or not os.path.exists(illus_path):
        illus_path = ""

    enable_illus = bool(illus_path and os.path.exists(illus_path))
    if not enable_illus:
        print("  ℹ️ Không có ảnh minh họa xác thực 100%, hệ thống tự động tắt thẻ popup proof để tránh ảnh lạc đề!")

    post = Post(
        username=data.get("username", "threads.user"),
        content=data.get("content", ""),
        avatar_color=clean_hex_color(data.get("avatar_color", "")),
        is_verified=data.get("is_verified", True),
        likes=data.get("likes", random.randint(25000, 75000)),
        video_title=data.get("video_title", ""),
        video_caption=data.get("video_caption", ""),
        hashtags=data.get("hashtags", ""),
        illustration_prompt=illus_prompt,
        illustration_path=illus_path,
        illustration_caption=data.get("illustration_caption", "") or f"Minh họa thực tế: {topic[:30]}",
        illustration_category=illus_cat,
        enable_illustration=enable_illus,
        has_visual_proof=True,
        visual_proof_placement=placement,
        sentence_reveal_mode=data.get("sentence_reveal_mode", "standard_full"),
        satisfying_category=data.get("satisfying_category", "gameplay"),
        recommended_theme=data.get("recommended_theme", "light"),
        story_context_type=ctx_type,
        cta_question=data.get("cta_question", ""),
        pacing_tempo=data.get("pacing_tempo", "normal"),
        avatar_url=author_avatar_default,
        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
        hook_energy=data.get("hook_energy", "high"),
        voice_id=author_voice_final,
        rate_offset=author_role.get("rate", "+18%"),
        pitch_offset=author_role.get("pitch", "+4Hz"),
        pause_before_ms=150,
        pause_after_ms=300,
        camera_motion="none",
        comments=comments
    )

    # Lưu kết quả ra file JSON mẫu
    out_json = os.path.join(OUT_DIR, "sample_post.json")
    content_base.save_to_json(post, out_json)

    if progress_cb:
        progress_cb("Hoàn thành kịch bản cảm xúc!", 100)

    print("\n" + "=" * 60)
    print("✨ ĐẠO DIỄN GEMINI ĐÃ SÁNG TẠO XONG KỊCH BẢN CẢM XÚC!")
    print("=" * 60)
    print(f"📊 Đồ thị cảm xúc: {post.emotional_arc}")
    print(f"📌 @{post.username} [{post.voice_id}]:")
    print(f"   {post.content}")
    print("\n💬 Các bình luận phân vai:")
    for i, c in enumerate(post.comments, 1):
        print(f"   [{i}] @{c.username} ({c.emotion.upper()}) [{c.voice_id} | SFX: {c.sfx.upper()} | Pause: {c.pause_after_ms}ms]:")
        print(f"       {c.content}")

    return post


if __name__ == "__main__":
    ai_post = generate_story_with_gemini()
