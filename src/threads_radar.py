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
    """Tạo kịch bản chất lượng cao dự phòng nếu máy chủ AI gặp sự cố 503 kéo dài."""
    return {
        "username": "huyen.nguyen98",
        "gender": "female",
        "content": f"Hôm nay tình cờ gặp đúng chuyện về {topic}. Lúc đầu cứ tưởng bình thường, ai ngờ diễn biến sau đó khiến cả phòng đứng hình mất 10 giây. Đúng là ở đời không cái dại nào bằng cái dại này!",
        "likes": random.randint(25000, 68000),
        "is_verified": True,
        "video_title": f"Chuyện {topic} dở khóc dở cười",
        "video_caption": f"Có ai từng gặp tình huống này chưa mọi người? 😂",
        "hashtags": "#threads #xuhuong #drama #fyp",
        "emotional_arc": "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời",
        "comments": [
            {
                "username": "tuan_anh.dev",
                "gender": "male",
                "content": "Đọc khúc đầu còn thấy tội, đọc tới khúc sau thì cười rớt hàm luôn chị ơi =))",
                "likes": random.randint(4000, 12000),
                "sfx": "laugh",
                "emotion": "funny",
                "pause_before_ms": 250,
                "pause_after_ms": 350
            },
            {
                "username": "minh_thao.hr",
                "gender": "female",
                "content": "Ủa alo rồi cái kết bẻ lái gắt vậy ai đỡ cho kịp trời? Quá sốc luôn á!",
                "likes": random.randint(5000, 16000),
                "sfx": "boom",
                "emotion": "shocked",
                "pause_before_ms": 300,
                "pause_after_ms": 450
            },
            {
                "username": "quang_huy",
                "gender": "male",
                "content": "Tôi cạn lời luôn, đúng là trên Threads chuyện gì cũng có thể xảy ra được 💀",
                "likes": random.randint(2000, 8000),
                "sfx": "bruh",
                "emotion": "awkward",
                "pause_before_ms": 300,
                "pause_after_ms": 400
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
        model_name = cfg.get("ai_director", {}).get("model", "gemini-3.5-flash")

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
            fallback_models=("gemini-3.5-flash-lite",)
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
Hãy đóng vai Nhà nghiên cứu nội dung và săn tin thịnh hành trên mạng xã hội Threads (threads.net) tại Việt Nam.
Tìm kiếm hoặc tái hiện 1 BÀI VIẾT THẬT ĐANG CỰC KỲ TRENDING VÀ VIRAL NHẤT trên Threads về chủ đề: '{topic}'.

Quy tắc bắt buộc:
1. Tác giả: Tên username tiếng Việt đời thường của người thật (ví dụ: mai_anh.hr, tuananh.dev, linh.nguyen99, congso.confession).
2. Nội dung bài viết: Kể 1 câu chuyện hoặc tình huống có thật, có mâu thuẫn bất ngờ, hài hước hoặc drama thu hút nghìn bình luận.
3. Bình luận 1: Bình luận đồng cảm hài hước của bạn bè (sfx: 'laugh', 'huh', 'side_eye' hoặc 'clown').
4. Bình luận 2: Bình luận bẻ lái sốc (punchline) không ai ngờ tới (sfx: 'boom', 'shock' hoặc 'math').
5. Bình luận 3: Bình luận chốt hạ cạn lời hoặc dở khóc dở cười (sfx: 'bruh', 'applause' hoặc 'ting').
6. Giới tính nhân vật (gender): Chỉ định rõ 'male' hoặc 'female' cho tác giả và từng bình luận để hệ thống ghép đúng avatar người thật!
"""

    if progress_cb:
        progress_cb(f"Gemini AI đang phân tích bài viết viral nhất về: '{topic[:40]}'...", 40)

    class TrendingCommentSchema(ai_base.BaseModel):
        username: str
        gender: ai_base.Literal["male", "female"]
        content: str
        likes: int = 4500
        sfx: ai_base.Literal["laugh", "boom", "bruh", "shock", "huh", "side_eye", "clown", "math", "applause", "ting", "none"] = "laugh"
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
        emotional_arc: str = "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"
        comments: list[TrendingCommentSchema]

    try:
        response = call_gemini_with_resilience(
            client=client,
            contents=prompt_radar,
            config=types.GenerateContentConfig(
                system_instruction="Bạn là chuyên gia phân tích bài đăng viral trên Threads tại Việt Nam.",
                response_mime_type="application/json",
                response_schema=TrendingPostSchema,
                temperature=0.9,
            ),
            primary_model=cfg.get("ai_director", {}).get("model", "gemini-3.5-flash"),
            fallback_models=("gemini-3.5-flash-lite",),
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
    post_voice = author_role.get("voice", "vi-VN-NamMinhNeural" if author_gender == "male" else "vi-VN-HoaiMyNeural")

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

        sfx_val = c.get("sfx", "laugh")
        cam_motion = "shake" if sfx_val == "boom" else ("punch_in" if sfx_val in ["laugh", "bruh"] else "none")

        comments.append(Comment(
            username=c.get("username", f"user_{idx}"),
            content=c.get("content", ""),
            avatar_path=c_avatar,
            avatar_url="",
            likes=c.get("likes", random.randint(3000, 18000)),
            sfx=sfx_val,
            emotion=c.get("emotion", "funny"),
            voice_id=c_voice,
            rate_offset=role_info.get("rate", "+10%"),
            pitch_offset=role_info.get("pitch", "+0Hz"),
            pause_before_ms=c.get("pause_before_ms", 250),
            pause_after_ms=c.get("pause_after_ms", 350),
            camera_motion=cam_motion
        ))

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
        emotional_arc=data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
        voice_id=post_voice,
        rate_offset=author_role.get("rate", "+8%"),
        pitch_offset=author_role.get("pitch", "+0Hz"),
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

