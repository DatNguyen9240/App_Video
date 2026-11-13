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
    content: str = Field(
        description="Nội dung bình luận tiếng Việt: DÀI TỪ 18 ĐẾN 28 TỪ (từ 1 đến 2 câu tự nhiên có ngữ cảnh). Người đồng cảm kể thêm tình huống dở khóc dở cười tương tự, người bắt bẻ chi tiết hoặc bóc phốt sâu cay, tuyệt đối KHÔNG viết cộc lốc vài từ!"
    )
    avatar_color: str = Field(default="#3b82f6", description="Mã màu hex (vd: #3b82f6, #ef4444, #10b981, #f59e0b)")
    likes: int = Field(default=4500, description="Lượt thích ước tính từ 1500 đến 25000")
    sfx: Literal["none", "sub_drop", "scratch", "ting", "boom", "laugh", "bruh", "pop"] = Field(
        default="none",
        description="Hiệu ứng âm thanh điểm nhấn. QUAN TRỌNG: 80% bình luận phải để 'none'! Toàn video chỉ tối đa 1-2 comment có SFX để tạo điểm nhấn bất ngờ (sub_drop cho bẻ lái sốc, scratch cho cạn lời/quay xe, ting cho điểm sáng đắt giá). Tuyệt đối không gắn SFX cho mọi comment!"
    )
    emotion: Literal["funny", "shocked", "awkward", "sarcastic", "insightful", "neutral"] = Field(
        default="funny", description="Sắc thái cảm xúc của câu nói"
    )
    pause_before_ms: int = Field(default=180, description="Khoảng lặng tự nhiên trước khi nói (120-250ms)")
    pause_after_ms: int = Field(default=220, description="Khoảng lặng tự nhiên sau khi dứt câu (150-300ms)")
    camera_motion: Literal["none", "punch_in", "shake"] = Field(
        default="none", description="Hiệu ứng chuyển động hình ảnh ('punch_in' phóng to nhẹ, 'shake' rung lắc khi sốc)"
    )


