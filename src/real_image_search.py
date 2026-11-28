"""
REAL WEB IMAGE SEARCH & CONTEXTUAL SCRAPER MODULE (v2.1)
=========================================================
Tìm kiếm và tải ảnh THẬT từ web chuẩn xác 100% theo ngữ cảnh câu chuyện:
1. Đa nguồn tìm kiếm: Ưu tiên Wikimedia Commons (ảnh thực tế do con người chụp, chất lượng cao, không rác/logo), sau đó đến Bing Image Search có bộ lọc nghiêm ngặt.
2. Bộ lọc chống Logo / Graphic / Thumbnail AI:
   - Tự động nhận diện và loại bỏ ảnh logo, icon, sơ đồ kỹ thuật, hình nền trắng tinh công nghiệp.
   - Kiểm tra tỷ lệ màu và độ bão hòa (loại bỏ ảnh có nền trắng đơn sắc > 50% hoặc số màu nghèo nàn).
   - Chặn tuyệt đối các từ khóa & URL: YouTube thumbnails, funny fails, dog/cat meme khi không đúng chủ đề.
3. THẨM ĐỊNH NGỮ NGHĨA BẰNG AI GEMINI VISION:
   - Trước khi nhận ảnh, AI kiểm tra trực tiếp xem ảnh có thực sự liên quan đến chủ đề hay không.
   - Loại bỏ triệt để các trường hợp nhầm lẫn (như ảnh công trường, tiền tệ, logo lạc đề).
4. Tự động chuẩn hóa và chia tách từ khóa (Query Sanitization).
5. Tự động crop & resize chuẩn tỷ lệ 940x620 cho Threads Card & Cutaway Proof.
6. Fallback thông minh vào kho ảnh thực tế chất lượng cao theo danh mục.
"""

import os
import re
import json
import time
import random
import shutil
import hashlib
import urllib.parse
import urllib.request
from typing import Union, List
from PIL import Image, ImageOps

try:
    import proxy_manager as _proxy_mgr
    _urlopen = _proxy_mgr.urlopen_with_proxy
except Exception:
    _urlopen = urllib.request.urlopen

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets", "illustrations")
OUT_DIR    = os.path.join(BASE_DIR, "output", "illustrations")

# Danh sách từ cấm / AI / rác cần lọc bỏ khỏi từ khóa và kết quả
BLACKLIST_QUERY_WORDS = [
    "meme", "funny meme", "funny fails", "fails", "fail", "compilation", "funny", "hai huoc",
    "ai", "midjourney", "pollinations", "drawing", "illustration",
    "cartoon", "anime", "3d render", "photorealistic", "photo realistic", "vector", "clipart",
    "logo", "icon", "banner", "symbol"
]

BLACKLIST_URL_PATTERNS = [
    "pollinations", "shutterstock", "gettyimages", "alamy", "dreamstime", "depositphotos",
    "vector", "clipart", "istockphoto", "123rf", "canva", "freepik", "watermark", "stock-photo",
    "ftcdn.net", "stockcake", "adobe.com/stock",
    "i.ytimg.com", "youtube.com", "funny_fail", "funny-fails", "maxresdefault", "hqdefault", "sddefault",
    "logo", "icon", "badge", "schematic", "diagram", "component", "catalog",
    "dealeraccelerate", "carfax", "autotrader", "volocars", "classiccars", "cars.com",
    "scribdassets", "tailieuhay", "kenhgiaovien", "loigiaihay", "vietjack", "hoc247",
    "wixmp", "deviantart", "vecteezy"
]


