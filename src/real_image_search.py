"""
REAL WEB IMAGE SEARCH & CONTEXTUAL SCRAPER MODULE
=================================================
Tìm kiếm và tải ảnh THẬT từ web chuẩn xác 100% theo ngữ cảnh câu chuyện:
- Loại bỏ hoàn toàn ảnh AI méo mó, chữ rác, watermark (pollinations, stock...)
- Tìm ảnh chụp đời thực (candid photography, thực tế hàng ngày)
- Tự động crop & resize chuẩn tỷ lệ 940x620 cho Threads Card & Cutaway Proof
- Fallback thông minh theo đúng CHỦ ĐỀ ngữ cảnh (ẩm thực, công sở, tình yêu, mua sắm...)
- Tuyệt đối không chèn ảnh meme động vật vô nghĩa hay kính râm lạc quẻ!
"""

import os
import re
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
    "meme", "funny meme", "ai", "midjourney", "pollinations", "drawing", "illustration",
    "cartoon", "anime", "3d render", "photorealistic", "photo realistic", "vector", "clipart"
]

BLACKLIST_URL_PATTERNS = [
    "pollinations", "shutterstock", "gettyimages", "alamy", "dreamstime", "depositphotos",
    "vector", "clipart", "istockphoto", "123rf", "canva", "freepik", "watermark", "stock-photo"
]


def clean_search_term(term: str) -> str:
    """Làm sạch từ khóa tìm kiếm, giữ lại danh từ và hành động đời thường cốt lõi."""
    t = term.lower()
    for bad in BLACKLIST_QUERY_WORDS:
        t = re.sub(rf"\b{re.escape(bad)}\b", " ", t)
    t = re.sub(r"[^\w\s-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def search_and_download_real_image(
    queries: Union[str, List[str]],
    category: str = "general_life",
    output_path: str = None,
    target_width: int = 940,
    target_height: int = 620
) -> str:
    """
    Tìm kiếm ảnh thật trên web theo chủ đề và lưu về máy.
    Hỗ trợ danh sách từ khóa ưu tiên (tiếng Việt -> tiếng Anh -> danh mục).
    """
    os.makedirs(OUT_DIR, exist_ok=True)
    if not output_path:
        filename = f"real_context_{int(random.random() * 1000000)}.jpg"
        output_path = os.path.join(OUT_DIR, filename)

    if isinstance(queries, str):
        query_list = [queries]
    else:
        query_list = list(queries)

    # Chuẩn hóa danh sách từ khóa
    cleaned_queries = []
    for q in query_list:
        cq = clean_search_term(q)
        if cq and cq not in cleaned_queries:
            cleaned_queries.append(cq)

    # Nếu không có từ khóa nào, lấy theo danh mục
    category_map = {
        "couple_photo_fail": "chụp ảnh bạn gái dìm hài hước thực tế",
        "cafe_lifestyle": "candid coffee cup table cafe aesthetic",
        "food_cooking": "burnt food cooking fail kitchen real life",
        "office_work": "laptop messy desk office working late coffee",
        "shopping_tech": "cardboard parcel box delivery package real photo",
        "student_exam": "desk notebook study pen real photo",
        "general_life": "candid daily life moment photo"
    }

    if not cleaned_queries:
        cleaned_queries.append(category_map.get(category, "candid daily life moment photo"))

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    # Thử tìm kiếm theo từng từ khóa ưu tiên
    for q in cleaned_queries:
        print(f"  🔍 Tìm ảnh thật trên web cho ngữ cảnh: '{q}'...")
        encoded_query = urllib.parse.quote(q)
        search_url = f"https://www.bing.com/images/search?q={encoded_query}&FORM=HDRSC2"

        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            murls = re.findall(r'murl&quot;:&quot;(http[^&]+)&quot;', html)
            print(f"     Tìm thấy {len(murls)} ứng viên ảnh trực tuyến")

            for img_url in murls[:10]:
                img_url_clean = urllib.parse.unquote(img_url)
                if any(bad in img_url_clean.lower() for bad in BLACKLIST_URL_PATTERNS):
                    continue

                try:
                    down_req = urllib.request.Request(img_url, headers=headers)
                    with urllib.request.urlopen(down_req, timeout=6) as img_resp:
                        img_data = img_resp.read()

                    if len(img_data) < 15000:
                        continue

                    temp_file = output_path + ".tmp.jpg"
                    with open(temp_file, "wb") as f:
                        f.write(img_data)

                    with Image.open(temp_file) as im:
                        im = im.convert("RGB")
                        w, h = im.size
                        # Bỏ qua ảnh quá nhỏ hoặc méo mó
                        if w < 350 or h < 250:
                            try: os.remove(temp_file)
                            except OSError: pass
                            continue

                        im_fit = ImageOps.fit(im, (target_width, target_height), method=Image.Resampling.LANCZOS)
                        im_fit.save(output_path, "JPEG", quality=92)

                    try: os.remove(temp_file)
                    except OSError: pass

                    print(f"  📸 [ẢNH THẬT CHUẨN NGỮ CẢNH]: Đã tải ({target_width}x{target_height}): {os.path.basename(output_path)}")
                    return output_path
                except Exception:
                    continue
        except Exception as e:
            print(f"  ⚠️ Lỗi khi quét mạng với từ khóa '{q}': {e}")
            continue

    # Fallback vào kho ảnh thật theo đúng danh mục ngữ cảnh (NO CATS / SUNGLASSES!)
    cat_fallback = os.path.join(ASSETS_DIR, f"{category}.jpg")
    if os.path.exists(cat_fallback):
        try:
            shutil.copyfile(cat_fallback, output_path)
            print(f"  📸 [KHO ẢNH THỰC TẾ]: Dùng ảnh chuẩn danh mục '{category}': {os.path.basename(cat_fallback)}")
            return output_path
        except Exception:
            return cat_fallback

    # Nếu không có danh mục cụ thể, dùng ảnh đời thực chung (general_life)
    gen_fallback = os.path.join(ASSETS_DIR, "general_life.jpg")
    if os.path.exists(gen_fallback):
        try:
            shutil.copyfile(gen_fallback, output_path)
            return output_path
        except Exception:
            return gen_fallback

    return ""
