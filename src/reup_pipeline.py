"""
REUP & CRAWL VIDEO PIPELINE (v1.0)
==================================
Quy trình tự động:
  1. Crawl danh sách video từ link kênh/playlist/từ khóa (TikTok, YouTube, Douyin, Bilibili, FB...) qua yt-dlp.
  2. Tải video gốc từ link (hỗ trợ proxy xoay vòng từ proxy_manager & cookies.txt).
  3. Trích xuất âm thanh -> Gemini Multimodal Audio bóc thoại theo timeline + dịch/viết lại tiếng Việt viral + SEO caption.
     (Nếu video không có lời thoại, tự động lấy mẫu frame để Gemini Vision viết lời bình theo diễn biến).
  4. Tổng hợp giọng đọc tiếng Việt bằng OmniVoice (03_tts.text_to_speech).
  5. Trộn âm thanh Audio Ducking mượt (pydub: gộp khoảng lặng < 2.5s, crossfade 300ms/500ms).
  6. Render video 9:16 (1080x1920): tự chọn GPU encoder, che phụ đề gốc bằng dải blur mềm viền,
     áp bộ lọc chống trùng lặp (anti-reup), chuẩn màu bt709 và burn-in phụ đề ASS sắc nét.
"""

import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, List, Optional

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import pipeline_tracker as default_tracker
from pydub import AudioSegment

REUP_WORK_DIR = os.path.join(BASE_DIR, "output", "reup_work")
FINAL_DIR = os.path.join(BASE_DIR, "output", "final")
REUP_MANUAL_COOKIES_PATH = os.path.join(BASE_DIR, "data", "reup_cookies.txt")

_CACHED_ENCODER: Optional[str] = None


def get_ffmpeg_exe() -> str:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


AudioSegment.converter = get_ffmpeg_exe()


def get_ffprobe_exe() -> str:
    sys_ffprobe = shutil.which("ffprobe")
    if sys_ffprobe:
        return sys_ffprobe
    ffmpeg_exe = get_ffmpeg_exe()
    candidate = ffmpeg_exe.replace("ffmpeg", "ffprobe")
    if candidate != ffmpeg_exe and os.path.exists(candidate):
        return candidate
    return "ffprobe"


def _get_ytdlp_base_cmd() -> List[str]:
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def _get_proxy_url() -> str:
    try:
        import proxy_manager
        return proxy_manager.get_next_proxy_url() or ""
    except Exception:
        return ""


def _get_gemini_keys() -> List[str]:
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


def _test_encoder_works(ffmpeg_exe: str, encoder: str) -> bool:
    try:
        cmd = [
            ffmpeg_exe, "-y", "-nostdin",
            "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.1",
            "-c:v", encoder,
            "-f", "null", "-"
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=6)
        return True
    except Exception:
        return False


def detect_best_encoder(ffmpeg_exe: str = "") -> str:
    """Tự động dò và cache encoder phần cứng nhanh nhất (NVENC/QSV/AMF/MF), fallback libx264."""
    global _CACHED_ENCODER
    if _CACHED_ENCODER is not None:
        return _CACHED_ENCODER
    exe = ffmpeg_exe or get_ffmpeg_exe()
    try:
        res = subprocess.run([exe, "-encoders"], capture_output=True, text=True, check=True, timeout=8)
        encoders_out = res.stdout
        for cand in ("h264_nvenc", "h264_qsv", "h264_amf", "h264_mf"):
            if cand in encoders_out and _test_encoder_works(exe, cand):
                _CACHED_ENCODER = cand
                return _CACHED_ENCODER
    except Exception:
        pass
    _CACHED_ENCODER = "libx264"
    return _CACHED_ENCODER


def _format_duration(seconds: float) -> str:
    if not seconds or seconds <= 0:
        return "0:00"
    sec = int(round(seconds))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _detect_platform_from_url(url: str) -> str:
    low = (url or "").lower()
    if "tiktok.com" in low:
        return "TikTok"
    if "douyin.com" in low or "iesdouyin.com" in low:
        return "Douyin"
    if "youtube.com" in low or "youtu.be" in low:
        return "YouTube"
    if "bilibili.com" in low or "b23.tv" in low:
        return "Bilibili"
    if "facebook.com" in low or "fb.watch" in low:
        return "Facebook"
    if "instagram.com" in low:
        return "Instagram"
    return "Web"


def normalize_video_url(raw_text: str) -> str:
    """
    Trích xuất URL sạch từ đoạn text chia sẻ (ví dụ text copy từ app Douyin/Bilibili)
    và chuẩn hóa dạng URL đặc biệt như ?modal_id=... thành /video/{id}.
    """
    txt = (raw_text or "").strip()
    if not txt:
        return ""
    m = re.search(r"https?://[^\s\u4e00-\u9fff\"'<>]+", txt)
    url = m.group(0).rstrip(".,;!?") if m else txt
    if not url.startswith(("http://", "https://")):
        return txt

    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "douyin.com" in parsed.netloc.lower() and "modal_id" in qs:
        vid = qs["modal_id"][0]
        return f"https://www.douyin.com/video/{vid}"
    return url


def _infer_cookie_domain(target_url: str = "", platform_source: str = "") -> str:
    """Infer default cookie domain from a target URL or selected platform."""
    hint = f"{target_url or ''} {platform_source or ''}".lower()
    if "bilibili" in hint or "b23.tv" in hint:
        return ".bilibili.com"
    if "tiktok" in hint:
        return ".tiktok.com"
    if "youtube" in hint or "youtu.be" in hint:
        return ".youtube.com"
    if "facebook" in hint or "fb.watch" in hint:
        return ".facebook.com"
    return ".douyin.com"


def _format_netscape_line(
    domain: str,
    name: str,
    value: str,
    *,
    path: str = "/",
    secure: bool = True,
    expires: Optional[int] = None,
) -> str:
    dom = (domain or ".douyin.com").strip()
    include_sub = "TRUE" if dom.startswith(".") else "FALSE"
    sec_flag = "TRUE" if secure else "FALSE"
    exp_ts = int(expires) if (expires is not None and int(expires) > 0) else (int(time.time()) + 30 * 86400)
    clean_path = (path or "/").strip() or "/"
    clean_name = name.replace("\t", " ").replace("\r", "").replace("\n", "").strip()
    clean_val = value.replace("\t", " ").replace("\r", "").replace("\n", "").strip()
    return f"{dom}\t{include_sub}\t{clean_path}\t{sec_flag}\t{exp_ts}\t{clean_name}\t{clean_val}"