def clean_search_term(term: str) -> str:
    """Làm sạch từ khóa tìm kiếm, loại bỏ từ rác, giữ lại danh từ cốt lõi."""
    t = term.lower()
    for bad in BLACKLIST_QUERY_WORDS:
        t = re.sub(rf"\b{re.escape(bad)}\b", " ", t)
    t = re.sub(r"[^\w\s-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_logo_or_graphic(im: Image.Image) -> bool:
    """
    Phát hiện xem ảnh có phải là LOGO, ICON hoặc SƠ ĐỒ KỸ THUẬT NỀN TRẮNG không.
    Ảnh đời thực luôn có ánh sáng tự nhiên, đổ bóng và dải màu phong phú (hàng nghìn màu).
    Ảnh logo thường có nền trắng tinh đơn sắc chiếm diện tích lớn và rất ít màu.
    """
    w, h = im.size
    if w < 400 or h < 300:
        return True
    aspect = w / h
    if aspect < 0.55 or aspect > 2.5:
        return True

    im_rgb = im.convert("RGB")
    thumb = im_rgb.resize((100, 100))
    pixels = list(thumb.getdata())

    # Đếm số pixel trắng tinh (R>235, G>235, B>235)
    white_count = sum(1 for p in pixels if p[0] > 235 and p[1] > 235 and p[2] > 235)
    white_ratio = white_count / len(pixels)
    unique_colors = len(set(pixels))

    # Kiểm tra ảnh đen trắng / đơn sắc tư liệu lịch sử (tối đa 256 sắc độ xám)
    is_monochrome = sum(1 for p in pixels if max(p) - min(p) < 18) > (len(pixels) * 0.85)
    if is_monochrome:
        if white_ratio > 0.65 or unique_colors < 35:
            return True
        return False

    # Nếu nền trắng chiếm trên 55% diện tích và số màu nghèo nàn -> Logo/sản phẩm catalog
    if white_ratio > 0.55 and unique_colors < 1500:
        return True

    # Nếu toàn bộ ảnh màu có dưới 300 màu trong 10,000 pixel -> Vector/đồ họa phẳng
    if unique_colors < 300:
        return True

    return False


def _get_gemini_keys() -> List[str]:
    """Lấy danh sách GEMINI_API_KEY từ DB Studio, biến môi trường và .env."""
    keys = []
    try:
        import db_store
        keys.extend(db_store.get_active_gemini_keys() or [])
    except Exception:
        pass
    env_k = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_k and env_k not in keys:
        keys.insert(0, env_k)
    if not keys:
        env_file = os.path.join(BASE_DIR, ".env")
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        k = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if k and k not in keys:
                            keys.append(k)
    return keys


def verify_image_with_gemini(image_path: str, query: str, fail_open: bool = True) -> bool:
    """Sử dụng Gemini Vision để thẩm định xem bức ảnh có thực sự liên quan đến chủ đề hay không."""
    try:
        from google import genai
        keys = _get_gemini_keys()
        if not keys:
            return fail_open

        with Image.open(image_path) as im:
            im_copy = im.convert("RGB")
            im_copy.thumbnail((512, 512))

        prompt = (
            f"Story/Scene context: {query}\n"
            "Evaluate if this image is a real-life photograph, authentic documentary/historical photo, or real product/scene photo that genuinely matches the specific situation, object, or person described above.\n"
            "Score from 1 to 10 (10 = exact match to the situation/object/person; 1 = off-topic or bad).\n"
            "Give score <= 3 if it is: a textbook/book cover, PDF/document page scan, exam sheet, cartoon/anime, AI-generated plastic look, brand logo, blank map/flag when a real scene is needed, or an unrelated generic object/car/building.\n"
            "Reply strictly in format: SCORE: <number> | YES or NO"
        )
        models = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]
        for key in keys[:3]:
            client = genai.Client(api_key=key)
            for m in models:
                try:
                    res = client.models.generate_content(
                        model=m,
                        contents=[im_copy, prompt]
                    )
                    raw_text = getattr(res, "text", None)
                    if not raw_text:
                        continue
                    ans = raw_text.strip().upper()
                    score_m = re.search(r"SCORE\s*:\s*(\d+)", ans)
                    if score_m:
                        return int(score_m.group(1)) >= 6
                    return "YES" in ans and "NO" not in ans.split("YES")[0]
                except Exception:
                    continue
        return fail_open
    except Exception as e:
        print(f"     ⚠️ Lỗi thẩm định Gemini Vision ({e})")
        return fail_open


