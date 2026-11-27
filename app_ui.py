"""
THREADS VIDEO STUDIO — MODERN WEB UI (V2.0 PRO)
================================================
Giao diện Web Studio hiện đại tích hợp Đạo Diễn AI & Giám Sát Quy Trình:
  - Tab 1: 🎬 Studio Sáng Tạo & Biên Tập (Scripting & Preview)
  - Tab 2: 📡 Tiến Trình & Đạo Diễn Cảm Xúc (Live Pipeline & Emotional Arc Monitor)
  - Tab 3: ⚙️ Cấu Hình Đạo Diễn (Director Config Studio - Không Hardcode)

Khởi chạy:
  python app_ui.py
"""

import os
import sys
import json
import glob
import random
import re
import time
import threading
import webbrowser
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SRC_DIR    = os.path.join(BASE_DIR, "src")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
FINAL_DIR  = os.path.join(OUTPUT_DIR, "final")
CARDS_DIR  = os.path.join(OUTPUT_DIR, "cards")
AUDIO_DIR  = os.path.join(OUTPUT_DIR, "audio")
MEMES_DIR  = os.path.join(BASE_DIR, "assets", "memes")
AVATARS_DIR = os.path.join(BASE_DIR, "assets", "avatars")
ASSETS_DIR  = os.path.join(BASE_DIR, "assets")
JS_DIR     = os.path.join(BASE_DIR, "assets", "js")
ENV_PATH   = os.path.join(BASE_DIR, ".env")

sys.path.insert(0, SRC_DIR)
import config as config_mod
import pipeline_tracker as tracker

import importlib.util

def load_mod(name, fname):
    p = os.path.join(SRC_DIR, fname)
    s = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

content_mod  = load_mod("content", "01_content.py")
ai_mod       = load_mod("ai_content", "01_ai_content.py")
card_mod     = load_mod("card_gen", "02_card_gen.py")
tts_mod      = load_mod("tts", "03_tts.py")
bg_mod       = load_mod("bg_audio", "04_bg_audio.py")
pipeline_mod = load_mod("pipeline", "05_pipeline.py")
visual_builder_mod = load_mod("visual_story_builder", "visual_story_builder.py")

from market_intelligence import (
    MarketIntelligenceStore,
    CloudflareBridgeClient,
    MarketSyncService,
    RemoteCommandPoller,
)

_market_runtime_lock = threading.Lock()
_market_runtime = None


def get_market_runtime(data_dir=None, client=None):
    """Initialize or return the singleton Market Intelligence runtime."""
    global _market_runtime
    with _market_runtime_lock:
        if _market_runtime is None:
            store = MarketIntelligenceStore(data_dir=data_dir)
            cf_client = client or CloudflareBridgeClient(env_path=ENV_PATH)
            try:
                default_interval = int(os.environ.get("MARKET_SYNC_INTERVAL_SEC", "300"))
            except ValueError:
                default_interval = 300
            try:
                poll_interval = int(os.environ.get("REMOTE_POLL_INTERVAL_SEC", "10"))
            except ValueError:
                poll_interval = 10

            sync_service = MarketSyncService(
                store=store,
                client=cf_client,
                default_interval_sec=default_interval,
            )
            remote_poller = RemoteCommandPoller(
                store=store,
                client=cf_client,
                sync_service=sync_service,
                poll_interval_sec=poll_interval,
            )
            _market_runtime = {
                "store": store,
                "client": cf_client,
                "sync_service": sync_service,
                "remote_poller": remote_poller,
            }
        return _market_runtime


def reset_market_runtime_for_tests(data_dir=None, client=None):
    """Test helper to swap runtime cleanly with isolated temp directory and mock client."""
    global _market_runtime
    with _market_runtime_lock:
        if _market_runtime is not None:
            try:
                _market_runtime["remote_poller"].stop(timeout=1.0)
                _market_runtime["sync_service"].stop(timeout=1.0)
                _market_runtime["store"].close()
            except Exception:
                pass
            _market_runtime = None
    return get_market_runtime(data_dir=data_dir, client=client)


def _render_all_cards_helper(post, theme="light", enable_attached_image=None, platform=None):
    """
    Hàm chuẩn hóa render toàn bộ bộ thẻ theo nền tảng (Threads / Facebook / Instagram / Phim Tư Liệu):
    - Thẻ Post chính (hỗ trợ đính kèm ảnh thực tế hoặc hiển thị phong cách FB/IG)
    - Khung Ảnh Minh Họa Nổi Bên Ngoài (Popup Proof Card với viền Neon & 3D shadow)
    - Tất cả các thẻ Comment với avatar thật và bong bóng chat chuẩn nền tảng
    - Định dạng Phim Tư Liệu: Tạo thẻ Header thời sự/hồ sơ kỳ thú
    """
    os.makedirs(CARDS_DIR, exist_ok=True)
    cfg = config_mod.load_config()
    illus_cfg = cfg.get("illustration_settings", {})

    if not platform or platform == "auto":
        if isinstance(post, dict):
            platform = post.get("platform", "threads")
        else:
            platform = getattr(post, "platform", "threads")
        if platform == "auto":
            platform = "threads"
    plat = (platform or "threads").lower().strip()

    # Xử lý riêng cho định dạng Phim Tư Liệu (Hình + Video)
    if plat == "documentary":
        header_path = os.path.join(CARDS_DIR, "header_badge.png")
        p_dict = post if isinstance(post, dict) else {}
        story_title = (p_dict.get("video_title") if p_dict else getattr(post, "video_title", "")) or \
                      (p_dict.get("title") if p_dict else getattr(post, "title", "")) or \
                      ((p_dict.get("content", "")[:50].upper() if p_dict else getattr(post, "content", "")[:50].upper()) if (p_dict.get("content") if p_dict else getattr(post, "content", "")) else "HỒ SƠ TƯ LIỆU QUỐC TẾ")
        story_badge = (p_dict.get("story_badge") if p_dict else getattr(post, "story_badge", "")) or "HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT"
        visual_builder_mod.create_header_badge_image(story_title, story_badge, header_path)
        return

    if theme == "auto" or not theme:
        rec_theme = post.get("recommended_theme", "light") if isinstance(post, dict) else getattr(post, "recommended_theme", "light")
        theme = rec_theme or "light"

    if isinstance(post, dict):
        username = post.get("username", "author")
        content = post.get("content", "")
        avatar_color = post.get("avatar_color", None)
        is_verified = bool(post.get("is_verified", False))
        likes = int(post.get("likes", 0))
        post_avatar = post.get("avatar_url", "") or post.get("avatar_path", "")
        enable_illus = bool(post.get("enable_illustration", True))
        post_illus = post.get("illustration_path", "")
        placement = post.get("visual_proof_placement", "attached_in_post")
        if enable_illus and post_illus and os.path.exists(post_illus):
            if not placement or placement == "none":
                placement = "attached_in_post"
            has_proof = True
        else:
            post_illus = ""
            enable_illus = False
            has_proof = False
        illus_cat = post.get("illustration_category", "general_life")
        comments = post.get("comments", [])
    else:
        username = post.username
        content = post.content
        avatar_color = getattr(post, "avatar_color", None)
        is_verified = getattr(post, "is_verified", False)
        likes = getattr(post, "likes", 0)
        post_avatar = getattr(post, "avatar_url", "") or getattr(post, "avatar_path", "")
        enable_illus = bool(getattr(post, "enable_illustration", True))
        post_illus = getattr(post, "illustration_path", "")
        placement = getattr(post, "visual_proof_placement", "attached_in_post")
        if enable_illus and post_illus and os.path.exists(post_illus):
            if not placement or placement == "none":
                placement = "attached_in_post"
            has_proof = True
        else:
            post_illus = ""
            enable_illus = False
            has_proof = False
        illus_cat = getattr(post, "illustration_category", "general_life")
        comments = getattr(post, "comments", [])

    # Tuyệt đối không fallback sang ảnh chung cư/nhà hàng không liên quan
    if not post_illus or not os.path.exists(post_illus):
        post_illus = ""
        enable_illus = False

    use_attached = (placement == "attached_in_post") or (plat in ["facebook", "instagram"] and enable_illus) or (enable_attached_image if enable_attached_image is not None else illus_cfg.get("enable_post_attached_image", False))
    attached_arg = post_illus if (enable_illus and use_attached and post_illus and os.path.exists(post_illus)) else None

    # 1. Thẻ bài đăng chính
    p_img = card_mod.render_post_card(
        username, content, avatar_color, is_verified, likes,
        theme=theme, avatar_source=post_avatar,
        attached_image_path=attached_arg,
        platform=plat
    )
    card_mod.save_card(p_img, os.path.join(CARDS_DIR, "post.png"))

    # Render các thẻ bước nếu bật sentence reveal
    reveal_mode = post.get("sentence_reveal_mode", "standard_full") if isinstance(post, dict) else getattr(post, "sentence_reveal_mode", "standard_full")
    raw_sentences = re.split(r"(?<=[.!?\n])\s+", content.strip())
    sentences = [s.strip() for s in raw_sentences if s.strip()]
    if reveal_mode == "progressive_line_reveal" and len(sentences) >= 2:
        for step_idx in range(1, len(sentences)):
            step_img = card_mod.render_post_card(
                username, content, avatar_color, is_verified, likes,
                theme=theme, avatar_source=post_avatar,
                attached_image_path=attached_arg,
                visible_sentence_count=step_idx,
                total_sentences_content=content,
                platform=plat
            )
            card_mod.save_card(step_img, os.path.join(CARDS_DIR, f"post_step_{step_idx}.png"))

    # 2. Khung ảnh minh họa nổi bên ngoài: CHỈ khi không gắn trong bài và được bật
    popup_path = os.path.join(CARDS_DIR, "popup_proof.png")
    allow_popup = enable_illus and (not use_attached) and illus_cfg.get("enable_popup_illustration", True)
    if allow_popup and post_illus and os.path.exists(post_illus):
        badge_lbl = illus_cfg.get("badge_text", "📸 BẰNG CHỨNG THỰC TẾ")
        caption_lbl = (post.get("illustration_caption", "") or post.get("video_title", "")) if isinstance(post, dict) else (getattr(post, "illustration_caption", "") or getattr(post, "video_title", ""))
        card_mod.create_popup_illustration_card(post_illus, popup_path, badge_text=badge_lbl, caption_text=caption_lbl)
    else:
        if os.path.exists(popup_path):
            try: os.remove(popup_path)
            except OSError: pass

    # 2b. Các thẻ ảnh tư liệu đa điểm rơi (True Story Cutaways)
    popups = post.get("popup_illustrations", []) if isinstance(post, dict) else getattr(post, "popup_illustrations", [])
    for p_idx, p_item in enumerate(popups, 1):
        p_src = p_item.get("src", "") or p_item.get("path", "")
        p_out = os.path.join(CARDS_DIR, f"popup_proof_{p_idx}.png")
        if p_src and os.path.exists(p_src) and not p_src.endswith(f"popup_proof_{p_idx}.png"):
            badge_lbl = p_item.get("badge", "TƯ LIỆU CÓ THẬT")
            caption_lbl = p_item.get("caption", "")
            card_mod.create_popup_illustration_card(p_src, p_out, badge_text=badge_lbl, caption_text=caption_lbl)
            p_item["path"] = p_out

    # 3. Các thẻ comment
    for i, c in enumerate(comments, 1):
        if isinstance(c, dict):
            c_user = c.get("username", f"user_{i}")
            c_text = c.get("content", "")
            c_color = c.get("avatar_color", None)
            c_ver = bool(c.get("is_verified", False))
            c_likes = int(c.get("likes", 0))
            c_avatar = c.get("avatar_url", "") or c.get("avatar_path", "")
            c_reply = c.get("reply_to") or username
        else:
            c_user = c.username
            c_text = c.content
            c_color = getattr(c, "avatar_color", None)
            c_ver = getattr(c, "is_verified", False)
            c_likes = getattr(c, "likes", 0)
            c_avatar = getattr(c, "avatar_url", "") or getattr(c, "avatar_path", "")
            c_reply = getattr(c, "reply_to", "") or username

        c_img = card_mod.render_comment_card(
            c_user, c_text, c_color, c_ver, c_likes,
            index=i, reply_to=c_reply, theme=theme, avatar_source=c_avatar,
            platform=plat
        )
        card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))

    # 4. Thẻ Outro Call-To-Action (2.4s cuối)
    try:
        outro_card_path = os.path.join(CARDS_DIR, "outro_cta.png")
        cta_q = (post.get("cta_question", "") if isinstance(post, dict) else getattr(post, "cta_question", "")) or "Còn bạn thì sao? Đã từng gặp tình huống éo le tương tự chưa?"
        card_mod.render_outro_cta_card(question_text=cta_q, output_path=outro_card_path, platform=plat)
    except Exception as e:
        print(f"[WARN] Failed to render outro_cta card in helper: {e}")