def normalize_cookie_text_to_netscape(
    raw_text: str,
    target_url: str = "",
    platform_source: str = "",
):
    """
    Convert pasted cookie text (raw HTTP Cookie header, JSON Cookie-Editor export, or Netscape cookies.txt)
    into standard Netscape cookies.txt format without writing/reading secrets outside caller control.
    """
    txt = (raw_text or "").strip()
    if not txt:
        raise ValueError("Nội dung cookie trống.")

    default_domain = _infer_cookie_domain(target_url, platform_source)
    default_exp = int(time.time()) + 30 * 86400
    lines_out: List[str] = []
    domains_found = set()
    detected_format = "header"

    stripped = txt.lstrip()
    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(stripped)
            cookie_objs = parsed.get("cookies") if isinstance(parsed, dict) else parsed
            if not isinstance(cookie_objs, list):
                raise ValueError("JSON cookie phải là mảng danh sách cookie.")
            detected_format = "json"
            for obj in cookie_objs:
                if not isinstance(obj, dict):
                    continue
                c_name = str(obj.get("name") or "").strip()
                if not c_name or obj.get("value") is None:
                    continue
                c_val = str(obj.get("value"))
                dom = str(obj.get("domain") or default_domain).strip() or default_domain
                if not dom.startswith(".") and obj.get("hostOnly") is False:
                    dom = f".{dom}"
                exp_raw = obj.get("expirationDate") or obj.get("expiry") or obj.get("expires")
                try:
                    exp_int = int(float(exp_raw)) if exp_raw else default_exp
                except (TypeError, ValueError):
                    exp_int = default_exp
                domains_found.add(dom)
                lines_out.append(
                    _format_netscape_line(
                        dom,
                        c_name,
                        c_val,
                        path=str(obj.get("path") or "/"),
                        secure=bool(obj.get("secure", True)),
                        expires=exp_int,
                    )
                )
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON cookie không hợp lệ: {exc}") from exc
    elif "\t" in txt:
        detected_format = "netscape"
        for raw_line in txt.splitlines():
            s = raw_line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split("\t")
            if len(parts) < 7:
                continue
            dom, include_sub, c_path, sec_flag, exp_str, c_name, c_val = parts[:7]
            c_name = c_name.strip()
            if not c_name:
                continue
            dom = dom.strip() or default_domain
            try:
                exp_int = int(float(exp_str)) if exp_str.strip() else default_exp
                if exp_int <= 0:
                    exp_int = default_exp
            except ValueError:
                exp_int = default_exp
            domains_found.add(dom)
            lines_out.append(
                f"{dom}\t{include_sub.strip().upper() or 'TRUE'}\t{c_path.strip() or '/'}\t"
                f"{sec_flag.strip().upper() or 'TRUE'}\t{exp_int}\t{c_name}\t{c_val.strip()}"
            )
    else:
        header_body = re.sub(r"^(?:cookie\s*:\s*)", "", txt, flags=re.IGNORECASE).strip()
        if (header_body.startswith('"') and header_body.endswith('"')) or (
            header_body.startswith("'") and header_body.endswith("'")
        ):
            header_body = header_body[1:-1].strip()
        skip_attrs = {"path", "domain", "expires", "max-age", "samesite"}
        segments = re.split(r"[;\r\n]+", header_body)
        for seg in segments:
            part = seg.strip()
            if not part or "=" not in part:
                continue
            c_name, c_val = part.split("=", 1)
            c_name = c_name.strip()
            if not c_name or c_name.lower() in skip_attrs:
                continue
            domains_found.add(default_domain)
            lines_out.append(_format_netscape_line(default_domain, c_name, c_val.strip(), expires=default_exp))

    if not lines_out:
        raise ValueError(
            "Không nhận diện được cookie hợp lệ (hỗ trợ chuỗi Cookie: a=b; c=d, JSON Cookie-Editor hoặc Netscape cookies.txt)."
        )

    netscape_body = "# Netscape HTTP Cookie File\n# Generated by App_Video Reup Direct Cookie Input\n" + "\n".join(lines_out) + "\n"
    return netscape_body, {
        "cookie_count": len(lines_out),
        "domains": sorted(domains_found),
        "format": detected_format,
    }


def _inspect_netscape_cookie_file(path: str) -> Optional[Dict]:
    if not os.path.exists(path) or os.path.getsize(path) <= 10:
        return None
    count = 0
    domains = set()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                parts = s.split("\t")
                if len(parts) >= 7 and parts[5].strip():
                    count += 1
                    domains.add(parts[0].strip())
        mtime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))
        return {
            "cookie_count": count,
            "domains": sorted(d for d in domains if d),
            "updated_at": mtime,
        }
    except OSError:
        return None


def get_manual_cookies_status(cookies_path: Optional[str] = None) -> Dict:
    """Return non-sensitive metadata for saved manual/root cookie files."""
    target_path = cookies_path or REUP_MANUAL_COOKIES_PATH
    manual_info = _inspect_netscape_cookie_file(target_path)
    if manual_info:
        return {"has_cookies": True, "source": "manual", **manual_info}
    root_cookies = os.path.join(BASE_DIR, "cookies.txt")
    root_info = _inspect_netscape_cookie_file(root_cookies)
    if root_info:
        return {"has_cookies": True, "source": "root_file", **root_info}
    return {"has_cookies": False, "source": None, "cookie_count": 0, "domains": [], "updated_at": None}