def search_wikipedia_images(query: str, limit: int = 4) -> List[str]:
    """Tìm ảnh đại diện chính xác của nhân vật/sự kiện từ bài viết Wikipedia tiếng Anh."""
    headers = {"User-Agent": "ThreadsVideoBot/2.0 (multimedia@app.local)"}
    queries_to_try = [query]
    words = query.split()
    if len(words) > 4:
        queries_to_try.append(" ".join(words[:4]))
    urls = []
    seen = set()
    for q in queries_to_try:
        encoded = urllib.parse.quote(q)
        url = (
            f"https://en.wikipedia.org/w/api.php?action=query&generator=search"
            f"&gsrsearch={encoded}&gsrlimit={limit}&prop=pageimages&piprop=original|thumbnail&pithumbsize=1000&format=json"
        )
        req = urllib.request.Request(url, headers=headers)
        try:
            with _urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            for _, p in sorted(pages.items(), key=lambda kv: kv[1].get("index", 999)):
                u = (p.get("thumbnail") or {}).get("source") or (p.get("original") or {}).get("source")
                if not u or u in seen:
                    continue
                path_lower = urllib.parse.urlparse(u).path.lower()
                if any(bad in path_lower for bad in (".svg", "icon", "logo", ".pdf", ".djvu")):
                    continue
                if any(path_lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    seen.add(u)
                    urls.append(u)
                    if len(urls) >= limit:
                        return urls
        except Exception:
            continue
    return urls


def search_wikimedia_images(query: str, limit: int = 5) -> List[str]:
    """Tìm ảnh chụp đời thực chất lượng cao từ kho Wikimedia Commons (Không quảng cáo, không logo)."""
    wiki_headers = {"User-Agent": "ThreadsVideoBot/2.0 (multimedia@app.local)"}
    encoded = urllib.parse.quote(query)
    url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded}&gsrnamespace=6&gsrlimit={limit * 2}&prop=imageinfo&iiprop=url|size|mime&iiurlwidth=1000&format=json"
    req = urllib.request.Request(url, headers=wiki_headers)
    try:
        with _urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pages = data.get("query", {}).get("pages", {})
        urls = []
        for pid, p in pages.items():
            infos = p.get("imageinfo", [])
            if infos:
                # Ưu tiên lấy thumbnail 1000px đã tối ưu hóa tải nhanh, nhẹ (<200KB)
                u = infos[0].get("thumburl") or infos[0].get("url")
                if not u:
                    continue
                path_lower = urllib.parse.urlparse(u).path.lower()
                # Loại bỏ vector svg, icon, và đặc biệt là trang scan PDF/DjVu trên Wikimedia
                if any(bad in path_lower for bad in (".svg", "icon", "logo", ".pdf", ".djvu", "page1-")):
                    continue
                if any(path_lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    urls.append(u)
                    if len(urls) >= limit:
                        break
        return urls
    except Exception:
        return []


def generate_wikimedia_visual_queries(topic: str, context: str = "") -> List[str]:
    """Sử dụng Gemini để chuyển đổi chủ đề/câu chuyện tiếng Việt thành 3-4 từ khóa tiếng Anh trực quan chuẩn Wikimedia."""
    try:
        from google import genai
        keys = _get_gemini_keys()
        if not keys:
            return []

        prompt = (
            f"Topic: {topic}\n"
            f"Context: {context[:200]}\n"
            "Task: Identify the core physical visual subject. "
            "Output 3 broad, high-yield English search phrases (1-3 words each) to find real documentary/candid photos of this subject on Wikimedia Commons. "
            "Return strictly a JSON array of strings."
        )
        for key in keys[:2]:
            client = genai.Client(api_key=key)
            for model_name in ["gemini-2.5-flash-lite", "gemini-2.5-flash"]:
                try:
                    res = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config={"response_mime_type": "application/json"}
                    )
                    terms = json.loads(res.text)
                    if isinstance(terms, list):
                        res_terms = [t.strip() for t in terms if isinstance(t, str) and t.strip()]
                        if res_terms:
                            return res_terms
                except Exception:
                    continue
    except Exception as e:
        print(f"  ⚠️ Lỗi khi sinh từ khóa Wikimedia ({e})")

    # Fallback ngoại tuyến: chuẩn hóa và sinh từ khóa dự phòng từ topic
    cleaned = clean_search_term(topic)
    return [cleaned, f"{cleaned} photo"] if cleaned else ["documentary archive"]


