"""
PROXY ROTATION & ANTI-BLOCK NETWORK MANAGER
===========================================
Cung cấp cơ chế xoay vòng Proxy (HTTP/HTTPS) cho toàn bộ request ra ngoài:
  - Wikimedia / Wikipedia / Bing Image & Video Search
  - Edge-TTS WebSocket
  - Threads Radar / Avatar Scraper
Tự động xoay vòng giữa các proxy trong SQLite DB (fallback .env) và lùi về
kết nối trực tiếp nếu chưa cấu hình proxy hoặc proxy tạm thời bị lỗi.
"""

import json
import threading
import time
import urllib.request
from typing import Optional

import db_store

_rr_lock = threading.Lock()
_rr_idx = 0


def get_next_proxy(http_only: bool = True, db_path: str = None) -> Optional[str]:
    """Lấy proxy tiếp theo theo cơ chế xoay vòng Round-Robin."""
    global _rr_idx
    try:
        proxies = db_store.get_active_proxies(db_path=db_path)
    except Exception:
        proxies = []

    if http_only:
        proxies = [p for p in proxies if p.lower().startswith(("http://", "https://"))]

    if not proxies:
        return None

    with _rr_lock:
        chosen = proxies[_rr_idx % len(proxies)]
        _rr_idx += 1
    return chosen


def _clone_request(req_or_url):
    if isinstance(req_or_url, urllib.request.Request):
        return urllib.request.Request(
            req_or_url.full_url,
            data=req_or_url.data,
            headers=dict(req_or_url.header_items()),
            method=req_or_url.get_method(),
        )
    return req_or_url


def urlopen_with_proxy(req_or_url, timeout: float = 8.0, max_proxy_tries: int = 2, allow_direct_fallback: bool = True):
    """
    Mở URL/Request qua Proxy xoay vòng; tự báo lỗi về DB và chuyển proxy kế tiếp
    nếu proxy chết, sau đó lùi về kết nối trực tiếp để không làm sập pipeline.
    """
    tried = set()
    for _ in range(max(1, max_proxy_tries)):
        proxy_url = get_next_proxy(http_only=True)
        if not proxy_url or proxy_url in tried:
            break
        tried.add(proxy_url)
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
            resp = opener.open(_clone_request(req_or_url), timeout=timeout)
            db_store.report_proxy_success(proxy_url)
            return resp
        except Exception as e:
            db_store.report_proxy_error(proxy_url, str(e))

    if not allow_direct_fallback and tried:
        raise RuntimeError("Không thể kết nối qua bất kỳ Proxy nào.")
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(_clone_request(req_or_url), timeout=timeout)


def test_proxy_connection(proxy_url: str, timeout: float = 6.0) -> dict:
    """Kiểm tra nhanh proxy còn sống hay không và trả về IP đầu ra + độ trễ (ms)."""
    norm = db_store.normalize_proxy_url(proxy_url)
    if not norm:
        return {"success": False, "error": "Proxy URL rỗng"}
    if not norm.lower().startswith(("http://", "https://")):
        return {"success": False, "error": "Hiện chỉ hỗ trợ kiểm tra trực tiếp HTTP/HTTPS proxy."}

    t0 = time.monotonic()
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": norm, "https": norm})
        )
        req = urllib.request.Request(
            "https://api.ipify.org?format=json",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        ms = int((time.monotonic() - t0) * 1000)
        return {"success": True, "ip": data.get("ip", "OK"), "latency_ms": ms}
    except Exception as e:
        return {"success": False, "error": str(e)}
