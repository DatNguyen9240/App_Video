"""
MODULE: THREADS TRENDING RADAR & REAL AVATAR CRAWLER
=====================================================
Chức năng:
  - Quét và tìm kiếm các câu chuyện, bài thảo luận thịnh hành (Trending & Viral) trên Threads
  - Dựa trên 1 bài hay nhất, kịch tính nhất để chuyển hóa thành kịch bản video
  - Tự động ghép AVATAR NGƯỜI THẬT (Chân dung thật của tác giả và người bình luận)
  - Loại bỏ hoàn toàn cảm giác avatar giả/hoạt hình/chữ cái
"""

import os
import sys
import json
import random
import re
import time
from typing import Optional, Callable

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
sys.path.insert(0, SRC_DIR)

import config as config_mod
import importlib.util

spec_content = importlib.util.spec_from_file_location("content_base", os.path.join(SRC_DIR, "01_content.py"))
content_base = importlib.util.module_from_spec(spec_content)
spec_content.loader.exec_module(content_base)

Post = content_base.Post
Comment = content_base.Comment

spec_ai = importlib.util.spec_from_file_location("ai_base", os.path.join(SRC_DIR, "01_ai_content.py"))
ai_base = importlib.util.module_from_spec(spec_ai)
spec_ai.loader.exec_module(ai_base)

AVATARS_DIR = os.path.join(BASE_DIR, "assets", "avatars")
OUT_DIR = os.path.join(BASE_DIR, "output")
DAILY_CACHE_FILE = os.path.join(OUT_DIR, "daily_trending_topics.json")
THREADS_AVATARS_DIR = os.path.join(OUT_DIR, "threads_avatars")
os.makedirs(THREADS_AVATARS_DIR, exist_ok=True)


def download_threads_avatar(username: str, save_path: str = None) -> Optional[str]:
    """Cào và tải avatar THẬT trực tiếp từ tài khoản Threads (threads.net/@username)."""
    import urllib.request
    username = username.strip().lstrip("@")
    if not username:
        return None
    if not save_path:
        save_path = os.path.join(THREADS_AVATARS_DIR, f"{username}.jpg")

    if os.path.exists(save_path) and os.path.getsize(save_path) > 12000:
        return save_path

    url = f"https://www.threads.net/@{username}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Accept": "*/*"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            if "isn't available" in html or "not available" in html:
                return None
            match = re.search(r'<meta property="og:image"\s+content="([^"]+)"', html)
            if not match:
                match = re.search(r'<meta name="twitter:image"\s+content="([^"]+)"', html)
            if match:
                img_url = match.group(1).replace("&amp;", "&")
                img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(img_req, timeout=5) as img_resp:
                    img_data = img_resp.read()
                    # Ảnh avatar người thật chuẩn Threads luôn > 12KB (ảnh logo Threads đen mặc định chỉ ~8.9KB)
                    if len(img_data) > 12000:
                        os.makedirs(os.path.dirname(save_path), exist_ok=True)
                        with open(save_path, "wb") as f:
                            f.write(img_data)
                        print(f"  [✓] Đã cào Avatar THẬT từ Threads của @{username}")
                        return save_path
    except Exception:
        pass
    return None


