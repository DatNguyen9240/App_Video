"""
SQLite Configuration & Multi-Key Rotation Store
===============================================
Lưu trữ nhiều GEMINI_API_KEY (tự động xoay vòng khi lỗi/hết quota) và cấu hình
Python Video Studio vào SQLite (`config/studio_config.db`), tự động fallback về `.env`
khi DB chưa có dữ liệu.
"""

import os
import sqlite3
import datetime
from typing import List, Dict, Optional

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DB_PATH = os.path.join(CONFIG_DIR, "studio_config.db")
ENV_PATH = os.path.join(BASE_DIR, ".env")

STUDIO_SETTING_DEFAULTS = {
    "PLATFORM": "threads",
    "LAYOUT_PRESET": "center",
    "BG_MODE": "split_gameplay",
    "CARD_THEME": "light",
    "TTS_VOICE": "nam_hai",
    "TTS_RATE": "+0%",
    "BGM_CHOICE": "gentle_piano_satie.mp3",
    "BGM_VOLUME": "25",
}


def _get_conn(db_path: str = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gemini_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL DEFAULT '',
            api_key TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            error_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            last_used_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS studio_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL DEFAULT '',
            proxy_url TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            error_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            last_used_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _read_env_vars() -> Dict[str, str]:
    result: Dict[str, str] = {}
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#") and "=" in s:
                        k, v = s.split("=", 1)
                        result[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    for k in list(STUDIO_SETTING_DEFAULTS.keys()) + ["GEMINI_API_KEY", "PROXY_URL", "PROXY_LIST", "HTTP_PROXY", "HTTPS_PROXY"]:
        if k not in result and os.environ.get(k):
            result[k] = os.environ[k]
    return result


def mask_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 10:
        return "*" * len(api_key)
    return f"{api_key[:6]}...{api_key[-4:]}"


def list_gemini_keys(db_path: str = None) -> List[Dict]:
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, label, api_key, is_active, error_count, last_error, last_used_at, created_at "
            "FROM gemini_keys ORDER BY error_count ASC, id ASC"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "label": r["label"] or f"Key #{r['id']}",
                "masked_key": mask_key(r["api_key"]),
                "is_active": bool(r["is_active"]),
                "error_count": r["error_count"],
                "last_error": r["last_error"],
                "last_used_at": r["last_used_at"],
                "created_at": r["created_at"],
                "source": "DATABASE",
            }
            for r in rows
        ]


def add_gemini_key(api_key: str, label: str = "", db_path: str = None) -> Dict:
    clean_key = (api_key or "").strip()
    if not clean_key:
        raise ValueError("GEMINI_API_KEY không được rỗng.")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    clean_label = (label or "").strip() or f"Gemini Key ({mask_key(clean_key)})"
    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO gemini_keys (label, api_key, is_active, error_count, created_at)
            VALUES (?, ?, 1, 0, ?)
            ON CONFLICT(api_key) DO UPDATE SET
                label = excluded.label,
                is_active = 1,
                error_count = 0,
                last_error = ''
            """,
            (clean_label, clean_key, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, label, api_key, is_active, error_count, created_at FROM gemini_keys WHERE api_key = ?",
            (clean_key,),
        ).fetchone()
        return {
            "id": row["id"],
            "label": row["label"],
            "masked_key": mask_key(row["api_key"]),
            "is_active": bool(row["is_active"]),
            "error_count": row["error_count"],
            "created_at": row["created_at"],
            "source": "DATABASE",
        }


def toggle_gemini_key(key_id: int, is_active: bool, db_path: str = None) -> bool:
    with _get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE gemini_keys SET is_active = ?, error_count = CASE WHEN ? = 1 THEN 0 ELSE error_count END WHERE id = ?",
            (1 if is_active else 0, 1 if is_active else 0, key_id),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_gemini_key(key_id: int, db_path: str = None) -> bool:
    with _get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM gemini_keys WHERE id = ?", (key_id,))
        conn.commit()
        return cur.rowcount > 0


def get_active_gemini_keys(db_path: str = None) -> List[str]:
    """
    Trả về danh sách GEMINI_API_KEY đang hoạt động (ưu tiên key ít lỗi nhất trong DB).
    Nếu DB chưa có key active nào, tự động fallback về GEMINI_API_KEY trong `.env`.
    """
    keys: List[str] = []
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT api_key FROM gemini_keys WHERE is_active = 1 ORDER BY error_count ASC, id ASC"
        ).fetchall()
        keys = [r["api_key"] for r in rows if r["api_key"]]

    env_vars = _read_env_vars()
    env_key = (env_vars.get("GEMINI_API_KEY") or "").strip()
    if env_key and env_key not in keys:
        keys.append(env_key)
    return keys


def report_key_success(api_key: str, db_path: str = None) -> None:
    if not api_key:
        return
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _get_conn(db_path) as conn:
        conn.execute(
            "UPDATE gemini_keys SET error_count = 0, last_error = '', last_used_at = ? WHERE api_key = ?",
            (now, api_key),
        )
        conn.commit()


def report_key_error(api_key: str, err_msg: str = "", db_path: str = None) -> None:
    if not api_key:
        return
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _get_conn(db_path) as conn:
        conn.execute(
            "UPDATE gemini_keys SET error_count = error_count + 1, last_error = ?, last_used_at = ? WHERE api_key = ?",
            ((err_msg or "")[:200], now, api_key),
        )
        conn.commit()


def get_studio_settings(db_path: str = None) -> Dict[str, str]:
    """Đọc cấu hình Studio từ DB, fallback sang `.env` và giá trị mặc định."""
    env_vars = _read_env_vars()
    merged = {}
    for k, default_val in STUDIO_SETTING_DEFAULTS.items():
        merged[k] = env_vars.get(k, default_val)

    with _get_conn(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM studio_settings").fetchall()
        for r in rows:
            if r["key"] in STUDIO_SETTING_DEFAULTS:
                merged[r["key"]] = r["value"]
    return merged


def save_studio_settings(settings_dict: Dict[str, str], db_path: str = None) -> Dict[str, str]:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _get_conn(db_path) as conn:
        for k, v in settings_dict.items():
            if k in STUDIO_SETTING_DEFAULTS and v is not None:
                conn.execute(
                    """
                    INSERT INTO studio_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (k, str(v), now),
                )
        conn.commit()
    return get_studio_settings(db_path)