def save_manual_cookies(
    raw_text: str,
    target_url: str = "",
    platform_source: str = "",
    cookies_path: Optional[str] = None,
) -> Dict:
    """Validate, convert to Netscape format, and save pasted cookies on disk safely."""
    target_path = cookies_path or REUP_MANUAL_COOKIES_PATH
    netscape_text, meta = normalize_cookie_text_to_netscape(
        raw_text,
        target_url=target_url,
        platform_source=platform_source,
    )
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    with open(target_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(netscape_text)
    st = get_manual_cookies_status(cookies_path=target_path)
    return {**st, "format": meta["format"]}


def clear_manual_cookies(cookies_path: Optional[str] = None) -> Dict:
    """Delete saved manual cookie file if present and return updated status."""
    target_path = cookies_path or REUP_MANUAL_COOKIES_PATH
    if os.path.exists(target_path):
        try:
            os.remove(target_path)
        except OSError:
            pass
    return get_manual_cookies_status(cookies_path=target_path)


def _build_ytdlp_auth_and_net_args(
    url: str = "",
    browser_cookies: str = "",
    custom_cookies: str = "",
    platform_source: str = "",
) -> List[str]:
    """Tạo args proxy, cookies, referer phù hợp cho Douyin / Bilibili / TikTok / YouTube."""
    args = []
    if custom_cookies and custom_cookies.strip():
        try:
            save_manual_cookies(custom_cookies, target_url=url, platform_source=platform_source)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        args.extend(["--cookies", REUP_MANUAL_COOKIES_PATH])
    else:
        manual_exists = os.path.exists(REUP_MANUAL_COOKIES_PATH) and os.path.getsize(REUP_MANUAL_COOKIES_PATH) > 10
        root_cookies = os.path.join(BASE_DIR, "cookies.txt")
        root_exists = os.path.exists(root_cookies) and os.path.getsize(root_cookies) > 10
        if browser_cookies == "manual":
            if manual_exists:
                args.extend(["--cookies", REUP_MANUAL_COOKIES_PATH])
            elif root_exists:
                args.extend(["--cookies", root_cookies])
        elif browser_cookies in ("chrome", "edge"):
            if root_exists:
                args.extend(["--cookies", root_cookies])
            else:
                args.extend(["--cookies-from-browser", browser_cookies])
        elif manual_exists:
            args.extend(["--cookies", REUP_MANUAL_COOKIES_PATH])
        elif root_exists:
            args.extend(["--cookies", root_cookies])

    proxy_url = _get_proxy_url()
    if proxy_url:
        args.extend(["--proxy", proxy_url])

    low = (url or "").lower()
    if "bilibili.com" in low or "b23.tv" in low:
        args.extend(["--add-header", "Referer:https://www.bilibili.com/"])
    elif "douyin.com" in low:
        args.extend(["--add-header", "Referer:https://www.douyin.com/"])
    return args


def _translate_search_keyword_to_zh(keyword: str) -> str:
    """Dịch nhanh từ khóa tiếng Việt/Anh sang tiếng Trung ngắn gọn để tìm đúng video trên Bilibili/Douyin."""
    kw = (keyword or "").strip()
    if not kw:
        return kw
    if re.search(r"[\u4e00-\u9fff]", kw):
        return kw

    # 1. Dịch nhanh qua Google Translate public endpoint (không tốn quota Gemini)
    try:
        import urllib.request
        gt_url = (
            "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q="
            + urllib.parse.quote(kw)
        )
        req = urllib.request.Request(gt_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        zh = "".join(p[0] for p in (data[0] or []) if p and p[0]).strip()
        if zh and re.search(r"[\u4e00-\u9fff]", zh):
            return zh
    except Exception:
        pass

    # 2. Fallback qua Gemini nếu endpoint trên lỗi
    keys = _get_gemini_keys()
    if keys:
        try:
            from google import genai
            prompt = (
                f"Translate this video search keyword to short Simplified Chinese (2-5 chars) for Bilibili/Douyin search.\n"
                f"Keyword: {kw}\nReturn ONLY the Chinese phrase."
            )
            for key in keys[:2]:
                client = genai.Client(api_key=key)
                for m in ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.8-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash"):
                    try:
                        res = client.models.generate_content(model=m, contents=prompt)
                        zh = (getattr(res, "text", None) or "").strip().splitlines()[0].strip()
                        if zh and re.search(r"[\u4e00-\u9fff]", zh):
                            return zh
                    except Exception:
                        continue
        except Exception:
            pass
    return kw


def crawl_bilibili_search(keyword: str, limit: int = 12) -> List[Dict]:
    """Tìm kiếm video Bilibili trực tiếp qua Web Search API (kèm buvid3/buvid4 từ finger/spi tránh 412)."""
    import requests
    search_kw = _translate_search_keyword_to_zh(keyword)
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
    })
    proxy_url = _get_proxy_url()
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        spi_resp = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=6, proxies=proxies)
        if spi_resp.status_code == 200:
            spi_data = spi_resp.json().get("data") or {}
            if spi_data.get("b_3"):
                s.cookies.set("buvid3", spi_data["b_3"])
            if spi_data.get("b_4"):
                s.cookies.set("buvid4", spi_data["b_4"])
    except Exception:
        s.get("https://www.bilibili.com/", timeout=6, proxies=proxies)

    resp = s.get(
        "https://api.bilibili.com/x/web-interface/search/type",
        params={"search_type": "video", "keyword": search_kw, "page": 1, "page_size": max(1, min(limit, 30))},
        headers={"Referer": "https://search.bilibili.com/"},
        timeout=10,
        proxies=proxies,
    )
    resp.raise_for_status()
    data = resp.json()
    raw_items = data.get("data", {}).get("result", []) or []
    results = []
    for item in raw_items[:limit]:
        bvid = item.get("bvid") or ""
        if not bvid:
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", str(item.get("title") or ""))).strip()
        pic = str(item.get("pic") or "")
        if pic.startswith("//"):
            pic = "https:" + pic
        dur_raw = str(item.get("duration") or "0:00")
        dur_sec = 0.0
        if ":" in dur_raw:
            parts = dur_raw.split(":")
            try:
                dur_sec = sum(int(p) * (60 ** idx) for idx, p in enumerate(reversed(parts)))
            except Exception:
                dur_sec = 0.0
        results.append({
            "id": bvid,
            "title": title or bvid,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "thumbnail": pic,
            "duration": dur_sec,
            "duration_str": _format_duration(dur_sec) if dur_sec > 0 else dur_raw,
            "uploader": item.get("author") or "Bilibili",
            "view_count": int(item.get("play") or 0),
            "platform": "Bilibili",
            "search_keyword": search_kw,
        })
    return results


