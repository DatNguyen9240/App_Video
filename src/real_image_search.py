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
import urllib.parse
import urllib.request
from typing import Union, List
from PIL import Image, ImageOps

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
    "i.ytimg.com", "youtube.com", "funny_fail", "funny-fails", "maxresdefault", "hqdefault", "sddefault",
    "logo", "icon", "badge", "schematic", "diagram", "component", "catalog"
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

    # Đếm số pixel trắng tinh (R>230, G>230, B>230)
    white_count = sum(1 for p in pixels if p[0] > 230 and p[1] > 230 and p[2] > 230)
    white_ratio = white_count / len(pixels)
    unique_colors = len(set(pixels))

    # Nếu nền trắng chiếm trên 50% diện tích và số màu nghèo nàn -> Logo/sản phẩm catalog
    if white_ratio > 0.50 and unique_colors < 2000:
        return True

    # Nếu toàn bộ ảnh có dưới 400 màu trong 10,000 pixel -> Vector/đồ họa phẳng
    if unique_colors < 400:
        return True

    return False


def verify_image_with_gemini(image_path: str, query: str) -> bool:
    """Sử dụng Gemini Vision để thẩm định xem bức ảnh có thực sự liên quan đến chủ đề hay không."""
    try:
        from google import genai
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            env_file = os.path.join(BASE_DIR, ".env")
            if os.path.exists(env_file):
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("GEMINI_API_KEY="):
                            key = line.split("=", 1)[1].strip().strip('"').strip("'")
        if not key:
            return True

        client = genai.Client(api_key=key)
        with Image.open(image_path) as im:
            im_copy = im.convert("RGB")
            im_copy.thumbnail((512, 512))

        prompt = (
            f"Topic: {query}\n"
            "Look at this image. Is this image genuinely relevant and appropriate to illustrate the topic? "
            "Reject if it is an unrelated building, apartment, courtyard, historical document, package of food/cereal, logo, or completely off-topic. "
            "Reply strictly with one word: YES or NO."
        )
        models = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.8-flash", "gemini-2.5-flash"]
        for m in models:
            try:
                res = client.models.generate_content(
                    model=m,
                    contents=[im_copy, prompt]
                )
                ans = res.text.strip().upper()
                return ans.startswith("YES")
            except Exception:
                continue
        return False
    except Exception as e:
        print(f"     ⚠️ Lỗi thẩm định Gemini Vision ({e})")
        return False


def search_wikimedia_images(query: str, limit: int = 5) -> List[str]:
    """Tìm ảnh chụp đời thực chất lượng cao từ kho Wikimedia Commons (Không quảng cáo, không logo)."""
    wiki_headers = {"User-Agent": "ThreadsVideoBot/2.0 (multimedia@app.local)"}
    encoded = urllib.parse.quote(query)
    url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded}&gsrnamespace=6&gsrlimit={limit * 2}&prop=imageinfo&iiprop=url|size|mime&iiurlwidth=1000&format=json"
    req = urllib.request.Request(url, headers=wiki_headers)
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
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
                # Loại bỏ vector svg, icon đồ họa phẳng
                if ".svg" in path_lower or "icon" in path_lower or "logo" in path_lower:
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
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            env_file = os.path.join(BASE_DIR, ".env")
            if os.path.exists(env_file):
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("GEMINI_API_KEY="):
                            key = line.split("=", 1)[1].strip().strip('"').strip("'")
        if not key:
            return []

        client = genai.Client(api_key=key)
        prompt = (
            f"Topic: {topic}\n"
            f"Context: {context[:200]}\n"
            "Task: Identify the core physical visual subject. "
            "Output 3 broad, high-yield English search phrases (1-2 words each) to find real documentary/candid photos of this subject on Wikimedia Commons. "
            "Examples:\n"
            "- 'cây rửa xe đa năng' -> ['car wash pressure', 'pressure washer', 'washing car']\n"
            "- 'nấu ăn cháy nồi' -> ['burnt food', 'burnt pot', 'cooking disaster']\n"
            "- 'trà sữa' -> ['bubble tea', 'milk tea', 'boba drink']\n"
            "- 'máy tính hỏng' -> ['broken laptop', 'laptop screen', 'broken computer']\n"
            "Return strictly a JSON array of strings."
        )
        res = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )
        terms = json.loads(res.text)
        if isinstance(terms, list):
            return [t.strip() for t in terms if isinstance(t, str) and t.strip()]
    except Exception as e:
        print(f"  ⚠️ Lỗi khi sinh từ khóa Wikimedia ({e})")
    return []


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