def normalize_query_terms(term: str) -> List[str]:
    """Chuẩn hóa và chuyển đổi các từ khóa tiếng Việt dễ gây nhầm lẫn sang từ thực tế/thương mại."""
    t = clean_search_term(term)
    if not t:
        return []
    variants = [t]
    tool_replacements = {
        "cây rửa xe": ["vòi xịt rửa xe", "súng rửa xe", "máy rửa xe mini"],
        "cây cọ rửa xe": ["cọ rửa xe ô tô", "bàn chải rửa xe"],
        "cây cọ": ["cọ rửa xe", "bàn chải rửa xe"],
        "cây lau nhà": ["chổi lau nhà tự vắt", "cây lau nhà thông minh"],
        "cây lau kính": ["cây lau kính thông minh", "dụng cụ gạt kính"],
        "cây hút bụi": ["máy hút bụi mini cầm tay"]
    }
    for bad_w, good_list in tool_replacements.items():
        if bad_w in t:
            for g in good_list:
                cand = t.replace(bad_w, g)
                if cand not in variants:
                    variants.append(cand)
    return variants


def search_bing_images(query: str, limit: int = 8, allow_ecommerce: bool = False) -> List[str]:
    """Tìm ảnh từ Bing Image Search (ưu tiên endpoint async trả về đầy đủ murl) với cơ chế lọc nghiêm ngặt."""
    is_en = all(ord(ch) < 128 for ch in query)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9" if is_en else "vi,en-US;q=0.8,en;q=0.6"
    }
    encoded = urllib.parse.quote(query)
    mkt_param = "&cc=US&mkt=en-US&setlang=en" if is_en else ""
    candidate_endpoints = [
        f"https://www.bing.com/images/async?q={encoded}&first=1&count={max(20, limit * 3)}&adlt=moderate{mkt_param}",
        f"https://www.bing.com/images/search?q={encoded}&FORM=HDRSC2{mkt_param}",
    ]
    vn_book_junk = (".vn/", "sach", "baitap", "giaokhoa", "tienganh", "fahasa", "nxbgd")
    ecom_domains = ("shopee", "lazada", "tiki")
    clean_urls = []
    seen = set()

    for search_url in candidate_endpoints:
        req = urllib.request.Request(search_url, headers=headers)
        try:
            with _urlopen(req, timeout=7) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            murls = re.findall(r'&quot;murl&quot;:&quot;(http[^&]+)&quot;', html)
            if not murls:
                murls = re.findall(r'"murl":"(http[^"]+)"', html)
            for u in murls[:max(16, limit * 3)]:
                dec = urllib.parse.unquote(u)
                dec_low = dec.lower()
                if dec in seen:
                    continue
                if any(bad in dec_low for bad in BLACKLIST_URL_PATTERNS):
                    continue
                if not allow_ecommerce and any(ec in dec_low for ec in ecom_domains):
                    continue
                if is_en and any(j in dec_low for j in vn_book_junk):
                    continue
                seen.add(dec)
                clean_urls.append(dec)
                if len(clean_urls) >= limit:
                    return clean_urls
            if clean_urls:
                return clean_urls
        except Exception:
            continue
    return clean_urls