def crawl_video_links(
    query_or_url: str,
    limit: int = 12,
    platform_source: str = "auto",
    browser_cookies: str = "",
    custom_cookies: str = "",
) -> List[Dict]:
    """
    Crawl danh sách video từ URL kênh/playlist/video (YouTube, Douyin, Bilibili, TikTok, FB)
    hoặc từ khóa tìm kiếm theo nguồn đã chọn (auto/youtube, bilibili, douyin).
    """
    raw = normalize_video_url(query_or_url)
    if not raw:
        return []

    is_url = raw.startswith("http://") or raw.startswith("https://")
    if not is_url:
        if platform_source == "bilibili":
            return crawl_bilibili_search(raw, limit=limit)
        if platform_source == "douyin":
            # Douyin không mở search API không cookie; thử tìm hoặc báo hướng dẫn rõ nếu không phải URL
            target = f"ytsearch{max(1, min(limit, 30))}:{raw} douyin"
        else:
            target = f"ytsearch{max(1, min(limit, 30))}:{raw}"
    else:
        target = raw

    cmd = _get_ytdlp_base_cmd() + [
        "--flat-playlist",
        "--dump-single-json",
        "--no-warnings",
        "--ignore-errors",
        "--playlist-end", str(max(1, min(limit, 30))),
    ] + _build_ytdlp_auth_and_net_args(
        target,
        browser_cookies=browser_cookies,
        custom_cookies=custom_cookies,
        platform_source=platform_source,
    ) + [target]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        if not res.stdout or not res.stdout.strip():
            err_msg = (res.stderr or "").strip()
            low_err = err_msg.lower()
            if "douyin.com" in target.lower() or "dpapi" in low_err or "could not find" in low_err or "cookies" in low_err:
                raise RuntimeError(
                    "Douyin/trình duyệt chưa lấy được cookie hợp lệ. Hãy dán trực tiếp chuỗi Cookie vào ô 'Nhập Cookie trực tiếp' ngay bên dưới (không cần tìm file cookies.txt)."
                )
            raise RuntimeError(err_msg or "Không lấy được dữ liệu từ link/từ khóa này.")
        data = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Lỗi phân tích dữ liệu crawl: {e}")

    entries = data.get("entries") if isinstance(data, dict) and "entries" in data else [data]
    results = []
    for item in (entries or []):
        if not isinstance(item, dict):
            continue
        vid_url = item.get("webpage_url") or item.get("url") or ""
        if vid_url and not vid_url.startswith("http"):
            vid_id = item.get("id") or vid_url
            ext_key = str(data.get("extractor_key", "")).lower()
            if "bilibili" in ext_key or str(vid_id).startswith("BV"):
                vid_url = f"https://www.bilibili.com/video/{vid_id}"
            elif "douyin" in ext_key:
                vid_url = f"https://www.douyin.com/video/{vid_id}"
            elif ext_key.startswith("youtube") or not is_url:
                vid_url = f"https://www.youtube.com/watch?v={vid_id}"
        if not vid_url:
            continue

        thumbs = item.get("thumbnails") or []
        thumb_url = item.get("thumbnail") or (thumbs[-1].get("url") if thumbs and isinstance(thumbs[-1], dict) else "")
        dur = float(item.get("duration") or 0)
        results.append({
            "id": str(item.get("id") or len(results) + 1),
            "title": (item.get("title") or "Video không tiêu đề").strip(),
            "url": vid_url,
            "thumbnail": thumb_url,
            "duration": dur,
            "duration_str": _format_duration(dur),
            "uploader": item.get("uploader") or item.get("channel") or data.get("uploader") or "",
            "view_count": int(item.get("view_count") or 0),
            "platform": _detect_platform_from_url(vid_url),
        })
        if len(results) >= limit:
            break
    return results


def download_video_from_url(
    url: str,
    work_dir: str,
    browser_cookies: str = "",
    custom_cookies: str = "",
) -> str:
    """
    Tải video từ link (YouTube/TikTok/Douyin/Bilibili/FB) về work_dir/source.mp4 qua yt-dlp.
    Ưu tiên codec H.264 (<=1080p) để tải nhanh/tương thích cao, stream tiến độ % lên tracker
    và tự động phát hiện kẹt tải (stall) để fallback nhanh.
    """
    os.makedirs(work_dir, exist_ok=True)
    clean_url = normalize_video_url(url)
    out_tpl = os.path.join(work_dir, "source.%(ext)s")
    ffmpeg_exe = get_ffmpeg_exe()

    format_candidates = [
        "bv*[vcodec^=avc][height<=1080]+ba/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "bv*[height<=720]+ba/b[height<=720]/b",
    ]

    last_err = ""
    for attempt_idx, fmt in enumerate(format_candidates):
        default_tracker.check_cancelled()
        for fn in os.listdir(work_dir):
            if fn.startswith("source."):
                try:
                    os.remove(os.path.join(work_dir, fn))
                except OSError:
                    pass

        cmd = _get_ytdlp_base_cmd() + [
            "-f", fmt,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings",
            "--newline",
            "--socket-timeout", "12",
            "--retries", "2",
            "--ffmpeg-location", ffmpeg_exe,
            "-o", out_tpl,
        ] + _build_ytdlp_auth_and_net_args(clean_url, browser_cookies=browser_cookies, custom_cookies=custom_cookies) + [clean_url]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        default_tracker.register_process(proc)
        tail_lines = []
        last_activity = time.monotonic()
        last_pct_ui = -1
        try:
            while True:
                default_tracker.check_cancelled()
                line = proc.stdout.readline() if proc.stdout else ""
                if line:
                    last_activity = time.monotonic()
                    s_line = line.strip()
                    if s_line:
                        tail_lines.append(s_line)
                        if len(tail_lines) > 15:
                            tail_lines.pop(0)
                        m_pct = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", s_line)
                        if m_pct:
                            dl_pct = float(m_pct.group(1))
                            ui_pct = 8 + int(dl_pct * 0.16)
                            if ui_pct > last_pct_ui:
                                last_pct_ui = ui_pct
                                default_tracker.update_progress(ui_pct, f"Đang tải video nguồn ({dl_pct:.0f}%)...")
                else:
                    if proc.poll() is not None:
                        break
                    if time.monotonic() - last_activity > 35:
                        proc.kill()
                        last_err = "Kết nối tải video bị treo quá 35s (0 bytes)."
                        break
                    time.sleep(0.15)

            if proc.returncode == 0:
                for fn in os.listdir(work_dir):
                    if fn.startswith("source.") and fn.lower().endswith((".mp4", ".mkv", ".webm", ".mov")) and not fn.endswith(".part"):
                        return os.path.join(work_dir, fn)
            else:
                last_err = "\n".join(tail_lines[-5:]) or f"Mã lỗi yt-dlp: {proc.returncode}"
        finally:
            default_tracker.unregister_process(proc)

        default_tracker.log(f"⚠️ Thử tải lần {attempt_idx + 1} chưa thành công, chuyển sang định dạng nhẹ hơn...", level="WARN")

    raise RuntimeError(f"Tải video thất bại: {last_err[-300:]}")