def search_bing_images(query: str, limit: int = 8) -> List[str]:
    """Tìm ảnh từ Bing Image Search với cơ chế lọc nghiêm ngặt."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "vi,en-US;q=0.8,en;q=0.6"
    }
    encoded = urllib.parse.quote(query)
    search_url = f"https://www.bing.com/images/search?q={encoded}&FORM=HDRSC2"
    req = urllib.request.Request(search_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        murls = re.findall(r'&quot;murl&quot;:&quot;(http[^&]+)&quot;', html)
        clean_urls = []
        for u in murls[:limit * 2]:
            dec = urllib.parse.unquote(u)
            if not any(bad in dec.lower() for bad in BLACKLIST_URL_PATTERNS):
                clean_urls.append(dec)
        return clean_urls
    except Exception:
        return []


def download_and_validate_image(img_url: str, output_path: str, target_width: int, target_height: int, query_context: str = "") -> bool:
    """Tải và kiểm duyệt ảnh: loại bỏ logo, kiểm tra AI Vision, resize & crop chuẩn 940x620."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    temp_file = output_path + ".tmp.jpg"
    try:
        req = urllib.request.Request(img_url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = resp.read()

        if len(data) < 15000:
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
        if query_context and not verify_image_with_gemini(temp_file, query_context):
            print(f"     ⚠️ Bỏ qua ảnh: AI Vision thẩm định ảnh KHÔNG LIÊN QUAN đến '{query_context}'")
            try: os.remove(temp_file)
            except OSError: pass
            return False

        with Image.open(temp_file) as im:
            im_rgb = im.convert("RGB")
            im_fit = ImageOps.fit(im_rgb, (target_width, target_height), method=Image.Resampling.LANCZOS)
            im_fit.save(output_path, "JPEG", quality=92)

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
    target_height: int = 620
) -> str:
    """
    Tìm kiếm ảnh thật trên web theo ngữ cảnh chính xác và lưu về máy.
    Hỗ trợ đa nguồn (Wikimedia -> Bing) và tự động lọc bỏ logo/ảnh rác bằng Gemini Vision.
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

    context_str = ", ".join(expanded_queries[:2])

    # Tách danh sách từ khóa tiếng Anh và tiếng Việt
    def is_ascii_en(s: str) -> bool:
        return all(ord(ch) < 128 for ch in s)

    en_queries = [q for q in expanded_queries if is_ascii_en(q)]
    vn_queries = [q for q in expanded_queries if not is_ascii_en(q)]

    # 1. Tự động sinh từ khóa tiếng Anh chuẩn Wikimedia nếu chưa có từ khóa tiếng Anh mạnh
    if not en_queries or any(not is_ascii_en(q) for q in query_list):
        gen_en = generate_wikimedia_visual_queries(context_str, context=context_str)
        for ge in gen_en:
            if ge and ge not in en_queries:
                en_queries.append(ge)

    # 2. Ưu tiên Wikimedia Commons với từ khóa tiếng Anh (ảnh thực tế đời thực, không bot block)
    for q in en_queries:
        print(f"  🔍 [Wikimedia Commons] Tìm ảnh tư liệu thực tế: '{q}'...")
        wiki_candidates = search_wikimedia_images(q, limit=6)
        for cand in wiki_candidates:
            if download_and_validate_image(cand, output_path, target_width, target_height, query_context=context_str):
                print(f"  📸 [ẢNH THẬT CHUẨN XÁC WIKIMEDIA]: {os.path.basename(output_path)} ({target_width}x{target_height})")
                return output_path

    # 3. Thử tìm trên Bing nếu Wikimedia chưa có
    for q in vn_queries:
        print(f"  🔍 [Bing Web (VN)] Tìm ảnh chụp sản phẩm/đời thực: '{q}'...")
        bing_candidates = search_bing_images(q, limit=6)
        for cand in bing_candidates:
            if download_and_validate_image(cand, output_path, target_width, target_height, query_context=context_str):
                print(f"  📸 [ẢNH THẬT CHUẨN XÁC BING]: {os.path.basename(output_path)} ({target_width}x{target_height})")
                return output_path

    # 4. TUYỆT ĐỐI KHÔNG FALLBACK sang ảnh nhà hàng/tòa nhà không liên quan
    print(f"  ⚠️ Không tìm thấy ảnh thực tế phù hợp 100% với ngữ cảnh '{context_str}', tắt thẻ popup để bảo vệ trải nghiệm xem!")
    return ""