def reset_studio_settings(db_path: str = None) -> Dict[str, str]:
    with _get_conn(db_path) as conn:
        conn.execute("DELETE FROM studio_settings")
        conn.commit()
    return get_studio_settings(db_path)


def normalize_proxy_url(raw: str) -> str:
    """Chuẩn hóa mọi định dạng proxy (ip:port, ip:port:user:pass, user:pass@ip:port, http://...) về URL chuẩn."""
    s = (raw or "").strip()
    if not s:
        return ""
    lower = s.lower()
    if lower.startswith(("http://", "https://", "socks4://", "socks5://")):
        return s
    if "@" not in s and s.count(":") == 3:
        host, port, user, pwd = s.split(":", 3)
        return f"http://{user}:{pwd}@{host}:{port}"
    return f"http://{s}"


def mask_proxy(proxy_url: str) -> str:
    """Ẩn mật khẩu proxy khi hiển thị trên giao diện Web."""
    if not proxy_url:
        return ""
    if "@" in proxy_url and "://" in proxy_url:
        scheme, rest = proxy_url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return proxy_url


def list_proxies(db_path: str = None) -> List[Dict]:
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, label, proxy_url, is_active, error_count, last_error, last_used_at, created_at "
            "FROM proxies ORDER BY error_count ASC, id ASC"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "label": r["label"] or f"Proxy #{r['id']}",
                "masked_proxy": mask_proxy(r["proxy_url"]),
                "is_active": bool(r["is_active"]),
                "error_count": r["error_count"],
                "last_error": r["last_error"],
                "last_used_at": r["last_used_at"],
                "created_at": r["created_at"],
                "source": "DATABASE",
            }
            for r in rows
        ]


def add_proxy(proxy_url: str, label: str = "", db_path: str = None) -> Dict:
    norm = normalize_proxy_url(proxy_url)
    if not norm:
        raise ValueError("Proxy URL không được rỗng.")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    clean_label = (label or "").strip() or f"Proxy ({mask_proxy(norm)})"
    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO proxies (label, proxy_url, is_active, error_count, created_at)
            VALUES (?, ?, 1, 0, ?)
            ON CONFLICT(proxy_url) DO UPDATE SET
                label = excluded.label,
                is_active = 1,
                error_count = 0,
                last_error = ''
            """,
            (clean_label, norm, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, label, proxy_url, is_active, error_count, created_at FROM proxies WHERE proxy_url = ?",
            (norm,),
        ).fetchone()
        return {
            "id": row["id"],
            "label": row["label"],
            "masked_proxy": mask_proxy(row["proxy_url"]),
            "is_active": bool(row["is_active"]),
            "error_count": row["error_count"],
            "created_at": row["created_at"],
            "source": "DATABASE",
        }


def toggle_proxy(proxy_id: int, is_active: bool, db_path: str = None) -> bool:
    with _get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE proxies SET is_active = ?, error_count = CASE WHEN ? = 1 THEN 0 ELSE error_count END WHERE id = ?",
            (1 if is_active else 0, 1 if is_active else 0, proxy_id),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_proxy(proxy_id: int, db_path: str = None) -> bool:
    with _get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
        conn.commit()
        return cur.rowcount > 0


def get_active_proxies(db_path: str = None) -> List[str]:
    """Trả về danh sách proxy đang bật từ DB (ưu tiên ít lỗi), fallback sang biến môi trường / .env."""
    proxies: List[str] = []
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT proxy_url FROM proxies WHERE is_active = 1 ORDER BY error_count ASC, id ASC"
        ).fetchall()
        proxies = [r["proxy_url"] for r in rows if r["proxy_url"]]

    env_vars = _read_env_vars()
    for k in ("PROXY_LIST", "PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY"):
        raw = (env_vars.get(k) or "").strip()
        if not raw:
            continue
        for item in raw.split(","):
            norm = normalize_proxy_url(item)
            if norm and norm not in proxies:
                proxies.append(norm)
    return proxies


def report_proxy_success(proxy_url: str, db_path: str = None) -> None:
    if not proxy_url:
        return
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _get_conn(db_path) as conn:
        conn.execute(
            "UPDATE proxies SET error_count = 0, last_error = '', last_used_at = ? WHERE proxy_url = ?",
            (now, proxy_url),
        )
        conn.commit()


def report_proxy_error(proxy_url: str, err_msg: str = "", db_path: str = None) -> None:
    if not proxy_url:
        return
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _get_conn(db_path) as conn:
        conn.execute(
            "UPDATE proxies SET error_count = error_count + 1, last_error = ?, last_used_at = ? WHERE proxy_url = ?",
            ((err_msg or "")[:200], now, proxy_url),
        )
        conn.commit()