def probe_video_info(video_path: str) -> Dict:
    """Lấy width, height, duration (seconds), has_audio của video nguồn (hỗ trợ cả ffmpeg fallback khi thiếu ffprobe)."""
    ffprobe_exe = get_ffprobe_exe()
    info = {"width": 1080, "height": 1920, "duration": 0.0, "has_audio": False}
    try:
        if shutil.which(ffprobe_exe) or os.path.exists(ffprobe_exe):
            cmd = [
                ffprobe_exe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", video_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                info["duration"] = float(data.get("format", {}).get("duration") or 0)
                for s in data.get("streams", []):
                    if s.get("codec_type") == "video" and s.get("width") and s.get("height"):
                        info["width"] = int(s["width"])
                        info["height"] = int(s["height"])
                    elif s.get("codec_type") == "audio":
                        info["has_audio"] = True
                if info["duration"] > 0:
                    return info
    except Exception:
        pass

    # Fallback bằng ffmpeg -i khi máy không có ffprobe
    try:
        ffmpeg_exe = get_ffmpeg_exe()
        res = subprocess.run([ffmpeg_exe, "-i", video_path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        err = res.stderr or ""
        m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
        if m_dur:
            info["duration"] = int(m_dur.group(1)) * 3600 + int(m_dur.group(2)) * 60 + float(m_dur.group(3))
        m_dim = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", err)
        if m_dim:
            info["width"] = int(m_dim.group(1))
            info["height"] = int(m_dim.group(2))
        if "Audio:" in err:
            info["has_audio"] = True
    except Exception:
        pass
    return info


def extract_and_translate_segments(
    video_path: str,
    work_dir: str,
    translation_mode: str = "viral",
    custom_prompt: str = "",
    duration_sec: float = 0.0,
) -> Dict:
    """
    Trích audio (hoặc frame nếu không có audio) để Gemini Multimodal AI:
    - Bóc thoại / nhận diện diễn biến theo timeline (start_ms, end_ms)
    - Dịch & viết lại tiếng Việt tự nhiên/viral
    - Sinh tiêu đề + caption SEO + hashtags
    """
    from google import genai
    from google.genai import types

    keys = _get_gemini_keys()
    if not keys:
        raise RuntimeError("Thiếu GEMINI_API_KEY để bóc tách và dịch kịch bản Reup.")

    ffmpeg_exe = get_ffmpeg_exe()
    audio_mp3 = os.path.join(work_dir, "extracted_16k.mp3")
    audio_wav = os.path.join(work_dir, "original_audio.wav")

    # Trích audio gốc dạng WAV (để mix nền) & MP3 16kHz nhẹ (gửi Gemini nghe)
    has_audio = False
    try:
        subprocess.run(
            [ffmpeg_exe, "-y", "-nostdin", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", audio_wav],
            capture_output=True, check=True, timeout=60
        )
        subprocess.run(
            [ffmpeg_exe, "-y", "-nostdin", "-i", audio_wav, "-ac", "1", "-ar", "16000", "-b:a", "48k", audio_mp3],
            capture_output=True, check=True, timeout=60
        )
        has_audio = os.path.exists(audio_mp3) and os.path.getsize(audio_mp3) > 2000
    except Exception:
        has_audio = False

    mode_desc = {
        "literal": "Dịch sát nghĩa, giữ đúng thông tin gốc nhưng câu từ tiếng Việt tự nhiên.",
        "natural": "Dịch tự nhiên, trôi chảy như người Việt bản xứ kể chuyện.",
        "viral": "Bản địa hóa theo phong cách viral TikTok/Reels/Threads Việt Nam: câu mở đầu (0-3s) cực kỳ tò mò bắt tai, câu từ ngắn gọn sắc bén dễ đọc thành tiếng.",
    }.get(translation_mode, "Dịch tự nhiên, ngắn gọn, cuốn hút.")

    extra_note = f"\nYêu cầu thêm từ người dùng: {custom_prompt.strip()}" if custom_prompt and custom_prompt.strip() else ""
    dur_hint = f"{duration_sec:.1f} giây" if duration_sec > 0 else "tự xác định theo nội dung"

    prompt = (
        f"Bạn là biên tập viên Reup Video chuyên nghiệp. Tổng thời lượng video: {dur_hint}.\n"
        f"Chế độ dịch/viết lại: {mode_desc}{extra_note}\n\n"
        "NHIỆM VỤ:\n"
        "1. Nghe toàn bộ lời thoại (hoặc quan sát các khung hình nếu không có thoại) trong file đính kèm.\n"
        "2. Chia thành các phân đoạn lời thoại/lời bình liên tiếp nhau theo thời gian (start_ms, end_ms) sao cho khớp nhịp video và mỗi đoạn dài vừa phải (10-22 từ tiếng Việt để đọc kịp tốc độ).\n"
        "3. Với mỗi đoạn, điền 'original_text' (ngôn ngữ gốc hoặc mô tả cảnh ngắn) và 'vi_text' (lời thoại tiếng Việt sẽ dùng để lồng tiếng AI và hiện phụ đề).\n"
        "4. Tạo thêm 'video_title' (tiêu đề ngắn in hoa dưới 10 từ), 'video_caption' (caption đăng TikTok/Reels), 'hashtags' (5-7 hashtag).\n"
        "Trả về JSON chuẩn theo cấu trúc:\n"
        '{"video_title": "...", "video_caption": "...", "hashtags": "...", "segments": [{"id": 1, "start_ms": 0, "end_ms": 3200, "original_text": "...", "vi_text": "..."}]}'
    )

    parts = [prompt]
    if has_audio:
        with open(audio_mp3, "rb") as f:
            parts.append(types.Part.from_bytes(data=f.read(), mime_type="audio/mp3"))
    else:
        # Fallback trích 6 frame đại diện khi video câm
        frames_dir = os.path.join(work_dir, "vision_frames")
        os.makedirs(frames_dir, exist_ok=True)
        fps_expr = f"6/{max(duration_sec, 6.0):.2f}"
        subprocess.run(
            [ffmpeg_exe, "-y", "-nostdin", "-i", video_path, "-vf", f"fps={fps_expr}", "-frames:v", "6", "-q:v", "4", os.path.join(frames_dir, "f_%02d.jpg")],
            capture_output=True, timeout=30
        )
        for fn in sorted(os.listdir(frames_dir)):
            fp = os.path.join(frames_dir, fn)
            with open(fp, "rb") as f:
                parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/jpeg"))

    models = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    last_err = None
    for key in keys[:3]:
        client = genai.Client(api_key=key)
        for m in models:
            try:
                res = client.models.generate_content(
                    model=m,
                    contents=parts,
                    config={"response_mime_type": "application/json"},
                )
                raw_txt = getattr(res, "text", None) or ""
                parsed = json.loads(raw_txt)
                segs = parsed.get("segments") or []
                if segs:
                    parsed["original_audio_wav"] = audio_wav if has_audio and os.path.exists(audio_wav) else ""
                    return parsed
            except Exception as e:
                last_err = e
                continue
    raise RuntimeError(f"Gemini không thể phân tích/dịch video Reup: {last_err}")


def _load_audio_via_ffmpeg(audio_path: str, tmp_wav_path: str = "") -> AudioSegment:
    """Đọc mọi định dạng audio bằng ffmpeg -> WAV rồi load qua AudioSegment.from_wav (không cần ffprobe)."""
    if audio_path.lower().endswith(".wav"):
        return AudioSegment.from_wav(audio_path)
    ffmpeg_exe = get_ffmpeg_exe()
    wav_tmp = tmp_wav_path or (os.path.splitext(audio_path)[0] + "_pcm.wav")
    subprocess.run(
        [ffmpeg_exe, "-y", "-nostdin", "-i", audio_path, "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", wav_tmp],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return AudioSegment.from_wav(wav_tmp)


def synthesize_reup_tts(
    segments: List[Dict],
    work_dir: str,
    voice: str = "nam_hai",
    rate: str = "+15%",
) -> List[Dict]:
    """Tổng hợp giọng đọc OmniVoice cho từng phân đoạn Reup và cập nhật timeline."""
    tts_mod = __import__("03_tts")
    tts_dir = os.path.join(work_dir, "tts_segments")
    os.makedirs(tts_dir, exist_ok=True)

    cursor_ms = 0
    enriched = []
    for idx, seg in enumerate(segments, 1):
        default_tracker.check_cancelled()
        text = (seg.get("vi_text") or "").strip()
        if not text:
            continue
        start_ms = max(cursor_ms, int(seg.get("start_ms") or 0))
        mp3_out = os.path.join(tts_dir, f"seg_{idx:03d}.mp3")
        wav_out = os.path.join(tts_dir, f"seg_{idx:03d}.wav")
        tts_mod.text_to_speech(text=text, output_path=mp3_out, voice=voice, rate=rate)

        seg_audio = _load_audio_via_ffmpeg(mp3_out, wav_out)
        dur_ms = len(seg_audio)
        end_ms = start_ms + dur_ms
        cursor_ms = end_ms + 180

        enriched.append({
            "id": idx,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": dur_ms,
            "vi_text": text,
            "original_text": seg.get("original_text", ""),
            "tts_path": wav_out if os.path.exists(wav_out) else mp3_out,
        })
    return enriched


def mix_reup_audio(
    segments: List[Dict],
    original_audio_wav: str,
    output_wav: str,
    video_duration_ms: int,
    original_volume: float = 0.0,
    bgm_volume: float = 0.15,
    ducking_db: float = -10.0,
    bgm_choice: str = "random",
) -> str:
    """
    Mix âm thanh gốc + nhạc nền BGM (assets/bgm) + giọng đọc OmniVoice với Audio Ducking mượt
    (gộp khoảng lặng < 2.5s, crossfade 300ms/500ms).
    """
    import random
    max_tts_end = max((s["end_ms"] for s in segments), default=0)
    total_ms = max(video_duration_ms, max_tts_end + 300, 1000)

    if original_audio_wav and os.path.exists(original_audio_wav) and original_volume > 0:
        orig = AudioSegment.from_wav(original_audio_wav)
        if len(orig) < total_ms:
            orig += AudioSegment.silent(duration=total_ms - len(orig))
        orig = orig[:total_ms] + (20 * math.log10(max(0.01, original_volume)))
    else:
        orig = AudioSegment.silent(duration=total_ms)

    # Tìm BGM từ assets/bgm (hoặc assets/music nếu có)
    bgm = None
    if bgm_choice != "none" and bgm_volume > 0:
        bgm_file = ""
        for folder in (os.path.join(BASE_DIR, "assets", "bgm"), os.path.join(BASE_DIR, "assets", "music")):
            if not os.path.isdir(folder):
                continue
            if bgm_choice and bgm_choice not in ("random", "auto"):
                cand = os.path.join(folder, bgm_choice)
                if os.path.isfile(cand):
                    bgm_file = cand
                    break
            files = [os.path.join(folder, fn) for fn in sorted(os.listdir(folder)) if fn.lower().endswith((".mp3", ".wav", ".m4a"))]
            if files:
                bgm_file = random.choice(files)
                break
        if bgm_file:
            try:
                bgm_raw = _load_audio_via_ffmpeg(bgm_file, os.path.join(os.path.dirname(output_wav), "bgm_pcm.wav"))
                if len(bgm_raw) < total_ms:
                    bgm_raw = bgm_raw * ((total_ms // len(bgm_raw)) + 1)
                bgm = bgm_raw[:total_ms] + (20 * math.log10(max(0.01, bgm_volume)))
            except Exception:
                bgm = None

    tts_track = AudioSegment.silent(duration=total_ms)
    speech_intervals = []
    for s in segments:
        if s.get("tts_path") and os.path.exists(s["tts_path"]):
            sp = _load_audio_via_ffmpeg(s["tts_path"])
            tts_track = tts_track.overlay(sp, position=s["start_ms"])
            speech_intervals.append((s["start_ms"], s["start_ms"] + len(sp)))

    bg_track = orig.overlay(bgm) if bgm else orig

    if speech_intervals and ducking_db != 0:
        ducked_bg = bg_track + ducking_db
        speech_intervals.sort()
        merged = []
        for st, en in speech_intervals:
            if not merged:
                merged.append([st, en])
            elif st <= merged[-1][1] + 2500:
                merged[-1][1] = max(merged[-1][1], en)
            else:
                merged.append([st, en])

        first_st = merged[0][0]
        final_bg = bg_track[0:first_st] if first_st > 0 else AudioSegment.silent(duration=0)
        for idx, (st, en) in enumerate(merged):
            st = max(0, min(st, total_ms))
            en = max(0, min(en, total_ms))
            # Giữ nguyên tổng thời lượng total_ms khi crossfade (bù phần chồng lấn trước khi append)
            cf_in = 300 if (len(final_bg) > 300 and st >= 300 and (en - st) > 300) else 0
            ducked_chunk = ducked_bg[st - cf_in:en]
            if cf_in > 0:
                final_bg = final_bg.append(ducked_chunk, crossfade=cf_in)
            else:
                final_bg += ducked_chunk

            next_st = merged[idx + 1][0] if idx < len(merged) - 1 else total_ms
            next_st = max(0, min(next_st, total_ms))
            if next_st > en:
                cf_out = 500 if (len(final_bg) > 500 and en >= 500 and (next_st - en) > 500) else 0
                unducked_chunk = bg_track[en - cf_out:next_st]
                if cf_out > 0:
                    final_bg = final_bg.append(unducked_chunk, crossfade=cf_out)
                else:
                    final_bg += unducked_chunk

        if len(final_bg) < total_ms:
            final_bg += AudioSegment.silent(duration=total_ms - len(final_bg))
        final_bg = final_bg[:total_ms]
        # Overlay trên nền tts_track (giữ trọn vẹn 100% độ dài giọng đọc)
        final_audio = tts_track.overlay(final_bg)
    else:
        final_audio = tts_track.overlay(bg_track)

    final_audio.export(output_wav, format="wav")
    return output_wav


def _ms_to_ass_time(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, cs = divmod(rem, 1000)
    return f"{h}:{m:02d}:{s:02d}.{cs // 10:02d}"


def _wrap_2_lines(text: str, max_chars: int = 34) -> str:
    words = text.split()
    if not words:
        return ""
    lines = ["", ""]
    idx = 0
    for w in words:
        cand = w if not lines[idx] else f"{lines[idx]} {w}"
        if len(cand) <= max_chars or not lines[idx]:
            lines[idx] = cand
        elif idx == 0:
            idx = 1
            lines[idx] = w
        else:
            lines[idx] = f"{lines[idx]} {w}"
    return "\\N".join(l for l in lines if l)


def write_reup_ass_subtitles(segments: List[Dict], ass_path: str, video_title: str = "") -> str:
    """Xuất file phụ đề ASS 1080x1920 sắc nét, dễ đọc trên nền video Reup."""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Sub,Arial,58,&H00FFFFFF,&H0000FFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,340,1\n"
        "Style: Title,Arial,54,&H0000D7FF,&H000000FF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,6,0,8,60,60,130,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    if video_title:
        total_end = max((s["end_ms"] for s in segments), default=15000) + 500
        safe_title = _wrap_2_lines(video_title.upper(), 28)
        events.append(f"Dialogue: 1,0:00:00.00,{_ms_to_ass_time(total_end)},Title,,0,0,0,,{safe_title}")

    for s in segments:
        wrapped = _wrap_2_lines(s["vi_text"], 34)
        events.append(f"Dialogue: 0,{_ms_to_ass_time(s['start_ms'])},{_ms_to_ass_time(s['end_ms'])},Sub,,0,0,0,,{wrapped}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")
    return ass_path


def _escape_ffmpeg_sub_path(path: str) -> str:
    try:
        rel = os.path.relpath(path, start=os.getcwd()).replace("\\", "/")
        if ":" not in rel:
            return rel
    except Exception:
        pass
    p = os.path.abspath(path).replace("\\", "/")
    if ":" in p:
        d, r = p.split(":", 1)
        return f"{d}\\:{r}"
    return p


def render_reup_video(
    source_video: str,
    mixed_audio_wav: str,
    ass_path: str,
    output_mp4: str,
    frame_mode: str = "blur_bg",
    mask_old_sub: bool = True,
    anti_reup: bool = True,
    mirror_video: bool = False,
    duration_sec: float = 0.0,
) -> str:
    """
    Render video Reup 1080x1920 @ 30fps (tối ưu tốc độ cao):
    - Tự chọn GPU encoder (NVENC/QSV/AMF/MF) hoặc libx264 veryfast
    - Nền mờ downscale-blur-upscale (nhanh gấp 16x) cho video ngang hoặc Fill Crop
    - Che phụ đề cũ bằng downscale-blur nhanh (không dùng geq từng pixel)
    - Chống trùng lặp nhẹ tự nhiên + xóa metadata + chuẩn màu bt709
    """
    ffmpeg_exe = get_ffmpeg_exe()
    os.makedirs(os.path.dirname(output_mp4), exist_ok=True)

    filters = []
    # 1. Tạo khung hình 1080x1920
    if frame_mode == "fill_crop":
        crop_expr = "crop=iw*0.96:ih*0.96:(iw-iw*0.96)/2:(ih-ih*0.96)/2," if anti_reup else ""
        filters.append(f"[0:v]{crop_expr}scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[base_v]")
    else:
        filters.append("[0:v]split=2[bg_src][fg_src]")
        filters.append(
            "[bg_src]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            "boxblur=8:2,eq=brightness=-0.12,scale=1080:1920:flags=bilinear[bg_blur]"
        )
        fg_crop = "crop=iw*0.96:ih*0.96:(iw-iw*0.96)/2:(ih-ih*0.96)/2," if anti_reup else ""
        filters.append(f"[fg_src]{fg_crop}scale=1080:1920:force_original_aspect_ratio=decrease[fg_fit]")
        filters.append("[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2[base_v]")

    cur = "[base_v]"

    # 2. Anti-reup nhẹ tự nhiên (hflip tùy chọn + chỉnh nhẹ màu/độ tương phản)
    anti_ops = []
    if mirror_video:
        anti_ops.append("hflip")
    if anti_reup:
        anti_ops.append("eq=contrast=1.04:saturation=1.07:brightness=0.01")
    if anti_ops:
        filters.append(f"{cur}{','.join(anti_ops)}[anti_v]")
        cur = "[anti_v]"

    # 3. Che phụ đề cũ bằng dải blur tối ưu (downscale -> boxblur -> upscale, tránh geq)
    if mask_old_sub:
        band_w, band_h = 920, 210
        band_x, band_y = 80, 1440
        filters.append(f"{cur}split=2[main_s][sub_s]")
        filters.append(
            f"[sub_s]crop={band_w}:{band_h}:{band_x}:{band_y},"
            f"scale=230:52,boxblur=4:2,scale={band_w}:{band_h}:flags=bilinear,eq=brightness=-0.18[blur_band]"
        )
        filters.append(f"[main_s][blur_band]overlay={band_x}:{band_y}[masked_v]")
        cur = "[masked_v]"

    # 4. Burn-in phụ đề ASS mới
    escaped_ass = _escape_ffmpeg_sub_path(ass_path)
    filters.append(f"{cur}subtitles='{escaped_ass}'[outv]")

    encoder = detect_best_encoder(ffmpeg_exe)
    enc_args = ["-c:v", encoder]
    if encoder == "libx264":
        enc_args.extend(["-preset", "superfast", "-crf", "22"])
    elif encoder in ("h264_nvenc", "h264_qsv"):
        enc_args.extend(["-preset", "fast"])
    elif encoder == "h264_amf":
        enc_args.extend(["-quality", "speed"])

    # Tính -t chính xác (min giữa video và audio) để tránh lỗi -shortest tràn RAM (-12 ENOMEM) trên FFmpeg 9.x
    vid_dur = probe_video_info(source_video).get("duration") or 0.0
    aud_dur = probe_video_info(mixed_audio_wav).get("duration") or 0.0
    if vid_dur > 0 and aud_dur > 0:
        target_dur = min(vid_dur, aud_dur)
    else:
        target_dur = vid_dur or aud_dur or duration_sec
    if duration_sec <= 0 and target_dur > 0:
        duration_sec = target_dur

    cmd = [
        ffmpeg_exe, "-y", "-nostdin",
        "-i", source_video,
        "-i", mixed_audio_wav,
        "-filter_complex", ";".join(filters),
        "-map", "[outv]",
        "-map", "1:a",
        "-map_metadata", "-1",
    ] + enc_args + [
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709",
        "-color_trc", "bt709",
        "-color_primaries", "bt709",
        "-c:a", "aac", "-b:a", "192k",
    ]
    if target_dur > 0:
        cmd.extend(["-t", f"{target_dur:.2f}"])
    cmd.append(output_mp4)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    default_tracker.register_process(proc)
    tail_lines = []
    start_ts = time.monotonic()
    last_ui_pct = 80
    try:
        while True:
            default_tracker.check_cancelled()
            line = proc.stderr.readline() if proc.stderr else ""
            if line:
                s_line = line.strip()
                if s_line:
                    tail_lines.append(s_line)
                    if len(tail_lines) > 20:
                        tail_lines.pop(0)
                    if duration_sec > 0:
                        m_t = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", s_line)
                        if m_t:
                            cur_s = int(m_t.group(1)) * 3600 + int(m_t.group(2)) * 60 + float(m_t.group(3))
                            pct = min(95, 80 + int((cur_s / duration_sec) * 15))
                            if pct > last_ui_pct:
                                last_ui_pct = pct
                                default_tracker.update_progress(pct, f"FFmpeg đang dựng video ({cur_s:.0f}s/{duration_sec:.0f}s)...")
            else:
                if proc.poll() is not None:
                    break
                if time.monotonic() - start_ts > max(900, int(duration_sec * 4)):
                    proc.kill()
                    raise RuntimeError("FFmpeg render Reup quá thời gian cho phép.")
                time.sleep(0.1)

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg render Reup lỗi ({proc.returncode}): {' | '.join(tail_lines[-6:])}")
    finally:
        default_tracker.unregister_process(proc)

    return output_mp4


def run_reup_pipeline(
    source_url: str,
    voice: str = "nam_hai",
    rate: str = "+15%",
    translation_mode: str = "viral",
    frame_mode: str = "blur_bg",
    mask_old_sub: bool = True,
    anti_reup: bool = True,
    mirror_video: bool = False,
    original_volume: float = 0.0,
    custom_prompt: str = "",
    output_filename: str = "",
    browser_cookies: str = "",
    bgm_choice: str = "random",
    custom_cookies: str = "",
) -> Dict:
    """Chạy trọn vẹn quy trình Reup 1 video từ URL và đồng bộ tiến trình vào pipeline_tracker."""
    job_slug = f"reup_{int(time.time())}"
    work_dir = os.path.join(REUP_WORK_DIR, job_slug)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(FINAL_DIR, exist_ok=True)

    default_tracker.start_step("script", f"Đang tải video nguồn từ link: {source_url[:60]}...", percent=8)
    source_video = download_video_from_url(
        source_url,
        work_dir,
        browser_cookies=browser_cookies,
        custom_cookies=custom_cookies,
    )
    vinfo = probe_video_info(source_video)
    dur_sec = vinfo.get("duration") or 0.0

    default_tracker.start_step("cards", "Gemini AI đang nghe bóc thoại & dịch/viết lại kịch bản...", percent=28)
    script_data = extract_and_translate_segments(
        source_video,
        work_dir,
        translation_mode=translation_mode,
        custom_prompt=custom_prompt,
        duration_sec=dur_sec,
    )
    default_tracker.finish_step("script")
    default_tracker.finish_step("cards")

    default_tracker.start_step("tts", f"OmniVoice đang lồng tiếng ({voice}) cho {len(script_data.get('segments', []))} câu...", percent=48)
    enriched_segs = synthesize_reup_tts(script_data.get("segments", []), work_dir, voice=voice, rate=rate)
    if not enriched_segs:
        raise RuntimeError("Không tạo được đoạn giọng đọc nào từ kịch bản Reup.")
    default_tracker.finish_step("tts")

    default_tracker.start_step("audio", "Đang trộn âm thanh & Audio Ducking mượt...", percent=65)
    mixed_wav = os.path.join(work_dir, "mixed_reup.wav")
    mix_reup_audio(
        enriched_segs,
        script_data.get("original_audio_wav", ""),
        mixed_wav,
        video_duration_ms=int(dur_sec * 1000),
        original_volume=original_volume,
        bgm_choice=bgm_choice,
    )
    ass_path = os.path.join(work_dir, "reup_subs.ass")
    write_reup_ass_subtitles(enriched_segs, ass_path, video_title=script_data.get("video_title", ""))
    default_tracker.finish_step("audio")

    default_tracker.start_step("composite", "FFmpeg đang dựng khung 9:16, che sub cũ & burn phụ đề mới...", percent=80)
    if not output_filename:
        output_filename = f"{job_slug}.mp4"
    out_mp4 = os.path.join(FINAL_DIR, output_filename)
    render_reup_video(
        source_video=source_video,
        mixed_audio_wav=mixed_wav,
        ass_path=ass_path,
        output_mp4=out_mp4,
        frame_mode=frame_mode,
        mask_old_sub=mask_old_sub,
        anti_reup=anti_reup,
        mirror_video=mirror_video,
        duration_sec=dur_sec,
    )
    default_tracker.finish_step("composite")

    default_tracker.start_step("export", "Đang đóng gói video Reup & caption SEO...", percent=96)
    caption_full = f"{script_data.get('video_caption', '')}\n\n{script_data.get('hashtags', '')}".strip()
    total_dur = max(dur_sec, (enriched_segs[-1]["end_ms"] / 1000.0) if enriched_segs else 0.0)
    default_tracker.finish_step("export")

    return {
        "video_filename": output_filename,
        "video_path": out_mp4,
        "video_title": script_data.get("video_title", ""),
        "caption": caption_full,
        "duration_sec": total_dur,
        "segments_count": len(enriched_segs),
    }