class PostSchema(BaseModel):
    username: str = Field(description="Tên tác giả bài viết, ví dụ: genz.tamsu, chuyen.congso, dev_nghiep")
    content: str = Field(description="Nội dung bài đăng chính của tác giả (Hook mở màn): BẮT BUỘC DÀI TỪ 45 ĐẾN 60 TỪ (khoảng 3 câu văn mềm mại có nhịp điệu). Kể rõ đầu đuôi hoàn cảnh, tình tiết éo le gây tò mò và cảm xúc dở khóc dở cười để dẫn dắt câu chuyện cuốn hút, nói trong 15-18 giây. Tuyệt đối không viết cộc lốc 1-2 câu ngắn!")
    avatar_color: str = Field(default="#8b5cf6", description="Mã màu hex cho avatar tác giả")
    is_verified: bool = Field(default=True, description="True nếu có tích xanh")
    likes: int = Field(default=35000, description="Lượt thích ước tính từ 15000 đến 85000")
    video_title: str = Field(default="", description="Tiêu đề video ngắn gọn, giật gân hài hước")
    video_caption: str = Field(default="", description="Đoạn caption đăng TikTok/Reels cuốn hút kèm câu hỏi kêu gọi bình luận")
    hashtags: str = Field(default="", description="Chuỗi 5-7 hashtag viral")
    illustration_prompt: str = Field(
        default="",
        description="2-4 từ khóa tìm kiếm ảnh đời thực chân thật bằng tiếng Anh hoặc tiếng Việt khớp 100% với cốt truyện hoặc vật phẩm gây cười trong bài (ví dụ: nếu là chụp ảnh dìm: 'bad angle candid girlfriend photo'; nếu là món ăn cháy: 'burnt fried egg kitchen fail'; nếu là hóa đơn điện: 'high electricity bill shock'; nếu là tai nghe hỏng: 'broken wireless earphone'). TUYỆT ĐỐI KHÔNG tìm kính râm 'sunglasses' trừ phi câu chuyện thật sự nói về kính râm! TUYỆT ĐỐI KHÔNG tìm chó mèo meme chung chung!"
    )
    illustration_vietnamese_query: str = Field(
        default="",
        description="Từ khóa tiếng Việt đời thường ngắn gọn (2-3 từ) để tìm ảnh minh họa thực tế (ví dụ: 'chụp ảnh bạn gái dìm', 'món ăn cháy khét', 'hóa đơn tiền điện', 'mua hàng trên mạng fail')"
    )
    illustration_category: Literal["couple_photo_fail", "cafe_lifestyle", "food_cooking", "office_work", "shopping_tech", "student_exam", "general_life"] = Field(
        default="general_life",
        description="Phân loại ngữ cảnh chính xác của câu chuyện"
    )
    has_visual_proof: bool = Field(
        default=True,
        description="LUÔN ĐẶT TRUE: Mọi video đều cần có 1 bức ảnh minh họa đời thực chân thật gắn kèm hoặc bung thẻ nổi ở cao trào để tăng tính xác thực và giữ chân người xem."
    )
    visual_proof_placement: Literal["attached_in_post", "popup_at_twist"] = Field(
        default="attached_in_post",
        description="Vị trí hiển thị ảnh: 'attached_in_post' (gắn trực tiếp trong bài tác giả chuẩn Threads) hoặc 'popup_at_twist' (bung thẻ ảnh nổi ở đoạn bẻ lái cao trào)."
    )
    sentence_reveal_mode: Literal["progressive_line_reveal", "standard_full"] = Field(
        default="standard_full",
        description="ĐẠO DIỄN NGHỆ THUẬT: AI tự quyết định cách mở màn bài viết tác giả: "
                    "- 'progressive_line_reveal': Thẻ bài bung nở từng câu theo nhịp đọc. Dành cho câu chuyện mang tính tò mò, hồi hộp, bẻ lái bất ngờ, bí mật, hoặc thơ ca/tâm sự sâu lắng (giúp giấu kết cục, chống người xem đọc lướt trước!). "
                    "- 'standard_full': Hiển thị trọn vẹn thẻ bài ngay từ đầu. Dành cho câu chuyện ngắn, đối đáp đời thường đơn giản."
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
        description="BẮT BUỘC TẠO CHÍNH XÁC TỪ 7 ĐẾN 8 BÌNH LUẬN (Tối thiểu 7 bình luận, không bao giờ được dưới 7!). Mỗi bình luận dài 18-28 từ. Đây là yêu cầu sống còn để toàn bộ video đạt độ dài TỐI THIỂU 1 PHÚT (60s - 75s) chuẩn TikTok Creator Rewards."
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
            f"- C1: Reaction mở đầu châm ngòi (reply_to username tác giả, sfx='none').\n"
            f"- C2 đến C{max(2, c_count - 2)}: Đối đáp, tranh cãi, bóc phốt hoặc kể thêm tình huống éo le (hãy dùng reply_to để các comment đối đáp lẫn nhau sống động).\n"
            f"- C{max(2, c_count - 1)}: Cú bẻ lái sốc 180° hoặc bóc mẽ đau điếng (gắn sfx='sub_drop').\n"
            f"- C{c_count}: Câu chốt hạ cạn lời / dí dỏm (sfx='scratch' hoặc 'none')."
        )
    elif dur_mode == "fast_30s":
        cmt_guide = (
            "SỐ LƯỢNG BÌNH LUẬN: ĐÚNG 4 BÌNH LUẬN (Thời lượng ngắn 30-40s):\n"
            "- C1: Đồng cảm / reaction hài hước (reply_to username tác giả, sfx='none').\n"
            "- C2: Kể thêm trải nghiệm éo le tương tự (reply C1, sfx='none').\n"
            "- C3: BẺ LÁI 180° sốc hoặc bóc phốt đau điếng (sfx='sub_drop').\n"
            "- C4: Chốt hạ cạn lời dí dỏm (sfx='none' hoặc 'scratch')."
        )
    else:  # "auto", "deep_60s", "viral_story_90s"
        cmt_guide = (
            "YÊU CẦU ĐỘ DÀI: VIDEO PHẢI DÀI TỐI THIỂU 1 PHÚT (TỪ 60S ĐẾN 75S - ĐẠT CHUẨN KIẾM TIỀN TIKTOK CREATOR REWARDS PROGRAM)!\n"
            "SỐ LƯỢNG BÌNH LUẬN: TỪ 7 ĐẾN 8 BÌNH LUẬN (Tạo chuỗi đối đáp nhiều tầng sâu sắc, tuyệt đối không được dưới 6 bình luận):\n"
            "- C1: Reaction mở đầu đồng cảm / ngạc nhiên (reply tác giả, sfx='none').\n"
            "- C2: Tranh cãi, phản biện hoặc kể thêm trải nghiệm éo le của bản thân (reply C1, sfx='none').\n"
            "- C3: Bắt thóp chi tiết sơ hở để cà khịa (reply C2 hoặc tác giả, sfx='none').\n"
            "- C4: Tác giả hoặc người trong cuộc vào đính chính thêm tình tiết oái oăm (reply C3, sfx='none').\n"
            "- C5: Người thứ 5 nhảy vào tiếp lời tạo không khí rôm rả (reply C1 hoặc C4, sfx='none').\n"
            "- C6: Cú BẺ LÁI BẤT NGỜ 180° đảo ngược toàn bộ sự thật (sfx='sub_drop').\n"
            "- C7: Đúc kết cạn lời / bóc mẽ dí dỏm (sfx='scratch').\n"
            "- C8 (nếu có): Câu chốt hạ hài hước viral để người xem bật cười thả tim (sfx='none').\n"
            "- QUY TẮC SFX: 85% bình luận để sfx='none'. Chỉ 1-2 comment bẻ lái bất ngờ nhất mới gắn sfx='sub_drop' hoặc 'scratch'."
        )

    prompt = (
        f"Bạn là người viết Threads chuyên nghiệp (SKILL: THREADS HUMAN GEN Z WRITER).\n"
        f"Hãy tạo 1 kịch bản bài đăng Threads và các bình luận bằng tiếng Việt thật tự nhiên, trau chuốt như người thật đang chia sẻ trải nghiệm với bạn bè.\n"
        f"Chủ đề: '{topic}'\n"
        f"Chế độ kịch bản: {dur_mode}\n\n"
        f"QUY TẮC CỐT LÕI (PHẢI GIỐNG 100% NGƯỜI THẬT KỂ CHUYỆN - BẮT BUỘC ĐẠT THỜI LƯỢNG ÍT NHẤT 1 PHÚT):\n"
        f"- BÀI VIẾT CHÍNH CỦA TÁC GIẢ: BẮT BUỘC DÀI TỪ 45 ĐẾN 60 TỪ (khoảng 3 câu văn mềm mại có nhịp điệu, nói trong 15-18 giây). Kể mở đầu một câu chuyện rất đời thực, cuốn hút và có bối cảnh chi tiết, khiến người đọc cảm thấy 'sao giống mình vậy' và muốn dừng lại xem hết.\n"
        f"{cmt_guide}\n"
        f"- CÁC BÌNH LUẬN: BẮT BUỘC TỪ 7 ĐẾN 8 BÌNH LUẬN. Mỗi bình luận PHẢI DÀI TỪ 18 ĐẾN 28 TỪ (từ 1 đến 2 câu đầy đủ ngữ cảnh), mang góc nhìn chân thật của bạn bè mạng xã hội: người đồng cảm góp thêm tình huống dở khóc dở cười, người bắt thóp chi tiết để cà khịa dí dỏm, người tranh luận phản biện qua lại. Tuyệt đối KHÔNG viết bình luận cộc lốc vài từ!\n"
        f"- HÌNH ẢNH MINH HỌA THỰC TẾ: Mọi câu chuyện đều có 1 bức ảnh chụp thực tế chân thật (đời thường, chụp dìm, thảm họa bếp núc, hóa đơn, bưu phẩm, laptop làm việc...) đính kèm bài viết tác giả ('attached_in_post') hoặc bung thẻ ảnh nổi ở cao trào ('popup_at_twist'). LUÔN để has_visual_proof = true và visual_proof_placement = 'attached_in_post' hoặc 'popup_at_twist'!\n"
        f"- NGÔN NGỮ GENZ TINH TẾ: Dùng từ ngữ tự nhiên của giới trẻ nhưng VỪA ĐỦ, tuyệt đối KHÔNG nhồi nhét tiếng lóng gượng ép, KHÔNG cố tỏ ra hài hước rẻ tiền.\n"
        f"- EMOJI VỪA PHẢI: Tối đa 0-1 emoji mỗi câu khi thật sự cần thiết (tránh spam emoji).\n"
        f"- USERNAME: Tên username tiếng Việt đời thường tự nhiên (ví dụ: 'linh.nguyen99', 'minh.triet01', 'hoang_anh.hr', 'be_meo_ngu_ngay').\n"
        f"- REPLY_TO: Ghi rõ username đang trả lời để tạo sự đối đáp sống động.\n"
        f"- PHONG CÁCH HIỆN CÂU (SENTENCE REVEAL): Nếu câu chuyện có tính TÒ MÒ, HỒI HỘP, BÍ MẬT, BẺ LÁI BẤT NGỜ hoặc TÂM SỰ SÂU LẮNG/THƠ CA -> BẮT BUỘC chọn sentence_reveal_mode = 'progressive_line_reveal' để từng dòng mở ra theo nhịp đọc. Với chuyện ngắn đơn giản -> chọn 'standard_full'.\n"
        f"- KHỚP VIDEO NỀN Ở GIỮA (satisfying_category): Chọn 'food' nếu nói về ăn uống/bếp núc; 'gameplay' nếu là IT/công sở/gay cấn; 'asmr' nếu là tâm sự chia sẻ; 'puzzle' nếu là trinh thám/bóc phốt.\n"
        f"- GIAO DIỆN SÁNG/TỐI (recommended_theme): Chọn 'dark' nếu là chuyện đêm muộn/bí mật rình mò/IT trực đêm; 'light' nếu là chuyện ban ngày/cafe/mua sắm.\n"
        f"- CÂU HỎI OUTRO VIRAL (cta_question): Nghĩ 1 câu hỏi mở sắc sảo ở 2.4s cuối đánh trúng tim đen để kích thích người xem nhảy vào bình luận.\n"
        f"- TUYỆT ĐỐI TRÁNH: Không viết như AI giải thích, không mở đầu sáo rỗng, không kết bài bằng đạo lý rập khuôn.\n\n"
        f"TIÊU CHUẨN KIỂM DUYỆT:\n"
        f"Trước khi trả kết quả, tự hỏi: 'Người thật có nói câu này ngoài đời không? Đọc lên có tự nhiên và truyền cảm không? Thời lượng có đủ dài trên 1 phút không?'\n\n"
        f"VÍ DỤ TONE THAM KHẢO (Đầy đủ 7 bình luận đối đáp liên hoàn, dài trên 1 phút):\n"
        f"Bài: 'Chồng tui bảo mua đèn ngủ thông minh cảm biến âm thanh cho lãng mạn. Giờ đêm trở mình đập muỗi một cái là phòng sáng rực như sân vận động Mỹ Đình, ổng ngáy một tiếng là đèn tự đổi bảy màu lộng lẫy 😭'\n"
        f"- C1: 'Rồi lúc hai vợ chồng cãi nhau cái đèn có chớp tắt báo động đỏ như vũ trường không bà? 😂' (reply tác giả)\n"
        f"- C2: 'Nhà tui mua đèn cảm biến chuyển động nè, đêm mèo chạy qua một cái là sáng trưng tưởng trộm đột nhập' (reply C1)\n"
        f"- C3: 'Ủa rồi lỡ nói mớ gọi tên nyc thì đèn có hú còi cứu hỏa báo cháy không?' (reply C2)\n"
        f"- C4: 'Hôm qua ổng lỡ ho một tiếng mà đèn nháy nhạc EDM chớp tắt liên tục làm tui tưởng động đất' (reply C3)\n"
        f"- C5: 'Cười xỉu, tính năng tự động hóa hôn nhân phiên bản cảm lạnh dữ dằn 💀' (reply tác giả)\n"
        f"- C6: 'Sáng nay tui phát hiện ổng lén tháo pin giấu vào ngăn kéo rồi, bảo mệt tim quá chịu hết nổi' (reply C5, sfx='sub_drop')\n"
        f"- C7: 'Đèn thông minh nhưng người mua thì bất lực toàn tập' (reply C6, sfx='scratch')\n"
    )

    if progress_cb:
        progress_cb(f"Gemini AI ({model_primary}) đang sáng tạo kịch bản...", 30)

    models_to_try = [model_primary, model_fallback, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
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
    for idx, c in enumerate(raw_comments, 1):
        u_name = c.get("username", f"user_{idx}").lower()
        role_key = f"commenter_{idx}"
        role_info = roles.get(role_key, {})

        # Tự động nhận diện giới tính theo username hoặc xen kẽ tự nhiên
        is_female = any(k in u_name for k in female_keywords)
        if not is_female and not any(k in u_name for k in male_keywords):
            is_female = (idx % 2 == 1)

        preset_list = female_presets if is_female else male_presets
        preset = preset_list[((idx - 1) // 2) % len(preset_list)]

        sfx_val = c.get("sfx", "laugh")
        camera_mot = c.get("camera_motion", "none")
        if sfx_val in ["boom", "shock"] and camera_mot == "none":
            camera_mot = "shake"
        elif sfx_val in ["laugh", "bruh", "applause"] and camera_mot == "none":
            camera_mot = "punch_in"

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

        assigned_voice = c.get("voice_id") or role_info.get("voice") or preset.get("voice", "vi-VN-HoaiMyNeural" if is_female else "vi-VN-NamMinhNeural")
        assigned_rate = role_info.get("rate") or emo_style_cfg.get("rate_boost") or preset.get("rate", "+20%")
        assigned_pitch = role_info.get("pitch") or emo_style_cfg.get("pitch_boost") or preset.get("pitch", "+2Hz")

        chosen_c_pool = f_avatar_pool if is_female else m_avatar_pool
        c_avatar_file = chosen_c_pool[(idx - 1) % len(chosen_c_pool)]
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

    # Đảm bảo video luôn đạt tối thiểu 7 bình luận để dài trên 1 phút (chuẩn TikTok Creator Rewards)
    if len(comments) < 7:
        sample_users = ["mai_lan.99", "duy_anh.it", "thuy_tien.ng", "quang_huy.dev", "kim_ngan.art", "tuan_minh.hr"]
        sample_replies = [
            f"Đọc xong câu chuyện của @{data.get('username', 'author')} mà tui phải ôm bụng cười, y chang hoàn cảnh tui tuần trước luôn á!",
            "Tính ra gặp tình huống này thì chỉ có nước cạn lời thôi chứ đỡ sao nổi, cười xỉu với độ lầy lội này luôn đó mọi người 😂",
            "Ai ngờ cái kết bẻ lái đỉnh cao vậy luôn trời, coi từ đầu tới cuối hồi hộp không rời mắt được giây nào!",
            "Công nhận đời thực lắm pha éo le hài hước ghê, lưu lại ngay mai mốt gặp còn biết đường mà xử lý khéo nha."
        ]
        needed = 7 - len(comments)
        for extra_idx in range(needed):
            c_user = sample_users[extra_idx % len(sample_users)]
            c_text = sample_replies[extra_idx % len(sample_replies)]
            c_reply_target = comments[-1].username if comments else data.get("username", "author")
            comments.append(Comment(
                username=c_user,
                content=c_text,
                reply_to=c_reply_target,
                avatar_color=clean_hex_color("#10b981"),
                avatar_url=m_avatar_pool[(len(comments)) % len(m_avatar_pool)],
                likes=random.randint(3000, 18000),
                sfx="none",
                emotion="funny",
                voice_id="minh_quan" if extra_idx % 2 == 0 else "khanh_linh",
                rate_offset="+18%",
                pitch_offset="+2Hz",
                pause_before_ms=200,
                pause_after_ms=300,
                camera_motion="none"
            ))

    # TỰ ĐỘNG TẢI HOẶC GÁN ẢNH MINH HỌA THẬT CHUẨN NGỮ CẢNH (LUÔN CÓ ẢNH THỰC TẾ)
    has_proof = True
    placement = data.get("visual_proof_placement", "attached_in_post")
    if placement not in ["attached_in_post", "popup_at_twist"]:
        placement = "attached_in_post"

    illus_prompt = data.get("illustration_prompt", "")
    illus_vn = data.get("illustration_vietnamese_query", "")
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

    # Nếu tìm kiếm trực tuyến chưa có ảnh, LUÔN dự phòng từ kho ảnh thật có sẵn theo ngữ cảnh
    if not illus_path or not os.path.exists(illus_path):
        candidate = os.path.join(BASE_DIR, "assets", "illustrations", f"{illus_cat}.jpg")
        if os.path.exists(candidate):
            illus_path = candidate
        else:
            illus_path = os.path.join(BASE_DIR, "assets", "illustrations", "general_life.jpg")

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
        illustration_caption=data.get("illustration_caption", ""),
        illustration_category=illus_cat,
        enable_illustration=True,
        has_visual_proof=True,
        visual_proof_placement=placement,
        sentence_reveal_mode=data.get("sentence_reveal_mode", "standard_full"),
        satisfying_category=data.get("satisfying_category", "gameplay"),
        recommended_theme=data.get("recommended_theme", "light"),
        story_context_type=ctx_type,
        cta_question=data.get("cta_question", ""),
        pacing_tempo=data.get("pacing_tempo", "normal"),
        avatar_url=(f_avatar_pool[0] if any(k in data.get("username", "").lower() for k in female_keywords) else m_avatar_pool[0]),
        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
        hook_energy=data.get("hook_energy", "high"),
        voice_id=author_role.get("voice", voice_cfg.get("default_author_voice", "adam")),
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