app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Threads Video Automation Studio Pro</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
  <!-- Vue 3 nạp từ file cục bộ (100% offline nội bộ, tránh lỗi unpkg.com bị chặn tại VN) -->
  <script src="/static/js/vue.global.js"></script>
  <script>
    if (typeof Vue === 'undefined') {
      document.write('<script src="https://cdnjs.cloudflare.com/ajax/libs/vue/3.4.21/vue.global.prod.js"><\\/script>');
    }
  </script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca' },
            dark: { 900: '#090d16', 800: '#0f172a', 700: '#1e293b', 600: '#334155' }
          }
        }
      }
    }
  </script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
    [v-cloak] { display: none !important; }
    body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #080c15; }
    .font-mono { font-family: 'JetBrains Mono', monospace; }
    .glass-panel {
      background: rgba(15, 23, 42, 0.72);
      backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
    }
    .glass-input {
      background: rgba(30, 41, 59, 0.55);
      border: 1px solid rgba(255, 255, 255, 0.1);
      transition: all 0.2s ease;
    }
    .glass-input:focus {
      border-color: #6366f1;
      box-shadow: 0 0 16px rgba(99, 102, 241, 0.3);
      background: rgba(30, 41, 59, 0.85);
    }
    .card-item-hover {
      transition: all 0.2s ease;
    }
    .card-item-hover:hover {
      border-color: rgba(99, 102, 241, 0.4);
      background: rgba(15, 23, 42, 0.9);
      box-shadow: 0 8px 24px -6px rgba(0, 0, 0, 0.6);
    }
    ::-webkit-scrollbar { width: 7px; height: 7px; }
    ::-webkit-scrollbar-track { background: rgba(15, 23, 42, 0.6); border-radius: 8px; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }
    ::-webkit-scrollbar-thumb:hover { background: #475569; }
  </style>
</head>
<body class="bg-[#080c15] text-slate-100 min-h-screen selection:bg-indigo-500 selection:text-white">
  <div id="app" v-cloak class="flex flex-col min-h-screen">
    
    <!-- Top Navigation Header -->
    <header class="h-16 border-b border-slate-800/80 glass-panel sticky top-0 z-50 px-4 sm:px-8 flex items-center justify-between">
      <div class="flex items-center space-x-3.5">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-brand-600 via-indigo-500 to-pink-500 flex items-center justify-center shadow-lg shadow-indigo-500/25">
          <i class="fa-solid fa-wand-magic-sparkles text-white text-lg"></i>
        </div>
        <div>
          <h1 class="text-lg font-bold bg-gradient-to-r from-white via-slate-100 to-indigo-300 bg-clip-text text-transparent flex items-center gap-2">
            Threads Video Studio <span class="text-[11px] px-2.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-semibold tracking-wide">AI Director 2.0</span>
          </h1>
          <div class="text-xs text-slate-400 flex items-center gap-2">
            <span class="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            Gemini 3.5 Flash • Đa Nền Tảng (Threads / FB / IG) • Đồ Thị Cảm Xúc
          </div>
        </div>
      </div>

      <!-- Center Tabs -->
      <nav class="flex items-center bg-slate-900/90 p-1.5 rounded-xl border border-slate-800 shadow-inner">
        <button @click="currentTab = 'studio'" :class="currentTab === 'studio' ? 'bg-brand-600 text-white font-bold shadow-md shadow-indigo-600/35' : 'text-slate-400 hover:text-slate-200'"
          class="px-4 py-1.5 rounded-lg text-xs transition flex items-center gap-2 cursor-pointer">
          <i class="fa-solid fa-film"></i>
          <span>1. Studio Sáng Tạo</span>
        </button>

        <button @click="currentTab = 'pipeline'" :class="currentTab === 'pipeline' ? 'bg-brand-600 text-white font-bold shadow-md shadow-indigo-600/35' : 'text-slate-400 hover:text-slate-200'"
          class="px-4 py-1.5 rounded-lg text-xs transition flex items-center gap-2 relative cursor-pointer">
          <i class="fa-solid fa-chart-line"></i>
          <span>2. Giám Sát Quy Trình</span>
          <span v-if="pipelineState.is_running" class="w-2.5 h-2.5 rounded-full bg-pink-500 animate-ping absolute -top-0.5 -right-0.5"></span>
        </button>

        <button @click="currentTab = 'config'" :class="currentTab === 'config' ? 'bg-brand-600 text-white font-bold shadow-md shadow-indigo-600/35' : 'text-slate-400 hover:text-slate-200'"
          class="px-4 py-1.5 rounded-lg text-xs transition flex items-center gap-2 cursor-pointer">
          <i class="fa-solid fa-sliders"></i>
          <span>3. Cấu Hình Đạo Diễn</span>
        </button>

        <button @click="currentTab = 'market'; loadMarketDashboard();" :class="currentTab === 'market' ? 'bg-brand-600 text-white font-bold shadow-md shadow-indigo-600/35' : 'text-slate-400 hover:text-slate-200'"
          class="px-4 py-1.5 rounded-lg text-xs transition flex items-center gap-2 relative cursor-pointer">
          <i class="fa-solid fa-magnifying-glass-chart"></i>
          <span>4. Market Intelligence</span>
          <span v-if="marketStatus.is_syncing" class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-ping absolute -top-0.5 -right-0.5"></span>
        </button>
      </nav>

      <!-- Right Action Tools -->
      <div class="flex items-center space-x-3">
        <button @click="openFinalFolder" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold glass-input hover:bg-slate-700/60 transition flex items-center gap-2 text-slate-300 cursor-pointer">
          <i class="fa-regular fa-folder-open text-indigo-400"></i> Thư Mục Video
        </button>
        <button @click="saveSettings(true)" class="px-4 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 shadow-md shadow-indigo-600/30 transition flex items-center gap-2 cursor-pointer">
          <i class="fa-solid fa-floppy-disk"></i> Lưu Nhanh
        </button>
      </div>
    </header>

    <!-- TAB 1: STUDIO SÁNG TẠO & BIÊN TẬP -->
    <main v-show="currentTab === 'studio'" class="flex-1 max-w-[1760px] w-full mx-auto px-4 sm:px-8 py-6 grid grid-cols-1 xl:grid-cols-12 gap-8">
      
      <!-- Left Panel: Controls & Script Editor (7 cols on XL, 8 cols on 2XL) -->
      <div class="xl:col-span-7 2xl:col-span-8 flex flex-col space-y-6">
        
        <!-- Section 1: AI Prompt & Generator -->
        <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-5">
          <div class="flex items-center justify-between border-b border-slate-800/80 pb-3">
            <h2 class="text-sm font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-2">
              <i class="fa-solid fa-wand-magic-sparkles text-indigo-400"></i> 1. Sáng Tạo Kịch Bản Bằng Gemini AI & Quét Threads
            </h2>
            <span class="text-xs px-3 py-1 rounded-full bg-slate-800 text-indigo-300 font-mono border border-slate-700/60 flex items-center gap-1.5 shadow-inner">
              <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
              gemini-3.5-flash
            </span>
          </div>

          <div class="space-y-4">
            <!-- Thanh nhập chủ đề (Rộng rãi, dễ nhìn) -->
            <div class="relative flex items-center">
              <i class="fa-solid fa-magnifying-glass absolute left-4 text-slate-400 text-base"></i>
              <input v-model="topicInput" @keyup.enter="generateScriptOnly" type="text"
                placeholder="Nhập chủ đề muốn làm video (ví dụ: cú lừa 250 áo phông của nhóm Tân Phát xít Đức, công sở, phỏng vấn...)"
                class="w-full glass-input rounded-xl pl-12 pr-4 py-3.5 text-sm focus:outline-none placeholder-slate-500 font-medium border border-slate-700/80 focus:border-indigo-500 transition shadow-inner">
            </div>

            <!-- TỰ ĐỘNG TÌM CHỦ ĐỀ THEO NGÀY (KHÔNG HARDCODE) -->
            <div class="p-4 rounded-2xl bg-slate-900/90 border border-slate-800 space-y-3.5 shadow-lg">
              <div class="flex items-center justify-between flex-wrap gap-2.5">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-xs font-bold text-amber-400 uppercase tracking-wide flex items-center gap-1.5">
                    <i class="fa-solid fa-calendar-day"></i> Quét chủ đề theo ngày:
                  </span>
                  <!-- Date Picker -->
                  <input type="date" v-model="selectedDate" @change="loadDailyTrendingTopics(false)"
                    class="glass-input rounded-xl px-3 py-1 text-xs text-slate-100 bg-slate-800 border border-slate-700 font-mono">

                  <button type="button" @click="selectedDate = new Date().toISOString().split('T')[0]; loadDailyTrendingTopics(false);"
                    class="text-[11px] px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition cursor-pointer font-medium">
                    Hôm nay
                  </button>
                </div>

                <!-- 2 Tab Chế độ & Quét bài -->
                <div class="flex items-center gap-1.5 bg-slate-950 p-1 rounded-xl border border-slate-800">
                  <button type="button" @click="topicModeTab = 'true_story'; loadDailyTrendingTopics(false);"
                    :class="topicModeTab === 'true_story' ? 'bg-gradient-to-r from-amber-600 to-red-600 text-white font-bold shadow-md' : 'text-slate-400 hover:text-slate-200'"
                    class="px-3 py-1 rounded-lg text-xs transition cursor-pointer flex items-center gap-1.5">
                    <i class="fa-solid fa-landmark text-xs"></i> Chuyện Thật Lịch Sử (On This Day)
                  </button>
                  <button type="button" @click="topicModeTab = 'social'; loadDailyTrendingTopics(false);"
                    :class="topicModeTab === 'social' ? 'bg-gradient-to-r from-indigo-600 to-blue-600 text-white font-bold shadow-md' : 'text-slate-400 hover:text-slate-200'"
                    class="px-3 py-1 rounded-lg text-xs transition cursor-pointer flex items-center gap-1.5">
                    <i class="fa-brands fa-threads text-xs"></i> Trend Threads
                  </button>
                  <button type="button" @click="loadDailyTrendingTopics(true)" :disabled="isRefreshingTrends"
                    title="Quét mới bằng Gemini AI"
                    class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-amber-300 text-xs transition cursor-pointer disabled:opacity-50">
                    <i class="fa-solid fa-arrows-rotate" :class="{'fa-spin': isRefreshingTrends}"></i>
                  </button>
                </div>
              </div>

              <!-- Danh sách chủ đề tìm thấy tự động theo ngày -->
              <div class="space-y-2">
                <div class="flex items-center justify-between text-xs text-slate-400">
                  <span class="font-medium text-slate-300">
                    {{ topicModeTab === 'true_story' ? '🏛️ Các sự kiện & kỳ án có thật trong lịch sử ' + (trendingTopicsInfo.date_display || selectedDate) + ':' : '🔥 Chủ đề bàn tán nóng trên Threads ' + (trendingTopicsInfo.date_display || selectedDate) + ':' }}
                  </span>
                  <span v-if="isRefreshingTrends" class="text-amber-400 text-xs font-semibold">
                    <i class="fa-solid fa-spinner fa-spin"></i> Đang quét qua AI...
                  </span>
                </div>

                <div class="flex flex-wrap gap-2">
                  <button v-for="(t, tIdx) in viralTopics" :key="tIdx" type="button"
                    @click="selectDailyTopic(t)"
                    class="px-3 py-1.5 rounded-xl text-xs font-medium transition cursor-pointer flex items-center gap-2 border text-left"
                    :class="topicModeTab === 'true_story' ? 'bg-amber-950/40 hover:bg-amber-900/70 text-amber-200 border-amber-600/40 hover:border-amber-400' : 'bg-indigo-950/40 hover:bg-indigo-900/70 text-indigo-200 border-indigo-600/40 hover:border-indigo-400'">
                    <span>{{ topicModeTab === 'true_story' ? '📜' : '💬' }}</span>
                    <span>{{ t }}</span>
                  </button>
                </div>
              </div>
            </div>

            <!-- Chọn Phong cách câu chuyện: Đời thường vs Chuyện thật có tư liệu -->
            <div class="p-3.5 rounded-xl bg-slate-900/80 border border-slate-800 space-y-2.5">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-slate-300 flex items-center gap-1.5 uppercase tracking-wide">
                  <i class="fa-solid fa-book-open text-amber-400"></i> Phong cách nội dung:
                </span>
                <span class="text-xs text-slate-400 font-medium hidden sm:inline">
                  {{ storyModeInput === 'true_story' ? 'Chuyện thật quốc tế: có video/ảnh tư liệu gốc phát ở giữa, tình tiết chuẩn xác' : 'Đời sống Gen Z: drama dí dỏm, đối đáp cà khịa đời thường' }}
                </span>
              </div>
              <div class="grid grid-cols-2 gap-2.5">
                <button type="button" @click="storyModeInput = 'auto'"
                  :class="storyModeInput !== 'true_story' ? 'bg-gradient-to-r from-indigo-600 to-blue-600 text-white font-bold shadow-md ring-2 ring-indigo-400/50' : 'bg-slate-800/80 hover:bg-slate-800 text-slate-300 border border-slate-700/60'"
                  class="py-2.5 px-3 rounded-xl text-xs font-semibold transition flex items-center justify-center gap-2 cursor-pointer">
                  <span>☕ Đời Thường Gen Z</span>
                </button>
                <button type="button" @click="storyModeInput = 'true_story'"
                  :class="storyModeInput === 'true_story' ? 'bg-gradient-to-r from-amber-600 to-red-600 text-white font-bold shadow-md ring-2 ring-amber-400/50' : 'bg-slate-800/80 hover:bg-slate-800 text-slate-300 border border-slate-700/60'"
                  class="py-2.5 px-3 rounded-xl text-xs font-semibold transition flex items-center justify-center gap-2 cursor-pointer">
                  <span>🌍 Chuyện Thật Quốc Tế (Tư Liệu)</span>
                </button>
              </div>
            </div>

            <!-- Chọn Nền tảng nội dung: Segmented Control rộng rãi, màu sắc trực quan -->
            <div class="p-3.5 rounded-xl bg-slate-900/80 border border-slate-800 space-y-2.5">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-slate-300 flex items-center gap-1.5 uppercase tracking-wide">
                  <i class="fa-solid fa-shapes text-sky-400"></i> Nền tảng nội dung & Phong cách hiển thị:
                </span>
                <span class="text-xs text-slate-400 font-medium hidden sm:inline">
                  {{ settings.platform === 'auto' ? 'AI tự động chọn nền tảng tối ưu theo nội dung' : (settings.platform === 'documentary' ? 'Định dạng Phim Tư Liệu: Video & Hình ảnh tư liệu xen kẽ toàn màn hình' : (settings.platform === 'facebook' ? 'Định dạng Facebook Confession / Post kèm tương tác' : (settings.platform === 'instagram' ? 'Định dạng Instagram Feed kèm Story Ring' : 'Định dạng Threads tối giản đối đáp nhanh'))) }}
                </span>
              </div>
              <div class="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
                <button type="button" @click="setPlatform('auto')"
                  :class="settings.platform === 'auto' ? 'bg-gradient-to-r from-purple-600 to-indigo-600 text-white font-bold shadow-md shadow-purple-600/35 ring-2 ring-purple-400/50' : 'bg-slate-800/80 hover:bg-slate-800 text-slate-300 border border-slate-700/60'"
                  class="py-2.5 px-3 rounded-xl text-xs font-semibold transition flex items-center justify-center gap-2 cursor-pointer">
                  <span class="text-base">🤖</span>
                  <span>Tự động</span>
                </button>
                <button type="button" @click="setPlatform('documentary')"
                  :class="settings.platform === 'documentary' ? 'bg-gradient-to-r from-red-600 to-amber-600 text-white font-bold shadow-md shadow-red-600/35 ring-2 ring-amber-400/50' : 'bg-slate-800/80 hover:bg-slate-800 text-slate-300 border border-slate-700/60'"
                  class="py-2.5 px-3 rounded-xl text-xs font-semibold transition flex items-center justify-center gap-2 cursor-pointer">
                  <span class="text-base">🎬</span>
                  <span>Hình + Video</span>
                </button>
                <button type="button" @click="setPlatform('threads')"
                  :class="settings.platform === 'threads' ? 'bg-indigo-600 text-white font-bold shadow-md shadow-indigo-600/35 ring-2 ring-indigo-400/50' : 'bg-slate-800/80 hover:bg-slate-800 text-slate-300 border border-slate-700/60'"
                  class="py-2.5 px-3 rounded-xl text-xs font-semibold transition flex items-center justify-center gap-2 cursor-pointer">
                  <span class="text-base">🧵</span>
                  <span>Threads</span>
                </button>
                <button type="button" @click="setPlatform('facebook')"
                  :class="settings.platform === 'facebook' ? 'bg-blue-600 text-white font-bold shadow-md shadow-blue-600/35 ring-2 ring-blue-400/50' : 'bg-slate-800/80 hover:bg-slate-800 text-slate-300 border border-slate-700/60'"
                  class="py-2.5 px-3 rounded-xl text-xs font-semibold transition flex items-center justify-center gap-2 cursor-pointer">
                  <span class="text-base">📘</span>
                  <span>Facebook</span>
                </button>
                <button type="button" @click="setPlatform('instagram')"
                  :class="settings.platform === 'instagram' ? 'bg-gradient-to-r from-pink-600 to-rose-600 text-white font-bold shadow-md shadow-pink-600/35 ring-2 ring-pink-400/50' : 'bg-slate-800/80 hover:bg-slate-800 text-slate-300 border border-slate-700/60'"
                  class="py-2.5 px-3 rounded-xl text-xs font-semibold transition flex items-center justify-center gap-2 cursor-pointer">
                  <span class="text-base">📸</span>
                  <span>Instagram</span>
                </button>
              </div>
            </div>

            <!-- Tùy chỉnh nhanh: Số lượng bình luận & Giọng đọc truyền cảm -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 p-4 rounded-xl bg-slate-900/70 border border-slate-800">
              <div class="space-y-1.5">
                <label class="text-xs font-bold text-slate-300 flex items-center gap-1.5">
                  <i class="fa-solid fa-list-ol text-cyan-400"></i>
                  <span>Số lượng câu trả lời / bình luận:</span>
                </label>
                <select v-model="commentCountInput" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                  <option value="auto">⏱️ Tự động (7 - 8 câu / Chuẩn 1 Phút+ Kiếm Tiền)</option>
                  <option value="7">⭐ Đúng 7 câu (Chuẩn 1 Phút+ TikTok Creator Rewards)</option>
                  <option value="8">🚀 Đúng 8 câu (Đại chiến kịch tính 70s+)</option>
                  <option value="6">💬 Đúng 6 câu (Khoảng 50s - 60s)</option>
                  <option value="5">💬 Đúng 5 câu</option>
                  <option value="4">💬 Đúng 4 câu</option>
                </select>
                <span class="text-xs text-slate-400 block">Chuẩn video dài trên 1 phút giúp kiếm tiền TikTok Creator Rewards</span>
              </div>

              <div class="space-y-1.5">
                <label class="text-xs font-bold text-slate-300 flex items-center gap-1.5">
                  <i class="fa-solid fa-microphone-lines text-pink-400"></i>
                  <span>Giọng đọc dẫn chuyện chính:</span>
                </label>
                <select v-model="authorVoiceInput" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                  <option value="nam_hai">🔥 Nam Hài Hước (Dí dỏm, tưng tửng, bắt trend - Khuyên dùng)</option>
                  <option value="nu_cakhia">🌸 Nữ Cà Khịa (Đanh đá, xéo xắt duyên dáng, đỉnh Threads)</option>
                  <option value="nam_tram_hai">🎙️ Nam Trầm Hài (Bựa, cạn lời, kiểu Độ Mixi / PewPew)</option>
                  <option value="nu_nhinhanh">✨ Nữ Nhí Nhảnh (Dễ thương, vui tươi, hài nhẹ)</option>
                  <option value="chi_google">🤖 Chị Google (Meme robot hài hước)</option>
                  <option value="vi-VN-NamMinhNeural">🇻🇳 Nam Minh Chuẩn (Nghiêm túc, ấm áp)</option>
                  <option value="vi-VN-HoaiMyNeural">🌸 Hoài My Chuẩn (Truyền cảm, tự nhiên)</option>
                  <option value="adam">🎙️ Adam (Podcast quốc tế)</option>
                  <option value="brian">🎙️ Brian (Điện ảnh quốc tế)</option>
                </select>
                <span class="text-xs text-slate-400 block">Giọng đọc tiếng Việt tự nhiên 100%, rõ ràng, truyền cảm và chuẩn nhịp điệu</span>
              </div>
            </div>

            <!-- Đúng 2 nút bấm hành động chính rõ ràng, cân đối -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
              <!-- Nút 1: Tạo kịch bản để duyệt và sửa trước -->
              <button @click="generateScriptOnly" :disabled="pipelineState.is_running || isGeneratingScript"
                title="AI nghĩ kịch bản và tải vào form để bạn tự do đọc duyệt và sửa từng câu chữ trước khi dựng video"
                class="py-3.5 px-5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs shadow-lg shadow-indigo-600/30 transition flex items-center justify-center gap-2.5 disabled:opacity-50 cursor-pointer">
                <i v-if="isGeneratingScript" class="fa-solid fa-spinner fa-spin text-base"></i>
                <i v-else class="fa-solid fa-pen-nib text-base"></i>
                <span class="text-sm">📝 TẠO KỊCH BẢN ĐỂ DUYỆT</span>
              </button>

              <!-- Nút 2: Tự động 100% 1-Click từ A-Z -->
              <button @click="createVideoOneClick" :disabled="pipelineState.is_running"
                title="Tự động 100% từ A đến Z: AI kịch bản -> Avatar thật -> Render video 60 FPS ngay"
                class="py-3.5 px-5 rounded-xl bg-gradient-to-r from-pink-600 via-rose-500 to-indigo-600 hover:opacity-95 text-white font-extrabold text-xs shadow-xl shadow-pink-500/30 transition flex items-center justify-center gap-2.5 disabled:opacity-50 cursor-pointer">
                <i v-if="pipelineState.is_running" class="fa-solid fa-spinner fa-spin text-base"></i>
                <i v-else class="fa-solid fa-bolt text-base"></i>
                <span class="text-sm">🚀 1-CLICK TỰ ĐỘNG XUẤT VIDEO</span>
              </button>
            </div>


            <!-- Emotional Arc Badge -->
            <div v-if="post.emotional_arc" class="p-3 rounded-xl bg-indigo-950/40 border border-indigo-800/40 flex items-center justify-between text-xs">
              <span class="text-indigo-300 font-semibold flex items-center gap-2"><i class="fa-solid fa-wave-square text-indigo-400"></i> Đồ thị cảm xúc:</span>
              <span class="text-slate-200 font-medium">{{ post.emotional_arc }}</span>
            </div>
          </div>
        </div>

        <!-- Section 2: Script Detail Editor & Review Workspace -->
        <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-5">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3.5 flex-wrap gap-2">
            <div>
              <h2 class="text-sm font-bold uppercase tracking-wider text-pink-400 flex items-center gap-2">
                <i class="fa-solid fa-pen-to-square"></i> 2. Bảng Soạn & Duyệt Kịch Bản (Tự Do Chỉnh Sửa)
              </h2>
              <p class="text-xs text-slate-400 mt-1">Sửa câu từ, chọn ảnh người thật, đổi Meme GIF/SFX, thêm hoặc xóa bình luận tùy ý mà không bị gò bó.</p>
            </div>
            <button @click="refreshCardPreview" class="px-3.5 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold flex items-center gap-2 transition shadow-md shadow-indigo-600/30 cursor-pointer">
              <i class="fa-solid fa-rotate-right"></i>
              <span>Cập Nhật Thẻ Xem Trước</span>
            </button>
          </div>

          <!-- Post Editor (Author) (Khi chọn Threads / FB / IG) -->
          <template v-if="settings.platform !== 'documentary'">
            <div class="p-5 rounded-2xl bg-slate-900/80 border border-slate-800 space-y-4 shadow-md">
            <div class="flex items-center justify-between border-b border-slate-800/80 pb-3 flex-wrap gap-2">
              <div class="flex items-center gap-2.5 flex-wrap">
                <span class="text-xs font-bold text-amber-400 flex items-center gap-1.5 uppercase tracking-wide">
                  <i class="fa-solid fa-feather-pointed"></i> BÀI ĐĂNG CỦA TÁC GIẢ (HOOK MỞ MÀN)
                </span>
                <span class="text-xs px-2.5 py-0.5 rounded-full font-semibold border" 
                  :class="post.sentence_reveal_mode === 'progressive_line_reveal' ? 'bg-amber-950/50 text-amber-300 border-amber-600/50' : 'bg-slate-800 text-slate-400 border-slate-700'">
                  <i class="fa-solid mr-1" :class="post.sentence_reveal_mode === 'progressive_line_reveal' ? 'fa-layer-group' : 'fa-file-lines'"></i>
                  {{ post.sentence_reveal_mode === 'progressive_line_reveal' ? 'Bung câu từng dòng' : 'Hiện trọn vẹn' }}
                </span>
                <span v-if="post.recommended_theme" class="text-xs px-2.5 py-0.5 rounded-full font-semibold border"
                  :class="post.recommended_theme === 'dark' ? 'bg-slate-900 text-indigo-300 border-indigo-500/50' : 'bg-amber-950/40 text-amber-300 border-amber-600/40'">
                  <i class="fa-solid mr-1" :class="post.recommended_theme === 'dark' ? 'fa-moon' : 'fa-sun'"></i>
                  Theme: {{ post.recommended_theme === 'dark' ? 'Tối' : 'Sáng' }}
                </span>
                <span v-if="post.satisfying_category" class="text-xs px-2.5 py-0.5 rounded-full font-semibold border bg-emerald-950/40 text-emerald-300 border-emerald-600/40">
                  <i class="fa-solid fa-shapes mr-1"></i>
                  Nền: {{ post.satisfying_category === 'food' ? 'Ẩm thực' : (post.satisfying_category === 'puzzle' ? '3D Puzzle' : (post.satisfying_category === 'asmr' ? 'ASMR' : (post.satisfying_category === 'gameplay' ? 'Gameplay' : post.satisfying_category))) }}
                </span>
              </div>
              <div class="flex items-center gap-4">
                <label class="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                  <input type="checkbox" v-model="post.is_verified" class="rounded bg-slate-800 border-slate-700 text-indigo-500 focus:ring-0">
                  <span class="text-indigo-400 font-semibold">Tích xanh ✓</span>
                </label>
                <div class="flex items-center gap-1.5 text-xs text-slate-300">
                  <i class="fa-regular fa-heart text-rose-500 text-sm"></i>
                  <input v-model="post.likes" type="number" class="glass-input rounded-lg px-2.5 py-1 text-xs w-24 text-right font-mono" placeholder="likes">
                </div>
              </div>
            </div>

            <!-- 3 Cột điều khiển bài đăng tác giả: Tên, Giọng đọc, Nhịp hiện câu -->
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
              <div class="space-y-1">
                <label class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
                  <i class="fa-solid fa-user text-indigo-400"></i> Tên tác giả:
                </label>
                <input v-model="post.username" type="text" class="w-full glass-input rounded-xl px-3 py-2 text-xs font-semibold text-slate-100">
              </div>

              <div class="space-y-1">
                <label class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
                  <i class="fa-solid fa-microphone text-pink-400"></i> Giọng đọc bài chính:
                </label>
                <select v-model="post.voice_id" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200 font-medium">
                  <option value="nam_hai">🔥 Nam Hài Hước (Dí dỏm, tưng tửng)</option>
                  <option value="nu_cakhia">🌸 Nữ Cà Khịa (Đanh đá, hài hước)</option>
                  <option value="nam_tram_hai">🎙️ Nam Trầm Hài (Bựa, cạn lời)</option>
                  <option value="nu_nhinhanh">✨ Nữ Nhí Nhảnh (Dễ thương)</option>
                  <option value="chi_google">🤖 Chị Google (Meme robot)</option>
                  <option value="vi-VN-NamMinhNeural">🇻🇳 Nam Minh Chuẩn</option>
                  <option value="vi-VN-HoaiMyNeural">🌸 Hoài My Chuẩn</option>
                  <option value="adam">🎙️ Adam (Podcast)</option>
                  <option value="brian">🎙️ Brian (Điện ảnh)</option>
                </select>
              </div>

              <div class="space-y-1">
                <label class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
                  <i class="fa-solid fa-layer-group text-amber-400"></i> Nhịp hiện câu:
                </label>
                <select v-model="post.sentence_reveal_mode" @change="refreshCardPreview" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200 font-medium">
                  <option value="progressive_line_reveal">✨ Bung từng câu theo nhịp đọc</option>
                  <option value="standard_full">📄 Hiện trọn vẹn cả bài</option>
                </select>
              </div>
            </div>

            <!-- Textarea nội dung bài đăng -->
            <div class="space-y-1">
              <label class="text-xs text-slate-300 font-semibold">Nội dung bài viết:</label>
              <textarea v-model="post.content" rows="4"
                class="w-full glass-input rounded-xl p-3.5 text-sm focus:outline-none placeholder-slate-500 font-normal leading-relaxed text-slate-100"
                placeholder="Nội dung bài đăng chính..."></textarea>
            </div>
            
            <!-- Avatar Picker Row for Author -->
            <div class="flex items-center gap-3 p-3 rounded-xl bg-slate-950/50 border border-slate-800/80 flex-wrap">
              <img :src="getAvatarThumb(post.avatar_url || post.avatar_path)" alt="Author Avatar"
                class="w-10 h-10 rounded-full border-2 border-indigo-500/60 object-cover shadow-md bg-slate-800 flex-shrink-0">
              <div class="flex-1 min-w-[200px]">
                <input v-model="post.avatar_url" type="text" placeholder="Dán link ảnh đại diện hoặc để trống để tự động lấy từ kho người thật"
                  class="w-full glass-input rounded-xl px-3 py-1.5 text-xs font-mono text-slate-300">
              </div>
              <button @click="openAvatarModal('author')"
                class="px-4 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-indigo-300 border border-indigo-500/40 text-xs font-semibold flex items-center gap-2 transition cursor-pointer shadow-sm">
                <i class="fa-solid fa-image-portrait"></i>
                <span>📷 Chọn Avatar Người Thật</span>
              </button>
            </div>
          </div>

          <!-- Story Visual Format & Proof Box -->
          <div class="p-5 rounded-2xl bg-slate-900/80 border border-slate-800 space-y-4 shadow-md">
            <div class="flex items-center justify-between border-b border-slate-800/80 pb-3 flex-wrap gap-2">
              <div class="flex items-center gap-2">
                <span v-if="post.enable_illustration" class="text-xs font-bold text-pink-400 flex items-center gap-1.5 uppercase tracking-wide">
                  <i class="fa-solid fa-camera-retro"></i> ĐỊNH DẠNG: CÓ ẢNH BẰNG CHỨNG THỰC TẾ
                </span>
                <span v-else class="text-xs font-bold text-emerald-400 flex items-center gap-1.5 uppercase tracking-wide">
                  <i class="fa-solid fa-comments"></i> ĐỊNH DẠNG TỰ NHIÊN: THUẦN HỘI THOẠI & ĐỐI ĐÁP (KHÔNG SPAM ẢNH)
                </span>
              </div>
              <label class="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                <input type="checkbox" v-model="post.enable_illustration" @change="refreshCardPreview"
                  class="rounded bg-slate-800 border-slate-700 text-pink-500 focus:ring-0">
                <span class="font-semibold text-xs" :class="post.enable_illustration ? 'text-pink-400' : 'text-slate-400'">
                  {{ post.enable_illustration ? 'Đang bật ảnh bằng chứng' : '+ Bật ảnh cho bài này' }}
                </span>
              </label>
            </div>

            <!-- Khi Tắt Ảnh: Thông báo nhịp dựng tự nhiên -->
            <div v-if="!post.enable_illustration" class="flex items-center justify-between text-xs text-slate-300 p-3.5 rounded-xl bg-slate-950/40 border border-slate-800/60 leading-relaxed">
              <div class="flex items-center gap-2.5">
                <i class="fa-solid fa-sparkles text-emerald-400 text-base"></i>
                <span>Video sẽ chạy theo <strong>phong cách thuần thoại tự nhiên</strong>: tập trung 100% vào sự đối đáp nhịp nhàng của các bình luận, chuyển động camera và âm thanh SFX kịch tính, không bị dập khuôn chèn ảnh gượng ép.</span>
              </div>
            </div>

            <!-- Khi Bật Ảnh: Điều khiển chi tiết -->
            <div v-else class="space-y-4">
              <div class="flex items-center gap-5 text-xs bg-slate-950/50 p-3 rounded-xl border border-slate-800/70 flex-wrap">
                <span class="font-bold text-slate-300">Cách xuất hiện:</span>
                <label class="flex items-center gap-2 cursor-pointer text-slate-200">
                  <input type="radio" value="popup_at_twist" v-model="post.visual_proof_placement" @change="refreshCardPreview" class="text-pink-500">
                  <span>Bung thẻ ảnh nổi ở đoạn bẻ lái (Cutaway Pop-up)</span>
                </label>
                <label class="flex items-center gap-2 cursor-pointer text-slate-200">
                  <input type="radio" value="attached_in_post" v-model="post.visual_proof_placement" @change="refreshCardPreview" class="text-indigo-400">
                  <span>Gắn trực tiếp trong bài tác giả (chuẩn FB / IG / Threads thật)</span>
                </label>
              </div>

              <div class="flex flex-col sm:flex-row gap-5 items-start">
                <!-- Thumbnail -->
                <div class="relative w-full sm:w-48 h-32 rounded-xl overflow-hidden border border-slate-700 bg-slate-950 flex-shrink-0 group shadow-lg">
                  <img v-if="post.illustration_path" :src="getIllustrationThumb(post.illustration_path)"
                    alt="Proof Illustration" class="w-full h-full object-cover">
                  <div v-else class="w-full h-full flex flex-col items-center justify-center text-slate-500 text-xs gap-1.5">
                    <i class="fa-solid fa-image text-2xl text-slate-600"></i>
                    <span>Chưa có ảnh</span>
                  </div>
                </div>

                <!-- Controls -->
                <div class="flex-1 space-y-3 w-full">
                  <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div class="space-y-1">
                      <label class="text-xs font-semibold text-slate-300">Danh mục ngữ cảnh chuẩn:</label>
                      <select v-model="post.illustration_category" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                        <option value="couple_photo_fail">📸 Chụp ảnh bạn gái dìm / sống ảo fail</option>
                        <option value="cafe_lifestyle">☕ Quán cafe / Thức uống sống ảo</option>
                        <option value="food_cooking">🍳 Nấu ăn cháy khét / Thảm họa bếp núc</option>
                        <option value="office_work">💼 Bàn làm việc công sở / Laptop / Tăng ca</option>
                        <option value="shopping_tech">📦 Kiện hàng bưu phẩm / Mua sắm online</option>
                        <option value="student_exam">📚 Bài vở / Thi cử / Học sinh</option>
                        <option value="general_life">🌟 Đời thường chân thật</option>
                      </select>
                    </div>

                    <div class="space-y-1">
                      <label class="text-xs font-semibold text-slate-300">Chú thích trên ảnh (dưới 8 từ):</label>
                      <input v-model="post.illustration_caption" type="text" placeholder="VD: Tác phẩm bạn trai chụp..."
                        class="w-full glass-input rounded-xl px-3 py-2 text-xs text-slate-200 font-medium">
                    </div>
                  </div>

                  <!-- Search & Upload Bar -->
                  <div class="flex items-center gap-2.5 pt-1 flex-wrap">
                    <div class="relative flex-1 min-w-[220px]">
                      <input v-model="customImageQuery" @keyup.enter="searchNewIllustration" type="text"
                        :placeholder="post.illustration_prompt || 'Nhập từ khóa tìm ảnh thật (VD: khóa xe, chụp ảnh dìm)...'"
                        class="w-full glass-input rounded-xl pl-3 pr-8 py-2 text-xs text-slate-200">
                      <i v-if="isSearchingImage" class="fa-solid fa-spinner fa-spin absolute right-3 top-2.5 text-indigo-400 text-xs"></i>
                    </div>

                    <button @click="searchNewIllustration" :disabled="isSearchingImage"
                      class="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold flex items-center gap-1.5 transition shadow-sm cursor-pointer disabled:opacity-50">
                      <i class="fa-solid fa-magnifying-glass"></i>
                      <span>Tìm Ảnh Thật Trên Web</span>
                    </button>

                    <label class="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-semibold flex items-center gap-1.5 transition cursor-pointer">
                      <i class="fa-solid fa-upload text-pink-400"></i>
                      <span>Tải Ảnh Từ Máy</span>
                      <input type="file" accept="image/*" class="hidden" @change="uploadCustomIllustration">
                    </label>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Comments Editor -->
          <div class="space-y-4 pt-1">
            <div class="flex items-center justify-between text-xs font-bold text-slate-400 uppercase tracking-wide">
              <span class="flex items-center gap-2 text-indigo-300 text-sm">
                <i class="fa-solid fa-comments"></i>
                <span>Các Bình Luận Tương Tác & Bẻ Lái ({{ post.comments ? post.comments.length : 0 }} bình luận)</span>
              </span>
              <button @click="addComment"
                class="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs flex items-center gap-2 transition shadow-md shadow-emerald-600/30 cursor-pointer">
                <i class="fa-solid fa-plus"></i>
                <span>➕ Thêm Bình Luận Mới</span>
              </button>
            </div>
            
            <div v-for="(cmt, idx) in post.comments" :key="idx"
              class="p-4 rounded-2xl bg-slate-900/60 hover:bg-slate-900/80 border border-slate-800 hover:border-slate-700/80 transition shadow-lg space-y-3">
              
              <!-- Row 1: Header (Badge, Username, Reply-to & Delete Button) -->
              <div class="flex items-center justify-between gap-3 flex-wrap border-b border-slate-800/70 pb-2.5">
                <div class="flex items-center gap-2.5 flex-wrap">
                  <span class="w-7 h-7 rounded-lg bg-indigo-500/20 text-indigo-300 text-xs flex items-center justify-center font-bold border border-indigo-500/30">#{{ idx+1 }}</span>
                  <div class="flex items-center gap-1.5">
                    <span class="text-xs text-slate-400 font-medium">@</span>
                    <input v-model="cmt.username" type="text" placeholder="username" class="glass-input rounded-xl px-3 py-1 text-xs w-36 font-semibold text-slate-200">
                  </div>
                  
                  <!-- Reply-To Selector -->
                  <div class="flex items-center gap-1.5 text-xs bg-slate-800/80 px-2.5 py-1 rounded-xl border border-slate-700/60">
                    <span class="text-slate-400"><i class="fa-solid fa-reply text-indigo-400"></i> Trả lời:</span>
                    <select v-model="cmt.reply_to" class="bg-transparent text-indigo-300 font-semibold focus:outline-none text-xs cursor-pointer">
                      <option :value="post.username">@{{ post.username || 'tác_giả' }} (Bài đăng)</option>
                      <option v-for="(otherCmt, cIdx) in post.comments.slice(0, idx)" :key="cIdx" :value="otherCmt.username">
                        @{{ otherCmt.username || ('cmt_' + (cIdx+1)) }} (#{{ cIdx + 1 }})
                      </option>
                    </select>
                  </div>
                </div>

                <div class="flex items-center gap-3">
                  <!-- Likes -->
                  <div class="flex items-center gap-1.5 text-xs text-slate-400">
                    <i class="fa-regular fa-heart text-rose-500"></i>
                    <input v-model="cmt.likes" type="number" class="glass-input rounded-lg px-2.5 py-1 text-xs w-20 text-right font-mono" placeholder="likes">
                  </div>
                  <!-- Nút Xóa Comment -->
                  <button @click="removeComment(idx)" title="Xóa bình luận này"
                    class="w-7 h-7 rounded-lg bg-rose-500/15 hover:bg-rose-500/30 text-rose-400 flex items-center justify-center transition border border-rose-500/30 cursor-pointer">
                    <i class="fa-solid fa-trash-can text-xs"></i>
                  </button>
                </div>
              </div>

              <!-- Row 2: Controls (Emotion, Voice, Meme/SFX) in a clean 3-column grid -->
              <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                <div>
                  <label class="text-xs font-semibold text-slate-400 block mb-1">Cảm xúc:</label>
                  <select v-model="cmt.emotion" class="w-full glass-input rounded-xl px-3 py-1.5 text-xs bg-slate-800 text-indigo-300 font-medium">
                    <option value="funny">😂 Hài hước</option>
                    <option value="shocked">💥 Bẻ lái sốc</option>
                    <option value="awkward">💀 Cạn lời</option>
                    <option value="sarcastic">😏 Châm biếm</option>
                    <option value="insightful">✨ Thông minh</option>
                  </select>
                </div>

                <div>
                  <label class="text-xs font-semibold text-slate-400 block mb-1">Giọng đọc:</label>
                  <select v-model="cmt.voice_id" class="w-full glass-input rounded-xl px-3 py-1.5 text-xs bg-slate-800 text-slate-200 font-medium">
                    <option value="nu_cakhia">🌸 Nữ Cà Khịa (Đanh đá, hài)</option>
                    <option value="nam_hai">🔥 Nam Hài Hước (Dí dỏm, tưng tửng)</option>
                    <option value="nam_tram_hai">🎙️ Nam Trầm Hài (Bựa, cạn lời)</option>
                    <option value="nu_nhinhanh">✨ Nữ Nhí Nhảnh (Dễ thương)</option>
                    <option value="chi_google">🤖 Chị Google (Robot meme)</option>
                    <option value="vi-VN-HoaiMyNeural">🌸 Hoài My Chuẩn</option>
                    <option value="vi-VN-NamMinhNeural">🇻🇳 Nam Minh Chuẩn</option>
                    <option value="adam">🎙️ Adam (Podcast)</option>
                    <option value="brian">🎙️ Brian (Điện ảnh)</option>
                  </select>
                </div>

                <div>
                  <label class="text-xs font-semibold text-slate-400 block mb-1">Meme & SFX:</label>
                  <select v-model="cmt.sfx" class="w-full glass-input rounded-xl px-3 py-1.5 text-xs bg-slate-800 text-slate-200 font-medium">
                    <option value="none">✨ Không SFX (Tự nhiên / Lofi)</option>
                    <option value="do_mixi">🎙️ Độ Mixi (Cười / Cay thế nhờ / Hợp lý)</option>
                    <option value="pewpew">🎙️ PewPew (Quá chuẩn luôn / Tấm chiếu mới)</option>
                    <option value="bat_ngo_chua">👵 Bất ngờ chưa bà già! (bat_ngo_chua.gif)</option>
                    <option value="cuu_toi_voi">🆘 Trời ơi cứu tui với! (cuu_toi_voi.gif)</option>
                    <option value="so_qua_co">😱 Úi sợ quá sợ quá! (so_qua_co.gif)</option>
                    <option value="emotional_damage">💥 Steven He: Emotional Damage!</option>
                    <option value="oh_no">🤣 Oh No no no (Điệu cười meme kinh điển)</option>
                    <option value="fbi_open_up">🚨 FBI Open Up! (fbi_open_up.gif)</option>
                    <option value="why_are_you_running">🏃 Why are you running?! (why_are_you_running.gif)</option>
                    <option value="coffin_dance">⚰️ Coffin Dance (coffin_dance.gif)</option>
                    <option value="to_be_continued">🎸 To Be Continued (to_be_continued.gif)</option>
                    <option value="gta_wasted">💀 GTA V Wasted (gta_wasted.gif)</option>
                    <option value="sad_violin">🎻 Sad Violin (sad_violin.gif)</option>
                    <option value="bonk">🏏 Cheems Bonk! (bonk.gif)</option>
                    <option value="sub_drop">🔥 Sub Bass Drop (Bẻ lái sốc đĩnh đạc)</option>
                    <option value="scratch">💿 Vinyl Scratch (Quay xe / Cạn lời)</option>
                    <option value="ting">🔔 Crystal Chime (Điểm sáng / Nảy số)</option>
                    <option value="pop">🫧 Bubble Pop (Nhẹ nhàng)</option>
                    <option value="boom">💥 Mind Blown bẻ lái (boom.gif)</option>
                    <option value="laugh">😂 Cười nghiêng ngả (laugh.gif)</option>
                    <option value="bruh">💀 Cạn lời Facepalm (bruh.gif)</option>
                    <option value="shock">🙀 Sốc hoảng loạn (shock.gif)</option>
                    <option value="huh">🐱 Mèo "Huh?" ngơ ngác (huh.gif)</option>
                    <option value="side_eye">👀 Liếc xéo nghi ngờ (side_eye.gif)</option>
                    <option value="clown">🤡 Tự hóa chú hề (clown.gif)</option>
                    <option value="math">📐 Tính toán hoang mang (math.gif)</option>
                    <option value="applause">👏 Vỗ tay / Nâng ly (applause.gif)</option>
                    <option value="dancing_cat">💃 Mèo nhảy ăn mừng (dancing_cat.gif)</option>
                  </select>
                </div>
              </div>

              <!-- Row 3: Content textarea -->
              <textarea v-model="cmt.content" rows="3"
                class="w-full glass-input rounded-xl p-3.5 text-sm focus:outline-none placeholder-slate-500 leading-relaxed text-slate-100"
                :placeholder="'Bình luận ' + (idx+1) + '...'"></textarea>

              <!-- Row 4: Avatar Picker for Commenter -->
              <div class="flex items-center gap-3 pt-0.5 text-xs text-slate-400 flex-wrap">
                <img :src="getAvatarThumb(cmt.avatar_url || cmt.avatar_path)" alt="Comment Avatar"
                  class="w-8 h-8 rounded-full border border-slate-700 object-cover bg-slate-800 flex-shrink-0">
                <div class="flex-1 min-w-[200px]">
                  <input v-model="cmt.avatar_url" type="text" placeholder="Link ảnh hoặc để trống để gán ảnh thật"
                    class="w-full glass-input rounded-xl px-3 py-1.5 text-xs font-mono text-slate-300">
                </div>
                <button @click="openAvatarModal('comment', idx)"
                  class="px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-indigo-300 border border-indigo-500/30 text-xs font-semibold flex items-center gap-1.5 transition cursor-pointer">
                  <i class="fa-solid fa-image-portrait"></i>
                  <span>📷 Chọn Avatar</span>
                </button>
              </div>
            </div>
          </div>

          <!-- Outro Viral CTA Question Box -->
          <div class="p-4 rounded-2xl bg-purple-950/40 border border-purple-800/60 space-y-2.5 shadow-md">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-purple-300 flex items-center gap-2">
                <i class="fa-solid fa-bullhorn text-purple-400 text-sm"></i> CÂU HỎI KÊU GỌI TƯƠNG TÁC OUTRO (2.4s CUỐI)
              </span>
              <span class="text-xs text-purple-400 font-mono">Tự động kích nổ bình luận người xem</span>
            </div>
            <input v-model="post.cta_question" @change="refreshCardPreview" type="text"
              placeholder="VD: Còn bạn thì sao? Nếu rơi vào tình huống này bạn sẽ xử lý thế nào?..."
              class="w-full glass-input rounded-xl px-4 py-2.5 text-xs text-purple-100 placeholder-purple-400/50 border border-purple-700/60 focus:border-purple-500 font-medium bg-purple-950/50">
          </div>
        </template>

        <!-- Phim Tư Liệu Editor (Hình Ảnh + Video Phân Cảnh) -->
        <template v-else>
          <!-- 1. Bối Cảnh & Header Phim Tư Liệu -->
          <div class="p-5 rounded-2xl bg-gradient-to-r from-red-950/40 via-slate-900 to-amber-950/30 border border-red-500/40 space-y-4 shadow-xl">
            <div class="flex items-center justify-between border-b border-red-500/30 pb-3 flex-wrap gap-2">
              <span class="text-xs font-extrabold text-amber-300 flex items-center gap-2 uppercase tracking-wide">
                <i class="fa-solid fa-film text-red-400"></i> BỐI CẢNH & TIÊU ĐỀ PHIM TƯ LIỆU
              </span>
              <span class="text-xs px-2.5 py-0.5 rounded-full font-bold bg-red-600/20 text-red-300 border border-red-500/40">
                🎬 Visual Documentary 60 FPS
              </span>
            </div>

            <!-- Tiêu đề & Nhãn Badge -->
            <div class="flex items-center justify-between text-xs text-slate-400 border-b border-slate-800 pb-2">
              <span class="flex items-center gap-1.5 text-amber-400 font-semibold">
                <i class="fa-solid fa-calendar-day"></i> Sự kiện tư liệu theo ngày: {{ trendingTopicsInfo.date_display || selectedDate }}
              </span>
              <span class="text-[11px] text-slate-500 font-mono">Tự động cấu trúc hóa phân cảnh & lời dẫn chuyện</span>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
              <div class="space-y-1">
                <label class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
                  <i class="fa-solid fa-heading text-red-400"></i> Tiêu đề phim (In hoa nổi bật):
                </label>
                <input v-model="post.video_title" @change="refreshCardPreview" type="text"
                  placeholder="VD: CÚ LỪA 250 CHIẾC ÁO PHÔNG TÂN PHÁT XÍT"
                  class="w-full glass-input rounded-xl px-3 py-2 text-xs font-bold text-white uppercase border border-red-500/40">
              </div>

              <div class="space-y-1">
                <label class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
                  <i class="fa-solid fa-tag text-amber-400"></i> Nhãn chuyên mục / Tổ chức:
                </label>
                <input v-model="post.story_badge" @change="refreshCardPreview" type="text"
                  placeholder="VD: HỒ SƠ KỲ THÚ • EXIT DEUTSCHLAND"
                  class="w-full glass-input rounded-xl px-3 py-2 text-xs font-semibold text-amber-300 border border-amber-500/40">
              </div>
            </div>

            <!-- Lời mở đầu / Tóm tắt câu chuyện -->
            <div class="space-y-1">
              <label class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
                <i class="fa-solid fa-feather text-sky-400"></i> Lời dẫn mở màn (Hook lôi cuốn):
              </label>
              <textarea v-model="post.content" rows="3"
                class="w-full glass-input rounded-xl p-3 text-xs leading-relaxed text-slate-100 border border-slate-700/80"
                placeholder="Lời dẫn tóm tắt câu chuyện..."></textarea>
            </div>
          </div>

          <!-- 2. Danh Sách Phân Cảnh (Video Clips & Ảnh Tư Liệu Xen Kẽ) -->
          <div class="space-y-4 pt-1">
            <div class="flex items-center justify-between text-xs font-bold text-slate-400 uppercase tracking-wide">
              <span class="flex items-center gap-2 text-red-400 text-sm">
                <i class="fa-solid fa-clapperboard"></i>
                <span>Các Phân Cảnh Tư Liệu ({{ post.documentary_scenes ? post.documentary_scenes.length : 0 }} Cảnh)</span>
              </span>
              <button @click="addDocumentaryScene"
                class="px-4 py-2 rounded-xl bg-gradient-to-r from-red-600 to-amber-600 hover:opacity-90 text-white font-bold text-xs flex items-center gap-2 transition shadow-md shadow-red-600/30 cursor-pointer">
                <i class="fa-solid fa-plus"></i>
                <span>➕ Thêm Phân Cảnh Mới</span>
              </button>
            </div>

            <div v-for="(sc, sIdx) in (post.documentary_scenes || [])" :key="sIdx"
              class="p-4 rounded-2xl bg-slate-900/70 hover:bg-slate-900/90 border border-slate-800 hover:border-red-500/40 transition shadow-lg space-y-3">
              <!-- Row 1: Header (Badge, Type, Delete) -->
              <div class="flex items-center justify-between gap-3 flex-wrap border-b border-slate-800/70 pb-2.5">
                <div class="flex items-center gap-2.5 flex-wrap">
                  <span class="w-7 h-7 rounded-lg bg-red-500/20 text-red-300 text-xs flex items-center justify-center font-bold border border-red-500/30">#{{ sIdx + 1 }}</span>
                  <!-- Type Selector -->
                  <select v-model="sc.type" class="glass-input rounded-xl px-2.5 py-1 text-xs font-bold"
                    :class="sc.type === 'video' ? 'text-red-300 bg-red-950/40 border-red-500/40' : 'text-amber-300 bg-amber-950/40 border-amber-500/40'">
                    <option value="video">🎬 Video Clip</option>
                    <option value="image">🖼️ Hình Ảnh Tư Liệu (Ken Burns)</option>
                  </select>
                  <!-- Badge Label -->
                  <input v-model="sc.badge" type="text" placeholder="Nhãn cảnh (VD: LỄ HỘI ROCK CỰC ĐOAN)"
                    class="glass-input rounded-xl px-3 py-1 text-xs w-48 font-semibold text-slate-200">
                </div>

                <div class="flex items-center gap-3">
                  <!-- SFX Selector -->
                  <div class="flex items-center gap-1.5 text-xs text-slate-400">
                    <span>SFX:</span>
                    <select v-model="sc.sfx" class="glass-input rounded-lg px-2 py-1 text-xs bg-slate-800 text-slate-200 font-medium">
                      <option value="none">✨ Không SFX</option>
                      <option value="tension_riser">🎻 Căng thẳng dồn dập (Tension Riser)</option>
                      <option value="ting">🔔 Crystal Ting (Phát hiện)</option>
                      <option value="sub_drop">🔥 Sub Bass Drop (Bẻ lái sốc)</option>
                      <option value="boom">💥 Mind Blown (Bùng nổ)</option>
                      <option value="water_splash">🌊 Tiếng nước giặt (Foley)</option>
                      <option value="applause">👏 Vỗ tay thán phục</option>
                      <option value="scratch">💿 Scratch đĩa (Quay xe)</option>
                    </select>
                  </div>

                  <!-- Delete button -->
                  <button @click="removeDocumentaryScene(sIdx)" title="Xóa phân cảnh này"
                    class="w-7 h-7 rounded-lg bg-rose-500/15 hover:bg-rose-500/30 text-rose-400 flex items-center justify-center transition border border-rose-500/30 cursor-pointer">
                    <i class="fa-solid fa-trash-can text-xs"></i>
                  </button>
                </div>
              </div>

              <!-- Row 2: Media preview & Path -->
              <div class="grid grid-cols-1 sm:grid-cols-12 gap-3 items-center">
                <div class="sm:col-span-3 h-20 rounded-xl overflow-hidden bg-black/80 flex items-center justify-center border border-slate-800">
                  <video v-if="sc.type === 'video' && sc.path" :src="'/api/documentary_media?path=' + sc.path" class="w-full h-full object-cover" muted loop autoplay></video>
                  <img v-else-if="sc.type === 'image' && sc.path" :src="'/api/documentary_media?path=' + sc.path" class="w-full h-full object-cover">
                  <div v-else class="text-[10px] text-slate-400 text-center px-2">
                    <i class="fa-solid fa-clapperboard text-amber-400 text-sm block mb-1"></i>
                    <span>Slide điện ảnh chữ nghệ thuật</span>
                  </div>
                </div>
                <div class="sm:col-span-9 space-y-1">
                  <div class="flex items-center justify-between">
                    <label class="text-[11px] text-slate-400 font-mono">Đường dẫn file video/ảnh thật:</label>
                    <label class="text-[11px] text-amber-300 hover:text-amber-200 cursor-pointer flex items-center gap-1 font-semibold">
                      <i class="fa-solid fa-upload"></i> Tải file từ máy
                      <input type="file" accept="image/*,video/*" class="hidden" @change="uploadSceneMedia($event, sIdx)">
                    </label>
                  </div>
                  <input v-model="sc.path" type="text" placeholder="Để trống để dùng slide điện ảnh chữ nghệ thuật, hoặc nhập đường dẫn file thật..."
                    class="w-full glass-input rounded-xl px-3 py-1.5 text-xs font-mono text-slate-300">
                </div>
              </div>

              <!-- Row 3: Narration script -->
              <div class="space-y-1">
                <label class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
                  <i class="fa-solid fa-microphone-lines text-pink-400"></i> Lời đọc phân cảnh (Giọng dẫn chuyện Nam Minh/Hoài My đọc đoạn này):
                </label>
                <textarea v-model="sc.text" rows="2"
                  class="w-full glass-input rounded-xl p-3 text-xs leading-relaxed text-slate-100"
                  placeholder="Lời dẫn chuyện cho phân cảnh này..."></textarea>
              </div>
            </div>
          </div>

          <!-- Nút Render Nhanh cho Phim Tư Liệu -->
          <div class="pt-2">
            <button @click="startRender" :disabled="pipelineState.is_running"
              class="w-full py-4 px-6 rounded-2xl bg-gradient-to-r from-red-600 via-amber-600 to-indigo-600 hover:opacity-95 text-white font-extrabold text-sm shadow-xl shadow-red-600/30 transition flex items-center justify-center gap-3 cursor-pointer disabled:opacity-50">
              <i v-if="pipelineState.is_running" class="fa-solid fa-spinner fa-spin text-lg"></i>
              <i v-else class="fa-solid fa-clapperboard text-lg"></i>
              <span>🎬 RENDER VIDEO PHIM TƯ LIỆU 60 FPS (HÌNH + VIDEO)</span>
            </button>
          </div>
        </template>
      </div>

        <!-- Section 3: Phong Cách Dựng Video & Bố Cục Đa Dạng (Không Bị Bó Lại) -->
        <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-5">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3.5">
            <h2 class="text-sm font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-2">
              <i class="fa-solid fa-sliders text-cyan-400"></i> 3. Phong Cách Dựng Video & Bố Cục (Tùy Biến Rộng Rãi)
            </h2>
            <span class="text-xs text-slate-400">Tùy biến chuẩn TikTok, Reels & Shorts 60 FPS</span>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            <!-- Nhóm A: Nền Tảng & Giao Diện Thẻ -->
            <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-3.5">
              <h3 class="text-xs font-bold uppercase tracking-wide text-indigo-400 flex items-center gap-1.5">
                <i class="fa-solid fa-layer-group"></i> Nền Tảng & Thẻ Card
              </h3>

              <!-- Nền tảng nội dung (Platform) -->
              <div class="space-y-1">
                <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                  <i class="fa-solid fa-shapes text-sky-400"></i> Nền tảng hiển thị:
                </label>
                <select v-model="settings.platform" @change="onPlatformChange" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                  <option value="auto">🤖 Tự Động (AI Phân Tích Chọn Nền Tảng)</option>
                  <option value="threads">🧵 Threads (Đối đáp nhanh, tinh gọn)</option>
                  <option value="facebook">📘 Facebook Post (Kèm ảnh & Like/Share)</option>
                  <option value="instagram">📸 Instagram Feed (Ảnh lớn & Story Ring)</option>
                </select>
                <span class="text-xs text-slate-500 block">Kiểu dáng thẻ, reaction & avatar</span>
              </div>

              <!-- Giao diện thẻ -->
              <div class="space-y-1">
                <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                  <i class="fa-solid fa-palette text-amber-400"></i> Giao diện thẻ bài viết:
                </label>
                <select v-model="settings.card_theme" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                  <option value="auto">🤖 Tự Động Theo Kịch Bản (AI Khuyên Dùng Sáng/Tối)</option>
                  <option value="light">☀️ Sáng (Chuẩn Threads thật 100%)</option>
                  <option value="dark">🌙 Tối (Dark Mode OLED)</option>
                  <option value="glass">🔮 Glassmorphism (Kính mờ viền sáng)</option>
                  <option value="cyber_neon">⚡ Cyber Neon (Viền hồng dạ quang)</option>
                </select>
                <span class="text-xs text-slate-500 block">Màu nền và độ tương phản của thẻ chữ</span>
              </div>
            </div>

            <!-- Nhóm B: Bố Cục Khung Hình & Nền Video -->
            <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-3.5">
              <h3 class="text-xs font-bold uppercase tracking-wide text-pink-400 flex items-center gap-1.5">
                <i class="fa-solid fa-film"></i> Bố Cục Khung Hình & Nền Video
              </h3>

              <!-- Bố cục video (Layout Presets) -->
              <div class="space-y-1">
                <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                  <i class="fa-solid fa-table-columns text-indigo-400"></i> Bố cục khung hình:
                </label>
                <select v-model="settings.layout_preset" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                  <option value="sandwich">🥪 Sandwich Viral: Thẻ Trên - Video Ở Giữa - Thẻ Dưới (Hot TikTok)</option>
                  <option value="center">🎯 Căn Giữa Toàn Màn Hình (Focus Chuẩn TikTok/Reels)</option>
                  <option value="split_screen">📱 Nửa Trên Gameplay - Nửa Dưới Thẻ (Split-Screen)</option>
                  <option value="bottom_story">📖 Storyteller (Hơi thấp, tối giản)</option>
                </select>
                <span class="text-xs text-slate-500 block">Vị trí đặt thẻ chữ trên tỷ lệ dọc 9:16</span>
              </div>

              <!-- Tùy chọn Video ở giữa (Sandwich Center Video) -->
              <div class="space-y-1.5 p-3 rounded-xl bg-slate-950/50 border border-slate-800/80">
                <div class="flex items-center justify-between">
                  <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-layer-group text-teal-400"></i> Video Kẹp Ở Giữa:
                  </label>
                  <label class="flex items-center gap-1.5 text-[11px] text-teal-300 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_center_satisfying_video" class="rounded bg-slate-800 border-slate-700 text-teal-500">
                    <span>Bật video giữa</span>
                  </label>
                </div>
                <select v-if="directorConfig.visual_layout.enable_center_satisfying_video || settings.layout_preset === 'sandwich'" 
                  v-model="directorConfig.visual_layout.center_satisfying_category" 
                  class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200 font-medium">
                  <option value="random">🤖 Tự Động Theo Chủ Đề (Viral Sandwich)</option>
                  <option value="gameplay">🎮 Minecraft Parkour & Gameplay 60FPS</option>
                  <option value="puzzle">🧩 3D Puzzle & Ghép Hình Thỏa Mãn</option>
                  <option value="food">🍰 Mukbang / Đồ Ngọt / Nấu Ăn</option>
                  <option value="asmr">🧼 Cắt Xà Phòng ASMR & Thư Giãn</option>
                  <option value="kinetic_sand">⏳ Cát Động Lực (Kinetic Sand)</option>
                  <option value="hypnotic">🌀 Hypnotic Neon Vòng Xoáy Thư Giãn</option>
                  <option value="custom">📁 File Video Tư Liệu Riêng (Custom MP4)</option>
                </select>
                <div v-if="(directorConfig.visual_layout.enable_center_satisfying_video || settings.layout_preset === 'sandwich') && directorConfig.visual_layout.center_satisfying_category === 'custom'" class="pt-1">
                  <input v-model="post.center_video_custom_path" type="text" placeholder="Nhập đường dẫn file MP4 trên máy (VD: assets/my_video.mp4)..." class="w-full glass-input rounded-xl px-3 py-1.5 text-xs text-slate-200 font-mono">
                </div>
                <span class="text-[10px] text-slate-500 block">Kẹp video cuốn hút ở giữa màn hình bo góc 26px mềm mại</span>
              </div>

              <!-- Chế độ nền video -->
              <div class="space-y-1">
                <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                  <i class="fa-solid fa-photo-film text-pink-400"></i> Chế độ nền video:
                </label>
                <select v-model="settings.bg_mode" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                  <option value="auto">🤖 Tự Động Khớp Chủ Đề (AI Smart Satisfying Matching)</option>
                  <option value="sandwich_puzzle">🧩 Sandwich: Ghép Hình Puzzle Thỏa Mãn Ở Giữa (Khuyên Dùng)</option>
                  <option value="sandwich_food">🍰 Sandwich: Ăn Uống Mukbang / Bánh Ngọt Ở Giữa</option>
                  <option value="sandwich_asmr">🧼 Sandwich: Cắt Xà Phòng ASMR Ở Giữa</option>
                  <option value="minecraft">🎮 Minecraft Parkour Full 9:16</option>
                  <option value="gta5">🚗 GTA 5 Mega Ramp Stunt 9:16</option>
                  <option value="subway">🏃 Subway Surfers High Speed 9:16</option>
                  <option value="asmr">🧼 Satisfying ASMR Cắt Xà Phòng Full 9:16</option>
                  <option value="split_gameplay">🔥 Split (Gameplay 60FPS + Thẻ dưới)</option>
                  <option value="random_gameplay">🎲 Ngẫu Nhiên Kho Gameplay (GTA / Minecraft / Subway / ASMR)</option>
                  <option value="infinity_neon">🌀 3D Infinity Cyberpunk (Neon)</option>
                  <option value="infinity_dark">🌑 3D Infinity Dark Luxury (Kim loại)</option>
                  <option value="infinity_gold">✨ 3D Infinity Golden Matrix (Vàng)</option>
                  <option value="kinetic_sand">⏳ Cát Động Học Thư Giãn (Kinetic Sand 9:16)</option>
                  <option value="hypnotic">🌀 Vòng Xoáy Huyền Ảo Thư Giãn (Sacred Spiral 9:16)</option>
                  <option value="puzzle">🔮 Kính Vạn Hoa Thư Giãn (Kaleidoscope 9:16)</option>
                  <option value="gradient_cyber">🌌 Gradient Dark Cyberpunk (Tĩnh)</option>
                  <option value="gradient_lofi">☕ Gradient Lofi Cozy (Ấm cúng)</option>
                </select>
                <span class="text-xs text-slate-500 block">Video động chạy phía sau thẻ chữ</span>
              </div>
            </div>

            <!-- Nhóm C: Giọng Đọc, Tốc Độ & BGM -->
            <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-3.5">
              <h3 class="text-xs font-bold uppercase tracking-wide text-emerald-400 flex items-center gap-1.5">
                <i class="fa-solid fa-headphones"></i> Giọng Đọc, Tốc Độ & Nhạc Nền
              </h3>

              <div class="grid grid-cols-2 gap-2.5">
                <!-- Giọng đọc tác giả -->
                <div class="space-y-1">
                  <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-microphone text-emerald-400"></i> Giọng đọc chính:
                  </label>
                  <select v-model="settings.voice" class="w-full glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
                    <option value="nam_hai">🔥 Nam Hài Hước (Dí dỏm, tưng tửng, bắt trend - Khuyên dùng)</option>
                    <option value="nu_cakhia">🌸 Nữ Cà Khịa (Đanh đá, hài hước, duyên dáng)</option>
                    <option value="nam_tram_hai">🎙️ Nam Trầm Hài (Bựa, cạn lời, kiểu Độ Mixi)</option>
                    <option value="nu_nhinhanh">✨ Nữ Nhí Nhảnh (Dễ thương, vui tươi)</option>
                    <option value="chi_google">🤖 Chị Google (Meme robot)</option>
                    <option value="vi-VN-NamMinhNeural">🇻🇳 Nam Minh Chuẩn (Ấm áp)</option>
                    <option value="vi-VN-HoaiMyNeural">🌸 Hoài My Chuẩn (Truyền cảm)</option>
                    <option value="adam">🎙️ Adam (Podcast)</option>
                    <option value="brian">🎙️ Brian (Điện ảnh)</option>
                  </select>
                </div>

                <!-- Tốc độ đọc -->
                <div class="space-y-1">
                  <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-gauge-high text-amber-400"></i> Tốc độ đọc:
                  </label>
                  <select v-model="settings.rate" class="w-full glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
                    <option value="+25%">⚡ Rất nhanh (+25%)</option>
                    <option value="+20%">🔥 Nhanh (+20%)</option>
                    <option value="+18%">🚀 Nhanh mượt (+18%)</option>
                    <option value="+15%">✨ Nhanh vừa (+15%)</option>
                    <option value="+10%">🌟 Tự nhiên (+10%)</option>
                    <option value="+0%">🐢 Bình thường (+0%)</option>
                  </select>
                </div>
              </div>

              <!-- Nhạc nền TikTok & Âm lượng -->
              <div class="space-y-1.5 pt-0.5">
                <div class="flex items-center justify-between">
                  <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                    <i class="fa-solid fa-music text-rose-400"></i> Nhạc nền (BGM):
                  </label>
                  <span class="text-xs font-mono text-rose-300 font-bold" v-if="settings.bgm_choice !== 'none'">
                    {{ settings.bgm_volume }}%
                  </span>
                  <span class="text-xs font-mono text-slate-500 font-bold" v-else>
                    Tắt
                  </span>
                </div>
                <select v-model="settings.bgm_choice" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                  <option v-for="b in bgmList" :key="b.id" :value="b.id">{{ b.name }}</option>
                </select>
                <div class="flex items-center gap-2 pt-1" v-if="settings.bgm_choice !== 'none'">
                  <i class="fa-solid fa-volume-low text-xs text-slate-400"></i>
                  <input type="range" min="5" max="80" step="5" v-model.number="settings.bgm_volume"
                    class="w-full accent-rose-500 h-1.5 bg-slate-700 rounded-lg cursor-pointer">
                  <i class="fa-solid fa-volume-high text-xs text-slate-400"></i>
                </div>
                <span class="text-[10px] text-slate-500 block pt-0.5" v-if="settings.bgm_choice !== 'none'">
                  Khuyên dùng 20% - 30% để nhạc êm ái, rõ tiếng làm nền tôn giọng đọc
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- Big Render Action Button: Duyệt Kịch Bản & Render Ngay -->
        <div>
          <button @click="startRender" :disabled="isRenderingVideo"
            class="w-full py-4 rounded-2xl bg-gradient-to-r from-brand-600 via-indigo-500 to-pink-500 hover:opacity-95 transition font-extrabold text-base shadow-xl shadow-indigo-600/30 flex items-center justify-center gap-3 disabled:opacity-50 text-white cursor-pointer">
            <i v-if="isRenderingVideo" class="fa-solid fa-spinner fa-spin text-xl"></i>
            <i v-else class="fa-solid fa-circle-check text-xl text-emerald-300"></i>
            <span>{{ isRenderingVideo ? ('⏳ ' + pipelineState.message) : '🚀 DUYỆT KỊCH BẢN & BẮT ĐẦU DỰNG VIDEO (RENDER NGAY)' }}</span>
          </button>
          <p class="text-center text-xs text-slate-400 mt-2">
            Hệ thống sẽ lấy chính xác kịch bản và phong cách dựng video bạn vừa duyệt để xuất file 60 FPS chuẩn Studio.
          </p>
        </div>

      </div>

      <!-- Right Panel: Live Preview & Player (5 cols on XL, 4 cols on 2XL, Sticky) -->
      <div class="xl:col-span-5 2xl:col-span-4 flex flex-col space-y-6 xl:sticky xl:top-20 self-start">
        
        <!-- Video Player / Card Preview Container -->
        <div class="glass-panel rounded-2xl p-6 shadow-xl flex flex-col items-center">
          <div class="w-full flex items-center justify-between mb-4 border-b border-slate-800 pb-3">
            <div class="flex items-center gap-2 bg-slate-900/80 p-1 rounded-xl border border-slate-800">
              <button @click="previewMode = 'video'" :class="previewMode === 'video' ? 'bg-indigo-600 text-white font-bold shadow-sm' : 'text-slate-400 hover:text-slate-200'" class="text-xs px-3 py-1.5 rounded-lg transition flex items-center gap-1.5 cursor-pointer">
                <i class="fa-solid fa-play"></i> Xem Video
              </button>
              <button @click="previewMode = 'cards'" :class="previewMode === 'cards' ? 'bg-indigo-600 text-white font-bold shadow-sm' : 'text-slate-400 hover:text-slate-200'" class="text-xs px-3 py-1.5 rounded-lg transition flex items-center gap-1.5 cursor-pointer">
                <i class="fa-solid fa-images"></i> Xem Thẻ Card
              </button>
            </div>
            <span class="text-xs text-slate-400 font-mono px-2 py-1 rounded-lg bg-slate-800/80 border border-slate-700/60">9:16 Vertical HD</span>
          </div>

          <!-- Video Player Display -->
          <div v-show="previewMode === 'video'" class="w-full flex flex-col items-center">
            <div class="w-[300px] h-[533px] bg-black rounded-3xl overflow-hidden shadow-2xl border-4 border-slate-800 relative flex items-center justify-center group">
              <video v-if="latestVideoUrl" :src="latestVideoUrl" controls autoplay loop
                class="w-full h-full object-cover"></video>
              <div v-else class="text-center p-6 text-slate-400 text-xs space-y-2">
                <i class="fa-solid fa-video-slash text-4xl text-slate-600"></i>
                <div class="font-medium">Chưa có video nào. Nhấn Render để tạo ngay!</div>
              </div>
            </div>

            <!-- Video Actions & Caption -->
            <div v-if="latestVideoUrl" class="w-full mt-5 space-y-3">
              <div class="flex items-center gap-2.5">
                <a :href="latestVideoUrl" download class="flex-1 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-bold text-center text-white transition shadow-md shadow-indigo-600/30">
                  <i class="fa-solid fa-download mr-1.5"></i> Tải Video Về Máy
                </a>
                <button @click="openFinalFolder" class="px-3.5 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-slate-200 border border-slate-700 transition" title="Mở thư mục video">
                  <i class="fa-solid fa-folder-open text-indigo-400"></i>
                </button>
              </div>

              <!-- Caption Box -->
              <div class="p-3.5 rounded-xl bg-slate-900/80 border border-slate-800 text-xs space-y-2">
                <div class="flex items-center justify-between text-slate-400">
                  <span class="font-bold text-slate-300">Caption & Hashtag:</span>
                  <button @click="copyCaption" class="text-indigo-400 hover:text-indigo-300 font-semibold flex items-center gap-1 cursor-pointer">
                    <i class="fa-regular fa-copy"></i> Sao chép
                  </button>
                </div>
                <div class="text-slate-300 whitespace-pre-line max-h-28 overflow-y-auto font-mono text-xs leading-relaxed">
                  {{ latestCaption || 'Chưa có caption' }}
                </div>
              </div>
            </div>
          </div>

          <!-- Cards Preview Display -->
          <div v-show="previewMode === 'cards'" class="w-full space-y-4 max-h-[820px] overflow-y-auto pr-2">
            <div v-if="!post || !post.username || !post.content" class="text-center p-8 text-slate-500 text-xs space-y-2.5">
              <i class="fa-solid fa-wand-magic-sparkles text-3xl text-indigo-400/60 mb-2"></i>
              <div class="font-bold text-slate-200 text-sm">Chưa Có Kịch Bản</div>
              <div class="text-slate-400 max-w-[280px] mx-auto leading-relaxed">
                Nhập chủ đề ở khung bên trái rồi bấm <strong class="text-indigo-400">"Tạo Kịch Bản Để Duyệt"</strong> hoặc <strong class="text-pink-400">"Tự Động 1-Click"</strong> để tạo mới!
              </div>
            </div>

            <!-- Xem trước khi là Phim Tư Liệu (Hình + Video) -->
            <template v-else-if="settings.platform === 'documentary'">
              <div class="space-y-4">
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-2">
                    <span class="text-xs text-red-400 font-bold uppercase tracking-wider flex items-center gap-1.5">
                      <i class="fa-solid fa-clapperboard"></i> Phim Tư Liệu
                    </span>
                    <span class="px-2.5 py-0.5 rounded-full text-xs font-bold border bg-red-600/20 text-red-300 border-red-500/40">
                      🎬 Hình + Video
                    </span>
                  </div>
                  <button @click="refreshCardPreview" class="text-xs text-red-400 hover:text-red-300 font-semibold flex items-center gap-1 cursor-pointer">
                    <i class="fa-solid fa-arrows-rotate"></i> Cập nhật
                  </button>
                </div>

                <!-- Thẻ Tiêu Đề Header Badge -->
                <div class="space-y-1.5">
                  <div class="text-xs text-amber-300 font-semibold flex items-center justify-between">
                    <span>Thẻ Tiêu Đề Phim Tư Liệu:</span>
                    <span class="text-xs text-slate-400 font-mono">{{ post.story_badge || 'HỒ SƠ KỲ THÚ' }}</span>
                  </div>
                  <img :src="'/api/card/header_badge.png?t=' + cardTimestamp" alt="Header Badge" class="w-full rounded-2xl shadow-lg border border-red-500/40">
                </div>

                <!-- Timeline Phân Cảnh Video & Hình Ảnh -->
                <div class="text-xs text-indigo-300 font-bold uppercase tracking-wider flex items-center justify-between pt-1">
                  <span>Timeline Phân Cảnh ({{ (post.documentary_scenes || []).length }} Cảnh):</span>
                </div>

                <div v-for="(sc, sIdx) in (post.documentary_scenes || [])" :key="sIdx"
                  class="p-3.5 rounded-2xl bg-slate-900/80 border border-slate-700/80 space-y-2 shadow-md hover:border-amber-500/50 transition">
                  <div class="flex items-center justify-between text-xs">
                    <span class="px-2 py-0.5 rounded-full font-bold text-[11px]"
                      :class="sc.type === 'video' ? 'bg-red-500/20 text-red-300 border border-red-500/40' : 'bg-amber-500/20 text-amber-300 border border-amber-500/40'">
                      {{ sc.type === 'video' ? '🎬 Video Clip #' + (sIdx+1) : '🖼️ Hình Ảnh #' + (sIdx+1) }}
                    </span>
                    <span class="text-amber-300 font-bold text-[11px]">{{ sc.badge }}</span>
                  </div>

                  <!-- Media Preview thumbnail -->
                  <div class="h-28 w-full rounded-xl overflow-hidden bg-black/80 flex items-center justify-center relative border border-slate-800">
                    <video v-if="sc.type === 'video'" :src="'/api/documentary_media?path=' + sc.path" class="w-full h-full object-cover" muted loop autoplay></video>
                    <img v-else :src="'/api/documentary_media?path=' + sc.path" class="w-full h-full object-cover">
                    <span class="absolute bottom-1.5 right-2 px-1.5 py-0.5 rounded text-[10px] bg-black/70 text-white font-mono">
                      SFX: {{ sc.sfx || 'none' }}
                    </span>
                  </div>

                  <p class="text-xs text-slate-300 line-clamp-2 italic leading-relaxed">"{{ sc.text }}"</p>
                </div>
              </div>
            </template>

            <!-- Xem trước thẻ Threads / Facebook / Instagram -->
            <template v-else>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="text-xs text-indigo-300 font-bold uppercase tracking-wider flex items-center gap-1.5">
                    <i class="fa-solid fa-layer-group"></i> Thẻ xem trước
                  </span>
                  <span class="px-2.5 py-0.5 rounded-full text-xs font-bold border flex items-center gap-1"
                    :class="{
                      'bg-indigo-600/20 text-indigo-300 border-indigo-500/40': (post.platform || 'threads') === 'threads',
                      'bg-blue-600/20 text-blue-300 border-blue-500/40': (post.platform || 'threads') === 'facebook',
                      'bg-pink-600/20 text-pink-300 border-pink-500/40': (post.platform || 'threads') === 'instagram'
                    }">
                    <span>{{ post.platform === 'facebook' ? '📘 Facebook' : (post.platform === 'instagram' ? '📸 Instagram' : '🧵 Threads') }}</span>
                    <span v-if="settings.platform === 'auto'" class="text-[10px] text-amber-300 bg-amber-500/20 px-1.5 rounded font-normal">AI chọn</span>
                  </span>
                </div>
                <button @click="refreshCardPreview" class="text-xs text-indigo-400 hover:text-indigo-300 font-semibold flex items-center gap-1 cursor-pointer">
                  <i class="fa-solid fa-arrows-rotate"></i> Cập nhật thẻ
                </button>
              </div>

              <!-- Thẻ Post chính -->
              <div class="space-y-1.5">
                <div class="text-xs text-slate-300 font-semibold flex items-center justify-between">
                  <span>Thẻ bài viết của tác giả:</span>
                  <span class="text-xs text-slate-500 font-mono">@{{ post.username }}</span>
                </div>
                <img :src="'/api/card/post.png?t=' + cardTimestamp" alt="Post Card" class="w-full rounded-2xl shadow-lg border border-slate-700/60">
              </div>
              
              <!-- Danh sách thẻ các bình luận -->
              <div v-for="(cmt, idx) in post.comments" :key="idx" class="space-y-1.5 pt-1">
                <div class="text-xs text-slate-300 font-semibold flex items-center justify-between">
                  <span>Thẻ bình luận #{{ idx + 1 }} (@{{ cmt.username }}):</span>
                  <span class="text-xs text-slate-400 font-mono">{{ cmt.emotion }} • {{ cmt.sfx }}</span>
                </div>
                <img :src="'/api/card/comment_' + (idx + 1) + '.png?t=' + cardTimestamp" :alt="'Comment ' + (idx + 1)" class="w-full rounded-2xl shadow-lg border border-slate-700/60">
              </div>

              <!-- Thẻ Outro Kêu Gọi Tương Tác 2.4s -->
              <div v-if="post.cta_question" class="space-y-1.5 pt-2">
                <div class="text-xs text-purple-300 font-semibold flex items-center justify-between">
                  <span>📢 Thẻ Outro kêu gọi tương tác (2.4s cuối):</span>
                  <span class="text-xs text-purple-400 font-mono">viral outro</span>
                </div>
                <img :src="'/api/card/outro_cta.png?t=' + cardTimestamp" alt="Outro CTA Card" class="w-full rounded-2xl shadow-lg border border-purple-500/40">
              </div>
            </template>
          </div>
        </div>

        <!-- Section 4: Batch Factory (Sản Xuất Hàng Loạt) -->
        <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-3.5">
          <div class="flex items-center justify-between">
            <h3 class="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center gap-2">
              <i class="fa-solid fa-industry"></i> Sản Xuất Hàng Loạt (Batch Mode)
            </h3>
            <span class="text-xs text-slate-400 font-medium">Tự động 100%</span>
          </div>

          <p class="text-xs text-slate-300 leading-relaxed">
            Hệ thống tự động dùng Gemini AI nghĩ kịch bản khác nhau, cắt nền và render liên tục theo số lượng bạn chọn.
          </p>

          <div class="flex items-center gap-3">
            <button v-for="cnt in [3, 5, 10]" :key="cnt" @click="startBatch(cnt)" :disabled="pipelineState.is_running"
              class="flex-1 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700/60 text-xs font-bold text-slate-200 transition disabled:opacity-40 cursor-pointer shadow-sm">
              Tạo {{ cnt }} Video
            </button>
          </div>
        </div>

      </div>

    </main>

    <!-- TAB 2: GIÁM SÁT QUY TRÌNH (LIVE PIPELINE & EMOTION INSPECTOR) -->
    <main v-show="currentTab === 'pipeline'" class="flex-1 max-w-[1760px] w-full mx-auto px-4 sm:px-8 py-6 space-y-6">
      
      <!-- Top Hero Progress Bar -->
      <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-4">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-base font-bold text-white flex items-center gap-2.5">
              <span class="w-3 h-3 rounded-full" :class="pipelineState.is_running ? 'bg-pink-500 animate-ping' : (pipelineState.current_step_id === 'done' ? 'bg-emerald-400' : 'bg-slate-500')"></span>
              {{ pipelineState.current_step_title || 'Sẵn sàng' }}
            </h2>
            <p class="text-xs text-slate-400 mt-1">{{ pipelineState.message }}</p>
          </div>

          <div class="text-right">
            <span class="text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-brand-500 via-indigo-300 to-pink-400 font-mono">
              {{ pipelineState.progress_percent }}%
            </span>
          </div>
        </div>

        <!-- Progress bar line -->
        <div class="w-full h-3 bg-slate-800 rounded-full overflow-hidden p-0.5 border border-slate-700/60">
          <div class="h-full bg-gradient-to-r from-brand-600 via-indigo-500 to-pink-500 rounded-full transition-all duration-300"
            :style="{ width: pipelineState.progress_percent + '%' }"></div>
        </div>

        <!-- Khi hệ thống ĐANG CHẠY (1-Click hoặc Render): Hiển thị trạng thái tự động A-Z, không giục bấm nút -->
        <div v-if="pipelineState.is_running" class="p-4 rounded-2xl bg-indigo-950/60 border border-indigo-500/40 flex items-center justify-between gap-4 flex-wrap shadow-lg">
          <div class="flex items-center gap-3">
            <span class="w-8 h-8 rounded-xl bg-pink-500/20 text-pink-400 flex items-center justify-center text-sm">
              <i class="fa-solid fa-bolt fa-spin"></i>
            </span>
            <div>
              <h4 class="text-xs font-bold text-white flex items-center gap-2">
                <span>⚡ ĐANG TỰ ĐỘNG SẢN XUẤT TRỌN GÓI 1-CLICK TỪ A ĐẾN Z...</span>
                <span class="px-2 py-0.5 rounded-full bg-pink-500/20 text-pink-300 text-[10px] font-mono">1-CLICK PIPELINE</span>
              </h4>
              <p class="text-[11px] text-indigo-200/70 mt-0.5">
                Hệ thống đang tự động thu âm giọng đọc, mix nhạc nền và dựng video 60 FPS. Bạn KHÔNG cần bấm thêm bất cứ nút nào — video sẽ tự động phát khi xong!
              </p>
            </div>
          </div>
          <button @click="currentTab = 'studio'" class="px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-medium cursor-pointer">
            <i class="fa-solid fa-eye text-indigo-400 mr-1"></i> Xem kịch bản ở Tab 1
          </button>
        </div>

        <!-- Action Callout CHỈ HIỆN khi người dùng bấm "TẠO KỊCH BẢN ĐỂ DUYỆT" (đã xong bước 1 & 2 và đang tạm dừng chờ duyệt) -->
        <div v-else-if="!pipelineState.is_running && pipelineState.steps[1] && pipelineState.steps[1].status === 'done' && pipelineState.steps[2] && pipelineState.steps[2].status === 'idle'"
          class="p-5 rounded-2xl bg-gradient-to-r from-indigo-950 via-purple-950 to-slate-900 border-2 border-indigo-500/60 flex items-center justify-between gap-4 flex-wrap shadow-xl">
          <div class="space-y-1">
            <h3 class="text-sm font-extrabold text-white flex items-center gap-2">
              <span class="px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 text-xs font-bold border border-emerald-500/30">
                ✅ ĐÃ TẠO XONG KỊCH BẢN & THẺ
              </span>
              <span>Bạn đang ở chế độ Duyệt Kịch Bản!</span>
            </h3>
            <p class="text-xs text-indigo-200/80">
              Bạn có thể quay lại Tab 1 để kiểm tra/sửa câu chữ, hoặc bấm nút dưới đây để bắt đầu thu âm và dựng video:
            </p>
          </div>

          <div class="flex items-center gap-3">
            <button @click="currentTab = 'studio'" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1.5 text-xs font-semibold cursor-pointer">
              <i class="fa-solid fa-pen-to-square text-indigo-400"></i>
              <span>Xem/Sửa Kịch Bản (Tab 1)</span>
            </button>
            <button @click="startRender" :disabled="pipelineState.is_running"
              class="px-5 py-2.5 rounded-xl bg-gradient-to-r from-pink-500 via-rose-500 to-indigo-600 hover:opacity-95 text-white font-extrabold text-xs shadow-lg shadow-pink-500/30 transition flex items-center gap-2 animate-pulse cursor-pointer">
              <i class="fa-solid fa-play text-sm"></i>
              <span>BẤM RENDER VIDEO NGAY (BƯỚC 3 -> 6)</span>
            </button>
          </div>
        </div>

        <!-- Khi ĐÃ HOÀN THÀNH XUẤT XƯỞNG VIDEO: -->
        <div v-else-if="pipelineState.current_step_id === 'done'" class="p-4 rounded-2xl bg-emerald-950/60 border border-emerald-500/40 flex items-center justify-between gap-4 flex-wrap shadow-lg">
          <div class="flex items-center gap-3">
            <span class="w-8 h-8 rounded-xl bg-emerald-500/20 text-emerald-300 flex items-center justify-center text-sm">
              <i class="fa-solid fa-circle-check"></i>
            </span>
            <div>
              <h4 class="text-xs font-bold text-emerald-200">🎉 VIDEO HOÀN CHỈNH ĐÃ XUẤT XƯỞNG THÀNH CÔNG (60 FPS)!</h4>
              <p class="text-[11px] text-emerald-300/70 mt-0.5">Video đã được tối ưu hóa âm thanh, đồ họa và lưu vào thư mục output.</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button @click="openFinalFolder" class="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs transition flex items-center gap-2 shadow-md shadow-emerald-600/30 cursor-pointer">
              <i class="fa-regular fa-folder-open"></i> Mở Thư Mục Video
            </button>
            <button @click="currentTab = 'studio'" class="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-semibold cursor-pointer">
              <i class="fa-solid fa-rotate-left mr-1"></i> Tạo Video Khác
            </button>
          </div>
        </div>

        <!-- Quick Action Bar khi ở trạng thái nghỉ: -->
        <div v-else class="flex items-center justify-between pt-1 border-t border-slate-800/60 flex-wrap gap-2 text-xs">
          <div class="flex items-center gap-2">
            <button @click="currentTab = 'studio'" class="px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1.5 font-medium cursor-pointer">
              <i class="fa-solid fa-arrow-left text-slate-400"></i>
              <span>Về Tab 1: Studio Sáng Tạo (Xem Kịch Bản & Thẻ)</span>
            </button>
          </div>
          <button @click="startRender" :disabled="pipelineState.is_running"
            class="px-5 py-2 rounded-xl bg-gradient-to-r from-pink-600 to-indigo-600 hover:opacity-90 text-white font-bold transition flex items-center gap-2 shadow-md shadow-pink-500/20 disabled:opacity-50 cursor-pointer">
            <i class="fa-solid fa-play"></i>
            <span>Bắt Đầu Render Video</span>
          </button>
        </div>
      </div>

      <!-- 6-Stage Stepper Grid (Spacious 6 columns on XL) -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
        <div v-for="(step, idx) in pipelineState.steps" :key="step.id"
          class="p-4 rounded-xl border transition-all duration-200"
          :class="{
            'bg-slate-900/40 border-slate-800 text-slate-400': step.status === 'idle',
            'bg-indigo-950/40 border-indigo-500/60 text-white shadow-lg shadow-indigo-500/10 ring-1 ring-indigo-500/40': step.status === 'running',
            'bg-emerald-950/30 border-emerald-600/40 text-emerald-200': step.status === 'done',
            'bg-red-950/40 border-red-500/50 text-red-200': step.status === 'error'
          }">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-bold uppercase tracking-wider flex items-center gap-2">
              <i v-if="step.status === 'running'" class="fa-solid fa-spinner fa-spin text-indigo-400"></i>
              <i v-else-if="step.status === 'done'" class="fa-solid fa-circle-check text-emerald-400"></i>
              <i v-else-if="step.status === 'error'" class="fa-solid fa-circle-exclamation text-red-400"></i>
              <i v-else class="fa-regular fa-circle text-slate-600"></i>
              {{ step.title }}
            </span>
            <span class="text-[10px] font-mono opacity-60">{{ step.time_str || '--:--' }}</span>
          </div>
          <p class="text-xs text-slate-400 line-clamp-2">{{ step.desc }}</p>
        </div>
      </div>

      <!-- Emotional Arc & Dopamine Timeline -->
      <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-4">
        <div class="flex items-center justify-between border-b border-slate-800 pb-3">
          <h3 class="text-sm font-bold uppercase tracking-wider text-pink-400 flex items-center gap-2">
            <i class="fa-solid fa-wave-square"></i> Đồ Thị Cảm Xúc & Nhịp Thở Kịch Tính (Emotional Pacing)
          </h3>
          <span class="text-xs text-slate-500 font-mono">Dopamine Retention Loop</span>
        </div>

        <div v-if="pipelineState.emotional_arc_data && pipelineState.emotional_arc_data.length > 0" class="space-y-3">
          <div v-for="(beat, idx) in pipelineState.emotional_arc_data" :key="idx"
            class="p-4 rounded-xl bg-slate-900/60 border border-slate-800/80 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
            
            <div class="flex-1 space-y-1">
              <div class="flex items-center gap-2">
                <span class="text-xs font-bold text-white">{{ beat.beat }}</span>
                <span class="text-[11px] px-2 py-0.5 rounded-md font-semibold"
                  :class="{
                    'bg-amber-500/20 text-amber-300 border border-amber-500/30': beat.emotion === 'high' || beat.emotion === 'funny',
                    'bg-red-500/20 text-red-300 border border-red-500/30': beat.emotion === 'shocked',
                    'bg-purple-500/20 text-purple-300 border border-purple-500/30': beat.emotion === 'awkward',
                    'bg-blue-500/20 text-blue-300 border border-blue-500/30': beat.emotion === 'sarcastic'
                  }">
                  {{ beat.emotion.toUpperCase() }}
                </span>
                <span class="text-xs text-slate-400">{{ beat.role }}</span>
              </div>
              <p class="text-xs text-slate-300 italic">"{{ beat.text }}"</p>
            </div>

            <!-- Metadata indicators -->
            <div class="flex items-center gap-3 text-xs font-mono text-slate-400 flex-wrap">
              <span class="flex items-center gap-1 bg-slate-800/80 px-2 py-1 rounded-md">
                <i class="fa-solid fa-microphone-lines text-indigo-400"></i> {{ beat.voice }}
              </span>
              <span class="flex items-center gap-1 bg-slate-800/80 px-2 py-1 rounded-md">
                <i class="fa-solid fa-stopwatch text-cyan-400"></i> {{ beat.duration_s }}s
              </span>
              <span v-if="beat.sfx !== 'none'" class="flex items-center gap-1 bg-pink-950/60 text-pink-300 px-2 py-1 rounded-md border border-pink-800/40">
                <i class="fa-solid fa-bolt text-pink-400"></i> SFX: {{ beat.sfx }}
              </span>
              <span class="flex items-center gap-1 bg-slate-800/80 px-2 py-1 rounded-md">
                <i class="fa-solid fa-pause text-amber-400"></i> Ngừng {{ beat.pause_ms }}ms
              </span>
            </div>

          </div>
        </div>

        <div v-else class="text-center py-8 text-slate-500 text-xs">
          <i class="fa-solid fa-chart-line text-2xl mb-2"></i>
          <div>Chưa có dữ liệu đồ thị cảm xúc. Khi bạn bấm Render hoặc AI Tạo Bài, đồ thị sẽ xuất hiện tại đây!</div>
        </div>
      </div>

      <!-- Live Terminal Console Log -->
      <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-3">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2.5">
          <div class="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-slate-300">
            <i class="fa-solid fa-terminal text-indigo-400"></i> Nhật Ký Thời Gian Thực (Live Terminal Logs)
          </div>
          <span class="text-[10px] text-slate-500 font-mono">{{ pipelineState.logs.length }} sự kiện</span>
        </div>

        <div class="bg-black/80 rounded-xl p-4 font-mono text-xs max-h-64 overflow-y-auto space-y-1.5 border border-slate-800">
          <div v-for="(log, idx) in pipelineState.logs" :key="idx" class="flex items-start gap-2">
            <span class="text-slate-500">[{{ log.time }}]</span>
            <span :class="log.level === 'ERROR' ? 'text-red-400 font-bold' : (log.text.startsWith('✅') ? 'text-emerald-400' : 'text-slate-300')">
              {{ log.text }}
            </span>
          </div>
          <div v-if="pipelineState.logs.length === 0" class="text-slate-600 italic">
            Chưa có log ghi nhận.
          </div>
        </div>
      </div>

    </main>

    <!-- TAB 3: CẤU HÌNH ĐẠO DIỄN (DIRECTOR CONFIG STUDIO) -->
    <main v-show="currentTab === 'config'" class="flex-1 max-w-[1760px] w-full mx-auto px-4 sm:px-8 py-6 space-y-6">
      <div class="glass-panel rounded-2xl p-6 shadow-xl space-y-6">
        
        <div class="flex items-center justify-between border-b border-slate-800 pb-4">
          <div>
            <h2 class="text-base font-bold text-white flex items-center gap-2">
              <i class="fa-solid fa-sliders text-indigo-400"></i> Cấu Hình Toàn Bộ Hệ Thống (Director Config)
            </h2>
            <p class="text-xs text-slate-400 mt-1">
              Tuyệt đối không hardcode. Mọi tham số nhịp thở, giọng đọc, prompt và âm lượng đều có thể chỉnh sửa tại đây.
            </p>
          </div>

          <div class="flex items-center gap-2.5">
            <button @click="resetDirectorConfig" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700">
              <i class="fa-solid fa-rotate-left mr-1"></i> Khôi Phục Mặc Định
            </button>
            <button @click="saveDirectorConfig" class="px-4 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white shadow-md shadow-indigo-600/30">
              <i class="fa-solid fa-floppy-disk mr-1"></i> Lưu Cấu Hình Đạo Diễn
            </button>
          </div>
        </div>

        <div v-if="directorConfig" class="space-y-6">
          
          <!-- Nhóm 1: Nhịp thở & Pacing cảm xúc -->
          <div class="space-y-3">
            <h3 class="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center gap-2">
              <i class="fa-solid fa-stopwatch"></i> 1. Cấu Hình Nhịp Thở & Khoảng Lặng Kịch Tính (Dramatic Pauses)
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Ngừng trước cú Twist (ms):</label>
                <input v-model.number="directorConfig.emotional_pacing.punchline_pre_pause_ms" type="number" class="w-full glass-input rounded-lg p-2 text-xs font-mono text-slate-200">
                <span class="text-[10px] text-slate-500">Khoảng lặng để người xem kịp tò mò</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Ngừng sau SFX Meme (ms):</label>
                <input v-model.number="directorConfig.emotional_pacing.punchline_post_pause_ms" type="number" class="w-full glass-input rounded-lg p-2 text-xs font-mono text-slate-200">
                <span class="text-[10px] text-slate-500">Thời gian để người xem "ngấm" tiếng cười</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Khoảng cách giữa các bình luận (ms):</label>
                <input v-model.number="directorConfig.emotional_pacing.comment_gap_ms" type="number" class="w-full glass-input rounded-lg p-2 text-xs font-mono text-slate-200">
                <span class="text-[10px] text-slate-500">Nhịp chuyển thẻ bình luận</span>
              </div>
            </div>
          </div>

          <!-- Nhóm 1.1: Nhạc Nền BGM & Dynamic Ducking -->
          <div class="space-y-3" v-if="directorConfig.emotional_pacing">
            <h3 class="text-xs font-bold uppercase tracking-wider text-rose-400 flex items-center gap-2">
              <i class="fa-solid fa-music"></i> 1.1. Cấu Hình Nhạc Nền BGM & Dynamic Ducking (Tự Động Nén Khi Nói)
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer font-semibold">
                  <input type="checkbox" v-model="directorConfig.emotional_pacing.enable_bgm" class="rounded bg-slate-800 border-slate-700 text-rose-500">
                  <span>Bật nhạc nền TikTok (BGM)</span>
                </label>
                <span class="text-[10px] text-slate-500 block">Trộn nhạc nền êm dịu làm tăng cảm xúc người xem</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <div class="flex items-center justify-between">
                  <label class="text-xs text-slate-400 font-semibold">Âm lượng nhạc nền:</label>
                  <span class="text-xs font-mono font-bold text-rose-400">{{ Math.round((directorConfig.emotional_pacing.bgm_volume_normal || 0.25) * 100) }}%</span>
                </div>
                <input type="range" min="0.05" max="0.80" step="0.05" v-model.number="directorConfig.emotional_pacing.bgm_volume_normal" class="w-full accent-rose-500 h-1.5 bg-slate-700 rounded-lg cursor-pointer">
                <span class="text-[10px] text-slate-500 block">Khuyên dùng 20% - 30% để làm nền êm ái, rõ tiếng</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer font-semibold">
                  <input type="checkbox" v-model="directorConfig.emotional_pacing.enable_bgm_ducking" class="rounded bg-slate-800 border-slate-700 text-rose-500">
                  <span>Dynamic Ducking (Tự hạ nhạc khi nói)</span>
                </label>
                <span class="text-[10px] text-slate-500 block">Tự động hạ nhỏ nhạc khi có giọng nói, tự nổi lên khi nghỉ</span>
              </div>
            </div>
          </div>

          <!-- Nhóm 2: Bảng phân vai giọng đọc -->
          <div class="space-y-3">
            <h3 class="text-xs font-bold uppercase tracking-wider text-pink-400 flex items-center gap-2">
              <i class="fa-solid fa-users-viewfinder"></i> 2. Bảng Phân Vai Giọng Đọc Đa Tầng (Voice Casting)
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div v-for="(r, rkey) in directorConfig.voice_casting.roles" :key="rkey"
                class="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <div class="flex items-center justify-between text-xs font-bold text-white">
                  <span>Vai: {{ rkey }}</span>
                  <span class="text-[11px] text-slate-400 font-mono">{{ r.rate }} • {{ r.pitch }}</span>
                </div>
                <div class="grid grid-cols-3 gap-2">
                  <select v-model="r.voice" class="col-span-2 glass-input rounded-lg p-1.5 text-xs bg-slate-800 text-slate-200">
                    <option value="nam_hai">🔥 Nam Hài Hước</option>
                    <option value="nu_cakhia">🌸 Nữ Cà Khịa</option>
                    <option value="nam_tram_hai">🎙️ Nam Trầm Hài</option>
                    <option value="nu_nhinhanh">✨ Nữ Nhí Nhảnh</option>
                    <option value="chi_google">🤖 Chị Google</option>
                    <option value="vi-VN-NamMinhNeural">🇻🇳 Nam Minh</option>
                    <option value="vi-VN-HoaiMyNeural">🌸 Hoài My</option>
                    <option value="adam">🎙️ Adam</option>
                    <option value="brian">🎙️ Brian</option>
                  </select>
                  <input v-model="r.rate" type="text" placeholder="+10%" class="glass-input rounded-lg p-1.5 text-xs font-mono text-center">
                </div>
              </div>
            </div>
          </div>

          <!-- Nhóm 3: Kho Meme GIF Hoạt Hình Động -->
          <div class="space-y-3">
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center gap-2">
                <i class="fa-solid fa-face-grin-squint-tears"></i> 3. Kho Meme GIF Hoạt Hình Động (Animated Meme Reactions)
              </h3>
              <span class="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30">
                Tự động chạy Loop tại điểm rơi Punchline
              </span>
            </div>
            
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3.5">
              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/laugh.gif" alt="Laugh Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">laugh.gif</div>
                <div class="text-[10px] text-pink-400 font-medium">Cười nghiêng ngả</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/boom.gif" alt="Boom Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">boom.gif</div>
                <div class="text-[10px] text-amber-400 font-medium">Bẻ lái sốc (Mind Blown)</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/bruh.gif" alt="Bruh Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">bruh.gif</div>
                <div class="text-[10px] text-indigo-400 font-medium">Cạn lời (Facepalm)</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/shock.gif" alt="Shock Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">shock.gif</div>
                <div class="text-[10px] text-rose-400 font-medium">Hoảng hốt sốc</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/huh.gif" alt="Huh Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">huh.gif</div>
                <div class="text-[10px] text-purple-400 font-medium">Mèo "Huh?" ngơ ngác</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/side_eye.gif" alt="Side Eye Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">side_eye.gif</div>
                <div class="text-[10px] text-yellow-400 font-medium">Liếc xéo nghi ngờ</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/clown.gif" alt="Clown Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">clown.gif</div>
                <div class="text-[10px] text-red-400 font-medium">Tự hóa chú hề</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/math.gif" alt="Math Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">math.gif</div>
                <div class="text-[10px] text-cyan-400 font-medium">Tính toán hoang mang</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/ting.gif" alt="Ting Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">ting.gif</div>
                <div class="text-[10px] text-emerald-400 font-medium">Nảy số thông minh</div>
              </div>

              <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 text-center space-y-2 group hover:border-indigo-500/60 transition">
                <div class="w-full h-24 rounded-lg overflow-hidden bg-black/50 flex items-center justify-center">
                  <img src="/api/meme/applause.gif" alt="Applause Meme" class="w-full h-full object-contain">
                </div>
                <div class="text-[11px] font-bold text-slate-200">applause.gif</div>
                <div class="text-[10px] text-amber-300 font-medium">Vỗ tay / Nâng ly</div>
              </div>
            </div>
          </div>

          <!-- Nhóm 4: Chất lượng Video Studio & Vocal Mastering -->
          <div class="space-y-3">
            <h3 class="text-xs font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-2">
              <i class="fa-solid fa-compact-disc"></i> 4. Chất Lượng Video Studio & Xử Lý Âm Thanh Phòng Thu (Mastering)
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Tốc độ khung hình (FPS):</label>
                <select v-model.number="directorConfig.visual_layout.fps" class="w-full glass-input rounded-lg p-2 text-xs bg-slate-800 text-slate-200">
                  <option :value="60">60 FPS (Siêu mượt mà)</option>
                  <option :value="30">30 FPS (Tiêu chuẩn)</option>
                </select>
                <span class="text-[10px] text-slate-500">60 FPS giúp đường hầm 3D cực mượt</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Mức nén CRF (Độ nét):</label>
                <select v-model.number="directorConfig.visual_layout.video_crf" class="w-full glass-input rounded-lg p-2 text-xs bg-slate-800 text-slate-200">
                  <option :value="17">CRF 17 (Cực nét - Lossless)</option>
                  <option :value="18">CRF 18 (Khuyên dùng - Chuẩn Studio)</option>
                  <option :value="20">CRF 20 (Dung lượng nhẹ)</option>
                </select>
                <span class="text-[10px] text-slate-500">CRF thấp cho hình ảnh nét căng không vỡ hạt</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Hiệu ứng trượt thẻ (Slide-Up):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_slide_up_animation" class="rounded bg-slate-800 border-slate-700 text-indigo-500">
                    <span>Bật chuyển động trượt mượt mà</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Thẻ trượt êm từ dưới lên kèm Ease-Out</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Chuẩn hóa âm lượng (LUFS):</label>
                <select v-model.number="directorConfig.audio_mastering.target_lufs" class="w-full glass-input rounded-lg p-2 text-xs bg-slate-800 text-slate-200">
                  <option :value="-14">-14 LUFS (Chuẩn TikTok & Shorts)</option>
                  <option :value="-16">-16 LUFS (Nhẹ nhàng vừa phải)</option>
                </select>
                <span class="text-[10px] text-slate-500">Giọng đọc to rõ không bị TikTok nén nhỏ</span>
              </div>
            </div>
          </div>

          <!-- Nhóm 5: Ảnh Minh Họa & Pop-up Proof Card Nổi -->
          <div class="space-y-3" v-if="directorConfig.illustration_settings">
            <h3 class="text-xs font-bold uppercase tracking-wider text-pink-400 flex items-center gap-2">
              <i class="fa-solid fa-camera-retro"></i> 5. Ảnh Minh Họa Bằng Chứng & Pop-Up Card Nổi Bên Ngoài
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Khung ảnh nổi bên ngoài (Pop-up):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.illustration_settings.enable_popup_illustration" class="rounded bg-slate-800 border-slate-700 text-pink-500">
                    <span class="font-bold text-pink-400">Bật Pop-Up Proof Card (Twist Beat)</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Hiện lên chốc lát với hiệu ứng Punch-in Bounce và biến mất</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Ảnh gắn kèm trong thẻ câu hỏi đầu:</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.illustration_settings.enable_post_attached_image" class="rounded bg-slate-800 border-slate-700 text-indigo-500">
                    <span>Đính kèm ảnh trong bài đăng</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Mặc định tắt để thẻ câu hỏi gọn gàng, chữ to rõ</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Thời lượng hiển thị Pop-up:</label>
                <select v-model.number="directorConfig.illustration_settings.popup_duration_s" class="w-full glass-input rounded-lg p-2 text-xs bg-slate-800 text-slate-200">
                  <option :value="2.2">2.2 giây (Chớp nhoáng)</option>
                  <option :value="2.8">2.8 giây (Khuyên dùng - Chuẩn nhịp đọc)</option>
                  <option :value="3.5">3.5 giây (Xem kĩ chi tiết)</option>
                </select>
                <span class="text-[10px] text-slate-500">Đủ thời gian để người xem hiểu trò đùa / câu trả lời</span>
              </div>
            </div>
          </div>

          <!-- Nhóm 6: Bộ Công Cụ Tối Ưu Viral & Giữ Chân Triệu View -->
          <div class="space-y-3" v-if="directorConfig.visual_layout">
            <h3 class="text-xs font-bold uppercase tracking-wider text-purple-400 flex items-center gap-2">
              <i class="fa-solid fa-fire text-amber-400"></i> 6. Bộ Công Cụ Tối Ưu Viral Triệu View (Viral Growth Suite)
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Thẻ Outro Kêu Gọi Tương Tác:</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_outro_cta" class="rounded bg-slate-800 border-slate-700 text-purple-500">
                    <span class="font-bold text-purple-300">Bật Outro CTA Cuối Video</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Kêu gọi thả tim, bình luận & follow tăng tương tác 300%</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Âm Thanh Foley Chuyển Cảnh:</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_foley_sfx" class="rounded bg-slate-800 border-slate-700 text-amber-500">
                    <span class="font-bold text-amber-300">Bật Foley SFX (Whoosh/Pop/Camera)</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Tiếng vút gió, bong bóng và màn trập máy ảnh sống động</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Tự Động Tạo Ảnh Bìa (Thumbnail):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_auto_thumbnail" class="rounded bg-slate-800 border-slate-700 text-emerald-500">
                    <span class="font-bold text-emerald-300">Bật Auto Clickbait Cover (9:16)</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Render ảnh bìa sắc nét kèm nhãn giật gân tăng CTR</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Phụ Đề Động (Kinetic Subtitles):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_kinetic_subtitles" class="rounded bg-slate-800 border-slate-700 text-cyan-500">
                    <span class="font-bold text-cyan-300">Bật Phụ Đề Nổi Đậm Nét</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Phụ đề vàng/trắng nổi bật phong cách Reels/TikTok</span>
              </div>

              <!-- Thanh tiến trình Completion Progress Bar -->
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Thanh Tiến Trình Chạy Đáy (Retention):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_completion_progress_bar" class="rounded bg-slate-800 border-slate-700 text-rose-500">
                    <span class="font-bold text-rose-300">Bật Progress Bar Đáy Video</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Kích thích tâm lý xem hết 100% video (Completion Rate)</span>
              </div>

              <!-- Vignette Điện Ảnh -->
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Vignette Điện Ảnh (Tối 4 Góc):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_cinematic_vignette" class="rounded bg-slate-800 border-slate-700 text-indigo-500">
                    <span class="font-bold text-indigo-300">Bật Cinematic Vignette</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Làm tối dịu 4 góc, hút mắt người xem vào trung tâm</span>
              </div>

              <!-- Âm Thanh Không Gian 3D (Spatial Panning) -->
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Âm Thanh Nổi 3D (Spatial Audio):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_spatial_audio_panning" class="rounded bg-slate-800 border-slate-700 text-emerald-500">
                    <span class="font-bold text-emerald-300">Bật Stereo Panning Trái/Phải</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Tác giả ở giữa, các bình luận đối đáp lệch trái/phải như thật</span>
              </div>

              <!-- Tension Riser Swell -->
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Gió Rít Trước Cú Twist (Tension Riser):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_tension_riser" class="rounded bg-slate-800 border-slate-700 text-amber-500">
                    <span class="font-bold text-amber-300">Bật Tension Riser Swell</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Âm thanh căng thẳng tăng dần 1.1s trước khi nổ bẻ lái</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Chế độ Meme GIF Đè Màn Hình:</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_meme_reaction_popups" class="rounded bg-slate-800 border-slate-700 text-pink-500">
                    <span :class="directorConfig.visual_layout.enable_meme_reaction_popups ? 'text-pink-300 font-bold' : 'text-slate-400'">
                      {{ directorConfig.visual_layout.enable_meme_reaction_popups ? 'Bật GIF Meme đè ra giữa' : 'Tắt (Chỉ dùng hiệu ứng chuyển động & SFX)' }}
                    </span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Tắt để video sang trọng, chỉ dùng âm thanh & chuyển động thẻ</span>
              </div>

              <!-- Chuẩn thời lượng kiếm tiền tối thiểu 1 phút -->
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Đảm Bảo Thời Lượng Trên 1 Phút:</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enforce_min_duration_60s" class="rounded bg-slate-800 border-slate-700 text-teal-500">
                    <span class="font-bold text-teal-300">Chuẩn Kiếm Tiền (>= 60 Giây)</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Đảm bảo video luôn đạt trên 60s để nhận tiền TikTok Creator Rewards</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Video Thỏa Mãn Ở Giữa (Sandwich):</label>
                <div class="pt-1">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_center_satisfying_video" class="rounded bg-slate-800 border-slate-700 text-teal-500">
                    <span class="font-bold text-teal-300">Bật Video Thỏa Mãn Ở Giữa</span>
                  </label>
                </div>
                <select v-if="directorConfig.visual_layout.enable_center_satisfying_video" v-model="directorConfig.visual_layout.center_satisfying_category" class="w-full glass-input rounded-lg p-2 text-xs bg-slate-800 text-slate-200 mt-1">
                  <option value="random">🎲 Tự Động Trộn Ngẫu Nhiên (Viral Sandwich)</option>
                  <option value="puzzle">🧩 3D Puzzle & Ghép Hình Thông Minh</option>
                  <option value="gameplay">🏃 Gameplay Parkour & Stunt 60FPS</option>
                  <option value="asmr">🫧 ASMR Bọt Mịn & Thư Giãn Thị Giác</option>
                  <option value="hypnotic">🌀 Hypnotic Geometry & Neon Matrix</option>
                  <option value="kinetic_sand">⏳ Cát Động Lực & Cắt Khuôn</option>
                  <option value="food">🍰 Food Prep & Nghệ Thuật Ẩm Thực</option>
                </select>
                <span class="text-[10px] text-slate-500">Bố cục Sandwich kẹp video cuốn hút ở giữa bo góc 26px mềm mại</span>
              </div>

              <!-- Bộ Chuyển Cảnh Đa Dạng (Dynamic Motion Transitions) -->
              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Hiệu Ứng Chuyển Cảnh Thẻ (Transitions):</label>
                <select v-model="directorConfig.visual_layout.transition_style" class="w-full glass-input rounded-lg p-2 text-xs bg-slate-800 text-slate-200 mt-1">
                  <option value="dynamic_conversational">⚡ Đối thoại thông minh (Spring Pop + Trượt trái/phải)</option>
                  <option value="spring_pop">🎾 Nảy lò xo nhún giảm chấn (iOS Spring Curve)</option>
                  <option value="slide_up">⬆️ Trượt lên êm dịu (Ease-Out Slide-Up)</option>
                </select>
                <span class="text-[10px] text-slate-500">Mô phỏng chuyển động vật lý nảy nhún và đối đáp chat chân thực</span>
              </div>

              <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 space-y-1.5">
                <label class="text-xs text-slate-400 font-semibold">Meme Chạy Xuyên Suốt (Anchor):</label>
                <div class="pt-2">
                  <label class="flex items-center gap-2 text-xs text-slate-200 cursor-pointer">
                    <input type="checkbox" v-model="directorConfig.visual_layout.enable_continuous_anchor_meme" class="rounded bg-slate-800 border-slate-700 text-indigo-500">
                    <span class="text-slate-300">Meme loop liên tục ở góc</span>
                  </label>
                </div>
                <span class="text-[10px] text-slate-500">Khuyên tắt để không gian sạch, tối đa độ thỏa mãn thị giác</span>
              </div>
            </div>
          </div>

          <!-- Nhóm 7: AI Prompt & Model -->
          <div class="space-y-3">
            <h3 class="text-xs font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-2">
              <i class="fa-solid fa-brain"></i> 7. Cấu Hình Đạo Diễn & Thời Lượng Kịch Bản
            </h3>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="space-y-1">
                <label class="text-xs font-semibold text-slate-300">Chế độ thời lượng kịch bản</label>
                <select v-model="directorConfig.ai_director.duration_mode"
                  class="w-full glass-input rounded-xl px-3 py-2 text-xs focus:outline-none">
                  <option value="auto">Tự động (3-6 bình luận theo độ sâu chủ đề)</option>
                  <option value="fast_30s">Nhanh 30s (Đúng 3 bình luận - Chuẩn TikTok ngắn)</option>
                  <option value="deep_60s">Kể chuyện 60s (4-5 bình luận - Drama đối đáp & Twist kép)</option>
                  <option value="viral_story_90s">Viral Story 90s (6-7 bình luận - Phốt dài liên hoàn)</option>
                </select>
                <span class="text-[10px] text-slate-500">Quyết định số lượng bình luận và cấu trúc đồ thị cảm xúc</span>
              </div>
              <div class="space-y-1">
                <label class="text-xs font-semibold text-slate-300">Model Gemini AI</label>
                <select v-model="directorConfig.ai_director.model"
                  class="w-full glass-input rounded-xl px-3 py-2 text-xs focus:outline-none">
                  <option value="gemini-3.5-flash">gemini-3.5-flash (Khuyên dùng - Nhanh & Sáng tạo)</option>
                  <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Siêu nhẹ)</option>
                  <option value="gemini-flash-latest">gemini-flash-latest (Bản mới nhất)</option>
                </select>
                <span class="text-[10px] text-slate-500">Động cơ trí tuệ nhân tạo đạo diễn kịch bản</span>
              </div>
            </div>
            <label class="text-xs font-semibold text-slate-300 block">System Instruction (Chỉ dẫn phong cách)</label>
            <textarea v-model="directorConfig.ai_director.system_instruction" rows="4"
              class="w-full glass-input rounded-xl p-3 text-xs focus:outline-none font-mono text-slate-300"></textarea>
          </div>

          <!-- Nhóm 8: Quản Lý Nhiều GEMINI_API_KEY (DB Xoay Vòng + Fallback .env) -->
          <div class="space-y-3 pt-2 border-t border-slate-800/80">
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center gap-2">
                <i class="fa-solid fa-key"></i> 8. Quản Lý Gemini API Keys (SQLite DB + Xoay Vòng Tự Động)
              </h3>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-950/60 text-emerald-300 border border-emerald-700/40">
                Ưu tiên DB • Tự động fallback .env
              </span>
            </div>

            <div class="flex flex-col sm:flex-row gap-2">
              <input v-model="newGeminiLabel" type="text" placeholder="Nhãn (VD: Key phụ 1)"
                class="sm:w-44 glass-input rounded-xl px-3 py-2 text-xs text-slate-200">
              <input v-model="newGeminiKey" type="password" placeholder="Dán GEMINI_API_KEY (AIzaSy...)"
                class="flex-1 glass-input rounded-xl px-3 py-2 text-xs text-slate-200 font-mono">
              <button type="button" @click="addGeminiKey"
                class="px-4 py-2 rounded-xl bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold cursor-pointer shrink-0">
                <i class="fa-solid fa-plus mr-1"></i> Thêm Key vào DB
              </button>
            </div>

            <div v-if="geminiKeys.length === 0" class="text-[11px] text-slate-400 bg-slate-900/50 rounded-xl p-3 border border-slate-800">
              Chưa có key nào trong SQLite DB — hệ thống đang tự động dùng <code class="text-amber-300">GEMINI_API_KEY</code> từ file <code class="text-amber-300">.env</code>.
            </div>

            <div v-else class="space-y-1.5">
              <div v-for="k in geminiKeys" :key="k.id"
                class="flex items-center justify-between gap-3 px-3 py-2 rounded-xl bg-slate-900/60 border border-slate-800 text-xs">
                <div class="flex items-center gap-2.5 min-w-0">
                  <span class="w-2 h-2 rounded-full shrink-0" :class="k.is_active ? 'bg-emerald-400' : 'bg-slate-600'"></span>
                  <span class="font-semibold text-slate-200 truncate">{{ k.label }}</span>
                  <code class="text-[11px] text-slate-400 font-mono">{{ k.masked_key }}</code>
                  <span v-if="k.error_count > 0" class="text-[10px] px-1.5 py-0.5 rounded bg-rose-950/60 text-rose-300 border border-rose-700/40">
                    {{ k.error_count }} lỗi
                  </span>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                  <button type="button" @click="toggleGeminiKey(k)"
                    class="px-2.5 py-1 rounded-lg text-[11px] font-medium border cursor-pointer"
                    :class="k.is_active ? 'border-emerald-700/50 text-emerald-300 bg-emerald-950/40' : 'border-slate-700 text-slate-400 bg-slate-800'">
                    {{ k.is_active ? 'Đang bật' : 'Đang tắt' }}
                  </button>
                  <button type="button" @click="deleteGeminiKey(k.id)"
                    class="px-2 py-1 rounded-lg text-[11px] text-rose-400 hover:bg-rose-950/40 cursor-pointer">
                    <i class="fa-solid fa-trash"></i>
                  </button>
                </div>
              </div>
            </div>
          </div>

        </div>
      </div>
    </main>

    <!-- TAB 4: MARKET INTELLIGENCE & REMOTE SYNC -->
    <main v-show="currentTab === 'market'" class="flex-1 max-w-[1760px] w-full mx-auto px-4 sm:px-8 py-6 space-y-5">

      <!-- Top Header, Sync Controls & Sub-tabs -->
      <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
        <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4 border-b border-slate-800 pb-4">
          <div>
            <h2 class="text-base font-bold text-white flex items-center gap-2.5 flex-wrap">
              <i class="fa-solid fa-magnifying-glass-chart text-emerald-400"></i>
              <span>Market Intelligence — V3.1 (Tương thích V1/V2)</span>
              <span class="text-[11px] px-2.5 py-0.5 rounded-full font-semibold border"
                :class="marketStatus.cloudflare?.token_configured ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' : 'bg-amber-500/15 text-amber-300 border-amber-500/30'">
                {{ marketStatus.cloudflare?.token_configured ? 'Cloudflare Auth OK' : 'Chưa cấu hình CLOUDFLARE_APP_TOKEN' }}
              </span>
              <span class="text-[11px] px-2.5 py-0.5 rounded-full font-semibold border"
                :class="marketStatus.stats?.last_sync_status === 'ok' ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30' : (marketStatus.is_syncing ? 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30' : 'bg-rose-500/15 text-rose-300 border-rose-500/30')">
                Sync: {{ marketStatus.is_syncing ? 'đang đồng bộ' : (marketStatus.stats?.last_sync_status || 'idle') }}
              </span>
            </h2>
            <p class="text-xs text-slate-400 mt-1">
              Đồng bộ từ <span class="font-mono text-slate-300">AI_WORKSPACE/01_Market_Data/</span> (tự loại trừ <span class="font-mono text-slate-300">Archive/</span>, giữ nguyên lịch sử V1/V2) • Cập nhật gần nhất:
              <strong class="text-slate-200">{{ formatIsoTime(marketStatus.stats?.last_sync_at) || 'Chưa đồng bộ' }}</strong>
            </p>
            <p v-if="marketStatus.stats?.last_error" class="text-[11px] text-rose-300 mt-1">
              ⚠️ Lỗi gần nhất: {{ marketStatus.stats.last_error }}
            </p>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <div class="flex items-center gap-2 bg-slate-900/90 px-3 py-1.5 rounded-xl border border-slate-800 text-xs">
              <span class="text-slate-400">Chu kỳ nền:</span>
              <select v-model.number="marketConfigInterval" @change="updateMarketConfig"
                class="bg-transparent text-indigo-300 font-semibold focus:outline-none cursor-pointer">
                <option :value="60" class="bg-slate-900">1 phút</option>
                <option :value="300" class="bg-slate-900">5 phút (Mặc định)</option>
                <option :value="600" class="bg-slate-900">10 phút</option>
                <option :value="900" class="bg-slate-900">15 phút</option>
                <option :value="1800" class="bg-slate-900">30 phút</option>
              </select>
            </div>

            <button @click="testMarketConnection" :disabled="isTestingConnection"
              class="px-3 py-2 rounded-xl text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center gap-1.5 cursor-pointer disabled:opacity-50">
              <i class="fa-solid fa-plug-circle-check text-cyan-400" :class="{'fa-spin': isTestingConnection}"></i>
              <span>Kiểm tra Cloudflare</span>
            </button>

            <button @click="triggerMarketSyncNow" :disabled="marketStatus.is_syncing"
              class="px-4 py-2 rounded-xl text-xs font-bold bg-gradient-to-r from-emerald-600 to-teal-600 hover:opacity-95 text-white shadow-lg shadow-emerald-600/25 transition flex items-center gap-1.5 cursor-pointer disabled:opacity-50">
              <i class="fa-solid fa-arrows-rotate" :class="{'fa-spin': marketStatus.is_syncing}"></i>
              <span>{{ marketStatus.is_syncing ? 'Đang đồng bộ...' : 'Sync Now' }}</span>
            </button>

            <label title="Nhập file JSON cục bộ để kiểm tra"
              class="py-2 px-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-semibold cursor-pointer">
              <i class="fa-solid fa-file-import text-amber-400 mr-1"></i> Nhập JSON
              <input type="file" accept=".json,application/json" class="hidden" @change="uploadLocalMarketJson">
            </label>
          </div>
        </div>

        <div v-if="marketConnectionNotice" class="p-3 rounded-xl border text-xs flex items-center justify-between gap-3"
          :class="marketConnectionNotice.ok ? 'bg-emerald-950/50 border-emerald-500/40 text-emerald-200' : 'bg-amber-950/50 border-amber-500/40 text-amber-200'">
          <span>{{ marketConnectionNotice.text }}</span>
          <button @click="marketConnectionNotice = null" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
        </div>

        <!-- 5 Market Intelligence Sub-tabs -->
        <div class="flex flex-wrap items-center gap-2 pt-1">
          <button @click="switchMarketSubTab('overview')"
            :class="marketSubTab === 'overview' ? 'bg-emerald-600 text-white font-bold shadow' : 'bg-slate-900/90 text-slate-400 hover:text-slate-200 border border-slate-800'"
            class="px-3.5 py-1.5 rounded-xl text-xs transition cursor-pointer">📊 Tổng quan</button>
          <button @click="switchMarketSubTab('threads')"
            :class="marketSubTab === 'threads' ? 'bg-indigo-600 text-white font-bold shadow' : 'bg-slate-900/90 text-slate-400 hover:text-slate-200 border border-slate-800'"
            class="px-3.5 py-1.5 rounded-xl text-xs transition cursor-pointer">
            🧵 Threads ({{ marketStatus.stats?.by_platform?.threads ?? 0 }})
          </button>
          <button @click="switchMarketSubTab('tiktok')"
            :class="marketSubTab === 'tiktok' ? 'bg-cyan-600 text-white font-bold shadow' : 'bg-slate-900/90 text-slate-400 hover:text-slate-200 border border-slate-800'"
            class="px-3.5 py-1.5 rounded-xl text-xs transition cursor-pointer">
            🎵 TikTok ({{ marketStatus.stats?.by_platform?.tiktok ?? 0 }})
          </button>
          <button @click="switchMarketSubTab('shopee')"
            :class="marketSubTab === 'shopee' ? 'bg-orange-600 text-white font-bold shadow' : 'bg-slate-900/90 text-slate-400 hover:text-slate-200 border border-slate-800'"
            class="px-3.5 py-1.5 rounded-xl text-xs transition cursor-pointer">
            🛒 Shopee ({{ marketStatus.stats?.by_platform?.shopee ?? 0 }})
          </button>
          <button @click="switchMarketSubTab('reports')"
            :class="marketSubTab === 'reports' ? 'bg-amber-600 text-white font-bold shadow' : 'bg-slate-900/90 text-slate-400 hover:text-slate-200 border border-slate-800'"
            class="px-3.5 py-1.5 rounded-xl text-xs transition cursor-pointer">📋 Báo cáo</button>
        </div>
      </div>

      <!-- SUB-TAB 1: TỔNG QUAN -->
      <div v-show="marketSubTab === 'overview'" class="space-y-5">
        <!-- V3.1 Run Status & Source Error Distinction Banner -->
        <div v-if="marketReport?.run_status || marketStatus.stats?.latest_v3_screening?.run_status"
          class="p-4 rounded-2xl border text-xs flex flex-col md:flex-row md:items-center justify-between gap-3"
          :class="marketReport?.is_source_error ? 'bg-rose-950/60 border-rose-500/50 text-rose-200' : (marketReport?.is_empty_market_valid ? 'bg-slate-900/90 border-slate-700 text-slate-200' : 'bg-emerald-950/35 border-emerald-500/35 text-emerald-100')">
          <div class="space-y-1">
            <div class="font-bold flex items-center gap-2 flex-wrap">
              <span>{{ marketReport?.run_status_label_vi || '✅ Threads Trend Intelligence V3.1' }}</span>
              <span v-if="marketReport?.run_id || marketStatus.stats?.latest_v3_screening?.run_id" class="text-[10px] font-mono px-2 py-0.5 rounded bg-black/30 text-slate-300">
                {{ marketReport?.run_id || marketStatus.stats?.latest_v3_screening?.run_id }}
              </span>
            </div>
            <div v-if="marketReport?.is_source_error" class="text-[11px] text-rose-300">
              ⚠️ Trạng thái nguồn lỗi/thiếu dữ liệu — không kết luận "thị trường không có trend".
            </div>
            <div v-else-if="marketReport?.is_empty_market_valid" class="text-[11px] text-slate-300">
              ℹ️ Nguồn chạy bình thường và đã quét đủ ứng viên nhưng không có bài đạt chuẩn V3.1 trong cửa sổ hiện tại.
            </div>
            <div v-if="marketReport?.screening" class="text-[11px] text-slate-300 font-mono">
              Sàng lọc: Quét {{ marketReport.screening.scanned_count ?? '—' }} • Đạt chuẩn {{ marketReport.screening.qualified_count ?? '—' }} • Loại {{ marketReport.screening.rejected_count ?? '—' }} • Xác minh chéo Web {{ marketReport.screening.cross_verified_count ?? '—' }}
            </div>
          </div>
          <div class="flex flex-wrap gap-1.5 text-[11px]">
            <button @click="jumpToThreadsTier('very_hot')" class="px-2.5 py-1 rounded-lg bg-rose-500/20 border border-rose-500/40 text-rose-200 font-bold cursor-pointer hover:bg-rose-500/30">🔥 Very Hot: {{ marketStatus.stats?.threads_by_trend_tier?.very_hot ?? 0 }}</button>
            <button @click="jumpToThreadsTier('hot')" class="px-2.5 py-1 rounded-lg bg-amber-500/20 border border-amber-500/40 text-amber-200 font-bold cursor-pointer hover:bg-amber-500/30">⚡ Hot: {{ marketStatus.stats?.threads_by_trend_tier?.hot ?? 0 }}</button>
            <button @click="jumpToThreadsTier('emerging')" class="px-2.5 py-1 rounded-lg bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 font-bold cursor-pointer hover:bg-emerald-500/30">🌱 Emerging: {{ marketStatus.stats?.threads_by_trend_tier?.emerging ?? 0 }}</button>
            <button @click="jumpToThreadsTier('watchlist')" class="px-2.5 py-1 rounded-lg bg-sky-500/20 border border-sky-500/40 text-sky-200 font-bold cursor-pointer hover:bg-sky-500/30">👀 Watchlist: {{ marketStatus.stats?.threads_by_trend_tier?.watchlist ?? 0 }}</button>
            <button @click="jumpToThreadsTier('unverified')" class="px-2.5 py-1 rounded-lg bg-slate-800 border border-slate-600 text-slate-300 font-bold cursor-pointer hover:bg-slate-700">❓ Unverified: {{ marketStatus.stats?.threads_by_trend_tier?.unverified ?? 0 }}</button>
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div class="glass-panel rounded-2xl p-4 space-y-1.5">
            <div class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">Số bản ghi theo nền tảng</div>
            <div class="text-2xl font-extrabold text-white font-mono">{{ marketStatus.stats?.total_items ?? 0 }}</div>
            <div class="flex flex-wrap gap-1.5 text-[11px] font-bold">
              <span class="px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">Threads: {{ marketStatus.stats?.by_platform?.threads ?? 0 }}</span>
              <span class="px-2 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">TikTok: {{ marketStatus.stats?.by_platform?.tiktok ?? 0 }}</span>
              <span class="px-2 py-0.5 rounded bg-orange-500/15 text-orange-300 border border-orange-500/30">Shopee: {{ marketStatus.stats?.by_platform?.shopee ?? 0 }}</span>
            </div>
            <div class="text-[10px] text-slate-400">Từ {{ marketStatus.stats?.total_raw_files ?? 0 }} file Drive (tách riêng file strategy/audit/contract)</div>
          </div>

          <div class="glass-panel rounded-2xl p-4 space-y-1.5">
            <div class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">Chất lượng dữ liệu (Usable vs Cần xác minh)</div>
            <div class="flex items-baseline gap-3">
              <span class="text-xl font-extrabold text-emerald-300 font-mono">{{ marketStatus.stats?.by_quality?.usable ?? 0 }} <span class="text-xs font-normal">usable</span></span>
              <span class="text-lg font-bold text-amber-300 font-mono">{{ marketStatus.stats?.by_quality?.needs_verification ?? 0 }} <span class="text-xs font-normal">needs_verif</span></span>
            </div>
            <div class="text-[11px] text-slate-400 font-mono">
              T: {{ marketStatus.stats?.by_platform_quality?.threads?.usable ?? 0 }}/{{ marketStatus.stats?.by_platform_quality?.threads?.total ?? 0 }} |
              TT: {{ marketStatus.stats?.by_platform_quality?.tiktok?.usable ?? 0 }}/{{ marketStatus.stats?.by_platform_quality?.tiktok?.total ?? 0 }} |
              SP: {{ marketStatus.stats?.by_platform_quality?.shopee?.usable ?? 0 }}/{{ marketStatus.stats?.by_platform_quality?.shopee?.total ?? 0 }}
            </div>
            <div class="text-[10px] text-slate-500">Chưa gắn nhãn (cũ): {{ marketStatus.stats?.by_quality?.unlabeled ?? 0 }}</div>
          </div>

          <div class="glass-panel rounded-2xl p-4 space-y-1.5">
            <div class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">Tỷ lệ đầy đủ dữ liệu tương tác</div>
            <div class="text-xs font-mono text-slate-200 space-y-0.5 pt-0.5">
              <div>Likes: <strong class="text-pink-300">{{ marketStatus.stats?.engagement_completeness?.likes_pct ?? '—' }}%</strong> ({{ marketStatus.stats?.engagement_completeness?.with_likes ?? 0 }} bài)</div>
              <div>Comments: <strong class="text-sky-300">{{ marketStatus.stats?.engagement_completeness?.comments_pct ?? '—' }}%</strong> ({{ marketStatus.stats?.engagement_completeness?.with_comments ?? 0 }} bài)</div>
              <div>Views: <strong class="text-cyan-300">{{ marketStatus.stats?.engagement_completeness?.views_pct ?? '—' }}%</strong> ({{ marketStatus.stats?.engagement_completeness?.with_views ?? 0 }} bản ghi)</div>
            </div>
            <div class="text-[10px] text-slate-500">Thiếu = null (không quy đổi về 0)</div>
          </div>

          <div class="glass-panel rounded-2xl p-4 space-y-1.5">
            <div class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">Cập nhật & Telegram Poller</div>
            <div class="text-xs font-bold" :class="marketStatus.remote_poller?.running && marketStatus.remote_poller?.configured ? 'text-emerald-300' : 'text-amber-300'">
              <i class="fa-brands fa-telegram text-sky-400 mr-1"></i>
              {{ marketStatus.remote_poller?.running ? (marketStatus.remote_poller?.configured ? 'Online Polling (D1)' : 'Chờ Token') : 'Offline' }}
            </div>
            <div class="text-[11px] text-slate-300 font-mono truncate">{{ formatIsoTime(marketStatus.stats?.last_sync_at) || 'Chưa đồng bộ' }}</div>
            <div class="text-[10px] text-slate-400">Token: {{ marketStatus.cloudflare?.token_masked || 'Chưa cấu hình' }}</div>
          </div>
        </div>

        <!-- Top Cơ hội có bằng chứng & Chủ đề Trending V3.1 -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-3">
          <div class="flex items-center justify-between gap-2 flex-wrap">
            <h3 class="text-xs font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-2">
              <i class="fa-solid fa-bullseye"></i> Top Cơ Hội Có Bằng Chứng (Đã loại unverified / needs_verification & tên nền tảng)
            </h3>
            <button @click="switchMarketSubTab('reports')" class="text-xs text-indigo-300 hover:underline cursor-pointer">Xem báo cáo đầy đủ →</button>
          </div>
          <div v-if="(marketStatus.stats?.trends_snapshot?.topics || []).length" class="flex flex-wrap items-center gap-1.5 text-[11px] pb-1">
            <span class="text-[10px] uppercase font-bold text-indigo-300 mr-1">🔥 Chủ đề Trending VN:</span>
            <span v-for="(tp, idx) in marketStatus.stats.trends_snapshot.topics.slice(0, 10)" :key="'ov-tp-'+idx"
              class="px-2 py-0.5 rounded-lg bg-indigo-500/15 border border-indigo-500/30 text-indigo-200">
              {{ tp.topic }} <span v-if="tp.posts_label" class="text-[10px] text-indigo-300/80">({{ tp.posts_label }})</span>
            </span>
          </div>
          <div v-if="(marketReport?.affiliate_opportunities || []).length" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 text-xs">
            <div v-for="(a, i) in (marketReport.affiliate_opportunities || []).slice(0, 6)" :key="'ov-aff-'+i"
              class="p-3 rounded-xl bg-slate-900/75 border border-slate-800 space-y-1">
              <div class="font-bold text-white line-clamp-1">{{ a.product || a.brand_or_topic }}</div>
              <div class="flex items-center gap-1.5 text-[10px] flex-wrap">
                <span v-if="a.priority" class="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300">ưu tiên: {{ a.priority }}</span>
                <span v-if="a.purchase_intent && a.purchase_intent !== a.priority" class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300">intent: {{ a.purchase_intent }}</span>
                <span v-if="a.brand_or_topic && a.brand_or_topic !== a.priority" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">{{ a.brand_or_topic }}</span>
              </div>
              <p v-if="a.evidence_summary" class="text-[11px] text-slate-300 line-clamp-2">{{ a.evidence_summary }}</p>
              <a v-if="(a.evidence_urls || []).length" :href="a.evidence_urls[0]" target="_blank" rel="noreferrer"
                class="text-[10px] text-sky-400 hover:underline inline-block truncate max-w-full">🔗 {{ a.evidence_urls[0] }}</a>
            </div>
          </div>
          <div v-else class="text-xs text-slate-400 italic py-2">Chưa có cơ hội đủ điều kiện từ dữ liệu hiện tại.</div>
        </div>

        <!-- Sync History & Crawl Center -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-3">
            <h3 class="text-xs font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-2">
              <i class="fa-solid fa-clock-rotate-left"></i> Lịch sử đồng bộ & Lệnh Telegram (D1)
            </h3>
            <div class="space-y-2 max-h-56 overflow-y-auto text-xs">
              <div v-for="run in (marketStatus.recent_runs || [])" :key="run.run_id"
                class="p-2.5 rounded-xl bg-slate-900/70 border border-slate-800 flex items-center justify-between gap-3">
                <div class="min-w-0">
                  <div class="font-semibold text-slate-200">
                    {{ run.trigger_source }} •
                    <span :class="run.status === 'completed' ? 'text-emerald-400' : (run.status === 'failed' ? 'text-rose-400' : 'text-cyan-400')">{{ run.status }}</span>
                  </div>
                  <div class="text-[10px] text-slate-400 font-mono">{{ formatIsoTime(run.started_at) }}</div>
                  <div v-if="run.error_message" class="text-[10px] text-rose-300 truncate">{{ run.error_message }}</div>
                </div>
                <div class="text-right font-mono text-[11px] text-slate-300 whitespace-nowrap">
                  <div>+{{ run.records_inserted }} mới / ~{{ run.records_updated }} upd</div>
                  <div class="text-[10px] text-slate-500">{{ run.files_downloaded }}/{{ run.files_checked }} files • {{ run.records_deduped }} bỏ qua</div>
                </div>
              </div>
              <div v-if="!(marketStatus.recent_runs || []).length" class="text-slate-500 italic py-3 text-center">Chưa có lượt đồng bộ.</div>
            </div>

            <div v-if="(marketStatus.stats?.non_crawl_files || []).length" class="pt-2 border-t border-slate-800 space-y-1">
              <div class="text-[10px] font-bold uppercase text-slate-400">File tài liệu hệ thống tách riêng (không nhập vào bản ghi crawl)</div>
              <div class="flex flex-wrap gap-1.5 text-[10px]">
                <span v-for="nf in marketStatus.stats.non_crawl_files" :key="nf.file_name"
                  class="px-2 py-0.5 rounded bg-slate-900 text-slate-300 border border-slate-700 font-mono">
                  {{ nf.file_name }} [{{ nf.file_role }}]
                </span>
              </div>
            </div>
          </div>

          <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-3">
            <div class="flex items-center justify-between gap-2 flex-wrap">
              <h3 class="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center gap-2">
                <i class="fa-solid fa-spider"></i> Crawl Center — Hàng Đợi D1
              </h3>
              <select v-model="crawlScheduleProfile" @change="updateCrawlScheduleProfile"
                class="glass-input rounded-lg px-2.5 py-1 text-[11px] bg-slate-800 text-amber-300 font-semibold">
                <option value="production_6h">Chu kỳ đề xuất Worker: Production (6h)</option>
                <option value="test_5m">Chu kỳ đề xuất Worker: Test giới hạn (5p)</option>
              </select>
            </div>
            <div class="text-[11px] text-slate-300 bg-slate-900/60 px-3 py-2 rounded-xl border border-slate-800 space-y-0.5">
              <div>
                Poll thực tế gần nhất từ Muse:
                <strong class="font-mono text-emerald-300">{{ crawlScheduleInfo?.muse_last_poll_at ? formatIsoTime(crawlScheduleInfo.muse_last_poll_at) : 'Chưa ghi nhận lượt poll từ Muse' }}</strong>
              </div>
              <div class="text-[10px] text-slate-400">
                <span v-if="crawlScheduleInfo?.expected_pickup_at">Dự kiến nhận kế tiếp (theo poll thật): {{ formatIsoTime(crawlScheduleInfo.expected_pickup_at) }}</span>
                <span v-else>⚠️ Chưa có lịch Muse bật tự động — chỉ đổi cấu hình Worker không tự kích hoạt Muse.</span>
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-12 gap-2">
              <select v-model="crawlRequestForm.platform" class="sm:col-span-3 glass-input rounded-lg px-2.5 py-1.5 text-xs bg-slate-800 text-slate-200">
                <option value="all">Tất cả (All)</option>
                <option value="threads">Threads</option>
                <option value="tiktok">TikTok</option>
                <option value="shopee">Shopee</option>
              </select>
              <select v-model.number="crawlRequestForm.requested_count" class="sm:col-span-2 glass-input rounded-lg px-2 py-1.5 text-xs bg-slate-800 text-slate-200">
                <option :value="20">20 bài</option>
                <option :value="50">50 bài</option>
                <option :value="100">100 bài</option>
                <option :value="200">200 bài</option>
              </select>
              <input v-model="crawlRequestForm.keywords" type="text" placeholder="Từ khóa / ngách sản phẩm..."
                class="sm:col-span-5 glass-input rounded-lg px-3 py-1.5 text-xs text-slate-200">
              <button @click="submitCrawlRequest"
                class="sm:col-span-2 px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold cursor-pointer">
                Gửi Queue
              </button>
            </div>

            <div class="space-y-2 max-h-48 overflow-y-auto text-xs pt-1">
              <div v-for="crw in crawlRequests" :key="crw.id"
                class="p-2.5 rounded-xl bg-slate-900/70 border border-slate-800 flex items-center justify-between gap-2">
                <div class="min-w-0">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="px-1.5 py-0.5 rounded bg-slate-800 text-indigo-300 font-bold uppercase text-[10px]">{{ crw.platform }}</span>
                    <span class="font-mono text-[11px] text-slate-300">{{ crw.requested_count || 50 }} bài</span>
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold"
                      :class="crw.status === 'completed' ? 'bg-emerald-500/20 text-emerald-300' : (crw.status === 'running' ? 'bg-cyan-500/20 text-cyan-300' : (crw.status === 'queued' ? 'bg-amber-500/20 text-amber-300' : 'bg-slate-800 text-slate-400'))">
                      {{ crw.status === 'queued' ? 'Đang chờ Muse claim' : (crw.status === 'running' ? 'Muse đang crawl' : crw.status) }}
                    </span>
                    <span class="text-[10px] text-slate-400">[{{ crw.requested_by_source === 'telegram' ? 'Thủ công Telegram' : (crw.requested_by_source === 'app_video' ? 'Thủ công App_Video' : crw.requested_by_source) }}]</span>
                  </div>
                  <div class="text-[10px] text-slate-400 truncate mt-0.5">
                    {{ crw.keywords || '(theo cấu hình mặc định)' }} • Tạo lúc: {{ formatIsoTime(crw.created_at) }}
                  </div>
                </div>
                <button v-if="crw.status === 'queued'" @click="cancelCrawlRequest(crw.id)"
                  class="px-2.5 py-1 rounded-lg bg-rose-500/20 hover:bg-rose-500/35 text-rose-300 border border-rose-500/30 text-[10px] font-semibold shrink-0 cursor-pointer">
                  Hủy chờ
                </button>
              </div>
              <div v-if="!crawlRequests.length" class="text-slate-500 italic py-4 text-center">
                Chưa có yêu cầu Crawl nào trong hàng đợi D1.
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- SUB-TAB 2: THREADS (V3.1 TIERS & LEGACY V1/V2 BACKWARD COMPATIBLE) -->
      <div v-show="marketSubTab === 'threads'" class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
        <!-- Trending Themes Snapshot from Threads -->
        <div v-if="(marketStatus.stats?.trends_snapshot?.topics || []).length" class="p-3.5 rounded-xl bg-indigo-950/35 border border-indigo-500/30 space-y-2">
          <div class="flex items-center justify-between gap-2 flex-wrap text-xs">
            <span class="font-bold text-indigo-300 uppercase tracking-wider">🔥 Chủ đề đang thịnh hành trên Threads ({{ formatIsoTime(marketStatus.stats.trends_snapshot.captured_at) || 'mới nhất' }})</span>
            <span v-if="marketStatus.stats?.latest_v3_screening?.run_id" class="text-[10px] font-mono text-slate-400">{{ marketStatus.stats.latest_v3_screening.run_id }}</span>
          </div>
          <div class="flex flex-wrap gap-1.5 text-[11px]">
            <button v-for="(tp, idx) in marketStatus.stats.trends_snapshot.topics" :key="'th-tp-'+idx"
              @click="marketFilters.search = tp.topic; loadMarketItems(0);"
              class="px-2.5 py-1 rounded-lg bg-slate-900/90 hover:bg-indigo-600/30 border border-slate-700 text-slate-200 cursor-pointer transition">
              {{ tp.topic }} <span v-if="tp.posts_label" class="text-[10px] text-indigo-300 font-semibold">• {{ tp.posts_label }}</span>
            </button>
          </div>
        </div>

        <!-- Tier Quick Filter Pills (Very Hot, Hot, Emerging, Watchlist, Unverified + Legacy V1/V2) -->
        <div class="flex flex-wrap items-center gap-1.5 text-xs">
          <button @click="marketFilters.trend_tier = 'all'; loadMarketItems(0);"
            :class="marketFilters.trend_tier === 'all' ? 'bg-indigo-600 text-white font-bold shadow' : 'bg-slate-900 text-slate-300 border border-slate-800'"
            class="px-3 py-1.5 rounded-xl cursor-pointer transition">Tất cả theo 5 phân tầng</button>
          <button @click="marketFilters.trend_tier = 'very_hot'; loadMarketItems(0);"
            :class="marketFilters.trend_tier === 'very_hot' ? 'bg-rose-600 text-white font-bold shadow' : 'bg-rose-950/40 text-rose-200 border border-rose-500/30'"
            class="px-3 py-1.5 rounded-xl cursor-pointer transition">🔥 Very Hot ({{ marketStatus.stats?.threads_by_trend_tier?.very_hot ?? 0 }})</button>
          <button @click="marketFilters.trend_tier = 'hot'; loadMarketItems(0);"
            :class="marketFilters.trend_tier === 'hot' ? 'bg-amber-600 text-white font-bold shadow' : 'bg-amber-950/40 text-amber-200 border border-amber-500/30'"
            class="px-3 py-1.5 rounded-xl cursor-pointer transition">⚡ Hot ({{ marketStatus.stats?.threads_by_trend_tier?.hot ?? 0 }})</button>
          <button @click="marketFilters.trend_tier = 'emerging'; loadMarketItems(0);"
            :class="marketFilters.trend_tier === 'emerging' ? 'bg-emerald-600 text-white font-bold shadow' : 'bg-emerald-950/40 text-emerald-200 border border-emerald-500/30'"
            class="px-3 py-1.5 rounded-xl cursor-pointer transition">🌱 Emerging ({{ marketStatus.stats?.threads_by_trend_tier?.emerging ?? 0 }})</button>
          <button @click="marketFilters.trend_tier = 'watchlist'; loadMarketItems(0);"
            :class="marketFilters.trend_tier === 'watchlist' ? 'bg-sky-600 text-white font-bold shadow' : 'bg-sky-950/40 text-sky-200 border border-sky-500/30'"
            class="px-3 py-1.5 rounded-xl cursor-pointer transition">👀 Watchlist ({{ marketStatus.stats?.threads_by_trend_tier?.watchlist ?? 0 }})</button>
          <button @click="marketFilters.trend_tier = 'unverified'; loadMarketItems(0);"
            :class="marketFilters.trend_tier === 'unverified' ? 'bg-slate-600 text-white font-bold shadow' : 'bg-slate-900 text-slate-300 border border-slate-700'"
            class="px-3 py-1.5 rounded-xl cursor-pointer transition">❓ Unverified ({{ marketStatus.stats?.threads_by_trend_tier?.unverified ?? 0 }})</button>
          <button v-if="(marketStatus.stats?.threads_by_trend_tier?.legacy ?? 0) > 0"
            @click="marketFilters.trend_tier = 'legacy'; loadMarketItems(0);"
            :class="marketFilters.trend_tier === 'legacy' ? 'bg-purple-600 text-white font-bold shadow' : 'bg-slate-900 text-slate-400 border border-slate-800'"
            class="px-3 py-1.5 rounded-xl cursor-pointer transition">📜 Lịch sử V1/V2 ({{ marketStatus.stats?.threads_by_trend_tier?.legacy ?? 0 }})</button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-12 gap-2.5 items-center">
          <div class="md:col-span-3 relative">
            <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-slate-400 text-xs"></i>
            <input v-model="marketFilters.search" @keyup.enter="loadMarketItems(0)" type="text"
              placeholder="Tìm nội dung Threads, chủ đề, @tác giả, entities..."
              class="w-full glass-input rounded-xl pl-8 pr-3 py-2 text-xs text-slate-100">
          </div>
          <select v-model="marketFilters.trend_group" @change="loadMarketItems(0)"
            class="md:col-span-2 glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
            <option value="all">Mọi nhóm trend</option>
            <option value="trend_toan_thi_truong">Trend toàn thị trường</option>
            <option value="trend_affiliate">Trend Affiliate</option>
            <option value="trend_moi_noi">Trend mới nổi</option>
          </select>
          <select v-model="marketFilters.verification_status" @change="loadMarketItems(0)"
            class="md:col-span-2 glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
            <option value="all">Mọi xác minh</option>
            <option value="cross_verified_web">Cross-verified Web</option>
            <option value="connector_verified">Connector verified</option>
            <option value="unverified">Unverified</option>
          </select>
          <select v-model="marketFilters.purchase_intent" @change="loadMarketItems(0)"
            class="md:col-span-2 glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
            <option value="all">Mọi Buyer Intent</option>
            <option value="cao">cao</option>
            <option value="trung_binh">trung_binh</option>
            <option value="thap">thap</option>
            <option value="khong_ro">khong_ro</option>
          </select>
          <select v-model="marketFilters.sort_by" @change="loadMarketItems(0)"
            class="md:col-span-2 glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
            <option value="tier">Sắp xếp: Phân tầng V3.1</option>
            <option value="engagement">Sắp xếp: Tương tác cao nhất</option>
            <option value="intent">Sắp xếp: Ý định mua cao nhất</option>
            <option value="recent">Sắp xếp: Mới nhất</option>
          </select>
          <button @click="loadMarketItems(0)" class="md:col-span-1 py-2 px-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold cursor-pointer">Lọc</button>
        </div>

        <!-- Separated Tier Sections: Very Hot / Hot / Emerging / Watchlist / Unverified (+ Historical V1/V2) -->
        <div class="space-y-4">
          <div v-for="sec in visibleThreadsTierSections" :key="'sec-'+sec.key"
            class="rounded-xl border overflow-hidden" :class="sec.borderClass">
            <div class="px-4 py-2.5 flex items-center justify-between gap-2 flex-wrap border-b border-slate-800" :class="sec.headerClass">
              <div class="flex items-center gap-2 text-xs font-bold">
                <span>{{ sec.label }}</span>
                <span class="px-2 py-0.5 rounded-full bg-black/30 font-mono text-[11px]">{{ sec.items.length }} bài</span>
              </div>
              <span class="text-[11px] opacity-85">{{ sec.subtitle }}</span>
            </div>

            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs">
                <thead class="bg-slate-900/90 text-slate-400 uppercase text-[10px] font-bold border-b border-slate-800">
                  <tr>
                    <th class="px-3 py-2.5">Tác giả & Phân loại</th>
                    <th class="px-3 py-2.5">Chủ đề Trending, Tóm tắt & URL Bằng chứng</th>
                    <th class="px-3 py-2.5">Tương tác & Xác minh</th>
                    <th class="px-3 py-2.5">Phân tích Viral & Cơ hội Affiliate</th>
                    <th class="px-3 py-2.5 text-right">Thao tác</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/70 text-slate-200">
                  <tr v-if="!sec.items.length">
                    <td colspan="5" class="px-4 py-5 text-center text-slate-500 italic">Không có bài thuộc phân tầng {{ sec.label }} khớp bộ lọc hiện tại.</td>
                  </tr>
                  <tr v-for="item in sec.items" :key="item.id" class="hover:bg-slate-900/60">
                    <td class="px-3 py-3 align-top whitespace-nowrap space-y-1">
                      <div class="font-bold text-indigo-200">{{ item.author_handle || '—' }}</div>
                      <div class="text-[10px] text-slate-400">Loại: {{ item.extra_meta?.author_type || '—' }}</div>
                      <div v-if="item.trend_group" class="inline-block text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                        {{ item.trend_group }}
                      </div>
                      <div v-else-if="item.extra_meta?.crawl_bucket" class="inline-block text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                        {{ item.extra_meta.crawl_bucket }}
                      </div>
                      <div>
                        <span class="px-1.5 py-0.5 rounded-full text-[10px] border"
                          :class="item.purchase_intent === 'cao' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' : (item.purchase_intent === 'trung_binh' ? 'bg-amber-500/20 text-amber-300 border-amber-500/40' : 'bg-slate-800 text-slate-300 border-slate-700')">
                          intent: {{ item.purchase_intent || 'null' }}
                        </span>
                      </div>
                    </td>
                    <td class="px-3 py-3 align-top max-w-md space-y-1.5">
                      <div v-if="(item.extra_meta?.theme_tags || []).length" class="flex flex-wrap gap-1">
                        <span v-for="tg in item.extra_meta.theme_tags" :key="tg"
                          class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">#{{ tg }}</span>
                      </div>
                      <p class="text-slate-100 leading-relaxed">{{ item.summary_vi || item.text_content || '—' }}</p>
                      <div v-if="item.url || item.evidence_url" class="text-[10px]">
                        <a :href="item.url || item.evidence_url" target="_blank" rel="noreferrer" class="text-sky-400 hover:underline break-all">🔗 Bằng chứng: {{ item.url || item.evidence_url }}</a>
                      </div>
                      <div v-if="(item.extra_meta?.entities_mentioned || []).length" class="flex flex-wrap gap-1">
                        <span v-for="ent in item.extra_meta.entities_mentioned.slice(0, 5)" :key="ent"
                          class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">{{ ent }}</span>
                      </div>
                    </td>
                    <td class="px-3 py-3 align-top font-mono text-[11px] whitespace-nowrap space-y-0.5">
                      <div>❤️ {{ item.likes_count !== null && item.likes_count !== undefined ? item.likes_count.toLocaleString('vi-VN') : '—' }}</div>
                      <div>💬 {{ item.comments_count !== null && item.comments_count !== undefined ? item.comments_count.toLocaleString('vi-VN') : '—' }}</div>
                      <div v-if="item.reposts_count !== null && item.reposts_count !== undefined">🔁 Reposts: {{ item.reposts_count.toLocaleString('vi-VN') }}</div>
                      <div v-if="item.quotes_count !== null && item.quotes_count !== undefined">🗣️ Quotes: {{ item.quotes_count.toLocaleString('vi-VN') }}</div>
                      <div v-if="item.views_count !== null && item.views_count !== undefined">👁️ Views: {{ formatCompactMetric(item.views_count) }}</div>
                      <div class="pt-1">
                        <span class="px-1.5 py-0.5 rounded text-[10px] font-sans border"
                          :class="item.verification_status === 'cross_verified_web' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' : (item.verification_status === 'unverified' ? 'bg-rose-500/20 text-rose-300 border-rose-500/40' : 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30')">
                          {{ item.verification_status || item.data_quality || 'legacy' }}
                        </span>
                      </div>
                      <div v-if="item.extra_meta?.viral_analysis?.velocity_note" class="text-[10px] font-sans text-emerald-300 max-w-[180px] whitespace-normal">
                        📈 {{ item.extra_meta.viral_analysis.velocity_note }}
                      </div>
                    </td>
                    <td class="px-3 py-3 align-top max-w-sm text-[11px] space-y-1">
                      <template v-if="item.extra_meta?.viral_analysis">
                        <div v-if="item.extra_meta.viral_analysis.hook"><strong class="text-amber-300">Hook:</strong> {{ item.extra_meta.viral_analysis.hook }}</div>
                        <div v-if="item.extra_meta.viral_analysis.viral_reason"><strong class="text-cyan-300">Lý do viral:</strong> {{ item.extra_meta.viral_analysis.viral_reason }}</div>
                        <div v-if="item.extra_meta.viral_analysis.content_structure"><strong class="text-indigo-300">Cấu trúc:</strong> {{ item.extra_meta.viral_analysis.content_structure }}</div>
                        <div v-if="item.extra_meta.viral_analysis.viewer_need"><strong class="text-slate-300">Nhu cầu xem:</strong> {{ item.extra_meta.viral_analysis.viewer_need }}</div>
                        <div v-if="item.extra_meta.viral_analysis.adaptation_potential"><strong class="text-purple-300">Chuyển thể:</strong> {{ item.extra_meta.viral_analysis.adaptation_potential }}</div>
                        <div v-if="item.extra_meta.viral_analysis.affiliate_angle" class="p-1.5 rounded bg-emerald-950/60 border border-emerald-500/30 text-emerald-200">
                          <strong>💰 Cơ hội Affiliate:</strong> {{ item.extra_meta.viral_analysis.affiliate_angle }}
                        </div>
                      </template>
                      <template v-else-if="item.extra_meta?.hook_analysis">
                        <div v-if="item.extra_meta.hook_analysis.hook"><strong class="text-amber-300">Hook:</strong> {{ item.extra_meta.hook_analysis.hook }}</div>
                        <div v-if="item.extra_meta.hook_analysis.insight"><strong class="text-cyan-300">Insight:</strong> {{ item.extra_meta.hook_analysis.insight }}</div>
                        <div v-if="item.extra_meta.hook_analysis.content_angle"><strong class="text-emerald-300">Angle:</strong> {{ item.extra_meta.hook_analysis.content_angle }}</div>
                      </template>
                      <div v-else class="text-slate-500 italic">—</div>
                    </td>
                    <td class="px-3 py-3 align-top text-right whitespace-nowrap space-x-1">
                      <button @click="openMarketRawModal(item.id)" class="px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-[11px] cursor-pointer">Raw</button>
                      <button @click="useMarketItemInStudio(item)" class="px-2 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-[11px] font-semibold cursor-pointer">Nạp Studio</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div class="flex items-center justify-between text-xs text-slate-400">
          <span>Hiển thị {{ marketItems.length }} / {{ marketTotal }} bản ghi Threads</span>
          <div class="flex gap-2">
            <button @click="loadMarketItems(Math.max(0, marketOffset - marketLimit))" :disabled="marketOffset === 0" class="px-3 py-1 rounded-lg bg-slate-800 disabled:opacity-40 cursor-pointer">Trước</button>
            <button @click="loadMarketItems(marketOffset + marketLimit)" :disabled="marketOffset + marketLimit >= marketTotal" class="px-3 py-1 rounded-lg bg-slate-800 disabled:opacity-40 cursor-pointer">Sau</button>
          </div>
        </div>
      </div>

      <!-- SUB-TAB 3: TIKTOK -->
      <div v-show="marketSubTab === 'tiktok'" class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-12 gap-2.5 items-center">
          <div class="md:col-span-5 relative">
            <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-slate-400 text-xs"></i>
            <input v-model="marketFilters.search" @keyup.enter="loadMarketItems(0)" type="text"
              placeholder="Tìm tài khoản TikTok, caption, hashtag..."
              class="w-full glass-input rounded-xl pl-8 pr-3 py-2 text-xs text-slate-100">
          </div>
          <select v-model="marketFilters.data_quality" @change="loadMarketItems(0)"
            class="md:col-span-3 glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
            <option value="all">Mọi chất lượng</option>
            <option value="usable">Chỉ usable</option>
            <option value="needs_verification">Cần xác minh (needs_verification)</option>
          </select>
          <select v-model="marketFilters.sort_by" @change="loadMarketItems(0)"
            class="md:col-span-3 glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
            <option value="views">Sắp xếp: Views cao nhất</option>
            <option value="engagement">Sắp xếp: Tổng tương tác</option>
            <option value="recent">Sắp xếp: Mới nhất</option>
          </select>
          <button @click="loadMarketItems(0)" class="md:col-span-1 py-2 px-3 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold cursor-pointer">Lọc</button>
        </div>

        <div class="overflow-x-auto rounded-xl border border-slate-800">
          <table class="w-full text-left text-xs">
            <thead class="bg-slate-900/90 text-slate-400 uppercase text-[10px] font-bold border-b border-slate-800">
              <tr>
                <th class="px-3 py-3">Tài khoản & Nguồn</th>
                <th class="px-3 py-3">URL Video, Caption & Hashtag</th>
                <th class="px-3 py-3">Views / Likes / Comments / Shares</th>
                <th class="px-3 py-3">Affiliate Relevance & Hook</th>
                <th class="px-3 py-3">Chất lượng</th>
                <th class="px-3 py-3 text-right">Thao tác</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-800/70 text-slate-200">
              <tr v-if="!marketItems.length">
                <td colspan="6" class="px-4 py-10 text-center text-slate-500">Không có bản ghi TikTok khớp bộ lọc.</td>
              </tr>
              <tr v-for="item in marketItems" :key="item.id" class="hover:bg-slate-900/60">
                <td class="px-3 py-3 align-top whitespace-nowrap space-y-1">
                  <div class="font-bold text-cyan-200">{{ item.author_handle || '—' }}</div>
                  <div>
                    <span class="px-2 py-0.5 rounded text-[10px] font-semibold border"
                      :class="item.extra_meta?.source_type === 'official_creative_center' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' : 'bg-amber-500/15 text-amber-300 border-amber-500/30'">
                      {{ item.extra_meta?.source_type === 'official_creative_center' ? 'Chính thức' : 'Bên thứ ba (' + (item.extra_meta?.source_type || 'N/A') + ')' }}
                    </span>
                  </div>
                  <div class="text-[10px] font-mono text-slate-400">ID: {{ item.item_id || 'null' }}</div>
                </td>
                <td class="px-3 py-3 align-top max-w-md space-y-1">
                  <p class="text-slate-100">{{ item.summary_vi || item.text_content || '—' }}</p>
                  <div class="text-[11px] text-slate-400">Caption: {{ item.extra_meta?.caption || '— (nguồn chưa cung cấp)' }}</div>
                  <div v-if="(item.extra_meta?.hashtags || []).length" class="flex flex-wrap gap-1">
                    <span v-for="ht in item.extra_meta.hashtags" :key="ht" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-cyan-300">{{ ht }}</span>
                  </div>
                  <div v-if="item.url" class="text-[10px] space-y-0.5">
                    <a :href="item.url" target="_blank" rel="noreferrer" class="text-sky-400 hover:underline break-all">🔗 {{ item.url }}</a>
                    <div v-if="item.extra_meta?.url_status && item.extra_meta.url_status !== 'da_kiem_chung'"
                      class="inline-block px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">
                      ⚠️ URL chưa xác minh ({{ item.extra_meta.url_status }})
                    </div>
                  </div>
                  <div v-else class="text-[10px] text-amber-300">⚠️ URL chưa xác minh (không có video_url)</div>
                </td>
                <td class="px-3 py-3 align-top font-mono text-[11px] whitespace-nowrap space-y-0.5">
                  <div>👁️ Views: <strong class="text-cyan-300">{{ formatCompactMetric(item.views_count) }}</strong></div>
                  <div>❤️ Likes: {{ formatCompactMetric(item.likes_count) }}</div>
                  <div>💬 Comments: {{ formatCompactMetric(item.comments_count) }}</div>
                  <div>🔁 Shares: {{ formatCompactMetric(item.shares_count) }}</div>
                </td>
                <td class="px-3 py-3 align-top max-w-xs text-[11px] space-y-1">
                  <div>
                    <span class="px-2 py-0.5 rounded-full text-[10px] border"
                      :class="(item.extra_meta?.affiliate_relevance === 'cao' || item.extra_meta?.affiliate_relevance === 'trung_binh') ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' : 'bg-slate-800 text-slate-400 border-slate-700'">
                      Affiliate: {{ item.extra_meta?.affiliate_relevance || '—' }}
                    </span>
                  </div>
                  <div v-if="item.extra_meta?.affiliate_reason" class="text-slate-300 text-[10px]">{{ item.extra_meta.affiliate_reason }}</div>
                  <div v-if="item.extra_meta?.hook_analysis?.hook" class="text-amber-300 text-[10px]">Hook: {{ item.extra_meta.hook_analysis.hook }}</div>
                  <div v-if="item.extra_meta?.hook_analysis?.structure" class="text-indigo-300 text-[10px]">Angle/Cấu trúc: {{ item.extra_meta.hook_analysis.structure }}</div>
                </td>
                <td class="px-3 py-3 align-top whitespace-nowrap space-y-1">
                  <span class="px-2 py-0.5 rounded-full text-[10px] border"
                    :class="item.data_quality === 'usable' ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' : 'bg-rose-500/20 text-rose-300 border-rose-500/40'">
                    {{ item.data_quality || 'legacy' }}
                  </span>
                </td>
                <td class="px-3 py-3 align-top text-right whitespace-nowrap space-x-1">
                  <button @click="openMarketRawModal(item.id)" class="px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-[11px] cursor-pointer">Raw</button>
                  <button @click="useMarketItemInStudio(item)" class="px-2 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-[11px] font-semibold cursor-pointer">Nạp Studio</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- SUB-TAB 4: SHOPEE -->
      <div v-show="marketSubTab === 'shopee'" class="space-y-4">
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
          <div class="grid grid-cols-1 md:grid-cols-12 gap-2.5 items-center">
            <div class="md:col-span-5 relative">
              <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-slate-400 text-xs"></i>
              <input v-model="marketFilters.search" @keyup.enter="loadMarketItems(0)" type="text"
                placeholder="Tìm tên sản phẩm Shopee, shop_id, item_id..."
                class="w-full glass-input rounded-xl pl-8 pr-3 py-2 text-xs text-slate-100">
            </div>
            <select v-model="marketFilters.data_quality" @change="loadMarketItems(0)"
              class="md:col-span-3 glass-input rounded-xl px-2.5 py-2 text-xs bg-slate-800 text-slate-200">
              <option value="all">Mọi chất lượng</option>
              <option value="usable">Chỉ usable</option>
              <option value="needs_verification">Cần xác minh (needs_verification)</option>
            </select>
            <label class="md:col-span-3 flex items-center gap-2 text-xs text-slate-300 px-2 cursor-pointer">
              <input type="checkbox" v-model="shopeeAffiliateOnly" @change="loadMarketItems(0)" class="rounded">
              <span>Chỉ sản phẩm đủ điều kiện Affiliate</span>
            </label>
            <button @click="loadMarketItems(0)" class="md:col-span-1 py-2 px-3 rounded-xl bg-orange-600 hover:bg-orange-500 text-white text-xs font-semibold cursor-pointer">Lọc</button>
          </div>

          <div class="overflow-x-auto rounded-xl border border-slate-800">
            <table class="w-full text-left text-xs">
              <thead class="bg-slate-900/90 text-slate-400 uppercase text-[10px] font-bold border-b border-slate-800">
                <tr>
                  <th class="px-3 py-3">Sản phẩm & ID</th>
                  <th class="px-3 py-3">Giá gốc / Giá ưu đãi / Trạng thái</th>
                  <th class="px-3 py-3">Lượt bán & Đánh giá</th>
                  <th class="px-3 py-3">Trạng thái Deal & Khung giờ</th>
                  <th class="px-3 py-3">Affiliate & Quality</th>
                  <th class="px-3 py-3 text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-800/70 text-slate-200">
                <tr v-if="!marketItems.length">
                  <td colspan="6" class="px-4 py-10 text-center text-slate-500">Không có sản phẩm Shopee khớp điều kiện (chưa có sản phẩm nào đủ điều kiện Affiliate).</td>
                </tr>
                <tr v-for="item in marketItems" :key="item.id" class="hover:bg-slate-900/60">
                  <td class="px-3 py-3 align-top max-w-md space-y-1">
                    <div class="font-bold text-white">{{ item.title || '—' }}</div>
                    <p class="text-[11px] text-slate-300">{{ item.summary_vi || item.text_content || '—' }}</p>
                    <div class="text-[10px] font-mono text-slate-400">shop_id: {{ item.shop_id || 'null' }} • item_id: {{ item.item_id || 'null' }}</div>
                    <div v-if="item.url" class="text-[10px]"><a :href="item.url" target="_blank" rel="noreferrer" class="text-sky-400 hover:underline">🔗 Link sản phẩm</a></div>
                    <div v-else class="text-[10px] text-amber-300">Chưa có link sản phẩm trực tiếp <span v-if="item.extra_meta?.evidence_url">(<a :href="item.extra_meta.evidence_url" target="_blank" rel="noreferrer" class="text-sky-400 hover:underline">trang nguồn</a>)</span></div>
                  </td>
                  <td class="px-3 py-3 align-top font-mono text-[11px] whitespace-nowrap space-y-0.5">
                    <div>Giá gốc: {{ (item.extra_meta?.original_price ?? item.extra_meta?.price_original) ?? '—' }}</div>
                    <div>Giá ưu đãi: <strong class="text-orange-300">{{ item.price !== null && item.price !== undefined ? item.price : '—' }}</strong></div>
                    <div class="text-[10px] text-slate-400">Trạng thái giá: {{ item.extra_meta?.price_status || '—' }}</div>
                  </td>
                  <td class="px-3 py-3 align-top font-mono text-[11px] whitespace-nowrap space-y-0.5">
                    <div>Đã bán: {{ item.sold_count !== null && item.sold_count !== undefined ? item.sold_count : '—' }}</div>
                    <div>Đánh giá: {{ item.rating !== null && item.rating !== undefined ? item.rating : '—' }}</div>
                    <div v-if="item.extra_meta?.selection_signal" class="text-[10px] text-emerald-300">Tín hiệu: {{ item.extra_meta.selection_signal }}</div>
                  </td>
                  <td class="px-3 py-3 align-top text-[11px] whitespace-nowrap space-y-1">
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold border"
                      :class="item.extra_meta?.deal_status === 'sap_dien_ra' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' : 'bg-amber-500/20 text-amber-300 border-amber-500/40'">
                      {{ item.extra_meta?.deal_status === 'sap_dien_ra' ? 'Sắp diễn ra' : (item.extra_meta?.deal_status === 'dang_dien_ra' ? 'Đang diễn ra' : '—') }}
                    </span>
                    <div class="text-[10px] font-mono text-slate-400">Bắt đầu: {{ formatIsoTime(item.extra_meta?.frame_start_at) || '—' }}</div>
                    <div class="text-[10px] font-mono text-slate-400">Kết thúc: {{ formatIsoTime(item.extra_meta?.frame_end_at) || '—' }}</div>
                  </td>
                  <td class="px-3 py-3 align-top whitespace-nowrap space-y-1">
                    <div v-if="item.extra_meta?.affiliate_eligible === true && item.data_quality !== 'needs_verification'"
                      class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 text-[10px] font-bold">✅ Đủ điều kiện Affiliate</div>
                    <div v-else class="px-2 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30 text-[10px]">
                      ⛔ Chưa đủ điều kiện Affiliate
                    </div>
                    <div class="text-[10px] text-slate-400">{{ item.data_quality || 'legacy' }}</div>
                  </td>
                  <td class="px-3 py-3 align-top text-right whitespace-nowrap space-x-1">
                    <button @click="openMarketRawModal(item.id)" class="px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-[11px] cursor-pointer">Raw</button>
                    <button @click="useMarketItemInStudio(item)" class="px-2 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-[11px] font-semibold cursor-pointer">Nạp Studio</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Upcoming Vouchers Panel -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-2">
          <h3 class="text-xs font-bold uppercase tracking-wider text-orange-400 flex items-center gap-2">
            <i class="fa-solid fa-ticket"></i> Upcoming Vouchers (Shopee)
          </h3>
          <div v-if="(marketStatus.stats?.upcoming_vouchers || []).length" class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
            <div v-for="(v, i) in marketStatus.stats.upcoming_vouchers" :key="'uv-'+i"
              class="p-3 rounded-xl bg-slate-900/75 border border-slate-800 space-y-0.5">
              <div class="font-bold text-orange-300">{{ v.code_or_name }}</div>
              <div class="text-slate-300">{{ v.conditions }}</div>
              <div class="text-[10px] text-slate-400 font-mono">{{ v.valid_from }} → {{ v.valid_to }}</div>
            </div>
          </div>
          <div v-else class="text-xs text-slate-400 italic">
            Chưa có voucher sắp tung trong dữ liệu Shopee hiện tại (trang công khai chưa cung cấp — chờ phiên đăng nhập Shopee).
          </div>
        </div>
      </div>

      <!-- SUB-TAB 5: BÁO CÁO -->
      <div v-show="marketSubTab === 'reports'" class="glass-panel rounded-2xl p-6 shadow-xl space-y-4">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3.5">
          <div>
            <h3 class="text-sm font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-2 flex-wrap">
              <i class="fa-solid fa-file-chart-column"></i>
              <span>{{ marketReport?.latest_muse_report?.title || marketReport?.title || 'Báo Cáo Phân Tích Thị Trường' }}</span>
              <span v-if="marketReport?.schema_version === 'v3.1'"
                class="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/40">THREADS V3.1</span>
              <span v-if="marketReport?.is_corrected || marketReport?.latest_muse_report?.is_corrected"
                class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">CORRECTED V2</span>
            </h3>
            <p class="text-xs text-slate-400 mt-0.5">
              {{ marketReport?.source_label || 'Ưu tiên báo cáo Muse mới nhất' }}
              <span v-if="marketReport?.run_status">
                • Trạng thái: <strong :class="marketReport?.is_source_error ? 'text-rose-300' : 'text-emerald-300'">{{ marketReport.run_status }}</strong>
              </span>
              <span v-if="marketReport?.technical_status || marketReport?.data_quality_status">
                • Kỹ thuật: <strong class="text-cyan-300">{{ marketReport.technical_status || '—' }}</strong>
                • Chất lượng dữ liệu: <strong class="text-amber-300">{{ marketReport.data_quality_status || '—' }}</strong>
              </span>
            </p>
          </div>
          <div class="flex items-center gap-2">
            <select v-model="marketReportPlatform" @change="loadMarketReport"
              class="glass-input rounded-xl px-3 py-1.5 text-xs bg-slate-800 text-slate-200">
              <option value="all">Tất cả nguồn</option>
              <option value="threads">Threads</option>
              <option value="tiktok">TikTok</option>
              <option value="shopee">Shopee</option>
            </select>
            <button @click="loadMarketReport" class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs font-semibold cursor-pointer">Làm mới</button>
            <button @click="downloadMarketReportJson" v-if="marketReport" class="px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold cursor-pointer">
              <i class="fa-solid fa-download mr-1"></i> Tải JSON
            </button>
          </div>
        </div>

        <div v-if="marketReport?.run_status_label_vi" class="p-3 rounded-xl border text-xs"
          :class="marketReport?.is_source_error ? 'bg-rose-950/50 border-rose-500/40 text-rose-200' : (marketReport?.is_empty_market_valid ? 'bg-slate-900 border-slate-700 text-slate-300' : 'bg-emerald-950/30 border-emerald-500/30 text-emerald-200')">
          <strong>{{ marketReport.run_status_label_vi }}</strong>
          <span v-if="marketReport?.is_source_error"> — Không dùng kết quả này để kết luận "thị trường không có trend".</span>
        </div>

        <div v-if="marketReport?.telegram_summary" class="p-4 rounded-xl bg-indigo-950/35 border border-indigo-500/30 text-xs text-slate-100 whitespace-pre-line leading-relaxed">
          {{ marketReport.telegram_summary }}
        </div>

        <!-- V3.1 Screening & Tier Distribution -->
        <div v-if="marketReport?.screening || marketReport?.tier_distribution" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          <div v-if="marketReport?.screening" class="p-4 rounded-xl bg-slate-900/75 border border-slate-800 space-y-1.5">
            <div class="font-bold text-indigo-300 uppercase text-[11px]">Sàng lọc dữ liệu V3.1</div>
            <div class="font-mono text-slate-200">
              Quét: <strong>{{ marketReport.screening.scanned_count ?? '—' }}</strong> •
              Đạt chuẩn: <strong class="text-emerald-300">{{ marketReport.screening.qualified_count ?? '—' }}</strong> •
              Loại: <strong class="text-rose-300">{{ marketReport.screening.rejected_count ?? '—' }}</strong> •
              Xác minh chéo Web: <strong class="text-cyan-300">{{ marketReport.screening.cross_verified_count ?? '—' }}</strong>
            </div>
            <div v-if="marketReport.screening.sources" class="text-[11px] text-slate-400">Nguồn: {{ marketReport.screening.sources }}</div>
            <div v-if="Object.keys(marketReport.screening.rejected_reasons || {}).length" class="flex flex-wrap gap-1.5 pt-1">
              <span v-for="(cnt, rsn) in marketReport.screening.rejected_reasons" :key="'rr-'+rsn"
                class="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">{{ rsn }}: {{ cnt }}</span>
            </div>
          </div>
          <div v-if="marketReport?.tier_distribution" class="p-4 rounded-xl bg-slate-900/75 border border-slate-800 space-y-2">
            <div class="font-bold text-amber-300 uppercase text-[11px]">Phân bố 5 phân tầng & 3 nhóm Trend</div>
            <div class="flex flex-wrap gap-1.5 text-[11px]">
              <span class="px-2 py-0.5 rounded bg-rose-500/20 text-rose-200 border border-rose-500/30">🔥 Very Hot: {{ marketReport.tier_distribution.tiers?.very_hot ?? 0 }}</span>
              <span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-200 border border-amber-500/30">⚡ Hot: {{ marketReport.tier_distribution.tiers?.hot ?? 0 }}</span>
              <span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-200 border border-emerald-500/30">🌱 Emerging: {{ marketReport.tier_distribution.tiers?.emerging ?? 0 }}</span>
              <span class="px-2 py-0.5 rounded bg-sky-500/20 text-sky-200 border border-sky-500/30">👀 Watchlist: {{ marketReport.tier_distribution.tiers?.watchlist ?? 0 }}</span>
              <span class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">❓ Unverified: {{ marketReport.tier_distribution.tiers?.unverified ?? 0 }}</span>
            </div>
            <div v-if="Object.keys(marketReport.tier_distribution.groups || {}).length" class="flex flex-wrap gap-1.5 text-[10px] text-slate-300">
              <span v-for="(cnt, gKey) in marketReport.tier_distribution.groups" :key="'tg-'+gKey"
                class="px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-200 border border-indigo-500/30">{{ gKey }}: {{ cnt }}</span>
            </div>
          </div>
        </div>

        <div v-if="marketReport && Object.keys(marketReport.platform_results || {}).length" class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
          <div v-for="(pr, pKey) in marketReport.platform_results" :key="'pr-'+pKey"
            class="p-3.5 rounded-xl bg-slate-900/75 border border-slate-800 space-y-1.5">
            <div class="flex items-center justify-between font-bold uppercase text-white">
              <span>{{ pKey }}</span>
              <span class="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-emerald-300">{{ pr.business_usable_records ?? 0 }}/{{ pr.records ?? 0 }} usable</span>
            </div>
            <div class="text-[11px] text-slate-400">Kỹ thuật: {{ pr.technical_status || pr.status }} • Chất lượng: {{ pr.data_quality_status || '—' }}</div>
            <ul v-if="(pr.remaining_issues || []).length" class="text-[10px] text-amber-300 list-disc pl-4 space-y-0.5">
              <li v-for="(iss, idx) in pr.remaining_issues.slice(0, 3)" :key="'ri-'+idx">{{ iss }}</li>
            </ul>
          </div>
        </div>

        <div v-if="marketReport" class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs">
          <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-2">
            <div class="font-bold text-amber-300 uppercase text-[11px]">1. Xu hướng nổi bật (Đủ điều kiện Trend)</div>
            <ul class="space-y-2 text-slate-200" v-if="(marketReport.trending_highlights || []).length">
              <li v-for="(t, i) in marketReport.trending_highlights.slice(0, 6)" :key="'tr-'+i" class="space-y-0.5">
                <div class="font-semibold">• {{ t.title || t }}</div>
                <div v-if="t.why_hot" class="text-[11px] text-slate-300 pl-3">{{ t.why_hot }}</div>
                <div v-if="t.evidence" class="text-[10px] text-slate-400 pl-3">{{ t.evidence }}</div>
              </li>
            </ul>
            <div v-else class="text-slate-500 italic">Chưa có xu hướng nổi bật từ dữ liệu hiện tại.</div>
            <div v-if="(marketReport.top_viral || []).length" class="pt-2 border-t border-slate-800 space-y-1.5">
              <div class="font-bold text-rose-300 uppercase text-[10px]">Top bài Viral (ghi rõ mức xác minh)</div>
              <div v-for="(tv, i) in marketReport.top_viral.slice(0, 5)" :key="'tv-'+i" class="p-2 rounded bg-slate-950/60 border border-slate-800/80 space-y-0.5">
                <div class="font-semibold text-white">{{ tv.post }}</div>
                <div class="text-[10px] font-mono text-slate-300">❤️ {{ tv.likes ?? '—' }} • 💬 {{ tv.comments ?? '—' }} • 🔁 {{ tv.reposts ?? '—' }} • 🗣️ {{ tv.quotes ?? '—' }}</div>
                <div class="text-[10px] text-cyan-300">{{ tv.verification_status }}</div>
                <a v-if="tv.url" :href="tv.url" target="_blank" rel="noreferrer" class="text-[10px] text-sky-400 hover:underline break-all">🔗 {{ tv.url }}</a>
              </div>
            </div>
          </div>

          <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-2">
            <div class="font-bold text-rose-300 uppercase text-[11px]">2. Nhu cầu & Vấn đề người tiêu dùng</div>
            <ul class="space-y-1.5 text-slate-200" v-if="(marketReport.consumer_needs_problems || []).length">
              <li v-for="(n, i) in marketReport.consumer_needs_problems.slice(0, 6)" :key="'nd-'+i">
                • {{ n.title || n }}
                <a v-if="(n.evidence_urls || []).length" :href="n.evidence_urls[0]" target="_blank" rel="noreferrer" class="ml-1 text-[10px] text-sky-400 hover:underline">[link]</a>
              </li>
            </ul>
            <div v-else class="text-slate-500 italic">Chưa phát hiện nhóm vấn đề/phàn nàn nổi bật.</div>
            <div v-if="marketReport.emerging_and_signals" class="pt-2 border-t border-slate-800 space-y-1.5">
              <div class="font-bold text-emerald-300 uppercase text-[10px]">Mới nổi & Tín hiệu đơn lẻ (chưa phải Trend)</div>
              <div v-for="(sig, i) in (marketReport.emerging_and_signals.signals || []).slice(0, 3)" :key="'sg-'+i" class="p-2 rounded bg-emerald-950/40 border border-emerald-500/30 space-y-0.5">
                <div class="font-semibold text-emerald-200">[TÍN HIỆU] {{ sig.signal || sig.topic }}</div>
                <div class="text-[10px] text-slate-300">{{ sig.evidence }}</div>
              </div>
              <div v-for="(em, i) in (marketReport.emerging_and_signals.emerging || []).slice(0, 3)" :key="'em-'+i" class="p-2 rounded bg-slate-950/60 border border-slate-800 space-y-0.5">
                <div class="font-semibold text-sky-200">[MỚI NỔI] {{ em.topic }}</div>
                <div class="text-[10px] text-slate-400">{{ em.evidence }}</div>
              </div>
            </div>
          </div>

          <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-2">
            <div class="font-bold text-emerald-300 uppercase text-[11px]">3. Sản phẩm Affiliate đủ điều kiện & Bằng chứng</div>
            <div class="space-y-2" v-if="(marketReport.affiliate_opportunities || []).length">
              <div v-for="(a, i) in marketReport.affiliate_opportunities.slice(0, 6)" :key="'af-'+i"
                class="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800/80 space-y-1">
                <div class="font-semibold text-white flex items-center justify-between gap-2">
                  <span>{{ a.product || a.brand_or_topic }}</span>
                  <span v-if="a.priority" class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 shrink-0">{{ a.priority }}</span>
                </div>
                <div v-if="a.evidence_summary" class="text-[11px] text-slate-400">{{ a.evidence_summary }}</div>
                <a v-if="(a.evidence_urls || []).length" :href="a.evidence_urls[0]" target="_blank" rel="noreferrer" class="text-[10px] text-sky-400 hover:underline">{{ a.evidence_urls[0] }}</a>
              </div>
            </div>
            <div v-else class="text-slate-500 italic">Chưa có sản phẩm Affiliate đủ điều kiện.</div>
            <div v-if="(marketReport.priority_actions || []).length" class="pt-2 border-t border-slate-800 space-y-1">
              <div class="font-bold text-amber-300 uppercase text-[10px]">Hành động ưu tiên hôm nay</div>
              <ul class="space-y-1 text-[11px] text-slate-200">
                <li v-for="(act, i) in marketReport.priority_actions" :key="'pa-'+i">{{ act }}</li>
              </ul>
            </div>
          </div>

          <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-3">
            <div class="space-y-1.5">
              <div class="font-bold text-indigo-300 uppercase text-[11px]">4. Gợi ý Text Content & Short Video</div>
              <div v-for="(txt, i) in (marketReport.content_suggestions?.text_posts || []).slice(0, 4)" :key="'tp-'+i"
                class="flex items-center justify-between gap-2 p-2 rounded-lg bg-slate-950/50 border border-slate-800">
                <span class="text-slate-200 line-clamp-2">✍️ {{ txt }}</span>
                <button @click="topicInput = txt; currentTab = 'studio';" class="px-2 py-1 rounded bg-indigo-600 text-white text-[10px] font-semibold shrink-0 cursor-pointer">Tạo bài</button>
              </div>
              <div v-for="(vid, i) in (marketReport.content_suggestions?.short_videos || []).slice(0, 4)" :key="'sv-'+i"
                class="flex items-center justify-between gap-2 p-2 rounded-lg bg-slate-950/50 border border-slate-800">
                <span class="text-slate-200 line-clamp-2">🎬 {{ vid }}</span>
                <button @click="topicInput = vid; currentTab = 'studio';" class="px-2 py-1 rounded bg-pink-600 text-white text-[10px] font-semibold shrink-0 cursor-pointer">Tạo Video</button>
              </div>
            </div>
            <div class="pt-2 border-t border-slate-800 space-y-1">
              <div class="font-bold text-cyan-300 uppercase text-[11px]">5. Link nguồn tiêu biểu</div>
              <div class="flex flex-wrap gap-2">
                <a v-for="(lnk, i) in (marketReport.source_links || []).slice(0, 8)" :key="'sl-'+i"
                  :href="lnk.url" target="_blank" rel="noreferrer"
                  class="text-[10px] px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-sky-300 border border-slate-700">
                  {{ lnk.platform || 'src' }}:{{ lnk.post_id || (i+1) }} ↗
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Modal Raw JSON Market Item -->
    <div v-if="selectedMarketDetail" :style="{ display: selectedMarketDetail ? 'flex' : 'none' }" style="display: none;"
      class="fixed inset-0 bg-black/85 backdrop-blur-md items-center justify-center z-50 p-4">
      <div class="glass-panel max-w-4xl w-full rounded-2xl p-6 shadow-2xl border border-indigo-500/40 space-y-4 max-h-[90vh] flex flex-col">
        <div class="flex items-center justify-between border-b border-slate-800 pb-3">
          <div>
            <h3 class="text-sm font-bold text-white">Chi tiết bản ghi: {{ selectedMarketDetail.dedup_key }}</h3>
            <p class="text-xs text-slate-400">File gốc: {{ selectedMarketDetail.source_file_name }} • Schema: {{ selectedMarketDetail.schema_version }}</p>
          </div>
          <button @click="selectedMarketDetail = null" class="text-slate-400 hover:text-white p-1"><i class="fa-solid fa-xmark text-lg"></i></button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 overflow-y-auto flex-1">
          <div>
            <div class="text-xs font-bold text-emerald-400 mb-1.5">Dữ liệu đã chuẩn hóa</div>
            <pre class="bg-black/70 p-3 rounded-xl text-[11px] font-mono text-slate-200 overflow-auto max-h-[55vh] border border-slate-800">{{ JSON.stringify(selectedMarketDetail, null, 2) }}</pre>
          </div>
          <div>
            <div class="text-xs font-bold text-amber-400 mb-1.5">Dữ liệu gốc (Raw JSON)</div>
            <pre class="bg-black/70 p-3 rounded-xl text-[11px] font-mono text-slate-300 overflow-auto max-h-[55vh] border border-slate-800">{{ JSON.stringify(selectedMarketDetail.raw_payload, null, 2) }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- Modal Chọn Avatar Người Thật Trực Quan -->
    <div v-if="showAvatarModal" :style="{ display: showAvatarModal ? 'flex' : 'none' }" style="display: none;"
      class="fixed inset-0 bg-black/85 backdrop-blur-md items-center justify-center z-50 p-4">
      <div class="glass-panel max-w-2xl w-full rounded-2xl p-6 shadow-2xl border border-indigo-500/40 space-y-4 max-h-[90vh] flex flex-col">
        <div class="flex items-center justify-between border-b border-slate-800 pb-3">
          <div>
            <h3 class="text-base font-bold text-white flex items-center gap-2">
              <i class="fa-solid fa-image-portrait text-indigo-400"></i>
              <span>Kho Avatar Người Thật Sắc Nét HD</span>
            </h3>
            <p class="text-xs text-slate-400 mt-0.5">
              Đang chọn cho: <strong class="text-indigo-300">{{ (avatarTarget && avatarTarget.type === 'author') ? 'Tác giả bài đăng' : ('Bình luận #' + (((avatarTarget && avatarTarget.index) || 0) + 1)) }}</strong>
            </p>
          </div>
          <button @click="showAvatarModal = false" class="text-slate-400 hover:text-white p-1 rounded-lg">
            <i class="fa-solid fa-xmark text-lg"></i>
          </button>
        </div>

        <!-- Filter Tabs -->
        <div class="flex items-center gap-2 text-xs">
          <button @click="avatarTabFilter = 'all'" :class="avatarTabFilter === 'all' ? 'bg-indigo-600 text-white font-bold shadow-md' : 'bg-slate-800 text-slate-400 hover:text-slate-200'" class="px-3.5 py-1.5 rounded-lg transition">
            Tất Cả (12)
          </button>
          <button @click="avatarTabFilter = 'cong_so'" :class="avatarTabFilter === 'cong_so' ? 'bg-indigo-600 text-white font-bold shadow-md' : 'bg-slate-800 text-slate-400 hover:text-slate-200'" class="px-3.5 py-1.5 rounded-lg transition flex items-center gap-1.5">
            <i class="fa-solid fa-briefcase"></i> Công Sở / Văn Phòng (6)
          </button>
          <button @click="avatarTabFilter = 'sinh_vien'" :class="avatarTabFilter === 'sinh_vien' ? 'bg-indigo-600 text-white font-bold shadow-md' : 'bg-slate-800 text-slate-400 hover:text-slate-200'" class="px-3.5 py-1.5 rounded-lg transition flex items-center gap-1.5">
            <i class="fa-solid fa-graduation-cap"></i> Sinh Viên / Gen Z (6)
          </button>
        </div>

        <!-- Avatar Grid -->
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 overflow-y-auto p-1 flex-1">
          <div v-for="av in filteredAvatars" :key="av.id" @click="selectAvatar(av)"
            class="p-3 rounded-xl bg-slate-800/60 hover:bg-indigo-950/70 border border-slate-700/60 hover:border-indigo-400 transition cursor-pointer flex flex-col items-center text-center group">
            <img :src="av.url" :alt="av.label"
              class="w-16 h-16 rounded-full object-cover border-2 border-slate-700 group-hover:border-indigo-400 group-hover:scale-105 transition shadow-lg">
            <div class="text-xs font-semibold text-slate-200 mt-2.5 group-hover:text-indigo-300">{{ av.label }}</div>
            <div class="text-[10px] text-slate-400 mt-0.5">{{ av.sub }}</div>
          </div>
        </div>

        <div class="pt-2 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-500">
          <span>Click vào ảnh bất kỳ để chọn và tự động cập nhật thẻ</span>
          <button @click="showAvatarModal = false" class="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300">Đóng</button>
        </div>
      </div>
    </div>

    <!-- Toast Notification (Floating, Non-intrusive) -->
    <div v-if="toast.show" 
         class="fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-2xl backdrop-blur-md border flex items-center gap-3 transition-all transform animate-pulse"
         :class="toast.type === 'error' ? 'bg-rose-950/90 border-rose-500/50 text-rose-100 shadow-rose-900/40' : 'bg-slate-900/95 border-emerald-500/50 text-emerald-100 shadow-emerald-900/40'">
      <i :class="toast.type === 'error' ? 'fa-solid fa-circle-exclamation text-rose-400' : 'fa-solid fa-circle-check text-emerald-400'"></i>
      <span class="text-xs font-semibold">{{ toast.message }}</span>
    </div>

  </div>

  <script>
    const { createApp } = Vue;

    createApp({
      data() {
        return {
          toast: { show: false, message: '', type: 'success' },
          currentTab: 'studio',
          topicInput: '',
          storyModeInput: 'auto',
          commentCountInput: 'auto',
          authorVoiceInput: 'nam_hai',
          isGeneratingScript: false,
          isCrawlingTrending: false,
          previewMode: 'cards',
          cardTimestamp: Date.now(),
          showAvatarModal: false,
          avatarTarget: { type: 'author', index: 0 },
          avatarGallery: [
            { id: "female_office_1.jpg", label: "Chị Trưởng Phòng", sub: "Nữ Công Sở #1", category: "cong_so", url: "/api/avatar_file/female_office_1.jpg" },
            { id: "female_office_2.jpg", label: "Nữ Văn Phòng", sub: "Nữ Công Sở #2", category: "cong_so", url: "/api/avatar_file/female_office_2.jpg" },
            { id: "female_office_3.jpg", label: "Nữ Chuyên Viên", sub: "Nữ Công Sở #3", category: "cong_so", url: "/api/avatar_file/female_office_3.jpg" },
            { id: "male_office_1.jpg", label: "Anh Quản Lý", sub: "Nam Công Sở #1", category: "cong_so", url: "/api/avatar_file/male_office_1.jpg" },
            { id: "male_office_2.jpg", label: "Nam Nhân Viên IT", sub: "Nam Công Sở #2", category: "cong_so", url: "/api/avatar_file/male_office_2.jpg" },
            { id: "male_office_3.jpg", label: "Nam Chuyên Viên Dự Án", sub: "Nam Công Sở #3", category: "cong_so", url: "/api/avatar_file/male_office_3.jpg" },
            { id: "female_student_1.jpg", label: "Bạn Nữ Gen Z", sub: "Sinh Viên #1", category: "sinh_vien", url: "/api/avatar_file/female_student_1.jpg" },
            { id: "female_student_2.jpg", label: "Nữ Sinh Viên", sub: "Sinh Viên #2", category: "sinh_vien", url: "/api/avatar_file/female_student_2.jpg" },
            { id: "female_student_3.jpg", label: "Nữ Gen Z Năng Động", sub: "Sinh Viên #3", category: "sinh_vien", url: "/api/avatar_file/female_student_3.jpg" },
            { id: "male_student_1.jpg", label: "Bạn Nam Gen Z", sub: "Sinh Viên #1", category: "sinh_vien", url: "/api/avatar_file/male_student_1.jpg" },
            { id: "male_student_2.jpg", label: "Nam Sinh Viên", sub: "Sinh Viên #2", category: "sinh_vien", url: "/api/avatar_file/male_student_2.jpg" },
            { id: "male_student_3.jpg", label: "Nam Gen Z Trẻ Trung", sub: "Sinh Viên #3", category: "sinh_vien", url: "/api/avatar_file/male_student_3.jpg" }
          ],
          avatarTabFilter: 'all',
          trendingTopicsInfo: {
            date: '',
            date_display: '',
            updated_at: '',
            is_cached: false
          },
          selectedDate: new Date().toISOString().split('T')[0],
          topicModeTab: 'true_story',
          isRefreshingTrends: false,
          viralTopics: [],
          post: {
            platform: 'threads',
            username: '',
            content: '',
            likes: 0,
            is_verified: true,
            avatar_path: '',
            avatar_url: '',
            emotional_arc: '',
            illustration_prompt: '',
            illustration_path: '',
            illustration_caption: '',
            illustration_category: 'general_life',
            has_visual_proof: false,
            visual_proof_placement: 'none',
            sentence_reveal_mode: 'standard_full',
            satisfying_category: 'auto',
            recommended_theme: 'light',
            story_context_type: 'general_social',
            cta_question: '',
            pacing_tempo: 'normal',
            enable_illustration: false,
            documentary_scenes: [],
            story_badge: '',
            video_title: '',
            center_video_custom_path: '',
            comments: []
          },
          customImageQuery: '',
          isSearchingImage: false,
          settings: {
            platform: 'threads',
            layout_preset: 'center',
            bg_mode: 'auto',
            card_theme: 'auto',
            voice: 'adam',
            rate: '+16%',
            bgm_choice: 'gentle_piano_satie.mp3',
            bgm_volume: 25
          },
          bgmList: [
            { id: 'gentle_piano_satie.mp3', name: '🎹 Erik Satie: Gymnopedie No.1 (Dịu êm, sâu lắng, chuẩn tâm sự)' },
            { id: 'gentle_clair_de_lune.mp3', name: '🌙 Debussy: Clair De Lune (Thơ mộng, nhẹ nhàng, du dương)' },
            { id: 'gentle_chopin_nocturne.mp3', name: '🎼 Chopin: Nocturne Op 9 No 2 (Ấm áp, êm ái, thư giãn)' },
            { id: 'lofi_chill.mp3', name: '☕ Lofi Chill Cafe (Dịu êm, thư giãn nhẹ)' },
            { id: 'random', name: '🎲 Ngẫu nhiên (Tự động chọn nhạc nhẹ)' },
            { id: 'none', name: '🔇 Tắt nhạc nền (Chỉ giọng đọc & SFX)' }
          ],
          pipelineState: {
            is_running: false,
            current_step_id: 'idle',
            current_step_title: 'Sẵn sàng',
            progress_percent: 0,
            message: 'Hệ thống sẵn sàng',
            steps: [],
            emotional_arc_data: [],
            logs: []
          },
          directorConfig: {
            ai_director: {},
            audio_mastering: {},
            emotional_pacing: {},
            voice_casting: {},
            visual_layout: {
              enable_center_satisfying_video: false,
              center_satisfying_category: 'random',
              fps: 60,
              video_crf: 18,
              enable_slide_up_animation: true,
              enforce_min_duration_60s: false,
              transition_style: 'dynamic_conversational',
              enable_continuous_anchor_meme: false
            }
          },
          latestVideoUrl: '',
          latestCaption: '',
          geminiKeys: [],
          newGeminiKey: '',
          newGeminiLabel: '',
          pollTimer: null,
          marketStatus: { is_syncing: false, stats: {}, cloudflare: {}, remote_poller: {}, filter_options: {}, recent_runs: [] },
          marketItems: [],
          marketTotal: 0,
          marketOffset: 0,
          marketLimit: 25,
          marketConfigInterval: 300,
          marketFilters: { platform: 'all', content_group: 'all', purchase_intent: 'all', data_quality: 'all', trend_tier: 'all', trend_group: 'all', verification_status: 'all', sort_by: 'recent', search: '' },
          marketSubTab: 'overview',
          shopeeAffiliateOnly: false,
          selectedMarketDetail: null,
          isTestingConnection: false,
          marketConnectionNotice: null,
          crawlRequestForm: { platform: 'all', requested_count: 50, keywords: '' },
          crawlRequests: [],
          crawlScheduleProfile: 'production_6h',
          crawlScheduleInfo: null,
          marketReport: null,
          marketReportPlatform: 'all'
        }
      },
      computed: {
        filteredAvatars() {
          if (this.avatarTabFilter === 'all') return this.avatarGallery;
          return this.avatarGallery.filter(a => a.category === this.avatarTabFilter);
        },
        isRenderingVideo() {
          if (!this.pipelineState) return false;
          const runningSteps = ['tts', 'audio', 'composite', 'export'];
          return Boolean(this.pipelineState.is_running && runningSteps.includes(this.pipelineState.current_step_id));
        },
        visibleThreadsTierSections() {
          const defs = [
            {
              key: 'very_hot',
              label: '🔥 VERY HOT',
              subtitle: 'likes >= 1.000 hoặc views >= 50.000 (đã xác minh)',
              borderClass: 'border-rose-500/40',
              headerClass: 'bg-rose-950/50 text-rose-200',
            },
            {
              key: 'hot',
              label: '⚡ HOT',
              subtitle: 'likes >= 300 hoặc views >= 10.000 (đã xác minh)',
              borderClass: 'border-amber-500/40',
              headerClass: 'bg-amber-950/50 text-amber-200',
            },
            {
              key: 'emerging',
              label: '🌱 EMERGING',
              subtitle: 'Mới nổi <= 24h hoặc chủ đề trend chính thức (>= 20 likes / >= 5 cmt)',
              borderClass: 'border-emerald-500/40',
              headerClass: 'bg-emerald-950/45 text-emerald-200',
            },
            {
              key: 'watchlist',
              label: '👀 WATCHLIST',
              subtitle: 'Chưa đạt ngưỡng nhưng hook mạnh / mật độ bình luận cao',
              borderClass: 'border-sky-500/40',
              headerClass: 'bg-sky-950/45 text-sky-200',
            },
            {
              key: 'unverified',
              label: '❓ UNVERIFIED',
              subtitle: 'Chưa xác minh được — không tính vào đạt chuẩn hoặc viral/Affiliate',
              borderClass: 'border-slate-700',
              headerClass: 'bg-slate-900 text-slate-300',
            },
          ];
          const byTier = { very_hot: [], hot: [], emerging: [], watchlist: [], unverified: [], legacy: [] };
          for (const it of (this.marketItems || [])) {
            const vs = String(it.verification_status || '').toLowerCase();
            const tt = String(it.trend_tier || '').toLowerCase();
            if (vs === 'unverified' || tt === 'unverified') {
              byTier.unverified.push(it);
            } else if (tt && byTier[tt] !== undefined) {
              byTier[tt].push(it);
            } else {
              byTier.legacy.push(it);
            }
          }
          const activeFilter = String(this.marketFilters?.trend_tier || 'all').toLowerCase();
          const sections = defs
            .filter(d => activeFilter === 'all' || activeFilter === d.key)
            .map(d => ({ ...d, items: byTier[d.key] || [] }));
          if ((activeFilter === 'all' && byTier.legacy.length > 0) || activeFilter === 'legacy') {
            sections.push({
              key: 'legacy',
              label: '📜 DỮ LIỆU LỊCH SỬ V1/V2',
              subtitle: 'Bản ghi lịch sử giữ nguyên tương thích ngược (threads_crawl_*)',
              borderClass: 'border-purple-500/35',
              headerClass: 'bg-purple-950/35 text-purple-200',
              items: byTier.legacy,
            });
          }
          return sections;
        }
      },
      mounted() {
        this.loadSettings();
        this.loadBgmList();
        this.loadCurrentScript();
        this.loadLatestVideo();
        this.loadDailyTrendingTopics(false);
        this.loadDirectorConfig();
        this.loadGeminiKeys();
        this.loadAvatarGallery();
        this.loadMarketDashboard();
        this.startStatusPolling();
      },
      beforeUnmount() {
        if (this.pollTimer) clearInterval(this.pollTimer);
      },
      methods: {
        async loadDailyTrendingTopics(force = false) {
          this.isRefreshingTrends = true;
          try {
            const d = this.selectedDate || '';
            const m = this.topicModeTab || 'true_story';
            const res = await fetch(`/api/trending_topics?date=${d}&mode=${m}&force=${force}`);
            const data = await res.json();
            if (data.topics && data.topics.length > 0) {
              this.viralTopics = data.topics;
            }
            this.trendingTopicsInfo = {
              date: data.date || '',
              date_display: data.date_display || '',
              mode: data.mode || m,
              updated_at: data.updated_at || '',
              is_cached: data.is_cached || false
            };
          } catch(e) {
            console.error('Lỗi tải trending topics:', e);
          } finally {
            this.isRefreshingTrends = false;
          }
        },
        selectDailyTopic(topicText) {
          this.topicInput = topicText;
          if (this.topicModeTab === 'true_story') {
            this.storyModeInput = 'true_story';
            this.setPlatform('documentary');
          } else {
            this.storyModeInput = 'auto';
          }
        },
        startStatusPolling() {
          let marketTick = 0;
          this.pollTimer = setInterval(async () => {
            try {
              const res = await fetch('/api/pipeline_status');
              const data = await res.json();
              this.pipelineState = data;

              // Tự động load video khi vừa render xong
              if (data.current_step_id === 'done' && data.video_filename && !this.latestVideoUrl.includes(data.video_filename)) {
                this.latestVideoUrl = '/api/video/' + data.video_filename;
                this.latestCaption = data.caption;
                this.previewMode = 'video';
              }
            } catch(e) {}

            marketTick += 1;
            if ((this.currentTab === 'market' || this.marketStatus.is_syncing) && marketTick % 5 === 0) {
              const prevTotal = this.marketStatus.stats?.total_items || 0;
              const wasSyncing = Boolean(this.marketStatus.is_syncing);
              await this.loadMarketStatus();
              const newTotal = this.marketStatus.stats?.total_items || 0;
              if (newTotal !== prevTotal || (wasSyncing && !this.marketStatus.is_syncing)) {
                this.loadMarketItems(this.marketOffset);
              }
            }
          }, 800);
        },
        setPlatform(plat) {
          this.settings.platform = plat;
          if (!this.post) {
            this.post = { platform: plat };
          }
          if (plat !== 'auto') {
            this.post.platform = plat;
            if (plat === 'documentary') {
              if (!this.post.documentary_scenes || this.post.documentary_scenes.length === 0) {
                if (this.post.comments && this.post.comments.length > 0) {
                  this.convertCommentsToScenes();
                } else if (this.topicInput && /trojan|tân phát xít|exit deutschland|hardcore rebels/i.test(this.topicInput)) {
                  this.initTrojanScenes();
                } else {
                  this.initDocumentaryTemplate();
                }
              }
            } else if ((plat === 'facebook' || plat === 'instagram') && this.post.illustration_path) {
              this.post.enable_illustration = true;
            }
            this.refreshCardPreview();
          }
          this.saveSettings();
        },
        convertCommentsToScenes() {
          if (!this.post) this.post = {};
          this.post.platform = 'documentary';
          this.post.video_title = this.post.video_title || (this.topicInput ? this.topicInput.toUpperCase() : 'HỒ SƠ TƯ LIỆU QUỐC TẾ');
          this.post.story_badge = this.post.story_badge || 'HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT';
          const comments = this.post.comments || [];
          this.post.documentary_scenes = comments.slice(0, 6).map((c, i) => ({
            index: i + 1,
            type: (i % 2 === 0) ? 'video' : 'image',
            path: this.post.illustration_path || '',
            badge: `PHÂN CẢNH #${i + 1}`,
            text: c.content || '',
            sfx: (c.sfx && c.sfx !== 'none') ? c.sfx : 'tension_riser'
          }));
        },
        initDocumentaryTemplate() {
          if (!this.post) this.post = {};
          this.post.platform = 'documentary';
          this.post.video_title = this.post.video_title || (this.topicInput ? this.topicInput.toUpperCase() : 'HỒ SƠ TƯ LIỆU QUỐC TẾ');
          this.post.story_badge = this.post.story_badge || 'HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT';
          this.post.content = this.post.content || 'Nhập lời mở đầu dẫn dắt câu chuyện kịch tính tại đây...';
          this.post.documentary_scenes = [
            { index: 1, type: 'video', path: '', badge: 'MỞ MÀN BÍ ẨN', text: 'Lời dẫn mở màn về sự kiện có thật chấn động...', sfx: 'tension_riser' },
            { index: 2, type: 'image', path: '', badge: 'MANH MỐI ĐẦU TIÊN', text: 'Hình ảnh và bằng chứng đầu tiên của phi vụ...', sfx: 'ting' },
            { index: 3, type: 'video', path: '', badge: 'DIỄN BIẾN CAO TRÀO', text: 'Tình huống phát triển bất ngờ ngoài tầm kiểm soát...', sfx: 'none' },
            { index: 4, type: 'image', path: '', badge: 'BƯỚC NGOẶT ĐỊNH MỆNH', text: 'Thời khắc quan trọng quyết định kết cục sự việc...', sfx: 'sub_drop' },
            { index: 5, type: 'video', path: '', badge: 'CÚ BẺ LÁI BẤT NGỜ', text: 'Sự thật hé lộ khiến toàn bộ dư luận kinh ngạc...', sfx: 'boom' }
          ];
        },
        initTrojanScenes() {
          if (!this.post) this.post = {};
          this.post.platform = 'documentary';
          this.post.video_title = this.post.video_title || 'CÚ LỪA 250 CHIẾC ÁO PHÔNG TÂN PHÁT XÍT';
          this.post.story_badge = this.post.story_badge || 'HỒ SƠ KỲ THÚ • EXIT DEUTSCHLAND';
          this.post.content = this.post.content || 'Năm 2011, tại Gera nước Đức, một cú lừa ngoạn mục đã biến 250 phần tử cực đoan thành những tấm biển quảng cáo chống lại chính họ sau lần giặt đầu tiên.';
          this.post.documentary_scenes = [
            {
              index: 1,
              type: 'video',
              path: 'assets/documentary/trojan_tshirt/clips/clip1_festival.mp4',
              badge: 'LỄ HỘI ROCK CỰC ĐOAN',
              text: 'Năm 2011, tại Gera nước Đức, hơn 250 phần tử thuộc nhóm Tân Phát Xít đã đổ về một lễ hội nhạc Rock cực đoan.',
              sfx: 'tension_riser'
            },
            {
              index: 2,
              type: 'image',
              path: 'assets/documentary/trojan_tshirt/trojan_before.jpg',
              badge: '250 ÁO MIỄN PHÍ',
              text: 'Tại cổng vào, ban tổ chức bất ngờ phát miễn phí 250 chiếc áo phông đen cực ngầu, in hình đầu lâu cùng dòng chữ nổi loạn Hardcore Rebels.',
              sfx: 'ting'
            },
            {
              index: 3,
              type: 'video',
              path: 'assets/documentary/trojan_tshirt/clips/clip2_distribution.mp4',
              badge: 'TRANH NHAU MẶC ÁO',
              text: 'Tất cả các thành viên đều vô cùng hào hứng, tranh nhau nhận áo và mặc ngay tại chỗ mà không hề nghi ngờ bất kỳ điều gì.',
              sfx: 'none'
            },
            {
              index: 4,
              type: 'video',
              path: 'assets/documentary/trojan_tshirt/clips/clip3_washing.mp4',
              badge: 'BÍ MẬT MÁY GIẶT',
              text: 'Thế nhưng, sau đêm nhạc cuồng nhiệt, họ mang chiếc áo về nhà và cho vào máy giặt. Và điều kinh ngạc đã xảy ra ngay sau đó.',
              sfx: 'sub_drop'
            },
            {
              index: 5,
              type: 'image',
              path: 'assets/documentary/trojan_tshirt/trojan_after.jpg',
              badge: 'CÚ LỪA THẾ KỶ',
              text: 'Lớp mực đặc biệt trôi sạch, hình đầu lâu biến mất hoàn toàn, để lộ ra thông điệp: Chiếc áo đã giúp bạn thoát xác, và chúng tôi có thể giúp bạn từ bỏ chủ nghĩa cực đoan!',
              sfx: 'boom'
            },
            {
              index: 6,
              type: 'video',
              path: 'assets/documentary/trojan_tshirt/clips/clip4_media_reaction.mp4',
              badge: 'CHẤN ĐỘNG TRUYỀN THÔNG',
              text: 'Đây chính là chiến dịch Trojan T-Shirt chấn động nước Đức của tổ chức Exit Deutschland. Một pha bẻ lái thế kỷ khiến truyền thông toàn cầu ngả mũ thán phục!',
              sfx: 'applause'
            }
          ];
        },
        addDocumentaryScene() {
          if (!this.post.documentary_scenes) this.post.documentary_scenes = [];
          const idx = this.post.documentary_scenes.length + 1;
          this.post.documentary_scenes.push({
            index: idx,
            type: idx % 2 === 0 ? 'image' : 'video',
            path: '',
            badge: `PHÂN CẢNH #${idx}`,
            text: 'Lời dẫn chuyện phân cảnh tiếp theo...',
            sfx: 'tension_riser'
          });
        },
        removeDocumentaryScene(idx) {
          if (this.post.documentary_scenes && this.post.documentary_scenes.length > 1) {
            this.post.documentary_scenes.splice(idx, 1);
            this.post.documentary_scenes.forEach((sc, i) => sc.index = i + 1);
          } else {
            alert('Cần giữ lại ít nhất 1 phân cảnh!');
          }
        },
        onPlatformChange() {
          this.setPlatform(this.settings.platform);
        },
        async createVideoOneClick() {
          this.currentTab = 'pipeline';
          try {
            const currentSettings = {
              ...this.settings,
              center_satisfying_category: this.directorConfig?.visual_layout?.center_satisfying_category || 'random',
              enable_center_satisfying_video: this.directorConfig?.visual_layout?.enable_center_satisfying_video || false
            };
            const res = await fetch('/api/create_video_one_click', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                topic: this.topicInput,
                story_mode: this.storyModeInput,
                comment_count: this.commentCountInput,
                voice: this.authorVoiceInput,
                settings: currentSettings,
                post: this.post
              })
            });
            const data = await res.json();
            if (!data.success) {
              alert('Lỗi: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          }
        },
        getAvatarThumb(av) {
          if (!av) return '/api/avatar_file/female_office_1.jpg';
          const s = String(av);
          if (s.startsWith('http://') || s.startsWith('https://')) return s;
          const lastSlash = Math.max(s.lastIndexOf('/'), s.lastIndexOf(String.fromCharCode(92)));
          const clean = lastSlash >= 0 ? s.slice(lastSlash + 1) : s;
          if (clean.endsWith('.jpg') || clean.endsWith('.png') || clean.endsWith('.jpeg')) {
            return '/api/avatar_file/' + clean;
          }
          return '/api/avatar_file/female_office_1.jpg';
        },
        async loadAvatarGallery() {
          try {
            const res = await fetch('/api/avatar_gallery');
            const data = await res.json();
            if (data.avatars) {
              this.avatarGallery = data.avatars;
            }
          } catch(e) {}
        },
        getIllustrationThumb(path) {
          if (!path) return '';
          if (path.startsWith('http://') || path.startsWith('https://')) return path;
          const lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf(String.fromCharCode(92)));
          const clean = lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
          return '/api/illustration/' + clean + '?t=' + this.cardTimestamp;
        },
        async searchNewIllustration() {
          const q = this.customImageQuery || this.post.illustration_prompt || this.post.illustration_caption || '';
          if (!q && !this.post.illustration_category) return;
          this.isSearchingImage = true;
          try {
            const res = await fetch('/api/search_illustration', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                queries: q,
                category: this.post.illustration_category || 'general_life'
              })
            });
            const data = await res.json();
            if (data.success && data.illustration_path) {
              this.post.illustration_path = data.illustration_path;
              this.post.enable_illustration = true;
              this.post.has_visual_proof = true;
              if (!this.post.visual_proof_placement || this.post.visual_proof_placement === 'none') {
                this.post.visual_proof_placement = 'attached_in_post';
              }
              this.cardTimestamp = Date.now();
              this.refreshCardPreview();
            } else {
              alert('Không tìm thấy ảnh trên web cho từ khóa này. Hãy thử từ khóa khác!');
            }
          } catch(e) {
            alert('Lỗi tìm kiếm ảnh: ' + e);
          } finally {
            this.isSearchingImage = false;
          }
        },
        async uploadCustomIllustration(e) {
          const file = e.target.files[0];
          if (!file) return;
          const formData = new FormData();
          formData.append('file', file);
          try {
            const res = await fetch('/api/upload_illustration', {
              method: 'POST',
              body: formData
            });
            const data = await res.json();
            if (data.success && data.illustration_path) {
              this.post.illustration_path = data.illustration_path;
              this.post.enable_illustration = true;
              this.post.has_visual_proof = true;
              if (!this.post.visual_proof_placement || this.post.visual_proof_placement === 'none') {
                this.post.visual_proof_placement = 'attached_in_post';
              }
              this.cardTimestamp = Date.now();
              this.refreshCardPreview();
            } else {
              alert('Lỗi tải ảnh: ' + (data.error || 'Thử lại'));
            }
          } catch(e) {
            alert('Lỗi tải ảnh: ' + e);
          }
        },
        async uploadSceneMedia(e, sceneIdx) {
          const file = e.target.files[0];
          if (!file) return;
          const formData = new FormData();
          formData.append('file', file);
          try {
            const res = await fetch('/api/upload_illustration', {
              method: 'POST',
              body: formData
            });
            const data = await res.json();
            if (data.success && data.illustration_path) {
              if (this.post.documentary_scenes && this.post.documentary_scenes[sceneIdx]) {
                this.post.documentary_scenes[sceneIdx].path = data.illustration_path;
                const isVid = ['.mp4', '.mov', '.webm', '.mkv'].some(ext => (data.illustration_path || '').toLowerCase().endsWith(ext));
                this.post.documentary_scenes[sceneIdx].type = isVid ? 'video' : 'image';
              }
            } else {
              alert('Lỗi tải file: ' + (data.error || 'Thử lại'));
            }
          } catch(err) {
            alert('Lỗi tải file tư liệu: ' + err);
          }
        },
        openAvatarModal(targetType, commentIndex = 0) {
          this.avatarTarget = { type: targetType, index: commentIndex };
          this.showAvatarModal = true;
          if (!this.avatarGallery || this.avatarGallery.length === 0) {
            this.loadAvatarGallery();
          }
        },
        selectAvatar(avatar) {
          if (this.avatarTarget.type === 'author') {
            this.post.avatar_url = avatar.id;
          } else if (this.avatarTarget.type === 'comment') {
            if (this.post.comments && this.post.comments[this.avatarTarget.index]) {
              this.post.comments[this.avatarTarget.index].avatar_url = avatar.id;
            }
          }
          this.showAvatarModal = false;
          this.refreshCardPreview();
        },
        addComment() {
          if (!this.post.comments) this.post.comments = [];
          const idx = this.post.comments.length + 1;
          this.post.comments.push({
            username: `user_${Math.floor(1000 + Math.random() * 9000)}`,
            reply_to: this.post.username || '',
            content: `Bình luận tương tác số ${idx}...`,
            likes: 1200 + idx * 350,
            avatar_path: '',
            avatar_url: '',
            sfx: 'none',
            emotion: 'funny'
          });
          this.refreshCardPreview();
        },
        removeComment(idx) {
          if (this.post.comments && this.post.comments.length > 1) {
            this.post.comments.splice(idx, 1);
            this.refreshCardPreview();
          } else {
            alert('Cần giữ lại ít nhất 1 bình luận tương tác!');
          }
        },
        async generateScriptOnly() {
          await this.generateScript();
          this.currentTab = 'studio';
          this.previewMode = 'cards';
        },
        pickTopic(t) {
          this.topicInput = t;
          this.generateScriptOnly();
        },
        async crawlAndRenderVideo() {
          await this.createVideoOneClick();
        },
        async crawlTrendingThreads(autoRender = false) {
          this.isCrawlingTrending = true;
          if (autoRender === true) this.currentTab = 'pipeline';
          try {
            const res = await fetch('/api/crawl_trending_threads', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ topic: this.topicInput })
            });
            const data = await res.json();
            if (data.success) {
              this.post = data.post;
              this.cardTimestamp = Date.now();
              this.previewMode = 'cards';
              if (autoRender === true) {
                // Tự động tiếp tục dựng toàn bộ video (Bước 3 -> 6) để người dùng có thành phẩm ngay!
                await this.startRender();
              } else {
                this.currentTab = 'studio';
              }
            } else {
              this.notify('Lỗi quét trending Threads: ' + data.error, 'error');
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          } finally {
            this.isCrawlingTrending = false;
          }
        },
        async loadBgmList() {
          try {
            const res = await fetch('/api/bgm_list');
            const data = await res.json();
            if (data && data.bgm_list) {
              this.bgmList = data.bgm_list;
            }
          } catch(e) {}
        },
        async loadSettings() {
          try {
            const res = await fetch('/api/settings');
            const data = await res.json();
            this.settings.platform = data.platform || 'threads';
            this.settings.layout_preset = data.layout_preset || 'center';
            this.settings.bg_mode = data.bg_mode || 'split_gameplay';
            this.settings.card_theme = data.card_theme || 'light';
            this.settings.voice = data.voice || 'nam_hai';
            this.settings.rate = data.rate || '+0%';
            this.settings.bgm_choice = data.bgm_choice || 'gentle_piano_satie.mp3';
            this.settings.bgm_volume = data.bgm_volume !== undefined ? data.bgm_volume : 25;
          } catch(e) {}
        },
        notify(msg, type = 'success') {
          this.toast = { show: true, message: msg, type: type };
          if (this._toastTimer) clearTimeout(this._toastTimer);
          this._toastTimer = setTimeout(() => {
            this.toast.show = false;
          }, 2500);
        },
        async saveSettings(manual = false) {
          try {
            await fetch('/api/settings', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(this.settings)
            });
            if (manual === true) {
              this.notify('✅ Đã lưu cấu hình nhanh!', 'success');
            }
          } catch(e) {
            if (manual === true) {
              this.notify('Lỗi lưu cấu hình: ' + e, 'error');
            }
          }
        },
        async loadGeminiKeys() {
          try {
            const res = await fetch('/api/gemini_keys');
            const data = await res.json();
            this.geminiKeys = data.keys || [];
          } catch(e) {}
        },
        async addGeminiKey() {
          if (!this.newGeminiKey || !this.newGeminiKey.trim()) return;
          try {
            const res = await fetch('/api/gemini_keys', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ api_key: this.newGeminiKey.trim(), label: this.newGeminiLabel.trim() })
            });
            const data = await res.json();
            if (data.success) {
              this.newGeminiKey = '';
              this.newGeminiLabel = '';
              await this.loadGeminiKeys();
              this.notify('✅ Đã lưu GEMINI_API_KEY vào SQLite DB!', 'success');
            } else {
              this.notify(data.error || 'Lỗi thêm key', 'error');
            }
          } catch(e) {
            this.notify('Lỗi kết nối: ' + e, 'error');
          }
        },
        async toggleGeminiKey(k) {
          try {
            await fetch('/api/gemini_keys/' + k.id, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ is_active: !k.is_active })
            });
            await this.loadGeminiKeys();
          } catch(e) {}
        },
        async deleteGeminiKey(id) {
          try {
            await fetch('/api/gemini_keys/' + id, { method: 'DELETE' });
            await this.loadGeminiKeys();
            this.notify('Đã xóa key khỏi DB.', 'success');
          } catch(e) {}
        },
        async loadDirectorConfig() {
          try {
            const res = await fetch('/api/director_config');
            this.directorConfig = await res.json();
          } catch(e) {}
        },
        async saveDirectorConfig() {
          try {
            const res = await fetch('/api/director_config', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(this.directorConfig)
            });
            const data = await res.json();
            if (data.success) {
              alert('🎉 Đã lưu cấu hình Đạo Diễn thành công (config/director_config.json)!');
            } else {
              alert('Lỗi: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          }
        },
        async resetDirectorConfig() {
          if (!confirm('Khôi phục toàn bộ tham số về mặc định ban đầu?')) return;
          try {
            const res = await fetch('/api/reset_director_config', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
              this.directorConfig = data.config;
              alert('✅ Đã khôi phục cấu hình mặc định!');
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          }
        },
        async loadCurrentScript() {
          try {
            const res = await fetch('/api/script');
            const data = await res.json();
            if (data && data.username) {
              this.post = data;
            }
          } catch(e) {}
        },
        async generateScript() {
          this.isGeneratingScript = true;
          try {
            const res = await fetch('/api/generate_script', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                topic: this.topicInput,
                story_mode: this.storyModeInput,
                comment_count: this.commentCountInput,
                voice: this.authorVoiceInput,
                duration_mode: this.directorConfig?.ai_director?.duration_mode,
                platform: this.settings.platform
              })
            });
            const data = await res.json();
            if (data.success) {
              this.post = data.post;
              this.cardTimestamp = Date.now();
              this.previewMode = 'cards';
              this.currentTab = 'studio';
            } else {
              alert('Lỗi AI: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          } finally {
            this.isGeneratingScript = false;
          }
        },
        async refreshCardPreview() {
          try {
            await fetch('/api/save_and_render_cards', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ post: this.post, theme: this.settings.card_theme, platform: this.settings.platform })
            });
            this.cardTimestamp = Date.now();
            this.previewMode = 'cards';
          } catch(e) {
            alert('Lỗi render card: ' + e);
          }
        },
        async startRender() {
          this.currentTab = 'pipeline'; // Tự động chuyển sang tab Giám Sát Quy Trình
          try {
            const currentSettings = {
              ...this.settings,
              center_satisfying_category: this.directorConfig?.visual_layout?.center_satisfying_category || 'random',
              enable_center_satisfying_video: this.directorConfig?.visual_layout?.enable_center_satisfying_video || false
            };
            const res = await fetch('/api/render_video', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                post: this.post,
                settings: currentSettings
              })
            });
            const data = await res.json();
            if (!data.success) {
              alert('Lỗi khởi động render: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          }
        },
        async startBatch(count) {
          if (!confirm(`Bạn có chắc muốn tạo tự động ${count} video liên tiếp?`)) return;
          this.currentTab = 'pipeline';
          try {
            const res = await fetch('/api/batch_render', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ count: count })
            });
            const data = await res.json();
            if (data.success) {
              alert(`🎉 Hoàn tất sản xuất ${data.completed.length} video!`);
              this.loadLatestVideo();
            } else {
              alert('Lỗi batch: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          }
        },
        async loadLatestVideo() {
          try {
            const res = await fetch('/api/latest_video');
            const data = await res.json();
            if (data.exists) {
              this.latestVideoUrl = '/api/video/' + data.filename;
              this.latestCaption = data.caption;
            }
          } catch(e) {}
        },
        async openFinalFolder() {
          await fetch('/api/open_folder');
        },
        copyCaption() {
          if (!this.latestCaption) return;
          navigator.clipboard.writeText(this.latestCaption);
          this.notify('📋 Đã sao chép caption vào bộ nhớ tạm!', 'success');
        },
        formatIsoTime(val) {
          if (!val) return '';
          try {
            const d = new Date(val);
            if (Number.isNaN(d.getTime())) return String(val);
            return d.toLocaleString('vi-VN');
          } catch (e) {
            return String(val);
          }
        },
        async loadMarketDashboard() {
          await Promise.all([
            this.loadMarketStatus(),
            this.loadMarketItems(this.marketOffset),
            this.loadMarketReport(),
            this.loadCrawlRequests()
          ]);
        },
        async loadMarketReport() {
          try {
            const res = await fetch('/api/market/report?platform=' + encodeURIComponent(this.marketReportPlatform || 'all'));
            const data = await res.json();
            if (data.report) this.marketReport = data.report;
          } catch (e) {}
        },
        downloadMarketReportJson() {
          if (!this.marketReport) return;
          const blob = new Blob([JSON.stringify(this.marketReport, null, 2)], { type: 'application/json' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `market_report_${this.marketReportPlatform || 'all'}.json`;
          a.click();
          URL.revokeObjectURL(url);
        },
        async loadCrawlRequests() {
          try {
            const res = await fetch('/api/remote/crawl_requests');
            const data = await res.json();
            if (data.ok) {
              this.crawlRequests = data.items || [];
              if (data.schedule) {
                this.crawlScheduleInfo = data.schedule;
                this.crawlScheduleProfile = data.schedule.poll_profile || 'production_6h';
              }
            }
          } catch (e) {}
        },
        async loadMarketStatus() {
          try {
            const res = await fetch('/api/market/status');
            const data = await res.json();
            this.marketStatus = data;
            if (data.stats?.sync_interval_seconds) {
              this.marketConfigInterval = data.stats.sync_interval_seconds;
            }
          } catch (e) {}
        },
        formatCompactMetric(val) {
          if (val === null || val === undefined) return '—';
          const num = Number(val);
          if (!Number.isFinite(num)) return String(val);
          if (num >= 1000000) return (num / 1000000).toFixed(1).replace(/\\.0$/, '') + 'M (' + num.toLocaleString('vi-VN') + ')';
          return num.toLocaleString('vi-VN');
        },
        jumpToThreadsTier(tier) {
          this.marketSubTab = 'threads';
          this.marketFilters.platform = 'threads';
          this.marketFilters.trend_tier = tier || 'all';
          this.marketFilters.sort_by = 'tier';
          this.loadMarketItems(0);
        },
        switchMarketSubTab(subTab) {
          this.marketSubTab = subTab;
          if (subTab === 'threads' || subTab === 'tiktok' || subTab === 'shopee') {
            this.marketFilters.platform = subTab;
            this.marketFilters.sort_by = subTab === 'tiktok' ? 'views' : (subTab === 'threads' ? 'tier' : 'recent');
            this.loadMarketItems(0);
          } else if (subTab === 'reports') {
            this.loadMarketReport();
          } else {
            this.marketFilters.platform = 'all';
            this.loadMarketDashboard();
          }
        },
        async loadMarketItems(offset = 0) {
          this.marketOffset = Math.max(0, offset);
          const effLimit = this.marketSubTab === 'threads' ? Math.max(this.marketLimit, 100) : this.marketLimit;
          try {
            const qs = new URLSearchParams({
              platform: this.marketFilters.platform || 'all',
              content_group: this.marketFilters.content_group || 'all',
              purchase_intent: this.marketFilters.purchase_intent || 'all',
              data_quality: this.marketFilters.data_quality || 'all',
              trend_tier: this.marketFilters.trend_tier || 'all',
              trend_group: this.marketFilters.trend_group || 'all',
              verification_status: this.marketFilters.verification_status || 'all',
              sort_by: this.marketFilters.sort_by || 'recent',
              affiliate_only: (this.marketSubTab === 'shopee' && this.shopeeAffiliateOnly) ? '1' : '0',
              search: this.marketFilters.search || '',
              limit: String(effLimit),
              offset: String(this.marketOffset),
            });
            const res = await fetch('/api/market/items?' + qs.toString());
            const data = await res.json();
            this.marketItems = data.items || [];
            this.marketTotal = data.total || 0;
          } catch (e) {}
        },
        async triggerMarketSyncNow() {
          try {
            const res = await fetch('/api/market/sync', { method: 'POST' });
            const data = await res.json();
            this.notify(data.started ? '🔄 Đã bắt đầu đồng bộ nền...' : (data.message || 'Đang đồng bộ'), 'success');
            this.loadMarketStatus();
            setTimeout(() => this.loadMarketDashboard(), 1200);
          } catch (e) {
            this.notify('Lỗi kích hoạt sync: ' + e, 'error');
          }
        },
        async testMarketConnection() {
          this.isTestingConnection = true;
          try {
            const res = await fetch('/api/market/test_connection', { method: 'POST' });
            const data = await res.json();
            this.marketConnectionNotice = {
              ok: Boolean(data.connected),
              text: data.connected
                ? `✅ Kết nối Cloudflare Worker OK (DB: ${data.health?.db_ok}, Drive: ${data.health?.drive_ok})`
                : `⚠️ ${data.error || 'Không thể kết nối Cloudflare Worker'}`
            };
          } catch (e) {
            this.marketConnectionNotice = { ok: false, text: 'Lỗi kiểm tra kết nối: ' + e };
          } finally {
            this.isTestingConnection = false;
          }
        },
        async updateMarketConfig() {
          try {
            const res = await fetch('/api/market/config', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ sync_interval_seconds: this.marketConfigInterval })
            });
            const data = await res.json();
            if (data.success) {
              this.notify(`⏱️ Đã cập nhật chu kỳ đồng bộ nền: ${data.sync_interval_seconds}s`, 'success');
            }
          } catch (e) {}
        },
        async openMarketRawModal(itemId) {
          try {
            const res = await fetch('/api/market/items/' + encodeURIComponent(itemId));
            const data = await res.json();
            if (data.item) this.selectedMarketDetail = data.item;
          } catch (e) {}
        },
        useMarketItemInStudio(item) {
          const isUnverified = item.data_quality === 'needs_verification' ||
            (item.extra_meta && item.extra_meta.affiliate_eligible === false) ||
            (item.extra_meta && item.extra_meta.url_status && item.extra_meta.url_status !== 'da_kiem_chung');
          if (isUnverified) {
            const ok = confirm('⚠️ Dữ liệu này đang ở trạng thái needs_verification / chưa đủ bằng chứng xác minh.\\nBạn vẫn muốn nạp vào Studio để biên tập thủ công?');
            if (!ok) return;
          }
          const text = item.summary_vi || item.title || item.text_content || '';
          if (text) {
            this.topicInput = text.slice(0, 220);
            this.currentTab = 'studio';
            this.notify(isUnverified ? '⚠️ Đã nạp dữ liệu chưa xác minh vào Studio (cần kiểm chứng lại).' : '🎬 Đã nạp dữ liệu Market Intelligence vào Studio!', isUnverified ? 'warning' : 'success');
          }
        },
        async uploadLocalMarketJson(e) {
          const file = e.target.files[0];
          if (!file) return;
          const form = new FormData();
          form.append('file', file);
          try {
            const res = await fetch('/api/market/import_local', { method: 'POST', body: form });
            const data = await res.json();
            if (data.success) {
              this.notify(`✅ Đã nạp ${data.result.inserted} mới / ${data.result.deduped} trùng từ ${file.name}`, 'success');
              this.loadMarketDashboard();
            } else {
              this.notify('Lỗi nhập JSON: ' + data.error, 'error');
            }
          } catch (err) {
            this.notify('Lỗi tải file JSON: ' + err, 'error');
          }
        },
        async submitCrawlRequest() {
          try {
            const res = await fetch('/api/remote/crawl_requests', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                ...this.crawlRequestForm,
                poll_profile: this.crawlScheduleProfile
              })
            });
            const data = await res.json();
            if (data.ok) {
              this.notify(
                data.deduplicated
                  ? 'ℹ️ Đã có yêu cầu crawl tương tự đang chờ trong hàng đợi D1.'
                  : '📝 Đã gửi yêu cầu vào Crawl Queue D1 (đang chờ Muse claim).',
                'success'
              );
              this.crawlRequestForm.keywords = '';
              this.loadCrawlRequests();
            } else {
              this.notify(data.error || 'Không thể tạo Crawl Queue (kiểm tra kết nối Cloudflare).', 'error');
            }
          } catch (e) {
            this.notify('Lỗi gửi Crawl Queue: ' + e, 'error');
          }
        },
        async cancelCrawlRequest(crawlId) {
          try {
            const res = await fetch('/api/remote/crawl_requests/' + encodeURIComponent(crawlId) + '/cancel', { method: 'POST' });
            const data = await res.json();
            if (data.ok) {
              this.notify('🚫 Đã hủy yêu cầu đang chờ: ' + crawlId, 'success');
              this.loadCrawlRequests();
            } else {
              this.notify(data.error || 'Không thể hủy yêu cầu.', 'error');
            }
          } catch (e) {
            this.notify('Lỗi hủy yêu cầu: ' + e, 'error');
          }
        },
        async updateCrawlScheduleProfile() {
          try {
            const res = await fetch('/api/remote/crawl_schedule', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ poll_profile: this.crawlScheduleProfile })
            });
            const data = await res.json();
            if (data.ok) {
              this.crawlScheduleInfo = data;
              this.notify('ℹ️ Đã lưu cấu hình chu kỳ đề xuất trên Worker (chờ xác nhận khi Muse poll thật).', 'info');
            }
          } catch (e) {}
        }
      }
    }).mount('#app');
  </script>