def search_openverse_images(query: str, limit: int = 8) -> List[str]:
    """Tìm ảnh chụp đời thực miễn phí bản quyền từ Openverse API (Flickr, Wikimedia, thư viện ảnh CC)."""
    headers = {"User-Agent": "ThreadsVideoBot/2.0 (multimedia@app.local)"}
    encoded = urllib.parse.quote(query)
    url = f"https://api.openverse.org/v1/images/?q={encoded}&page_size={max(12, limit * 2)}&mature=false"
    req = urllib.request.Request(url, headers=headers)
    try:
        with _urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        urls = []
        seen = set()
        for item in data.get("results", []):
            u = item.get("url") or item.get("thumbnail")
            if not u or u in seen:
                continue
            u_low = u.lower()
            if ".svg" in u_low or any(bad in u_low for bad in BLACKLIST_URL_PATTERNS):
                continue
            seen.add(u)
            urls.append(u)
            if len(urls) >= limit:
                break
        return urls
    except Exception:
        return []


def download_and_validate_image(img_url: str, output_path: str, target_width: int, target_height: int, query_context: str = "", exclude_hashes: set = None, vision_fail_open: bool = True) -> bool:
    """Tải và kiểm duyệt ảnh: loại bỏ logo, kiểm tra trùng lặp MD5, kiểm tra AI Vision, resize & crop chuẩn."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    temp_file = output_path + ".tmp.jpg"
    try:
        req = urllib.request.Request(img_url, headers=headers)
        with _urlopen(req, timeout=6) as resp:
            data = resp.read()

        if len(data) < 15000:
            return False

        img_hash = hashlib.md5(data).hexdigest()
        if exclude_hashes is not None and img_hash in exclude_hashes:
            return False

        with open(temp_file, "wb") as f:
            f.write(data)

        with Image.open(temp_file) as im:
            if is_logo_or_graphic(im):
                print(f"     ⚠️ Bỏ qua ảnh: Phát hiện là logo / sơ đồ / đồ họa phẳng không phù hợp")
                try: os.remove(temp_file)
                except OSError: pass
                return False

        # Thẩm định ngữ nghĩa thông minh bằng AI Gemini Vision
        if query_context and not verify_image_with_gemini(temp_file, query_context, fail_open=vision_fail_open):
            print(f"     ⚠️ Bỏ qua ảnh: AI Vision thẩm định ảnh KHÔNG LIÊN QUAN đến '{query_context}'")
            try: os.remove(temp_file)
            except OSError: pass
            return False

        with Image.open(temp_file) as im:
            im_rgb = im.convert("RGB")
            # Ưu tiên giữ phần trên (centering y=0.22) để không bị cắt mất đầu/khuôn mặt nhân vật
            im_fit = ImageOps.fit(
                im_rgb,
                (target_width, target_height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.22),
            )
            im_fit.save(output_path, "JPEG", quality=92)

        if exclude_hashes is not None:
            exclude_hashes.add(img_hash)
        try: os.remove(temp_file)
        except OSError: pass
        return True
    except Exception:
        if os.path.exists(temp_file):
            try: os.remove(temp_file)
            except OSError: pass
        return False


def search_and_download_real_image(
    queries: Union[str, List[str]],
    category: str = "general_life",
    output_path: str = None,
    target_width: int = 940,
    target_height: int = 620,
    fallback_to_category: bool = True,
    exclude_urls: set = None,
    exclude_hashes: set = None,
    topic_context: str = "",
    prefer_lifestyle: bool = False,
) -> str:
    """
    Tìm kiếm ảnh thật trên web theo ngữ cảnh chính xác và lưu về máy.
    Hỗ trợ đa nguồn (Wikipedia / Wikimedia / Bing Async / Openverse) và tự động lọc bỏ logo/ảnh rác bằng Gemini Vision.
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    if not output_path:
        filename = f"real_context_{int(random.random() * 1000000)}.jpg"
        output_path = os.path.join(OUT_DIR, filename)

    if isinstance(queries, str):
        query_list = [queries]
    else:
        query_list = list(queries)

    # Phân tách và chuẩn hóa từ khóa
    expanded_queries = []
    for raw in query_list:
        if not raw:
            continue
        parts = [p.strip() for p in re.split(r"[,;]|(?:\bvà\b)|(?:\band\b)", raw) if p.strip()]
        for p in parts:
            for nq in normalize_query_terms(p):
                if nq and nq not in expanded_queries:
                    expanded_queries.append(nq)

    category_map = {
        "couple_photo_fail": "blurry funny photo",
        "cafe_lifestyle": "coffee cup cafe table",
        "food_cooking": "burnt cooking food kitchen",
        "office_work": "messy office desk laptop",
        "shopping_tech": "parcel delivery package box",
        "student_exam": "study desk notebook exam",
        "general_life": "candid daily life photo"
    }

    if not expanded_queries:
        expanded_queries.append(category_map.get(category, "candid daily life photo"))

    context_str = topic_context.strip() or ", ".join(expanded_queries[:2])

    # Tách danh sách từ khóa tiếng Anh và tiếng Việt
    def is_ascii_en(s: str) -> bool:
        return all(ord(ch) < 128 for ch in s)

    en_queries = [q for q in expanded_queries if is_ascii_en(q)]
    vn_queries = [q for q in expanded_queries if not is_ascii_en(q)]

    # Tự động thêm biến thể rút gọn 2-3 từ nếu query quá dài (4+ từ) để tránh trả về 0 kết quả
    broad_en = []
    for q in en_queries:
        words = q.split()
        if len(words) >= 4:
            short_q = " ".join(words[:3])
            if short_q not in en_queries and short_q not in broad_en:
                broad_en.append(short_q)
    en_queries.extend(broad_en)

    # 1. Tự động sinh từ khóa tiếng Anh chuẩn Wikimedia nếu chưa có từ khóa tiếng Anh
    if not en_queries:
        gen_en = generate_wikimedia_visual_queries(context_str, context=context_str)
        for ge in gen_en:
            if ge and ge not in en_queries:
                en_queries.append(ge)

    def _try_candidates(candidates: List[str], source_label: str, vision_fail_open: bool = True) -> str:
        for cand in candidates:
            if exclude_urls is not None and cand in exclude_urls:
                continue
            if download_and_validate_image(
                cand,
                output_path,
                target_width,
                target_height,
                query_context=context_str,
                exclude_hashes=exclude_hashes,
                vision_fail_open=vision_fail_open,
            ):
                if exclude_urls is not None:
                    exclude_urls.add(cand)
                print(f"  📸 [ẢNH THẬT CHUẨN XÁC {source_label}]: {os.path.basename(output_path)} ({target_width}x{target_height})")
                return output_path
        return ""

    is_lifestyle = prefer_lifestyle or (category != "general_life")

    if is_lifestyle:
        # Luồng đời sống / Threads / Review đồ vật: ưu tiên Bing Async (EN + VN) & Openverse trước
        for q in vn_queries + en_queries:
            print(f"  🔍 [Bing Web] Tìm ảnh đời thực: '{q}'...")
            res = _try_candidates(search_bing_images(q, limit=10, allow_ecommerce=not is_ascii_en(q)), "BING", vision_fail_open=False)
            if res:
                return res

        for q in en_queries:
            print(f"  🔍 [Openverse] Tìm ảnh đời thực CC: '{q}'...")
            res = _try_candidates(search_openverse_images(q, limit=8), "OPENVERSE", vision_fail_open=False)
            if res:
                return res

        for q in en_queries:
            print(f"  🔍 [Wikimedia Commons] Tìm ảnh tư liệu: '{q}'...")
            res = _try_candidates(search_wikimedia_images(q, limit=8), "WIKIMEDIA", vision_fail_open=True)
            if res:
                return res
    else:
        # Luồng Documentary / True Story: Wikipedia -> Bing Async -> Wikimedia -> Openverse
        for q in en_queries[:2]:
            print(f"  🔍 [Wikipedia] Tìm ảnh chính xác: '{q}'...")
            res = _try_candidates(search_wikipedia_images(q, limit=5), "WIKIPEDIA", vision_fail_open=True)
            if res:
                return res

        for q in en_queries:
            print(f"  🔍 [Wikimedia Commons] Tìm ảnh tư liệu thực tế: '{q}'...")
            res = _try_candidates(search_wikimedia_images(q, limit=8), "WIKIMEDIA", vision_fail_open=True)
            if res:
                return res

        for q in en_queries + vn_queries:
            print(f"  🔍 [Bing Web] Tìm ảnh tư liệu thực tế: '{q}'...")
            res = _try_candidates(search_bing_images(q, limit=10, allow_ecommerce=False), "BING", vision_fail_open=False)
            if res:
                return res

        for q in en_queries:
            print(f"  🔍 [Openverse] Tìm ảnh tư liệu CC: '{q}'...")
            res = _try_candidates(search_openverse_images(q, limit=8), "OPENVERSE", vision_fail_open=False)
            if res:
                return res

    # 3B. Vòng cứu hộ thông minh: nếu tất cả từ khóa ban đầu không ra ảnh khớp, nhờ Gemini sinh 3 từ khóa bối cảnh rộng hơn
    if topic_context:
        rescue_queries = [q for q in generate_wikimedia_visual_queries(context_str, context=context_str) if q not in en_queries]
        for rq in rescue_queries[:3]:
            print(f"  🔍 [Rescue Search] Mở rộng từ khóa bối cảnh: '{rq}'...")
            res = _try_candidates(search_bing_images(rq, limit=8), "BING-RESCUE", vision_fail_open=False)
            if res:
                return res
            res = _try_candidates(search_openverse_images(rq, limit=6), "OPENVERSE-RESCUE", vision_fail_open=False)
            if res:
                return res
            res = _try_candidates(search_wikimedia_images(rq, limit=6), "WIKIMEDIA-RESCUE", vision_fail_open=True)
            if res:
                return res

    # 4. Dự phòng ảnh thật chất lượng cao từ kho assets theo phân loại ngữ cảnh nếu web không có
    if fallback_to_category:
        cat_file = os.path.join(ASSETS_DIR, f"{category}.jpg")
        if os.path.exists(cat_file):
            print(f"  📸 [DỰ PHÒNG CHUẨN NGỮ CẢNH ASSETS]: Sử dụng ảnh {category}.jpg")
            try:
                shutil.copy2(cat_file, output_path)
                return output_path
            except Exception:
                return cat_file
        gen_file = os.path.join(ASSETS_DIR, "general_life.jpg")
        if os.path.exists(gen_file):
            try:
                shutil.copy2(gen_file, output_path)
                return output_path
            except Exception:
                return gen_file

    print(f"  ⚠️ Không tìm thấy ảnh thực tế phù hợp 100% với ngữ cảnh '{context_str}', tắt thẻ popup để bảo vệ trải nghiệm xem!")
    return ""