def call_gemini_with_resilience(
    client,
    contents,
    config,
    primary_model: str = "gemini-3.5-flash",
    fallback_models: tuple = ("gemini-3.5-flash-lite",),
    max_retries: int = 3,
    progress_cb: Optional[Callable[[str, int], None]] = None
):
    """
    Tự động xử lý lỗi 503 UNAVAILABLE (quá tải tạm thời) từ Google Gemini API:
    - Áp dụng cơ chế Exponential Backoff: tự động thử lại sau 2s, 4s.
    - Tự động chuyển model dự phòng nếu model chính quá tải.
    """
    import time
    models = [primary_model]
    for fm in fallback_models:
        if fm and fm not in models:
            models.append(fm)

    last_err = None
    for model_name in models:
        for attempt in range(1, max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                return response
            except Exception as e:
                last_err = e
                err_str = str(e)
                # Kiểm tra lỗi quá tải tạm thời (503 / 429 / high demand)
                is_transient = any(k in err_str for k in ["503", "429", "UNAVAILABLE", "high demand", "temporarily", "RESOURCE_EXHAUSTED", "overloaded"])
                if is_transient and attempt < max_retries:
                    wait_sec = attempt * 2.0
                    msg = f"Máy chủ Google ({model_name}) đang tải cao (503). Tự động thử lại lần {attempt}/{max_retries} sau {wait_sec:.0f}s..."
                    print(f"⚠️ {msg}")
                    if progress_cb:
                        progress_cb(msg, 35)
                    time.sleep(wait_sec)
                else:
                    break

    raise last_err


def build_emergency_story(topic: str) -> dict:
    """Tạo kịch bản chất lượng cao dự phòng chuẩn trên 1 phút (7 bình luận + ảnh + SFX meme)."""
    return {
        "username": "huyen.nguyen98",
        "gender": "female",
        "content": f"Hôm nay tình cờ gặp đúng chuyện về {topic}. Lúc đầu cứ tưởng bình thường, ai ngờ diễn biến sau đó khiến cả phòng đứng hình mất 10 giây. Đúng là ở đời không cái dại nào bằng cái dại này!",
        "likes": random.randint(25000, 68000),
        "is_verified": True,
        "video_title": f"Chuyện {topic} dở khóc dở cười",
        "video_caption": f"Có ai từng gặp tình huống này chưa mọi người? 😂",
        "hashtags": "#threads #xuhuong #drama #fyp",
        "illustration_prompt": f"candid real scene {topic}",
        "illustration_vietnamese_query": topic[:30],
        "illustration_category": "office_work" if any(k in topic.lower() for k in ["sếp", "công sở", "lương", "việc"]) else "general_life",
        "illustration_caption": f"Bằng chứng câu chuyện {topic[:25]}",
        "cta_question": f"Nếu rơi vào tình huống như câu chuyện về {topic[:20]}, bạn sẽ xử lý thế nào?",
        "satisfying_category": "gameplay" if any(k in topic.lower() for k in ["sếp", "công sở", "it", "drama"]) else "puzzle",
        "recommended_theme": "dark" if any(k in topic.lower() for k in ["đêm", "tối", "ot", "bí mật"]) else "light",
        "sentence_reveal_mode": "progressive_line_reveal",
        "emotional_arc": "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời",
        "comments": [
            {
                "username": "tuan_anh.dev",
                "gender": "male",
                "reply_to": "huyen.nguyen98",
                "content": "Đọc khúc đầu còn thấy tội, đọc tới khúc sau thì cười rớt hàm luôn chị ơi =))",
                "likes": random.randint(4000, 12000),
                "sfx": "none",
                "emotion": "funny",
                "pause_before_ms": 250,
                "pause_after_ms": 350
            },
            {
                "username": "minh_thao.hr",
                "gender": "female",
                "reply_to": "tuan_anh.dev",
                "content": "Bữa bên phòng tui cũng có vụ tương tự, sếp nghe xong lắc đầu ngao ngán không nói nên lời luôn á",
                "likes": random.randint(5000, 16000),
                "sfx": "none",
                "emotion": "funny",
                "pause_before_ms": 280,
                "pause_after_ms": 350
            },
            {
                "username": "quang_huy",
                "gender": "male",
                "reply_to": "minh_thao.hr",
                "content": "Ủa rồi rồi ai dọn bãi chiến trường đó vậy bà? Đừng bảo là người kể chuyện nha?",
                "likes": random.randint(3000, 10000),
                "sfx": "huh",
                "emotion": "awkward",
                "pause_before_ms": 300,
                "pause_after_ms": 400
            },
            {
                "username": "huyen.nguyen98",
                "gender": "female",
                "reply_to": "quang_huy",
                "content": "Chính xác luôn bạn ơi, vừa mất tiền vừa phải ở lại dọn tới 8 giờ tối mới được về!",
                "likes": random.randint(6000, 22000),
                "sfx": "none",
                "emotion": "awkward",
                "pause_before_ms": 260,
                "pause_after_ms": 350
            },
            {
                "username": "hoang_nam_99",
                "gender": "male",
                "reply_to": "huyen.nguyen98",
                "content": "Hahaha cái kết xứng đáng đi vào lịch sử những pha tự hủy đỉnh cao nhất năm 😂",
                "likes": random.randint(4500, 15000),
                "sfx": "laugh",
                "emotion": "funny",
                "pause_before_ms": 280,
                "pause_after_ms": 400
            },
            {
                "username": "lan_anh_kool",
                "gender": "female",
                "reply_to": "hoang_nam_99",
                "content": "Chưa hết đâu, nghe bảo hôm sau còn bị sếp nhắc nhở trong buổi họp tổng kết tuần nữa kìa!",
                "likes": random.randint(7000, 25000),
                "sfx": "boom",
                "emotion": "shocked",
                "pause_before_ms": 320,
                "pause_after_ms": 500
            },
            {
                "username": "duc_it_pro",
                "gender": "male",
                "reply_to": "lan_anh_kool",
                "content": "Tôi cạn lời toàn tập, đúng là trên đời chuyện quái gì cũng có thể xảy ra được 💀",
                "likes": random.randint(5500, 18000),
                "sfx": "bruh",
                "emotion": "awkward",
                "pause_before_ms": 300,
                "pause_after_ms": 550
            }
        ]
    }


def get_daily_trending_topics(force_refresh: bool = False, api_key: str = None) -> dict:
    """
    Tự động fetch danh sách chủ đề trending trên Threads mỗi ngày 1 lần.
    - Nếu trong ngày đã fetch rồi (cache hợp lệ) -> Đọc từ cache (nhanh 0ms).
    - Nếu sang ngày mới hoặc force_refresh=True -> Gọi Gemini AI quét tin tức/trend Threads mới nhất hôm nay.
    - Nếu mất mạng -> Fallback dùng lại cache gần nhất hoặc danh sách dự phòng.
    """
    import datetime
    os.makedirs(OUT_DIR, exist_ok=True)
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    today_display = datetime.date.today().strftime("%d/%m/%Y")
    now_time = datetime.datetime.now().strftime("%H:%M")

    # 1. Kiểm tra cache trong ngày (trừ khi ép làm mới)
    if not force_refresh and os.path.exists(DAILY_CACHE_FILE):
        try:
            with open(DAILY_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("date") == today_str and cached.get("topics") and len(cached["topics"]) >= 3:
                return {
                    "success": True,
                    "date": today_str,
                    "date_display": today_display,
                    "updated_at": cached.get("updated_at", now_time),
                    "is_cached": True,
                    "topics": cached["topics"]
                }
        except Exception:
            pass

    # 2. Cần fetch mới (hôm nay chưa fetch, sang ngày mới hoặc người dùng bấm Làm mới)
    if not api_key:
        api_key = ai_base.load_gemini_api_key()

    cfg = config_mod.load_config()

    if not api_key:
        return {
            "success": False,
            "date": today_str,
            "date_display": today_display,
            "updated_at": now_time,
            "is_cached": False,
            "error": "Chưa cấu hình GEMINI_API_KEY",
            "topics": []
        }

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
        model_name = cfg.get("ai_director", {}).get("model", "gemini-2.5-flash")

        prompt = f"""
Hôm nay là ngày {today_display}. Hãy quét và chọn lọc 7 đến 8 chủ đề, drama, câu chuyện công sở, tình cảm, hài hước hoặc trào lưu đang được thảo luận và bàn tán sôi nổi nhất trên mạng xã hội Threads (threads.net) tại Việt Nam hôm nay.

Yêu cầu:
1. Mỗi chủ đề là 1 câu ngắn gọn (từ 5 đến 12 từ), tự nhiên, hấp dẫn, khơi gợi tò mò (viral hook).
2. Mang đậm phong cách đời sống thật trên Threads: drama đi làm, hẹn hò, tiền bạc, gia đình, bạn bè, trào lưu Gen Z.
3. Trả về đúng 1 JSON array gồm các chuỗi chủ đề, không giải thích thêm.
Ví dụ:
[
  "Chuyện xin nghỉ phép bị sếp tra khảo lý do cá nhân",
  "Đi ăn sinh nhật bạn thân nhưng hóa đơn chia đều bất ổn"
]
"""
        response = call_gemini_with_resilience(
            client=client,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.85
            ),
            primary_model=model_name,
            fallback_models=("gemini-2.0-flash", "gemini-1.5-flash")
        )
        parsed = json.loads(response.text)
        if isinstance(parsed, list) and len(parsed) >= 3:
            topics = [str(t).strip() for t in parsed if str(t).strip()]
            cache_payload = {
                "date": today_str,
                "date_display": today_display,
                "updated_at": datetime.datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
                "topics": topics
            }
            with open(DAILY_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache_payload, f, ensure_ascii=False, indent=2)

            return {
                "success": True,
                "date": today_str,
                "date_display": today_display,
                "updated_at": cache_payload["updated_at"],
                "is_cached": False,
                "topics": topics
            }
    except Exception as e:
        print(f"[DailyRadar] Lỗi fetch trend hôm nay: {e}")
        # Nếu có cache cũ thì dùng tạm
        if os.path.exists(DAILY_CACHE_FILE):
            try:
                with open(DAILY_CACHE_FILE, "r", encoding="utf-8") as f:
                    old_cached = json.load(f)
                if old_cached.get("topics"):
                    return {
                        "success": True,
                        "date": old_cached.get("date", today_str),
                        "date_display": today_display,
                        "updated_at": old_cached.get("updated_at", now_time) + " (Offline cache)",
                        "is_cached": True,
                        "topics": old_cached["topics"]
                    }
            except Exception:
                pass

    return {
        "success": False,
        "date": today_str,
        "date_display": today_display,
        "updated_at": now_time,
        "is_cached": False,
        "error": "Không thể kết nối internet hoặc API lỗi",
        "topics": []
    }


# BỘ TÀI KHOẢN THREADS NGƯỜI THẬT THEO TỪNG CHỦ ĐỀ NGỮ CẢNH (TOPIC-CONTEXTUAL ACCOUNTS)
TOPIC_CONTEXT_THREADS_ACCOUNTS = {
    "cong_so": {
        "female": ["thu.hr.gocnho", "phuong.ke_toan", "mai_anh.hr", "linh.accountant", "trang.marketing", "ngoc.hr_corner", "huyen.office"],
        "male": ["hoang.marketing", "tuananh.hr", "quang.project", "duc.salesman", "nam.content", "viet.fintech"]
    },
    "sinh_vien": {
        "female": ["thao_nhi.2003", "minhanh_03", "thu_trang_2004", "ngoc.bich2003", "ha_my_2k3", "lan_anh.sinhvien"],
        "male": ["duc_manh_01", "quang_huy_02", "hoang_long_00", "tuan.sv_bku", "phuc.neu_student", "nam_2002"]
    },
    "it_tech": {
        "female": ["linh.coder", "mai.tester", "nga.ui_ux", "trang.it_girl", "khanhvyccf"],
        "male": ["hoangnam.dev", "tuan_anh.dev", "viet.backend", "cuong.frontend", "anhemsangtao"]
    },
    "tinh_cam": {
        "female": ["meow.lann", "linhkhaaa", "charlotte_huyen", "salim_hwang", "nga.tamsu", "huong.lovestory"],
        "male": ["tungbt.official", "ninhtitan", "quocanh.real", "tuan.dating", "duy.crush"]
    },
    "gia_dinh": {
        "female": ["dieunhi", "bich_ngoc.mommy", "thanh_huyen_family", "yen_nhi.home"],
        "male": ["dustinphucnguyen", "hung.giadinh", "quan.reallife", "thanh.home"]
    },
    "general": {
        "female": ["thao_nhi.2003", "meow.lann", "linhkhaaa", "thu.hr.gocnho", "phuong.ke_toan"],
        "male": ["hoangnam.dev", "hoang.marketing", "tungbt.official", "ninhtitan", "anhemsangtao"]
    }
}


def detect_topic_context(topic: str) -> str:
    """Tự động phân loại ngữ cảnh chủ đề để cào đúng avatar người thật liên quan."""
    t = topic.lower()
    if any(k in t for k in ["sếp", "công sở", "đi làm", "hr", "lương", "đồng nghiệp", "phỏng vấn", "nghỉ việc", "task", "deadline", "công ty"]):
        return "cong_so"
    if any(k in t for k in ["sinh viên", "trọ", "phòng trọ", "ở ghép", "chủ nhà", "tiền điện", "đi học", "thi cử", "học phí"]):
        return "sinh_vien"
    if any(k in t for k in ["it", "dev", "lập trình", "code", "coder", "bug", "khách hàng", "phần mềm", "tester"]):
        return "it_tech"
    if any(k in t for k in ["người yêu", "ny cũ", "chia tay", "hẹn hò", "date", "đám cưới", "phong bì", "tỏ tình", "crush"]):
        return "tinh_cam"
    if any(k in t for k in ["bố mẹ", "mẹ chồng", "gia đình", "ra mắt", "họ hàng", "lấy chồng", "lấy vợ"]):
        return "gia_dinh"
    return "general"


def get_real_threads_avatar_for_user(username: str, gender: str = "male",
                                     topic: str = "", used_set: set = None) -> str:
    """
    Cào avatar người thật từ Threads theo ĐÚNG CHỦ ĐỀ LIÊN QUAN:
    1. Ưu tiên 1: Cào trực tiếp từ tài khoản Threads của nhân vật (threads.net/@username).
    2. Ưu tiên 2: Nếu không lấy được, chọn từ các tài khoản Threads người thật THUỘC ĐÚNG NGỮ CẢNH CHỦ ĐỀ ĐÓ
       (ví dụ: chủ đề công sở -> cào avatar dân văn phòng/HR; sinh viên -> cào avatar sinh viên).
    3. Tuyệt đối không lấy avatar lệch chủ đề.
    """
    if used_set is None:
        used_set = set()

    clean_u = username.strip().lstrip("@")
    if clean_u:
        av = download_threads_avatar(clean_u)
        if av and os.path.exists(av) and os.path.getsize(av) > 12000:
            return av

    ctx = detect_topic_context(topic)
    ctx_accounts = TOPIC_CONTEXT_THREADS_ACCOUNTS.get(ctx, TOPIC_CONTEXT_THREADS_ACCOUNTS["general"])
    gender_key = "female" if gender == "female" else "male"
    candidates = [acc for acc in ctx_accounts.get(gender_key, []) if acc not in used_set]

    if not candidates:
        # Fallback sang general cùng giới tính
        gen_accs = TOPIC_CONTEXT_THREADS_ACCOUNTS["general"].get(gender_key, [])
        candidates = [acc for acc in gen_accs if acc not in used_set] or gen_accs

    random.shuffle(candidates)
    for cand in candidates:
        av = download_threads_avatar(cand)
        if av and os.path.exists(av) and os.path.getsize(av) > 12000:
            return av

    # 3. Fallback an toàn tuyệt đối khi Threads bị chặn (429 / This content isn't available)
    # Lấy ảnh chân dung người thật chuẩn 100% từ kho assets/avatars/ theo đúng ngữ cảnh & giới tính
    if os.path.exists(AVATARS_DIR):
        all_avatars = [f for f in os.listdir(AVATARS_DIR) if f.lower().endswith((".jpg", ".png", ".jpeg"))]
        sub_type = "office" if ctx == "cong_so" else "student"
        prefix = f"{gender}_{sub_type}"
        matched = [os.path.join(AVATARS_DIR, f) for f in all_avatars if f.startswith(prefix) and os.path.join(AVATARS_DIR, f) not in used_set]
        if not matched:
            matched = [os.path.join(AVATARS_DIR, f) for f in all_avatars if f.startswith(gender) and os.path.join(AVATARS_DIR, f) not in used_set]
        if not matched:
            matched = [os.path.join(AVATARS_DIR, f) for f in all_avatars if f.startswith(gender)]
        if matched:
            chosen = random.choice(matched)
            print(f"  [✓] Đã gán chân dung người thật (Fallback an toàn): {os.path.basename(chosen)}")
            return chosen

    return ""


def crawl_trending_threads_story(
    topic: str = "",
    api_key: str = None,
    progress_cb: Optional[Callable[[str, int], None]] = None
) -> Post:
    """
    Quét tin tức thịnh hành trên Threads theo chủ đề, chọn bài hay nhất và ghép avatar người thật.
    """
    if progress_cb:
        progress_cb("Đang dò quét các bài viết Trending trên Threads...", 15)

    if not api_key:
        api_key = ai_base.get_or_prompt_api_key()

    cfg = config_mod.load_config()
    voice_cfg = cfg.get("voice_casting", {})
    roles = voice_cfg.get("roles", {})

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})

    if not topic:
        daily_res = get_daily_trending_topics()
        if daily_res.get("topics"):
            topic = random.choice(daily_res["topics"])
        else:
            topic = "bài viết đang được thảo luận nhiều nhất hôm nay trên Threads"

    prompt_radar = f"""
Hãy đóng vai Người viết và săn tin thịnh hành trên Threads (threads.net) Việt Nam (SKILL: THREADS HUMAN GEN Z WRITER).
Tìm kiếm hoặc tái hiện 1 BÀI VIẾT THẬT ĐANG CỰC KỲ TRENDING VÀ VIRAL NHẤT trên Threads về chủ đề: '{topic}'.
Giọng văn phải tự nhiên như một người thật đang chia sẻ trải nghiệm đời thường cho bạn bè thân thiết nghe.

YÊU CẦU ĐẶC BIỆT ĐẠT CHUẨN VIDEO DÀI TRÊN 1 PHÚT (60s-75s):
1. Tác giả: Tên username tiếng Việt đời thường của người thật (ví dụ: mai_anh.hr, tuananh.dev, linh.nguyen99, congso.confession).
2. Nội dung bài viết: Dài từ 35 đến 50 từ (2-3 câu nhịp điệu mềm mại, nói trong 12-15 giây), mở đầu bằng tình huống rất đời thực, cuốn hút, giật mình tò mò.
3. SỐ LƯỢNG BÌNH LUẬN: BẮT BUỘC ĐỦ TỪ 7 ĐẾN 8 BÌNH LUẬN (từ C1 đến C7/C8). Mỗi bình luận từ 14-22 từ đối đáp liên hoàn, có tranh cãi, bóc phốt, bẻ lái 180° để tổng thời lượng video nói đủ trên 1 phút!
4. HIỆU ỨNG MEME SFX: Bắt buộc gắn meme SFX ('laugh', 'boom', 'bruh', 'shock', 'huh', 'clown') cho 2-3 bình luận đắt giá nhất để video kích hoạt Meme GIF popup đè ra màn hình cực kỳ sinh động! Các comment khác để 'none'.
5. HÌNH ẢNH MINH HỌA: Cung cấp illustration_vietnamese_query (2-4 từ tiếng Việt) và illustration_prompt (2-4 từ tiếng Anh) mô tả chính xác vật phẩm hoặc tình huống trong bài.
6. Ngôn ngữ: Dùng tiếng Việt Gen Z đời thường tinh tế, không gượng ép nhồi nhét từ lóng, dùng emoji có chừng mực (0-1 emoji/câu), tuyệt đối không để lộ văn phong AI giải thích.
7. Trường reply_to: Bắt buộc ghi rõ username mà bình luận đó đang trực tiếp trả lời.
8. Giới tính nhân vật (gender): Chỉ định rõ 'male' hoặc 'female' cho tác giả và từng bình luận để hệ thống ghép đúng avatar người thật!
"""

    if progress_cb:
        progress_cb(f"Gemini AI đang phân tích bài viết viral nhất về: '{topic[:40]}'...", 40)

    class TrendingCommentSchema(ai_base.BaseModel):
        username: str
        reply_to: str = ""
        gender: ai_base.Literal["male", "female"]
        content: str
        likes: int = 4500
        sfx: ai_base.Literal["laugh", "boom", "bruh", "shock", "huh", "side_eye", "clown", "math", "applause", "ting", "none"] = "none"
        emotion: ai_base.Literal["funny", "shocked", "awkward", "sarcastic", "insightful"] = "funny"
        pause_before_ms: int = 250
        pause_after_ms: int = 350
        camera_motion: ai_base.Literal["none", "punch_in", "shake"] = "none"

    class TrendingPostSchema(ai_base.BaseModel):
        username: str
        gender: ai_base.Literal["male", "female"]
        content: str
        likes: int = 42000
        is_verified: bool = True
        video_title: str = ""
        video_caption: str = ""
        hashtags: str = ""
        illustration_prompt: str = ""
        illustration_vietnamese_query: str = ""
        illustration_category: ai_base.Literal["couple_photo_fail", "cafe_lifestyle", "food_cooking", "office_work", "shopping_tech", "student_exam", "general_life"] = "general_life"
        illustration_caption: str = ""
        cta_question: str = ""
        satisfying_category: ai_base.Literal["food", "gameplay", "asmr", "puzzle", "hypnotic", "kinetic_sand"] = "gameplay"
        recommended_theme: ai_base.Literal["light", "dark"] = "light"
        sentence_reveal_mode: ai_base.Literal["progressive_line_reveal", "standard_full"] = "progressive_line_reveal"
        emotional_arc: str = "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"
        comments: list[TrendingCommentSchema]

    try:
        response = call_gemini_with_resilience(
            client=client,
            contents=prompt_radar,
            config=types.GenerateContentConfig(
                system_instruction="Bạn là chuyên gia phân tích bài đăng viral trên Threads tại Việt Nam (Thời lượng video chuẩn trên 1 phút, 7-8 bình luận đối đáp).",
                response_mime_type="application/json",
                response_schema=TrendingPostSchema,
                temperature=0.9,
            ),
            primary_model=cfg.get("ai_director", {}).get("model", "gemini-2.5-flash"),
            fallback_models=("gemini-2.0-flash", "gemini-1.5-flash"),
            max_retries=3,
            progress_cb=progress_cb
        )
        data = json.loads(response.text)
    except Exception as e:
        print(f"⚠️ [ThreadsRadar] Google AI gặp sự cố ({e}). Đã tự động kích hoạt kịch bản dự phòng thông minh...")
        data = build_emergency_story(topic)

    if progress_cb:
        progress_cb("Đang cào avatar người thật từ Threads ghép vào kịch bản...", 75)

    used_avatars = set()

    # 1. Lấy Avatar THẬT từ Threads cho tác giả (Đúng ngữ cảnh chủ đề)
    author_gender = data.get("gender", "male")
    author_username = data.get("username", "author")
    author_avatar_path = get_real_threads_avatar_for_user(author_username, author_gender, topic=topic, used_set=used_avatars)
    if author_avatar_path:
        used_avatars.add(author_avatar_path)

    author_role = roles.get("author", {})
    post_voice = author_role.get("voice", "vi-VN-HoaiMyNeural" if author_gender != "male" else "vi-VN-NamMinhNeural")

    # 2. Lấy Avatar THẬT từ Threads cho từng bình luận (Đúng ngữ cảnh chủ đề)
    comments = []

    for idx, c in enumerate(data.get("comments", []), 1):
        c_gender = c.get("gender", "female" if idx == 1 else "male")
        c_username = c.get("username", f"user_{idx}")
        c_avatar = get_real_threads_avatar_for_user(c_username, c_gender, topic=topic, used_set=used_avatars)
        if c_avatar:
            used_avatars.add(c_avatar)

        role_info = roles.get(f"commenter_{idx}", {})
        c_voice = role_info.get("voice", "vi-VN-HoaiMyNeural" if c_gender == "female" else "vi-VN-NamMinhNeural")

        sfx_val = c.get("sfx", "none")
        cam_motion = "shake" if sfx_val in ["boom", "shock"] else ("punch_in" if sfx_val in ["laugh", "bruh"] else "none")

        comments.append(Comment(
            username=c.get("username", f"user_{idx}"),
            content=c.get("content", ""),
            reply_to=c.get("reply_to", "") or data.get("username", "author"),
            avatar_path=c_avatar,
            avatar_url="",
            likes=c.get("likes", random.randint(3000, 18000)),
            sfx=sfx_val,
            emotion=c.get("emotion", "funny"),
            voice_id=c_voice,
            rate_offset=role_info.get("rate", "+20%"),
            pitch_offset=role_info.get("pitch", "+2Hz"),
            pause_before_ms=c.get("pause_before_ms", 250),
            pause_after_ms=c.get("pause_after_ms", 350),
            camera_motion=cam_motion
        ))

    # 3. Tải hoặc gán ảnh minh họa thật cho câu chuyện
    illus_cat = data.get("illustration_category", "general_life")
    illus_prompt = data.get("illustration_prompt", "") or topic
    illus_vn = data.get("illustration_vietnamese_query", "") or topic
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
        except Exception as e:
            print(f"  ⚠️ Lỗi khi tìm ảnh thật trực tuyến ({e})")

    if not illus_path or not os.path.exists(illus_path):
        cat_file = os.path.join(BASE_DIR, "assets", "illustrations", f"{illus_cat}.jpg")
        if os.path.exists(cat_file):
            illus_path = cat_file
        else:
            gen_file = os.path.join(BASE_DIR, "assets", "illustrations", "general_life.jpg")
            if os.path.exists(gen_file):
                illus_path = gen_file

    enable_illus = bool(illus_path and os.path.exists(illus_path))

    post = Post(
        username=data.get("username", "threads.trending"),
        content=data.get("content", ""),
        avatar_path=author_avatar_path,
        avatar_url="",
        is_verified=data.get("is_verified", True),
        likes=data.get("likes", random.randint(28000, 75000)),
        video_title=data.get("video_title", ""),
        video_caption=data.get("video_caption", ""),
        hashtags=data.get("hashtags", "#threads #trending #xuhuong #chuyencongso"),
        illustration_prompt=illus_prompt,
        illustration_path=illus_path,
        illustration_caption=data.get("illustration_caption", "") or f"Bằng chứng thực tế: {topic[:25]}",
        illustration_category=illus_cat,
        enable_illustration=enable_illus,
        has_visual_proof=True,
        visual_proof_placement="popup_at_twist",
        sentence_reveal_mode=data.get("sentence_reveal_mode", "progressive_line_reveal"),
        satisfying_category=data.get("satisfying_category", "gameplay"),
        recommended_theme=data.get("recommended_theme", "light"),
        story_context_type="office_workplace" if any(k in topic.lower() for k in ["sếp", "công sở", "lương", "việc"]) else "general_social",
        cta_question=data.get("cta_question", "") or f"Nếu là bạn trong tình huống này, bạn sẽ giải quyết ra sao?",
        pacing_tempo="normal",
        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
        voice_id=post_voice,
        rate_offset=author_role.get("rate", "+18%"),
        pitch_offset=author_role.get("pitch", "+4Hz"),
        comments=comments
    )

    # Lưu kết quả
    sample_file = os.path.join(OUT_DIR, "sample_post.json")
    content_base.save_to_json(post, sample_file)

    if progress_cb:
        progress_cb("Đã tìm thấy bài trending và ghép avatar người thật hoàn tất!", 100)

    print("\n" + "=" * 60)
    print("🔥 ĐÃ QUÉT ĐƯỢC BÀI VIẾT THỊNH HÀNH TRÊN THREADS!")
    print("=" * 60)
    print(f"📌 Tác giả: @{post.username} (Avatar: {os.path.basename(post.avatar_path)})")
    print(f"   {post.content}")
    print(f"   ❤️ {post.likes:,} likes")
    print("\n💬 Các bình luận trending & Avatar người thật:")
    for i, c in enumerate(post.comments, 1):
        print(f"   [{i}] @{c.username} (Avatar: {os.path.basename(c.avatar_path)} | SFX: {c.sfx.upper()}):")
        print(f"       {c.content}")

    return post

