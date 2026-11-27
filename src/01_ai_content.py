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
    sfx: Literal[
        "none", "laugh", "bruh", "boom", "sub_drop", "shock", "huh", "side_eye", "ting",
        "do_mixi", "pewpew", "emotional_damage", "oh_no",
        "bat_ngo_chua", "cuu_toi_voi", "so_qua_co", "fbi_open_up",
        "why_are_you_running", "coffin_dance", "to_be_continued", "gta_wasted", "sad_violin", "bonk"
    ] = Field(
        default="none",
        description="Hiệu ứng âm thanh và meme reaction điểm nhấn (Có hình GIF động + Giọng nói/SFX đồng bộ 100%). Phân bổ 2-3 comment có SFX tương ứng cảm xúc: 'do_mixi' (giọng Độ Mixi cười/cay cú/hợp lý), 'pewpew' (PewPew quá chuẩn luôn/ghê chưa), 'bat_ngo_chua' (Bất ngờ chưa bà già), 'cuu_toi_voi' (Trời ơi cứu tui với), 'so_qua_co' (Độ Mixi úi sợ quá), 'fbi_open_up' (Bắt quả tang phá cửa), 'why_are_you_running' (Why are you running), 'coffin_dance' (Khiêng quan tài tới số), 'to_be_continued' (Dừng hình JoJo kịch tính), 'gta_wasted' (Cái kết đắng Wasted), 'sad_violin' (Đàn kéo đau thương), 'bonk' (Cheems gõ đầu tỉnh ngộ), 'emotional_damage', 'oh_no', 'laugh', 'bruh', 'boom'/'sub_drop'."
    )
    emotion: Literal["funny", "shocked", "awkward", "sarcastic", "insightful", "neutral"] = Field(
        default="funny", description="Sắc thái cảm xúc của câu nói"
    )
    pause_before_ms: int = Field(default=220, description="Khoảng lặng tự nhiên trước khi nói (180-300ms)")
    pause_after_ms: int = Field(default=280, description="Khoảng lặng tự nhiên sau khi dứt câu (200-350ms)")
    camera_motion: Literal["none", "punch_in", "shake"] = Field(
        default="none", description="Hiệu ứng chuyển động hình ảnh ('punch_in' phóng to nhẹ, 'shake' rung lắc khi sốc)"
    )


class DocumentarySceneSchema(BaseModel):
    index: int = Field(default=1, description="Số thứ tự phân cảnh (1, 2, 3...)")
    type: Literal["image", "video"] = Field(default="image", description="'image' (ảnh tư liệu chụp thực tế) hoặc 'video' (clip chuyển động)")
    badge: str = Field(default="", description="Nhãn tiêu đề phân cảnh ngắn gọn in hoa (dưới 5 từ, vd: 'KẺ LỪA ĐẢO THẾ KỶ', 'BẢN HỢP ĐỒNG GIẢ', 'CÚ LẬT KÈO')")
    search_query_en: str = Field(default="", description="Từ khóa tiếng Anh chính xác để tự động tìm và tải ảnh tư liệu thật trên Wikimedia/Web cho cảnh này (vd: 'Victor Lustig mugshot', 'Eiffel Tower 1925 scrap metal', 'Antwerp World Diamond Centre vault')")
    narration: str = Field(default="", description="Lời dẫn chuyện lôi cuốn, truyền cảm của cảnh này bằng tiếng Việt (khoảng 20-35 từ) do giọng đọc AI diễn thuyết")
    sfx: Literal["none", "tension_riser", "ting", "sub_drop", "boom", "water_splash", "applause", "scratch"] = Field(default="none", description="Âm thanh điểm nhấn cảm xúc của cảnh")