def search_wikimedia_videos(query: str, limit: int = 5) -> List[str]:
    """Tìm video tư liệu thực tế (.webm/.mp4) từ Wikimedia Commons."""
    wiki_headers = {"User-Agent": "ThreadsVideoBot/2.0 (multimedia@app.local)"}
    encoded = urllib.parse.quote(f"{query} filetype:video")
    url = (
        f"https://commons.wikimedia.org/w/api.php?action=query&generator=search"
        f"&gsrsearch={encoded}&gsrnamespace=6&gsrlimit={limit * 2}"
        f"&prop=imageinfo&iiprop=url|size|mime&format=json"
    )
    req = urllib.request.Request(url, headers=wiki_headers)
    try:
        with _urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pages = data.get("query", {}).get("pages", {})
        urls = []
        for _, p in pages.items():
            infos = p.get("imageinfo", [])
            if not infos:
                continue
            info = infos[0]
            u = info.get("url", "")
            sz = int(info.get("size", 0) or 0)
            mime = (info.get("mime") or "").lower()
            if not u:
                continue
            if sz and (sz < 50_000 or sz > 18_000_000):
                continue
            path_lower = urllib.parse.urlparse(u).path.lower()
            if any(path_lower.endswith(ext) for ext in (".mp4", ".webm", ".ogv")) or "video" in mime:
                urls.append(u)
                if len(urls) >= limit:
                    break
        return urls
    except Exception:
        return []