</body>
</html>
"""

@app.route("/")
def index():
    return HTML_TEMPLATE, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/pipeline_status")
def api_pipeline_status():
    """Endpoint trả về trạng thái thời gian thực của quy trình sản xuất video."""
    return jsonify(tracker.get_state())


@app.route("/api/director_config", methods=["GET", "POST"])
def api_director_config():
    """Đọc và lưu cấu hình đạo diễn không hardcode."""
    if request.method == "POST":
        data = request.json
        success = config_mod.save_config(data)
        return jsonify({"success": success})
    else:
        return jsonify(config_mod.load_config(force_reload=True))


@app.route("/api/reset_director_config", methods=["POST"])
def api_reset_director_config():
    """Khôi phục cấu hình mặc định ban đầu."""
    config_mod.save_config(config_mod.DEFAULT_CONFIG)
    return jsonify({"success": True, "config": config_mod.DEFAULT_CONFIG})


@app.route("/api/bgm_list", methods=["GET"])
def api_bgm_list():
    """Lấy danh sách các bài nhạc nền hiện có trong kho assets/bgm."""
    bgm_dir = os.path.join(ASSETS_DIR, "bgm")
    items = [
        {"id": "random", "name": "🎲 Ngẫu nhiên (Kho nhạc nền TikTok/Lofi)"},
        {"id": "none", "name": "🔇 Tắt nhạc nền (Chỉ giọng đọc & SFX)"}
    ]
    if os.path.exists(bgm_dir):
        for f in sorted(os.listdir(bgm_dir)):
            if f.lower().endswith((".mp3", ".wav", ".m4a")):
                title = f
                if "chill" in f.lower():
                    title = f"☕ {f} (Lofi Chill thư giãn)"
                elif "upbeat" in f.lower():
                    title = f"⚡ {f} (Lofi Upbeat nhịp điệu)"
                else:
                    title = f"🎵 {f}"
                items.append({"id": f, "name": title})
    return jsonify({"bgm_list": items})


@app.route("/api/gemini_keys", methods=["GET", "POST"])
def api_gemini_keys():
    db_mod = load_mod("db_store", "db_store.py")
    if request.method == "POST":
        data = request.json or {}
        api_key = (data.get("api_key") or "").strip()
        label = (data.get("label") or "").strip()
        if not api_key:
            return jsonify({"success": False, "error": "api_key không được rỗng."}), 400
        item = db_mod.add_gemini_key(api_key, label=label)
        return jsonify({"success": True, "key": item})
    return jsonify({"success": True, "keys": db_mod.list_gemini_keys()})


@app.route("/api/gemini_keys/<int:key_id>", methods=["PATCH", "DELETE"])
def api_gemini_key_item(key_id):
    db_mod = load_mod("db_store", "db_store.py")
    if request.method == "DELETE":
        ok = db_mod.delete_gemini_key(key_id)
        return jsonify({"success": ok})
    data = request.json or {}
    ok = db_mod.toggle_gemini_key(key_id, bool(data.get("is_active", True)))
    return jsonify({"success": ok})


@app.route("/api/settings", methods=["GET", "POST", "DELETE"])
def api_settings():
    db_mod = load_mod("db_store", "db_store.py")
    if request.method == "DELETE":
        db_mod.reset_studio_settings()
        return jsonify({"success": True})
    if request.method == "POST":
        data = request.json or {}
        db_mod.save_studio_settings({
            "PLATFORM": data.get("platform", "threads"),
            "LAYOUT_PRESET": data.get("layout_preset", "center"),
            "BG_MODE": data.get("bg_mode", "split_gameplay"),
            "CARD_THEME": data.get("card_theme", "light"),
            "TTS_VOICE": data.get("voice", "nam_hai"),
            "TTS_RATE": data.get("rate", "+0%"),
            "BGM_CHOICE": data.get("bgm_choice", "gentle_piano_satie.mp3"),
            "BGM_VOLUME": str(data.get("bgm_volume", 25)),
        })
        return jsonify({"success": True})
    else:
        cfg_s = db_mod.get_studio_settings()
        try:
            bgm_vol = int(cfg_s.get("BGM_VOLUME", 25))
        except ValueError:
            bgm_vol = 25
        return jsonify({
            "platform": cfg_s.get("PLATFORM", "threads"),
            "layout_preset": cfg_s.get("LAYOUT_PRESET", "center"),
            "bg_mode": cfg_s.get("BG_MODE", "split_gameplay"),
            "card_theme": cfg_s.get("CARD_THEME", "light"),
            "voice": cfg_s.get("TTS_VOICE", "nam_hai"),
            "rate": cfg_s.get("TTS_RATE", "+0%"),
            "bgm_choice": cfg_s.get("BGM_CHOICE", "gentle_piano_satie.mp3"),
            "bgm_volume": bgm_vol,
        })


@app.route("/api/script", methods=["GET"])
def api_get_script():
    sample_file = os.path.join(OUTPUT_DIR, "sample_post.json")
    if os.path.exists(sample_file):
        with open(sample_file, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({})


@app.route("/api/generate_script", methods=["POST"])
def api_generate_script():
    data = request.json or {}
    topic = data.get("topic", "").strip() or None
    raw_c_count = data.get("comment_count") or data.get("target_comment_count") or None
    target_comment_count = None
    if raw_c_count and str(raw_c_count).lower() not in ["auto", "", "none", "0"]:
        try:
            target_comment_count = int(raw_c_count)
        except (ValueError, TypeError):
            target_comment_count = None

    author_voice = data.get("voice") or None
    duration_mode = data.get("duration_mode") or None

    try:
        tracker.reset()
        tracker.start_step("script", f"Gemini AI đang sáng tạo kịch bản theo chủ đề: '{topic or 'ngẫu nhiên'}'...", 20)

        platform = data.get("platform", "threads")
        story_mode = data.get("story_mode", "auto")
        post = ai_mod.generate_story_with_gemini(
            topic,
            duration_mode=duration_mode,
            target_comment_count=target_comment_count,
            platform=platform,
            story_mode=story_mode,
            progress_cb=lambda msg, pct: tracker.update_progress(pct, msg)
        )
        if author_voice:
            post.voice_id = author_voice

        # Tự động kết nối video và ảnh tư liệu thực tế nếu là True Story / Documentary
        if platform == "documentary" or getattr(post, "story_mode", "") == "true_story":
            from copy import deepcopy
            post.platform = "documentary"
            post.story_mode = "true_story"
            doc_query = getattr(post, "documentary_query", "") or topic or ""
            is_trojan = bool(topic) and any(k in doc_query.lower() or k in topic.lower() for k in ["trojan", "tân phát xít", "exit deutschland", "hardcore rebels"])
            if not getattr(post, "video_title", ""):
                post.video_title = "CÚ LỪA 250 CHIẾC ÁO PHÔNG TÂN PHÁT XÍT" if is_trojan else (topic[:60].upper() if topic else "HỒ SƠ KỲ THÚ CÓ THẬT")
            if not getattr(post, "story_badge", ""):
                post.story_badge = "HỒ SƠ KỲ THÚ • EXIT DEUTSCHLAND" if is_trojan else "HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT"

            # Chỉ fallback nếu AI chưa tạo sẵn documentary_scenes
            if not getattr(post, "documentary_scenes", None):
                if is_trojan:
                    post.documentary_scenes = deepcopy(visual_builder_mod.DEFAULT_TROJAN_SCENES)
                elif getattr(post, "comments", None):
                    scs = []
                    for idx, c in enumerate(post.comments, 1):
                        is_even = (idx % 2 == 0)
                        scs.append({
                            "index": idx,
                            "type": "image" if is_even else "video",
                            "path": getattr(post, "illustration_path", ""),
                            "badge": f"PHÂN CẢNH #{idx}",
                            "text": c.content,
                            "sfx": c.sfx if c.sfx != "none" else "tension_riser",
                            "voice": post.voice_id or "adam"
                        })
                    post.documentary_scenes = scs

        sample_file = os.path.join(OUTPUT_DIR, "sample_post.json")
        content_mod.save_post_to_json(post, sample_file)

        # Render trước các thẻ kèm avatar người thật & theme tối ưu
        tracker.start_step("cards", "Vẽ thẻ xem trước (Avatar người thật & Giao diện tối ưu)...", 80)
        _render_all_cards_helper(post, theme=getattr(post, "recommended_theme", "light"), platform=platform)
        tracker.finish_step("cards")
        tracker.finish_step("script")
        tracker.pause_for_review(f"Đã tạo kịch bản {len(post.comments)} bình luận & vẽ thẻ xem trước (Bước 2/6). Sẵn sàng render video!")

        return jsonify({
            "success": True,
            "post": {
                "platform": getattr(post, "platform", platform),
                "username": post.username,
                "content": post.content,
                "likes": post.likes,
                "is_verified": post.is_verified,
                "avatar_path": getattr(post, "avatar_path", ""),
                "avatar_url": getattr(post, "avatar_url", ""),
                "voice_id": getattr(post, "voice_id", "adam"),
                "illustration_prompt": getattr(post, "illustration_prompt", ""),
                "illustration_path": getattr(post, "illustration_path", ""),
                "illustration_caption": getattr(post, "illustration_caption", ""),
                "illustration_category": getattr(post, "illustration_category", "general_life"),
                "has_visual_proof": getattr(post, "has_visual_proof", False),
                "visual_proof_placement": getattr(post, "visual_proof_placement", "none"),
                "sentence_reveal_mode": getattr(post, "sentence_reveal_mode", "standard_full"),
                "satisfying_category": getattr(post, "satisfying_category", "auto"),
                "recommended_theme": getattr(post, "recommended_theme", "light"),
                "story_context_type": getattr(post, "story_context_type", "general_social"),
                "cta_question": getattr(post, "cta_question", ""),
                "pacing_tempo": getattr(post, "pacing_tempo", "normal"),
                "enable_illustration": getattr(post, "enable_illustration", False),
                "documentary_scenes": getattr(post, "documentary_scenes", []),
                "story_badge": getattr(post, "story_badge", ""),
                "video_title": getattr(post, "video_title", ""),
                "emotional_arc": getattr(post, "emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
                "comments": [
                    {
                        "username": c.username,
                        "reply_to": getattr(c, "reply_to", "") or post.username,
                        "content": c.content,
                        "likes": c.likes,
                        "avatar_path": getattr(c, "avatar_path", ""),
                        "avatar_url": getattr(c, "avatar_url", ""),
                        "sfx": getattr(c, "sfx", "none"),
                        "emotion": getattr(c, "emotion", "funny"),
                        "voice_id": getattr(c, "voice_id", "vi-VN-HoaiMyNeural")
                    }
                    for c in post.comments
                ]
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        tracker.set_error(str(e))
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/crawl_trending_threads", methods=["POST"])
def api_crawl_trending_threads():
    """Dò quét bài viết thịnh hành thật trên Threads và ghép Avatar người thật."""
    data = request.json or {}
    topic = data.get("topic", "").strip() or None
    try:
        radar_mod = load_mod("threads_radar", "threads_radar.py")
        tracker.reset()
        tracker.start_step("script", f"Threads Radar đang quét tin trending về: '{topic or 'ngẫu nhiên'}'...", 20)

        post = radar_mod.crawl_trending_threads_story(
            topic,
            progress_cb=lambda msg, pct: tracker.update_progress(pct, msg)
        )

        tracker.start_step("cards", "Đang ghép avatar người thật và render thẻ...", 80)
        _render_all_cards_helper(post, theme="light")
        tracker.finish_step("cards")
        tracker.finish_step("script")
        tracker.pause_for_review("Đã quét tin trending và ghép avatar người thật hoàn tất (Bước 2/6). Sẵn sàng render video!")

        return jsonify({
            "success": True,
            "post": {
                "username": post.username,
                "content": post.content,
                "likes": post.likes,
                "is_verified": post.is_verified,
                "avatar_path": getattr(post, "avatar_path", ""),
                "avatar_url": getattr(post, "avatar_url", ""),
                "emotional_arc": getattr(post, "emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
                "comments": [
                    {
                        "username": c.username,
                        "reply_to": getattr(c, "reply_to", "") or post.username,
                        "content": c.content,
                        "likes": c.likes,
                        "avatar_path": getattr(c, "avatar_path", ""),
                        "avatar_url": getattr(c, "avatar_url", ""),
                        "sfx": getattr(c, "sfx", "none"),
                        "emotion": getattr(c, "emotion", "funny")
                    }
                    for c in post.comments
                ]
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        tracker.set_error(str(e))
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/create_video_one_click", methods=["POST"])
def api_create_video_one_click():
    """Quy trình 1-Click tự động hoàn toàn: AI nghĩ kịch bản -> Lấy Avatar thật trên Threads -> Render video 60 FPS."""
    data = request.json or {}
    topic = data.get("topic", "").strip() or None
    sets = data.get("settings", {})
    layout_preset = sets.get("layout_preset", "center")
    bg_mode = sets.get("bg_mode", "auto")
    card_theme = sets.get("card_theme", "auto")
    bgm_choice = sets.get("bgm_choice", "random")
    raw_vol = sets.get("bgm_volume")
    bgm_volume = (float(raw_vol) / 100.0) if raw_vol is not None else None

    author_voice = data.get("voice") or sets.get("voice") or "nam_hai"

    def _full_video_worker():
        try:
            tracker.reset()
            platform = sets.get("platform", "threads")
            story_mode = data.get("story_mode", "auto")
            post_data = data.get("post") or {}

            # Xử lý riêng cho Phim Tư Liệu Hình + Video (Visual Documentary)
            if platform == "documentary" or story_mode == "true_story":
                post_scenes = post_data.get("documentary_scenes")
                if post_scenes:
                    tracker.start_step("script", f"Đang nạp kịch bản phân cảnh tư liệu cho: '{topic or 'Hồ sơ tư liệu'}'...", 25)
                    post = content_mod.Post(
                        username=post_data.get("username", "director"),
                        content=post_data.get("content", ""),
                        platform="documentary",
                        story_mode="true_story",
                        video_title=post_data.get("video_title") or (topic[:60].upper() if topic else "HỒ SƠ TƯ LIỆU QUỐC TẾ"),
                        story_badge=post_data.get("story_badge") or "HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT",
                        documentary_scenes=post_scenes,
                        voice_id=author_voice
                    )
                else:
                    tracker.start_step("script", f"AI đang sáng tạo kịch bản phim tư liệu theo chủ đề: '{topic or 'thịnh hành'}'...", 20)
                    ai_mod = load_mod("ai_content", "01_ai_content.py")
                    post = ai_mod.generate_story_with_gemini(
                        topic=topic,
                        platform="documentary",
                        story_mode="true_story",
                        progress_cb=lambda msg, pct: tracker.update_progress(pct, msg)
                    )
                if author_voice:
                    post.voice_id = author_voice
                post.platform = "documentary"
            else:
                post_cmts = post_data.get("comments")
                if post_cmts and len(post_cmts) > 0 and post_data.get("content"):
                    tracker.start_step("script", "Đang nạp kịch bản hiện tại...", 15)
                    post = content_mod.Post(
                        username=post_data.get("username", "threads_user"),
                        content=post_data.get("content", ""),
                        likes=post_data.get("likes", "1.2K"),
                        is_verified=post_data.get("is_verified", False),
                        avatar_path=post_data.get("avatar_path", ""),
                        avatar_url=post_data.get("avatar_url", ""),
                        voice_id=author_voice or post_data.get("voice_id", "nam_hai"),
                        illustration_path=post_data.get("illustration_path", ""),
                        enable_illustration=post_data.get("enable_illustration", False),
                        platform=platform,
                        comments=[
                            content_mod.Comment(
                                username=c.get("username", "user"),
                                reply_to=c.get("reply_to", post_data.get("username", "")),
                                content=c.get("content", ""),
                                likes=c.get("likes", "45"),
                                avatar_path=c.get("avatar_path", ""),
                                avatar_url=c.get("avatar_url", ""),
                                sfx=c.get("sfx", "none"),
                                emotion=c.get("emotion", "funny"),
                                voice_id=c.get("voice_id", "")
                            )
                            for c in post_cmts
                        ]
                    )
                else:
                    # BƯỚC 1: KỊCH BẢN & CÀO AVATAR THẬT TỪ THREADS
                    tracker.start_step("script", f"AI đang sáng tạo kịch bản & phân tích Threads về: '{topic or 'thịnh hành'}'...", 15)
                    radar_mod = load_mod("threads_radar", "threads_radar.py")
                    post = radar_mod.crawl_trending_threads_story(
                        topic=topic,
                        progress_cb=lambda msg, pct: tracker.update_progress(pct, msg)
                    )
                    if author_voice:
                        post.voice_id = author_voice
                    post.platform = platform
                tracker.finish_step("script")

            # Xử lý riêng cho Phim Tư Liệu Hình + Video (Visual Documentary)
            if platform == "documentary":
                from copy import deepcopy
                post_data = data.get("post") or {}
                scenes = post_data.get("documentary_scenes") or getattr(post, "documentary_scenes", [])
                if not scenes:
                    # Nếu có comments, chuyển thành scenes
                    comments = getattr(post, "comments", []) or post_data.get("comments", [])
                    if comments:
                        scenes = []
                        for idx, c in enumerate(comments, 1):
                            c_txt = c.content if hasattr(c, "content") else (c.get("content", "") if isinstance(c, dict) else str(c))
                            c_sfx = getattr(c, "sfx", "none") if hasattr(c, "sfx") else (c.get("sfx", "none") if isinstance(c, dict) else "none")
                            scenes.append({
                                "index": idx,
                                "type": "image" if (idx % 2 == 0) else "video",
                                "path": getattr(post, "illustration_path", ""),
                                "badge": f"PHÂN CẢNH #{idx}",
                                "text": c_txt,
                                "sfx": c_sfx if c_sfx != "none" else "tension_riser",
                                "voice": author_voice or getattr(post, "voice_id", "adam")
                            })
                    else:
                        scenes = deepcopy(visual_builder_mod.DEFAULT_TROJAN_SCENES)

                story_title = post_data.get("video_title") or getattr(post, "video_title", "") or (topic[:60].upper() if topic else "HỒ SƠ TƯ LIỆU QUỐC TẾ")
                story_badge = post_data.get("story_badge") or getattr(post, "story_badge", "") or "HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT"
                for sc in scenes:
                    if author_voice:
                        sc["voice"] = author_voice

                _render_all_cards_helper(post, theme=card_theme, platform="documentary")

                out_name = f"final_doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                visual_builder_mod.render_visual_documentary_video(
                    story_title=story_title,
                    story_badge=story_badge,
                    scenes=scenes,
                    output_filename=out_name,
                    tracker=tracker
                )
                return

            # Tự động kết nối video và ảnh tư liệu thực tế nếu là True Story Trojan T-Shirt
            if any(k in (topic or "").lower() for k in ["trojan", "tân phát xít", "exit deutschland", "hardcore rebels"]):
                center_doc = os.path.join(BASE_DIR, "assets", "documentary", "trojan_tshirt", "center_trojan_doc.mp4")
                if os.path.exists(center_doc):
                    post.center_video_custom_path = center_doc
                    card1 = os.path.join(CARDS_DIR, "proof_trojan_before.png")
                    card2 = os.path.join(CARDS_DIR, "proof_trojan_after.png")
                    post.popup_illustrations = [
                        {"path": card1, "target_comment_idx": 2, "badge": "TƯ LIỆU: BAN ĐẦU", "caption": "Chiếc áo đen in hình đầu lâu Hardcore Rebels"},
                        {"path": card2, "target_comment_idx": 5, "badge": "CÚ TWIST: SAU KHI GIẶT", "caption": "Lớp mực tan biến lộ thông điệp của Exit Deutschland"}
                    ]
                    print("  🎬 [TRUE STORY]: Đã tự động gắn video & 2 thẻ tư liệu chiếc áo phông Trojan!")

            if post_data.get("center_video_custom_path"):
                post.center_video_custom_path = post_data["center_video_custom_path"]
            if sets.get("center_satisfying_category") and sets.get("center_satisfying_category") != "random":
                post.satisfying_category = sets.get("center_satisfying_category")

            # BƯỚC 2: RENDER BỘ THẺ CHUẨN NỀN TẢNG
            tracker.start_step("cards", f"Đang vẽ bộ thẻ {platform.upper()} chuẩn (Avatar người thật)...", 30)
            _render_all_cards_helper(post, theme=card_theme, platform=platform)
            tracker.finish_step("cards")

            # BƯỚC 3 -> 6: CHẠY TIẾP ĐỂ DỰNG VIDEO THÀNH PHẨM (TTS, AUDIO, COMPOSITE, EXPORT)
            pipeline_mod.run_pipeline(
                post,
                bg_mode=bg_mode,
                card_theme=card_theme,
                reset_tracker=False,
                skip_cards_render=True,
                layout_preset=layout_preset,
                bgm_choice=bgm_choice,
                bgm_volume=bgm_volume,
                platform=platform
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            tracker.set_error(str(e))

    thread = threading.Thread(target=_full_video_worker)
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "started": True})


@app.route("/api/trending_topics", methods=["GET", "POST"])
def api_trending_topics():
    """Lấy danh sách chủ đề theo ngày (tự động fetch sự kiện lịch sử hoặc drama Threads theo ngày)."""
    force = request.args.get("force", "false").lower() == "true"
    target_date = request.args.get("date")
    mode = request.args.get("mode", "true_story")
    if request.method == "POST":
        data = request.json or {}
        if data.get("force"):
            force = True
        if data.get("date"):
            target_date = data["date"]
        if data.get("mode"):
            mode = data["mode"]
    try:
        radar_mod = load_mod("threads_radar", "threads_radar.py")
        result = radar_mod.get_daily_trending_topics(target_date=target_date, mode=mode, force_refresh=force)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        import datetime
        return jsonify({
            "success": False,
            "error": str(e),
            "date": target_date or datetime.date.today().strftime("%Y-%m-%d"),
            "topics": [
                "Bí ẩn vụ không tặc D.B. Cooper ôm 200.000 USD nhảy dù mất tích năm 1971",
                "Cú lừa ngoạn mục bán tháp Eiffel 2 lần của Victor Lustig năm 1925",
                "Vụ trộm thế kỷ 100 triệu USD kim cương tại hầm kiên cố Antwerp năm 2003",
                "Chiến dịch Trojan T-shirt biến 250 phần tử cực đoan thành biển quảng cáo",
                "Vụ đánh cắp bức tranh Mona Lisa khỏi bảo tàng Louvre năm 1911",
                "Vụ cướp đoàn tàu chở thư tín hoàng gia Anh chấn động thế giới năm 1963",
                "Phi vụ đào tẩu không tưởng khỏi nhà tù trên đảo đá Alcatraz năm 1962",
                "Bí ẩn bức thư đe dọa và danh tính thật của sát nhân Zodiac năm 1969"
            ]
        })


@app.route("/api/save_and_render_cards", methods=["POST"])
def api_save_cards():
    data = request.json
    p_data = data.get("post", {})
    theme = data.get("theme", "light")
    platform = data.get("platform") or p_data.get("platform") or "threads"
    try:
        _render_all_cards_helper(p_data, theme=theme, platform=platform)

        # Dọn dẹp các ảnh comment cũ thừa nếu số lượng comment giảm
        for old_file in glob.glob(os.path.join(CARDS_DIR, "comment_*.png")):
            try:
                base = os.path.basename(old_file)
                num = int(base.replace("comment_", "").replace(".png", ""))
                if num > len(p_data.get("comments", [])):
                    os.remove(old_file)
            except Exception:
                pass

        return jsonify({"success": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/static/js/<path:filename>")
def serve_static_js(filename):
    return send_from_directory(JS_DIR, filename)


@app.route("/assets/js/<path:filename>")
def serve_assets_js(filename):
    return send_from_directory(JS_DIR, filename)


@app.route("/api/card/<filename>")
def get_card_image(filename):
    return send_from_directory(CARDS_DIR, filename)


@app.route("/api/meme/<filename>")
def get_meme_image(filename):
    return send_from_directory(MEMES_DIR, filename)


@app.route("/api/illustration/<filename>")
def get_illustration_file(filename):
    out_path = os.path.join(OUTPUT_DIR, "illustrations", filename)
    if os.path.exists(out_path):
        return send_from_directory(os.path.join(OUTPUT_DIR, "illustrations"), filename)
    asset_path = os.path.join(BASE_DIR, "assets", "illustrations", filename)
    if os.path.exists(asset_path):
        return send_from_directory(os.path.join(BASE_DIR, "assets", "illustrations"), filename)
    return "Not Found", 404


@app.route("/api/search_illustration", methods=["POST"])
def api_search_illustration():
    data = request.json or {}
    queries = data.get("queries") or data.get("query") or ""
    category = data.get("category", "general_life")
    if isinstance(queries, str):
        queries = [q.strip() for q in queries.split(",") if q.strip()]
    try:
        real_img_mod = load_mod("real_image_search", "real_image_search.py")
        illus_dir = os.path.join(OUTPUT_DIR, "illustrations")
        os.makedirs(illus_dir, exist_ok=True)
        filename = f"manual_{int(time.time())}_{random.randint(100, 999)}.jpg"
        target_path = os.path.join(illus_dir, filename)
        res_path = real_img_mod.search_and_download_real_image(
            queries=queries,
            category=category,
            output_path=target_path,
            target_width=940,
            target_height=620
        )
        if res_path and os.path.exists(res_path):
            return jsonify({
                "success": True,
                "illustration_path": res_path,
                "filename": os.path.basename(res_path),
                "preview_url": f"/api/illustration/{os.path.basename(res_path)}?t={int(time.time())}"
            })
        return jsonify({"success": False, "error": "Không tìm thấy ảnh phù hợp"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/upload_illustration", methods=["POST"])
def api_upload_illustration():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "Chưa chọn file"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "error": "File trống"}), 400
    try:
        illus_dir = os.path.join(OUTPUT_DIR, "illustrations")
        os.makedirs(illus_dir, exist_ok=True)
        orig_ext = os.path.splitext(file.filename)[1].lower() or ".jpg"
        
        # Nếu là file video thì lưu trực tiếp, không qua xử lý Pillow
        video_exts = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
        if orig_ext in video_exts:
            filename = f"upload_{int(time.time())}{orig_ext}"
            target_path = os.path.join(illus_dir, filename)
            file.save(target_path)
            return jsonify({
                "success": True,
                "illustration_path": target_path,
                "filename": filename,
                "preview_url": f"/api/illustration/{filename}?t={int(time.time())}"
            })

        # Nếu là ảnh thì chuẩn hóa kích thước 940x620
        from PIL import Image, ImageOps
        filename = f"upload_{int(time.time())}.jpg"
        target_path = os.path.join(illus_dir, filename)
        with Image.open(file.stream) as im:
            im = im.convert("RGB")
            fit_im = ImageOps.fit(im, (940, 620), method=Image.Resampling.LANCZOS)
            fit_im.save(target_path, "JPEG", quality=92)
        return jsonify({
            "success": True,
            "illustration_path": target_path,
            "filename": filename,
            "preview_url": f"/api/illustration/{filename}?t={int(time.time())}"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/avatar_file/<filename>")
def get_avatar_file(filename):
    return send_from_directory(AVATARS_DIR, filename)


@app.route("/api/avatar_gallery")
def get_avatar_gallery():
    """Danh sách 12 ảnh chân dung người thật sắc nét phân loại theo ngữ cảnh chủ đề."""
    items = [
        {"id": "female_office_1.jpg", "label": "Chị Trưởng Phòng", "sub": "Nữ Công Sở #1", "category": "cong_so", "url": "/api/avatar_file/female_office_1.jpg"},
        {"id": "female_office_2.jpg", "label": "Nữ Văn Phòng", "sub": "Nữ Công Sở #2", "category": "cong_so", "url": "/api/avatar_file/female_office_2.jpg"},
        {"id": "female_office_3.jpg", "label": "Nữ Chuyên Viên", "sub": "Nữ Công Sở #3", "category": "cong_so", "url": "/api/avatar_file/female_office_3.jpg"},
        {"id": "male_office_1.jpg", "label": "Anh Quản Lý", "sub": "Nam Công Sở #1", "category": "cong_so", "url": "/api/avatar_file/male_office_1.jpg"},
        {"id": "male_office_2.jpg", "label": "Nam Nhân Viên IT", "sub": "Nam Công Sở #2", "category": "cong_so", "url": "/api/avatar_file/male_office_2.jpg"},
        {"id": "male_office_3.jpg", "label": "Nam Chuyên Viên Dự Án", "sub": "Nam Công Sở #3", "category": "cong_so", "url": "/api/avatar_file/male_office_3.jpg"},
        {"id": "female_student_1.jpg", "label": "Bạn Nữ Gen Z", "sub": "Sinh Viên #1", "category": "sinh_vien", "url": "/api/avatar_file/female_student_1.jpg"},
        {"id": "female_student_2.jpg", "label": "Nữ Sinh Viên", "sub": "Sinh Viên #2", "category": "sinh_vien", "url": "/api/avatar_file/female_student_2.jpg"},
        {"id": "female_student_3.jpg", "label": "Nữ Gen Z Năng Động", "sub": "Sinh Viên #3", "category": "sinh_vien", "url": "/api/avatar_file/female_student_3.jpg"},
        {"id": "male_student_1.jpg", "label": "Bạn Nam Gen Z", "sub": "Sinh Viên #1", "category": "sinh_vien", "url": "/api/avatar_file/male_student_1.jpg"},
        {"id": "male_student_2.jpg", "label": "Nam Sinh Viên", "sub": "Sinh Viên #2", "category": "sinh_vien", "url": "/api/avatar_file/male_student_2.jpg"},
        {"id": "male_student_3.jpg", "label": "Nam Gen Z Trẻ Trung", "sub": "Sinh Viên #3", "category": "sinh_vien", "url": "/api/avatar_file/male_student_3.jpg"}
    ]
    return jsonify({"success": True, "avatars": items})


@app.route("/api/reset_tracker", methods=["POST", "GET"])
def api_reset_tracker():
    tracker.pause_for_review("Hệ thống đã sẵn sàng dựng video.")
    return jsonify({"success": True})


def _async_render_worker(post_obj, bg_mode, card_theme, layout_preset="split_screen", bgm_choice=None, bgm_volume=None, platform="threads"):
    """Hàm chạy nền cho pipeline để không bao giờ bị timeout trên browser."""
    try:
        if platform == "documentary" or getattr(post_obj, "platform", "") == "documentary":
            from copy import deepcopy
            scenes = getattr(post_obj, "documentary_scenes", [])
            if not scenes:
                comments = getattr(post_obj, "comments", [])
                if comments:
                    scenes = []
                    for idx, c in enumerate(comments, 1):
                        c_txt = c.content if hasattr(c, "content") else (c.get("content", "") if isinstance(c, dict) else str(c))
                        c_sfx = getattr(c, "sfx", "none") if hasattr(c, "sfx") else (c.get("sfx", "none") if isinstance(c, dict) else "none")
                        scenes.append({
                            "index": idx,
                            "type": "image" if (idx % 2 == 0) else "video",
                            "path": getattr(post_obj, "illustration_path", ""),
                            "badge": f"PHÂN CẢNH #{idx}",
                            "text": c_txt,
                            "sfx": c_sfx if c_sfx != "none" else "tension_riser",
                            "voice": getattr(post_obj, "voice_id", "adam")
                        })
                else:
                    scenes = deepcopy(visual_builder_mod.DEFAULT_TROJAN_SCENES)

            p_content = getattr(post_obj, "content", "")
            story_title = getattr(post_obj, "video_title", "") or (p_content[:50].strip().upper() if p_content else "HỒ SƠ TƯ LIỆU QUỐC TẾ")
            story_badge = getattr(post_obj, "story_badge", "") or "HỒ SƠ KỲ THÚ • TƯ LIỆU THẬT"

            p_voice = getattr(post_obj, "voice_id", None) or "adam"
            for sc in scenes:
                if not sc.get("voice"):
                    sc["voice"] = p_voice

            out_name = f"final_doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            visual_builder_mod.render_visual_documentary_video(
                story_title=story_title,
                story_badge=story_badge,
                scenes=scenes,
                output_filename=out_name,
                tracker=tracker
            )
            return

        pipeline_mod.run_pipeline(
            post_obj,
            bg_mode=bg_mode,
            card_theme=card_theme,
            layout_preset=layout_preset,
            bgm_choice=bgm_choice,
            bgm_volume=bgm_volume,
            platform=platform
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        tracker.set_error(str(e))


@app.route("/api/render_video", methods=["POST"])
def api_render_video():
    data = request.json
    p_data = data.get("post", {})
    sets = data.get("settings", {})

    try:
        cfg = config_mod.load_config()
        roles = cfg.get("voice_casting", {}).get("roles", {})
        chosen_rate = sets.get("rate")
        chosen_voice = sets.get("voice")

        comments = []
        for idx, c in enumerate(p_data.get("comments", []), 1):
            r_info = roles.get(f"commenter_{idx}", {})
            cmt_rate = chosen_rate if chosen_rate else r_info.get("rate", "+20%")
            cmt_voice = c.get("voice_id") or r_info.get("voice", "vi-VN-HoaiMyNeural")
            comments.append(content_mod.Comment(
                username=c.get("username", "user"),
                content=c.get("content", ""),
                reply_to=c.get("reply_to", "") or p_data.get("username", "author"),
                avatar_color=c.get("avatar_color", "#8b5cf6"),
                avatar_path=c.get("avatar_path", ""),
                avatar_url=c.get("avatar_url", ""),
                likes=int(c.get("likes", 1200)),
                sfx=c.get("sfx", "none"),
                emotion=c.get("emotion", "funny"),
                voice_id=cmt_voice,
                rate_offset=cmt_rate,
                pitch_offset=r_info.get("pitch", "+2Hz"),
                pause_before_ms=int(c.get("pause_before_ms", 200)),
                pause_after_ms=int(c.get("pause_after_ms", 350))
            ))

        platform = sets.get("platform") or p_data.get("platform") or "threads"
        author_role = roles.get("author", {})
        post_rate = chosen_rate or author_role.get("rate", "+18%")
        post_voice = p_data.get("voice_id") or chosen_voice or author_role.get("voice", "adam")
        post_obj = content_mod.Post(
            username=p_data.get("username", "author"),
            content=p_data.get("content", ""),
            avatar_color=p_data.get("avatar_color", "#6366f1"),
            avatar_path=p_data.get("avatar_path", ""),
            avatar_url=p_data.get("avatar_url", ""),
            likes=int(p_data.get("likes", 25000)),
            is_verified=bool(p_data.get("is_verified", True)),
            voice_id=post_voice,
            rate_offset=post_rate,
            pitch_offset=author_role.get("pitch", "+4Hz"),
            illustration_prompt=p_data.get("illustration_prompt", ""),
            illustration_path=p_data.get("illustration_path", ""),
            illustration_caption=p_data.get("illustration_caption", ""),
            illustration_category=p_data.get("illustration_category", "general_life"),
            has_visual_proof=bool(p_data.get("has_visual_proof", False)),
            visual_proof_placement=p_data.get("visual_proof_placement", "none"),
            sentence_reveal_mode=p_data.get("sentence_reveal_mode", "standard_full"),
            satisfying_category=p_data.get("satisfying_category", "auto"),
            recommended_theme=p_data.get("recommended_theme", "light"),
            story_context_type=p_data.get("story_context_type", "general_social"),
            cta_question=p_data.get("cta_question", ""),
            pacing_tempo=p_data.get("pacing_tempo", "normal"),
            enable_illustration=bool(p_data.get("enable_illustration", False)),
            emotional_arc=p_data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
            platform=platform,
            comments=comments
        )
        post_obj.documentary_scenes = p_data.get("documentary_scenes", [])
        post_obj.story_badge = p_data.get("story_badge", "")
        post_obj.video_title = p_data.get("video_title", "")
        post_obj.center_video_custom_path = p_data.get("center_video_custom_path", "")
        if sets.get("center_satisfying_category") and sets.get("center_satisfying_category") != "random":
            post_obj.satisfying_category = sets.get("center_satisfying_category")

        layout_preset = sets.get("layout_preset", "center")
        bg_mode = sets.get("bg_mode", "auto")
        card_theme = sets.get("card_theme", "auto")
        bgm_choice = sets.get("bgm_choice", "random")
        raw_vol = sets.get("bgm_volume")
        bgm_volume = (float(raw_vol) / 100.0) if raw_vol is not None else None

        # Khởi chạy quy trình render trong background thread
        thread = threading.Thread(
            target=_async_render_worker,
            args=(post_obj, bg_mode, card_theme, layout_preset, bgm_choice, bgm_volume, platform)
        )
        thread.daemon = True
        thread.start()

        return jsonify({"success": True, "started": True})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/batch_render", methods=["POST"])
def api_batch_render():
    data = request.json
    count = int(data.get("count", 3))
    try:
        batch_mod = load_mod("batch", "07_batch_factory.py")
        completed = []
        api_key = ai_mod.get_or_prompt_api_key()
        cfg = config_mod.load_config()
        radar_mod = load_mod("threads_radar", "threads_radar.py")
        daily_res = radar_mod.get_daily_trending_topics()
        topics = daily_res.get("topics")
        if not topics:
            topics = [radar_mod.crawl_trending_threads_story().content[:40]]

        for i in range(1, count + 1):
            t = topics[(i - 1) % len(topics)]
            p = ai_mod.generate_story_with_gemini(t, api_key=api_key)
            v = pipeline_mod.run_pipeline(p)
            completed.append(os.path.basename(v))

        return jsonify({"success": True, "completed": completed})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/documentary_media")
def api_documentary_media():
    rel_path = request.args.get("path", "").strip()
    if not rel_path:
        return "Missing path", 400
    full_path = os.path.abspath(os.path.join(BASE_DIR, rel_path) if not os.path.isabs(rel_path) else rel_path)
    if not os.path.exists(full_path):
        return "File not found", 404
    return send_from_directory(os.path.dirname(full_path), os.path.basename(full_path))


@app.route("/api/latest_video")
def api_latest_video():
    files = [f for f in glob.glob(os.path.join(FINAL_DIR, "*.mp4")) if not os.path.basename(f).startswith("_")]
    if not files:
        return jsonify({"exists": False})
    latest = max(files, key=os.path.getmtime)
    cap_file = latest.replace(".mp4", "_caption.txt")
    caption = ""
    if os.path.exists(cap_file):
        with open(cap_file, "r", encoding="utf-8") as f:
            caption = f.read()
    return jsonify({
        "exists": True,
        "filename": os.path.basename(latest),
        "caption": caption
    })


@app.route("/api/video/<filename>")
def get_video_file(filename):
    return send_from_directory(FINAL_DIR, filename, mimetype="video/mp4")


@app.route("/api/open_folder")
def api_open_folder():
    try:
        os.makedirs(FINAL_DIR, exist_ok=True)
        if os.name == "nt":
            try:
                os.startfile(FINAL_DIR)
            except Exception:
                import subprocess
                subprocess.Popen(["explorer", FINAL_DIR])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", FINAL_DIR])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ─── MARKET INTELLIGENCE & TELEGRAM REMOTE CONTROL API ───────────────────────


@app.route("/api/market/status", methods=["GET"])
def api_market_status():
    rt = get_market_runtime()
    store = rt["store"]
    return jsonify({
        "is_syncing": rt["sync_service"].is_syncing,
        "stats": store.get_stats(),
        "filter_options": store.get_filter_options(),
        "recent_runs": store.list_sync_runs(limit=15),
        "cloudflare": rt["client"].get_public_config_summary(),
        "remote_poller": rt["remote_poller"].get_status(),
    })


@app.route("/api/market/test_connection", methods=["POST"])
def api_market_test_connection():
    rt = get_market_runtime()
    return jsonify(rt["client"].check_connection())


@app.route("/api/market/sync", methods=["POST"])
def api_market_sync():
    rt = get_market_runtime()
    wait = request.args.get("wait", "0") == "1"
    if wait:
        res = rt["sync_service"].sync_now(trigger_source="manual_ui")
        return jsonify({"success": res.get("status") == "completed", "result": res})
    res = rt["sync_service"].trigger_async_sync(trigger_source="manual_ui")
    return jsonify({"success": True, **res})


@app.route("/api/market/config", methods=["GET", "POST"])
def api_market_config():
    rt = get_market_runtime()
    if request.method == "POST":
        data = request.json or {}
        sec = data.get("sync_interval_seconds")
        updated = rt["sync_service"].get_interval_seconds()
        if sec is not None:
            updated = rt["sync_service"].set_interval_seconds(int(sec))
        return jsonify({"success": True, "sync_interval_seconds": updated})
    return jsonify({
        "sync_interval_seconds": rt["sync_service"].get_interval_seconds(),
        "cloudflare": rt["client"].get_public_config_summary(),
    })


@app.route("/api/market/items", methods=["GET"])
def api_market_items():
    rt = get_market_runtime()
    platform = request.args.get("platform", "all")
    content_group = request.args.get("content_group", "all")
    purchase_intent = request.args.get("purchase_intent", "all")
    data_quality = request.args.get("data_quality", "all")
    trend_tier = request.args.get("trend_tier", "all")
    trend_group = request.args.get("trend_group", "all")
    verification_status = request.args.get("verification_status", "all")
    sort_by = request.args.get("sort_by", "recent")
    affiliate_only = request.args.get("affiliate_only", "0") in ("1", "true", "True")
    search = request.args.get("search", "")
    try:
        limit = int(request.args.get("limit", "25"))
    except ValueError:
        limit = 25
    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        offset = 0
    return jsonify(
        rt["store"].query_items(
            platform=platform,
            content_group=content_group,
            purchase_intent=purchase_intent,
            data_quality=data_quality,
            trend_tier=trend_tier,
            trend_group=trend_group,
            verification_status=verification_status,
            search=search,
            sort_by=sort_by,
            affiliate_only=affiliate_only,
            limit=limit,
            offset=offset,
        )
    )


@app.route("/api/market/items/<item_id>", methods=["GET"])
def api_market_item_detail(item_id):
    rt = get_market_runtime()
    detail = rt["store"].get_item_detail(item_id)
    if not detail:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"item": detail})


@app.route("/api/market/files", methods=["GET"])
def api_market_files():
    rt = get_market_runtime()
    return jsonify({"files": rt["store"].list_raw_files(limit=50)})


@app.route("/api/market/runs", methods=["GET"])
def api_market_runs():
    rt = get_market_runtime()
    return jsonify({"runs": rt["store"].list_sync_runs(limit=30)})


@app.route("/api/market/import_local", methods=["POST"])
def api_market_import_local():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "Missing file"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400
    raw_bytes = f.read()
    rt = get_market_runtime()
    try:
        res = rt["store"].ingest_raw_file(
            file_id=f"local_{f.filename}",
            file_name=f.filename,
            raw_bytes=raw_bytes,
            folder_path="local_upload",
        )
        return jsonify({"success": True, "result": res})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/remote/status", methods=["GET"])
def api_remote_status():
    rt = get_market_runtime()
    return jsonify(rt["remote_poller"].get_status())


@app.route("/api/market/report", methods=["GET"])
def api_market_report():
    rt = get_market_runtime()
    platform = request.args.get("platform", "all")
    report = rt["store"].build_evidence_report(platform=platform)
    saved = rt["store"].list_reports(limit=10)
    return jsonify({"report": report, "saved_reports": saved})


@app.route("/api/remote/crawl_requests", methods=["GET", "POST"])
def api_remote_crawl_requests():
    rt = get_market_runtime()
    if not rt["client"].is_configured():
        return jsonify({"ok": False, "error": "Thiếu CLOUDFLARE_APP_TOKEN"}), 503
    try:
        if request.method == "POST":
            body = request.json or {}
            return jsonify(
                rt["client"].create_crawl_request(
                    platform=str(body.get("platform", "")).lower().strip(),
                    keywords=str(body.get("keywords", "")).strip(),
                    requested_count=int(body.get("requested_count") or 50),
                    poll_profile=body.get("poll_profile"),
                    dedup_token=body.get("dedup_token"),
                    options=body.get("options") if isinstance(body.get("options"), dict) else {},
                )
            )
        return jsonify(rt["client"].list_crawl_requests())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/api/remote/crawl_requests/<crawl_id>/cancel", methods=["POST"])
def api_remote_crawl_cancel(crawl_id):
    rt = get_market_runtime()
    if not rt["client"].is_configured():
        return jsonify({"ok": False, "error": "Thiếu CLOUDFLARE_APP_TOKEN"}), 503
    try:
        return jsonify(rt["client"].cancel_crawl_request(crawl_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/api/remote/crawl_schedule", methods=["POST"])
def api_remote_crawl_schedule():
    rt = get_market_runtime()
    if not rt["client"].is_configured():
        return jsonify({"ok": False, "error": "Thiếu CLOUDFLARE_APP_TOKEN"}), 503
    try:
        body = request.json or {}
        return jsonify(rt["client"].set_crawl_schedule(str(body.get("poll_profile", "production_6h"))))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


def start_studio(port=5000, auto_open=True):
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║     🎬 THREADS VIDEO AUTOMATION STUDIO 2.0 (AI DIRECTOR)  ║")
    print("╚" + "═" * 58 + "╝")
    print(f"👉 Studio đang mở tại: http://localhost:{port}")
    if os.environ.get("APP_VIDEO_DISABLE_BG_WORKERS", "0") != "1":
        rt = get_market_runtime()
        rt["sync_service"].start(sync_on_startup=True)
        rt["remote_poller"].start()
    if auto_open:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    bind_host = os.environ.get("APP_VIDEO_HOST", "127.0.0.1").strip() or "127.0.0.1"
    app.run(host=bind_host, port=port, debug=False)


if __name__ == "__main__":
    start_studio()