class PostSchema(BaseModel):
    username: str = Field(description="Tên tác giả bài viết, ví dụ: genz.tamsu, chuyen.congso, dev_nghiep")
    content: str = Field(description="Câu chuyện Threads mở đầu cuốn hút, có bối cảnh chi tiết và tình huống rõ ràng (từ 35 đến 50 từ, 2-3 câu nhịp điệu mềm mại, nói trong 12-15 giây). Bắt đầu bằng Hook giật mình tò mò, nêu ngay mâu thuẫn cốt lõi, không dài dòng lan man.")
    avatar_color: str = Field(default="#8b5cf6", description="Mã màu hex cho avatar tác giả")
    is_verified: bool = Field(default=True, description="True nếu có tích xanh")
    likes: int = Field(default=35000, description="Lượt thích ước tính từ 15000 đến 85000")
    video_title: str = Field(default="", description="Tiêu đề video ngắn gọn, giật gân hài hước hoặc tiêu đề hồ sơ kỳ thú")
    story_badge: str = Field(default="HỒ SƠ KỲ THÚ", description="Nhãn chuyên mục hồ sơ kỳ thú / tổ chức (vd: 'HỒ SƠ KỲ THÚ • NƯỚC PHÁP 1925', 'VỤ ÁN THẾ KỶ • ANTWERP')")
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
    recommended_platform: Literal["threads", "facebook", "instagram"] = Field(
        default="threads",
        description="NỀN TẢNG PHÙ HỢP NHẤT CHO CÂU CHUYỆN: 'facebook' (drama đời sống, confession, gia đình, công sở, tình cảm, bóc phốt, chuyện oái oăm cần ảnh bằng chứng); 'instagram' (phong cách sống, cafe, ẩm thực, du lịch, sống ảo, thú cưng, thẩm mỹ thị giác); 'threads' (đối đáp nhanh, trào phúng, witty banter, ngắn gọn)."
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
    story_mode: Literal["everyday_life", "true_story"] = Field(
        default="everyday_life",
        description="Chế độ câu chuyện: 'everyday_life' (đời sống thường nhật) hoặc 'true_story' (sự kiện có thật quốc tế, cú lừa lịch sử, hồ sơ vụ án bóc phốt thực tế)"
    )
    documentary_query: str = Field(
        default="",
        description="Nếu là true_story: từ khóa tiếng Anh/quốc tế chính xác để tra cứu tư liệu gốc của sự kiện (ví dụ: 'Exit Deutschland trojan t-shirt neo-nazi festival')"
    )
    emotional_arc: str = Field(default="Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời", description="Tóm tắt đường cong cảm xúc của video")
    hook_energy: Literal["high", "intense", "curious"] = Field(default="high", description="Mức năng lượng mở màn")
    documentary_scenes: list[DocumentarySceneSchema] = Field(
        default_factory=list,
        description="Nếu là true_story hoặc documentary: danh sách từ 5 đến 6 phân cảnh tư liệu hoàn chỉnh với từ khóa tiếng Anh tìm ảnh thật và lời dẫn chuyện"
    )
    comments: list[CommentSchema] = Field(
        description="Danh sách từ 7 đến 8 bình luận tiếng Việt tạo thành chuỗi drama/hội thoại cuốn hút dài trên 1 phút, tất cả cùng bàn luận, cà khịa, đối đáp xoay quanh tình huống của tác giả"
    )


# ─── QUẢN LÝ GEMINI API KEY ──────────────────────────────────────────────────

def load_gemini_api_key() -> str:
    """Đọc API Key từ DB (SQLite xoay vòng) hoặc fallback sang môi trường / file .env."""
    try:
        import db_store
        keys = db_store.get_active_gemini_keys()
        if keys:
            return keys[0]
    except Exception:
        pass

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
    platform: str = "threads",
    story_mode: str = "auto",
    api_key: str = None,
    progress_cb: Optional[Callable[[str, int], None]] = None
) -> Post:
    """
    Sử dụng Gemini API với cấu hình đạo diễn cảm xúc để tạo kịch bản hoàn chỉnh đa nền tảng (Threads / Facebook / Instagram).
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

    # Nhận diện chế độ kể chuyện (Đời thường vs Chuyện có thật từ nguồn quốc tế/lịch sử)
    is_true_story = (story_mode == "true_story")
    if story_mode == "auto":
        true_story_kws = ["có thật", "quốc tế", "nước ngoài", "lịch sử", "vụ án", "chiến dịch", "trojan", "tân phát xít", "phát xít đức", "exit deutschland", "sự kiện lịch sử", "bóc tách hồ sơ", "phi vụ lừa thế kỷ"]
        if any(kw in topic.lower() for kw in true_story_kws):
            is_true_story = True
    final_story_mode = "true_story" if is_true_story else "everyday_life"

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

    plat_choice = (platform or "auto").lower().strip()
    if plat_choice == "facebook":
        plat_guide = (
            "NỀN TẢNG NỘI DUNG: FACEBOOK POST & CONFESSION (BÀI VIẾT KÈM ẢNH ĐỜI THỰC & BÌNH LUẬN XÔM TỤ):\n"
            "- Phong cách kể chuyện: Giọng văn tâm sự/confession/drama đời sống, cuốn hút, gần gũi, mở đầu gây tò mò.\n"
            "- Bài viết chính có đính kèm ảnh chụp thực tế (has_visual_proof = true, visual_proof_placement = 'attached_in_post').\n"
            "- Các bình luận bên dưới như hội nhóm cư dân mạng/bạn bè Facebook vào thả haha, bóc phốt, đồng cảm, tag bạn bè."
        )
    elif plat_choice == "instagram":
        plat_guide = (
            "NỀN TẢNG NỘI DUNG: INSTAGRAM POST (BÀI ĐĂNG CHÚ TRỌNG HÌNH ẢNH SỐNG ĐỘNG & CAPTION TINH TẾ):\n"
            "- Phong cách kể chuyện: Aesthetic, trải nghiệm cá nhân, review tình huống oái oăm đời thường hoặc lifestyle.\n"
            "- Bài viết chính luôn lấy hình ảnh thực tế làm tâm điểm (has_visual_proof = true, visual_proof_placement = 'attached_in_post').\n"
            "- Các bình luận bên dưới thả tim, trêu chọc dí dỏm, hỏi địa chỉ/nguồn gốc sự việc."
        )
    elif plat_choice == "threads":
        plat_guide = (
            "NỀN TẢNG NỘI DUNG: THREADS (NGẮN GỌN, SNAPPY, HỘI THOẠI ĐỐI ĐÁP PHẢN ỨNG NHANH CỰC DÍ DỎM):\n"
            "- Phong cách kể chuyện: Trải nghiệm thực tế, nhịp nhanh, witty banter, các bình luận nối tiếp đối đáp liên hoàn."
        )
    else:  # "auto"
        plat_guide = (
            "NỀN TẢNG NỘI DUNG (AI TỰ ĐỘNG CHỌN NỀN TẢNG TỐI ƯU NHẤT - recommended_platform):\n"
            "Hãy tự động phân tích chủ đề và bối cảnh để quyết định chọn 1 trong 3 nền tảng phù hợp nhất:\n"
            "- Chọn 'facebook': Nếu là câu chuyện drama đời sống, gia đình, công sở, tình cảm rắc rối, bóc phốt, hoặc chuyện oái oăm cần đính kèm ảnh chụp làm chứng.\n"
            "- Chọn 'instagram': Nếu là trải nghiệm du lịch, quán cafe, ẩm thực, ăn uống, thú cưng, đồ hiệu, phong cách sống, chụp ảnh sống ảo.\n"
            "- Chọn 'threads': Nếu là mẩu đối đáp ngắn, hài hước, châm biếm, witty banter, chia sẻ nhanh suy nghĩ độc lạ."
        )

    prompt = (
        f"Bạn là người viết kịch bản mạng xã hội chuyên nghiệp (SKILL: MULTI-PLATFORM GEN Z WRITER).\n"
        f"Hãy tạo 1 kịch bản bài đăng và các bình luận bằng tiếng Việt thật tự nhiên, trau chuốt như người thật đang chia sẻ trải nghiệm với bạn bè.\n"
        f"Nền tảng mục tiêu: {plat_choice.upper()}\n"
        f"Chủ đề: '{topic}'\n"
        f"Chế độ kịch bản: {dur_mode}\n\n"
        f"{plat_guide}\n\n"
        f"QUY TẮC CỐT LÕI (PHẢI GIỐNG 100% NGƯỜI THẬT KỂ CHUYỆN - ĐẠT THỜI LƯỢNG ÍT NHẤT 1 PHÚT):\n"
        f"- BÀI VIẾT CHÍNH: Kể mở đầu một câu chuyện rất đời thực, cuốn hút và có bối cảnh chi tiết (khoảng 35-50 từ, 2-3 câu nhịp điệu mềm mại, nói trong 12-15 giây), nêu rõ vật phẩm/sự việc cụ thể khiến người đọc cảm thấy 'sao giống mình vậy' và muốn dừng lại xem hết.\n"
        f"{cmt_guide}\n"
        f"- TÍNH ĐỒNG NHẤT CỦA BÌNH LUẬN: Tất cả các comment phải xoay quanh câu chuyện và mâu thuẫn của bài viết chính. Đừng chuyển đề tài.\n"
        f"- CÁC BÌNH LUẬN: Mỗi bình luận từ 14-22 từ, mang góc nhìn chân thật của bạn bè mạng xã hội.\n"
        f"- NGÔN NGỮ GENZ TINH TẾ: Dùng từ ngữ tự nhiên của giới trẻ nhưng VỪA ĐỦ, tuyệt đối KHÔNG nhồi nhét tiếng lóng gượng ép, KHÔNG cố tỏ ra hài hước rẻ tiền.\n"
        f"- EMOJI VỪA PHẢI: Tối đa 0-1 emoji mỗi câu khi thật sự cần thiết (tránh spam emoji).\n"
        f"- USERNAME: Tên username tiếng Việt đời thường tự nhiên (ví dụ: 'linh.nguyen99', 'minh.triet01', 'hoang_anh.hr', 'tuan.oto').\n"
        f"- REPLY_TO: Ghi rõ username đang trả lời để tạo sự đối đáp sống động.\n"
        f"- HÌNH ẢNH MINH HỌA BẰNG CHỨNG: Mặc định TẮT (has_visual_proof = false) để video tập trung 100% vào hội thoại đời thực, nhịp đọc tự nhiên và hiệu ứng SFX. Tuyệt đối KHÔNG tự vẽ ảnh hay bịa ảnh AI ảo.\n"
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

    if is_true_story:
        prompt += (
            f"\n\n🔥 ĐẶC BIỆT: CHẾ ĐỘ KỂ CHUYỆN CÓ THẬT TỪ NGUỒN QUỐC TẾ / PHIM TƯ LIỆU (TRUE STORY & DOCUMENTARY):\n"
            f"- Chủ đề câu chuyện là một sự việc có thật ngoài đời hoặc phi vụ/cú lừa kinh điển quốc tế (ví dụ: {topic or 'cú lừa lịch sử / kỳ án thế giới'}). Hãy kể lại ĐÚNG 100% bản chất và diễn biến sự kiện thật.\n"
            f"- TIÊU ĐỀ VIDEO (video_title): Đặt tiêu đề in hoa giật gân, tò mò (dưới 10 từ, ví dụ: 'CÚ LỪA 250 CHIẾC ÁO PHÔNG TÂN PHÁT XÍT', 'VỤ ÁN BÁN THÁP EIFFEL 2 LẦN').\n"
            f"- NHÃN CHUYÊN MỤC (story_badge): Nhãn phân loại hồ sơ (ví dụ: 'HỒ SƠ KỲ THÚ • EXIT DEUTSCHLAND', 'SIÊU LỪA ĐẢO THẾ KỶ • NƯỚC PHÁP').\n"
            f"- THẺ MỞ ĐẦU (POST): Đặt hook cực sốc, nêu bật sự thật kỳ lạ hoặc kết quả không ai ngờ tới của sự kiện để người xem dừng tay nghe tiếp.\n"
            f"- PHÂN CẢNH TƯ LIỆU (documentary_scenes): BẮT BUỘC tạo đúng từ 5 đến 6 phân cảnh tư liệu hoàn chỉnh:\n"
            f"  + Mỗi cảnh có 'badge': nhãn in hoa ngắn gọn (dưới 5 từ, vd: 'LỄ HỘI ROCK', 'CÚ LỪA THẾ KỶ', 'BẢN HỢP ĐỒNG GIẢ', 'MÁY GIẶT KÍCH HOẠT').\n"
            f"  + 'search_query_en': từ khóa tiếng Anh cực chuẩn xác của nhân vật, sự kiện hoặc đồ vật để tìm ảnh chụp thực tế trên Wikimedia/Web (vd: 'Victor Lustig mugshot', 'Eiffel tower scrap metal 1925').\n"
            f"  + 'narration': lời dẫn chuyện lôi cuốn bằng tiếng Việt do giọng đọc AI đọc (từ 20-35 từ).\n"
            f"  + 'sfx': âm thanh kịch tính phù hợp ('tension_riser', 'ting', 'sub_drop', 'boom', 'water_splash', 'applause', 'scratch').\n"
            f"- TƯ LIỆU QUỐC TẾ: Điền 'documentary_query' là từ khóa tiếng Anh chính xác để tra cứu tư liệu gốc.\n"
            f"- Đặt story_mode = 'true_story'."
        )

    if progress_cb:
        progress_cb(f"Gemini AI ({model_primary}) đang sáng tạo kịch bản...", 30)

    models_to_try = [model_primary, model_fallback, "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
    # Loại bỏ trùng lặp giữ nguyên thứ tự
    seen = set()
    models_to_try = [x for x in models_to_try if x and not (x in seen or seen.add(x))]

    try:
        import db_store
        db_keys = db_store.get_active_gemini_keys()
    except Exception:
        db_keys = []
    keys_to_try = [api_key] if (api_key and api_key not in db_keys) else (db_keys or [api_key])
    if api_key and api_key in db_keys:
        keys_to_try = [api_key] + [k for k in db_keys if k != api_key]

    data = None
    last_err = None

    import time
    for key_idx, cur_key in enumerate(keys_to_try):
        if data:
            break
        cur_client = client if (key_idx == 0 and cur_key == api_key) else genai.Client(api_key=cur_key, http_options={"api_version": "v1beta"})
        key_failed_fatal = False
        for m in models_to_try:
            if data or key_failed_fatal:
                break
            for attempt in range(1, 4):
                try:
                    response = cur_client.models.generate_content(
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
                    try:
                        import db_store
                        db_store.report_key_success(cur_key)
                    except Exception:
                        pass
                    break
                except Exception as e:
                    last_err = e
                    err_str = str(e)
                    is_key_or_quota = any(k in err_str for k in ["429", "RESOURCE_EXHAUSTED", "API_KEY_INVALID", "403", "PERMISSION_DENIED", "quota"])
                    is_transient = any(k in err_str for k in ["503", "429", "UNAVAILABLE", "high demand", "temporarily", "RESOURCE_EXHAUSTED", "overloaded"])
                    if is_key_or_quota:
                        try:
                            import db_store
                            db_store.report_key_error(cur_key, err_str)
                        except Exception:
                            pass
                        if len(keys_to_try) > 1:
                            key_failed_fatal = True
                            break
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
        {"voice": "nu_cakhia", "rate": "+0%", "pitch": "+0Hz", "style": "sarcastic"},
        {"voice": "nu_nhinhanh", "rate": "+0%", "pitch": "+0Hz",  "style": "cheerful"},
        {"voice": "nu_cakhia", "rate": "+0%", "pitch": "+0Hz", "style": "excited"},
        {"voice": "nu_nhinhanh", "rate": "+0%", "pitch": "+0Hz",  "style": "sad"}
    ])
    male_presets = voice_cfg.get("male_voice_presets", [
        {"voice": "nam_hai", "rate": "+0%", "pitch": "+0Hz", "style": "cheerful"},
        {"voice": "nam_tram_hai", "rate": "+0%", "pitch": "+0Hz",  "style": "dramatic"},
        {"voice": "nam_hai", "rate": "+0%", "pitch": "+0Hz",  "style": "chat"},
        {"voice": "nam_tram_hai", "rate": "+0%", "pitch": "+0Hz",  "style": "angry"}
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
        author_voice_final = author_role.get("voice") or "nu_cakhia"
    else:
        author_voice_final = author_role.get("voice") or "nam_hai"

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
            assigned_voice = c.get("voice_id") or role_info.get("voice") or preset.get("voice", "nu_cakhia" if is_female else "nam_hai")
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

    # Xác định nền tảng hiển thị (Auto Platform Resolution)
    if plat_choice == "auto":
        chosen_plat = str(data.get("recommended_platform", "threads")).lower().strip()
        if chosen_plat not in ["threads", "facebook", "instagram"]:
            chosen_plat = "threads"
    else:
        chosen_plat = plat_choice

    # TỰ ĐỘNG TẢI HOẶC GÁN ẢNH MINH HỌA THẬT CHUẨN NGỮ CẢNH:
    has_proof = True
    placement = data.get("visual_proof_placement", "attached_in_post")
    if placement not in ["attached_in_post", "popup_at_twist"]:
        placement = "attached_in_post"

    illus_prompt = data.get("illustration_prompt", "") or topic or "candid real photo"
    illus_vn = data.get("illustration_vietnamese_query", "") or topic or "ảnh đời thực"
    illus_cat = data.get("illustration_category", "general_life")
    illus_path = ""

    try:
        from src.real_image_search import search_and_download_real_image
        search_terms = [t for t in [illus_prompt, illus_vn, topic] if t]
        illus_path = search_and_download_real_image(
            queries=search_terms,
            category=illus_cat,
            target_width=940,
            target_height=620,
            fallback_to_category=True
        )
    except Exception as e:
        print(f"  ⚠️ Lỗi khi tìm ảnh thật trực tuyến ({e})")

    # Fallback an toàn: nếu chưa có ảnh web, lấy ngay ảnh chuẩn từ assets/illustrations
    if not illus_path or not os.path.exists(illus_path):
        cat_file = os.path.join(BASE_DIR, "assets", "illustrations", f"{illus_cat}.jpg")
        if os.path.exists(cat_file):
            illus_path = cat_file
        else:
            gen_file = os.path.join(BASE_DIR, "assets", "illustrations", "general_life.jpg")
            if os.path.exists(gen_file):
                illus_path = gen_file

    enable_illus = bool(illus_path and os.path.exists(illus_path))
    if not enable_illus:
        has_proof = False
        placement = "none"

    # Xử lý các phân cảnh phim tư liệu nếu là Documentary / True Story
    raw_doc_scenes = data.get("documentary_scenes", [])
    doc_scenes = []
    if raw_doc_scenes:
        for idx, sc in enumerate(raw_doc_scenes, 1):
            if isinstance(sc, dict):
                sc_text = sc.get("narration") or sc.get("text") or ""
                doc_scenes.append({
                    "index": sc.get("index", idx),
                    "type": sc.get("type", "image"),
                    "badge": sc.get("badge", f"CẢNH #{idx}"),
                    "search_query_en": sc.get("search_query_en", ""),
                    "text": sc_text,
                    "sfx": sc.get("sfx", "none"),
                    "path": ""
                })

    post = Post(
        username=data.get("username", "threads.user"),
        content=data.get("content", ""),
        avatar_color=clean_hex_color(data.get("avatar_color", "")),
        is_verified=data.get("is_verified", True),
        likes=data.get("likes", random.randint(25000, 75000)),
        video_title=data.get("video_title", "") or (topic[:60].upper() if topic else "HỒ SƠ KỲ THÚ"),
        story_badge=data.get("story_badge", "") or "HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT",
        documentary_scenes=doc_scenes,
        video_caption=data.get("video_caption", ""),
        hashtags=data.get("hashtags", ""),
        illustration_prompt=illus_prompt,
        illustration_path=illus_path,
        illustration_caption=data.get("illustration_caption", "") or f"Minh họa thực tế: {topic[:30]}",
        illustration_category=illus_cat,
        enable_illustration=enable_illus,
        has_visual_proof=has_proof,
        visual_proof_placement=placement,
        sentence_reveal_mode=data.get("sentence_reveal_mode", "standard_full"),
        satisfying_category=data.get("satisfying_category", "gameplay"),
        recommended_theme=data.get("recommended_theme", "light"),
        story_context_type=ctx_type,
        cta_question=data.get("cta_question", ""),
        pacing_tempo=data.get("pacing_tempo", "normal"),
        story_mode=data.get("story_mode", final_story_mode),
        documentary_query=data.get("documentary_query", ""),
        avatar_url=author_avatar_default,
        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
        hook_energy=data.get("hook_energy", "high"),
        voice_id=author_voice_final,
        rate_offset=author_role.get("rate", "+18%"),
        pitch_offset=author_role.get("pitch", "+4Hz"),
        pause_before_ms=150,
        pause_after_ms=300,
        camera_motion="none",
        platform=chosen_plat,
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
    print(f"📱 Nền tảng: {post.platform.upper()} ({'AI Tự động tối ưu' if plat_choice == 'auto' else 'Người dùng chọn'})")
    print(f"📌 @{post.username} [{post.voice_id}]:")
    print(f"   {post.content}")
    print("\n💬 Các bình luận phân vai:")
    for i, c in enumerate(post.comments, 1):
        print(f"   [{i}] @{c.username} ({c.emotion.upper()}) [{c.voice_id} | SFX: {c.sfx.upper()} | Pause: {c.pause_after_ms}ms]:")
        print(f"       {c.content}")

    return post


if __name__ == "__main__":
    ai_post = generate_story_with_gemini()