def search_archive_org_videos(query: str, limit: int = 4) -> List[str]:
    """Tìm video tư liệu lịch sử / thời sự (.mp4) từ Internet Archive."""
    headers = {"User-Agent": "ThreadsVideoBot/2.0 (multimedia@app.local)"}
    encoded = urllib.parse.quote(f"({query}) AND mediatype:movies")
    search_url = f"https://archive.org/advancedsearch.php?q={encoded}&fl[]=identifier&rows={limit}&output=json"
    try:
        req = urllib.request.Request(search_url, headers=headers)
        with _urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        docs = data.get("response", {}).get("docs", [])
        urls = []
        for doc in docs:
            ident = doc.get("identifier")
            if not ident:
                continue
            meta_url = f"https://archive.org/metadata/{ident}/files"
            mreq = urllib.request.Request(meta_url, headers=headers)
            with _urlopen(mreq, timeout=6) as mresp:
                mdata = json.loads(mresp.read().decode("utf-8"))
            for f in mdata.get("result", []):
                fname = f.get("name", "")
                sz = int(f.get("size", 0) or 0)
                if fname.lower().endswith(".mp4") and 80_000 <= sz <= 20_000_000:
                    urls.append(f"https://archive.org/download/{ident}/{urllib.parse.quote(fname)}")
                    break
            if len(urls) >= limit:
                break
        return urls
    except Exception:
        return []


def search_and_download_real_video(
    queries: Union[str, List[str]],
    output_dir: str,
    prefix: str = "scene_vid",
    exclude_urls: set = None,
) -> str:
    """
    Tìm và tải video tư liệu thật (.mp4/.webm) cho các phân cảnh loại 'video'
    từ Wikimedia Commons và Internet Archive. Trả về đường dẫn file hoặc '' nếu không có.
    """
    os.makedirs(output_dir, exist_ok=True)
    query_list = [queries] if isinstance(queries, str) else list(queries)
    clean_queries = [clean_search_term(q) for q in query_list if q and clean_search_term(q)]
    if not clean_queries:
        return ""

    headers = {"User-Agent": "ThreadsVideoBot/2.0 (multimedia@app.local)"}
    for q in clean_queries[:3]:
        print(f"  🎬 [Wikimedia Video] Tìm clip tư liệu: '{q}'...")
        candidates = search_wikimedia_videos(q, limit=4)
        if not candidates:
            print(f"  🎬 [Archive.org] Tìm clip tư liệu: '{q}'...")
            candidates = search_archive_org_videos(q, limit=3)

        for cand in candidates:
            if exclude_urls is not None and cand in exclude_urls:
                continue
            ext = os.path.splitext(urllib.parse.urlparse(cand).path)[1].lower()
            if ext not in (".mp4", ".webm", ".ogv"):
                ext = ".mp4"
            out_path = os.path.join(output_dir, f"{prefix}{ext}")
            try:
                req = urllib.request.Request(cand, headers=headers)
                with _urlopen(req, timeout=15) as resp:
                    data = resp.read()
                if len(data) < 50_000:
                    continue
                with open(out_path, "wb") as f:
                    f.write(data)
                if exclude_urls is not None:
                    exclude_urls.add(cand)
                print(f"  🎥 [ĐÃ TẢI VIDEO TƯ LIỆU]: {os.path.basename(out_path)} ({len(data) // 1024} KB)")
                return out_path
            except Exception:
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                except OSError:
                    pass
                continue
    return ""
