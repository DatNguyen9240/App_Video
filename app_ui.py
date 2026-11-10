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
import threading
import subprocess
import webbrowser
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_file, send_from_directory

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SRC_DIR    = os.path.join(BASE_DIR, "src")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
FINAL_DIR  = os.path.join(OUTPUT_DIR, "final")
CARDS_DIR  = os.path.join(OUTPUT_DIR, "cards")
AUDIO_DIR  = os.path.join(OUTPUT_DIR, "audio")
MEMES_DIR  = os.path.join(BASE_DIR, "assets", "memes")
AVATARS_DIR = os.path.join(BASE_DIR, "assets", "avatars")
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
  <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
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
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
    body { font-family: 'Plus Jakarta Sans', sans-serif; }
    .font-mono { font-family: 'JetBrains Mono', monospace; }
    .glass-panel {
      background: rgba(15, 23, 42, 0.78);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .glass-input {
      background: rgba(30, 41, 59, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .glass-input:focus {
      border-color: #6366f1;
      box-shadow: 0 0 15px rgba(99, 102, 241, 0.25);
    }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #475569; }
  </style>
</head>
<body class="bg-dark-900 text-slate-100 min-h-screen">
  <div id="app" class="flex flex-col min-h-screen">
    
    <!-- Top Navigation Header -->
    <header class="h-16 border-b border-slate-800 glass-panel sticky top-0 z-50 px-6 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-brand-600 via-indigo-500 to-pink-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
          <i class="fa-solid fa-wand-magic-sparkles text-white text-lg"></i>
        </div>
        <div>
          <h1 class="text-lg font-bold bg-gradient-to-r from-white via-slate-200 to-indigo-300 bg-clip-text text-transparent">
            Threads Video Studio <span class="text-xs px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-semibold">AI Director 2.0</span>
          </h1>
          <div class="text-xs text-slate-400 flex items-center gap-2">
            <span class="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            Gemini 3.5 Flash • Multi-Voice Acting • Đồ Thị Cảm Xúc
          </div>
        </div>
      </div>

      <!-- Center Tabs -->
      <nav class="flex items-center bg-slate-900/80 p-1.5 rounded-xl border border-slate-800">
        <button @click="currentTab = 'studio'" :class="currentTab === 'studio' ? 'bg-brand-600 text-white font-bold shadow-md shadow-indigo-600/30' : 'text-slate-400 hover:text-slate-200'"
          class="px-4 py-1.5 rounded-lg text-xs transition flex items-center gap-2">
          <i class="fa-solid fa-film"></i>
          <span>1. Studio Sáng Tạo</span>
        </button>

        <button @click="currentTab = 'pipeline'" :class="currentTab === 'pipeline' ? 'bg-brand-600 text-white font-bold shadow-md shadow-indigo-600/30' : 'text-slate-400 hover:text-slate-200'"
          class="px-4 py-1.5 rounded-lg text-xs transition flex items-center gap-2 relative">
          <i class="fa-solid fa-chart-line"></i>
          <span>2. Giám Sát Quy Trình</span>
          <span v-if="pipelineState.is_running" class="w-2 h-2 rounded-full bg-pink-500 animate-ping absolute -top-0.5 -right-0.5"></span>
        </button>

        <button @click="currentTab = 'config'" :class="currentTab === 'config' ? 'bg-brand-600 text-white font-bold shadow-md shadow-indigo-600/30' : 'text-slate-400 hover:text-slate-200'"
          class="px-4 py-1.5 rounded-lg text-xs transition flex items-center gap-2">
          <i class="fa-solid fa-sliders"></i>
          <span>3. Cấu Hình Đạo Diễn</span>
        </button>
      </nav>

      <!-- Right Action Tools -->
      <div class="flex items-center space-x-3">
        <button @click="openFinalFolder" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold glass-input hover:bg-slate-700/60 transition flex items-center gap-2 text-slate-300">
          <i class="fa-regular fa-folder-open text-indigo-400"></i> Thư Mục Video
        </button>
        <button @click="saveSettings" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600/80 hover:bg-indigo-600 shadow-md shadow-indigo-600/30 transition flex items-center gap-2">
          <i class="fa-solid fa-floppy-disk"></i> Lưu Nhanh
        </button>
      </div>
    </header>

    <!-- TAB 1: STUDIO SÁNG TẠO & BIÊN TẬP -->
    <main v-show="currentTab === 'studio'" class="flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
      
      <!-- Left Panel: Controls & Script Editor (7 cols) -->
      <div class="lg:col-span-7 flex flex-col space-y-6">
        
        <!-- Section 1: AI Prompt & Generator -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-2">
              <i class="fa-solid fa-wand-magic-sparkles"></i> 1. Sáng Tạo Kịch Bản Bằng Gemini AI & Quét Threads
            </h2>
            <span class="text-xs px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-300 font-mono">gemini-3.5-flash</span>
          </div>

          <div class="space-y-3">
            <div class="flex items-center gap-2.5 flex-wrap">
              <input v-model="topicInput" @keyup.enter="generateScriptOnly" type="text"
                placeholder="Nhập chủ đề muốn làm video (ví dụ: Chuyện công sở, phỏng vấn xin việc, người yêu cũ... hoặc chọn trend bên dưới)"
                class="flex-1 min-w-[280px] glass-input rounded-xl px-4 py-3 text-sm focus:outline-none placeholder-slate-500 font-medium">
              
              <!-- Nút 1: Tạo kịch bản để duyệt và sửa trước -->
              <button @click="generateScriptOnly" :disabled="pipelineState.is_running || isGeneratingScript"
                title="AI nghĩ kịch bản và tải vào form để bạn tự do đọc duyệt và sửa từng câu chữ trước khi dựng video"
                class="px-4 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs shadow-md shadow-indigo-600/30 transition flex items-center gap-2 disabled:opacity-50 cursor-pointer">
                <i v-if="isGeneratingScript" class="fa-solid fa-spinner fa-spin text-sm"></i>
                <i v-else class="fa-solid fa-pen-nib text-sm"></i>
                <span>📝 AI TẠO KỊCH BẢN ĐỂ DUYỆT</span>
              </button>

              <!-- Nút 2: Cào bài viết thịnh hành thật trên Threads -->
              <button @click="crawlTrendingThreads(false)" :disabled="pipelineState.is_running || isCrawlingTrending"
                title="Quét bài viết thịnh hành thật trên Threads kèm avatar người thật đưa vào bảng duyệt"
                class="px-4 py-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-cyan-500/40 font-bold text-xs shadow-md transition flex items-center gap-2 disabled:opacity-50 cursor-pointer">
                <i v-if="isCrawlingTrending" class="fa-solid fa-spinner fa-spin text-sm"></i>
                <i v-else class="fa-solid fa-hashtag text-sm"></i>
                <span>🌐 DÒ QUÉT THREADS</span>
              </button>

              <!-- Nút 3: Tự động 100% 1-Click từ A-Z -->
              <button @click="createVideoOneClick" :disabled="pipelineState.is_running"
                title="Tự động 100% từ A đến Z: AI kịch bản -> Avatar thật -> Render video 60 FPS ngay"
                class="px-4 py-3 rounded-xl bg-gradient-to-r from-pink-600 via-rose-500 to-indigo-600 hover:opacity-95 text-white font-extrabold text-xs shadow-lg shadow-pink-500/30 transition flex items-center gap-2 disabled:opacity-50 cursor-pointer">
                <i v-if="pipelineState.is_running" class="fa-solid fa-spinner fa-spin text-sm"></i>
                <i v-else class="fa-solid fa-bolt text-sm"></i>
                <span>🚀 1-CLICK TỰ ĐỘNG</span>
              </button>
            </div>

            <!-- Quick Viral Chips (Daily Fresh Trends) -->
            <div class="space-y-2 pt-1">
              <div class="flex items-center justify-between text-xs flex-wrap gap-2">
                <div class="flex items-center gap-2">
                  <span class="text-amber-400 font-bold flex items-center gap-1.5">
                    <i class="fa-solid fa-fire text-amber-500"></i>
                    <span>Trend Threads Hôm Nay</span>
                    <span v-if="trendingTopicsInfo.date_display" class="px-2 py-0.5 rounded-full text-[10px] bg-amber-500/15 text-amber-300 border border-amber-500/30 font-mono font-medium">
                      {{ trendingTopicsInfo.date_display }}
                    </span>
                  </span>
                  <span v-if="trendingTopicsInfo.updated_at" class="text-[10px] text-slate-500 hidden md:inline">
                    (Cập nhật lúc: {{ trendingTopicsInfo.updated_at }})
                  </span>
                </div>
                <button @click="loadDailyTrendingTopics(true)" :disabled="isRefreshingTrends"
                  class="text-[11px] px-2.5 py-1 rounded-lg bg-slate-800/90 hover:bg-slate-700 text-indigo-300 hover:text-indigo-200 border border-slate-700/60 transition flex items-center gap-1.5 disabled:opacity-50 shadow-sm"
                  title="Quét lại xu hướng thịnh hành mới nhất trên Threads hôm nay">
                  <i class="fa-solid fa-arrows-rotate text-indigo-400" :class="{'fa-spin': isRefreshingTrends}"></i>
                  <span class="font-medium">{{ isRefreshingTrends ? 'Đang quét...' : 'Làm mới trend hôm nay' }}</span>
                </button>
              </div>

              <div class="flex items-center gap-1.5 flex-wrap">
                <button v-for="t in viralTopics" :key="t" @click="pickTopic(t)"
                  class="text-xs px-2.5 py-1 rounded-full bg-slate-800/80 hover:bg-slate-700 text-slate-300 border border-slate-700/50 transition hover:border-amber-500/60 hover:text-amber-200 hover:shadow-sm">
                  {{ t }}
                </button>
              </div>
            </div>

            <!-- Emotional Arc Badge -->
            <div v-if="post.emotional_arc" class="p-2.5 rounded-xl bg-indigo-950/40 border border-indigo-800/40 flex items-center justify-between text-xs">
              <span class="text-indigo-300 font-semibold"><i class="fa-solid fa-wave-square text-indigo-400 mr-1.5"></i> Đồ thị cảm xúc:</span>
              <span class="text-slate-300 font-medium">{{ post.emotional_arc }}</span>
            </div>
          </div>
        </div>

        <!-- Section 2: Script Detail Editor & Review Workspace -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3 flex-wrap gap-2">
            <div>
              <h2 class="text-sm font-bold uppercase tracking-wider text-pink-400 flex items-center gap-2">
                <i class="fa-solid fa-pen-to-square"></i> 2. Bảng Soạn & Duyệt Kịch Bản (Tự Do Chỉnh Sửa)
              </h2>
              <p class="text-xs text-slate-400 mt-0.5">Sửa câu từ, chọn ảnh người thật, đổi Meme GIF/SFX, thêm hoặc xóa bình luận tùy ý.</p>
            </div>
            <button @click="refreshCardPreview" class="px-3 py-1.5 rounded-xl bg-indigo-600/80 hover:bg-indigo-600 text-white text-xs font-semibold flex items-center gap-1.5 transition shadow-sm cursor-pointer">
              <i class="fa-solid fa-rotate-right"></i>
              <span>Cập Nhật Thẻ Xem Trước</span>
            </button>
          </div>

          <!-- Post Editor (Author) -->
          <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-3">
            <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
              <span class="text-xs font-bold text-amber-400 flex items-center gap-1.5">
                <i class="fa-solid fa-feather-pointed"></i> BÀI ĐĂNG CỦA TÁC GIẢ (HOOK MỞ MÀN)
              </span>
              <div class="flex items-center gap-3">
                <label class="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
                  <input type="checkbox" v-model="post.is_verified" class="rounded bg-slate-800 border-slate-700 text-indigo-500 focus:ring-0">
                  <span class="text-indigo-400 font-semibold">Tích xanh ✓</span>
                </label>
                <div class="flex items-center gap-1.5 text-xs text-slate-400">
                  <i class="fa-regular fa-heart text-rose-500"></i>
                  <input v-model="post.likes" type="number" class="glass-input rounded-lg px-2 py-0.5 text-xs w-20 text-right font-mono">
                </div>
              </div>
            </div>

            <div class="flex items-center gap-2.5">
              <span class="text-xs text-slate-400 font-medium">Tên tác giả:</span>
              <input v-model="post.username" type="text" class="glass-input rounded-lg px-2.5 py-1 text-xs w-44 font-semibold text-slate-200">
            </div>

            <textarea v-model="post.content" rows="3"
              class="w-full glass-input rounded-xl p-3 text-sm focus:outline-none placeholder-slate-500 font-normal leading-relaxed"
              placeholder="Nội dung bài đăng chính..."></textarea>
            
            <!-- Avatar Picker Row for Author -->
            <div class="flex items-center gap-2.5 pt-1 text-xs flex-wrap">
              <img :src="getAvatarThumb(post.avatar_url || post.avatar_path)" alt="Author Avatar"
                class="w-8 h-8 rounded-full border-2 border-indigo-500/60 object-cover shadow-sm bg-slate-800">
              <button @click="openAvatarModal('author')"
                class="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-indigo-300 border border-indigo-500/40 text-xs font-semibold flex items-center gap-1.5 transition">
                <i class="fa-solid fa-image-portrait"></i>
                <span>📷 Chọn Avatar Người Thật</span>
              </button>
              <input v-model="post.avatar_url" type="text" placeholder="Dán link ảnh đại diện hoặc để trống để tự động lấy từ kho người thật"
                class="flex-1 min-w-[200px] glass-input rounded-lg px-2.5 py-1 text-[11px] font-mono text-slate-300">
            </div>
          </div>

          <!-- Comments Editor -->
          <div class="space-y-3 pt-1">
            <div class="flex items-center justify-between text-xs font-bold text-slate-400 uppercase tracking-wide">
              <span class="flex items-center gap-2 text-indigo-300">
                <i class="fa-solid fa-comments"></i>
                <span>Các Bình Luận Tương Tác & Bẻ Lái ({{ post.comments ? post.comments.length : 0 }} bình luận)</span>
              </span>
              <button @click="addComment"
                class="px-3 py-1 rounded-lg bg-emerald-600/80 hover:bg-emerald-600 text-white font-bold text-xs flex items-center gap-1.5 transition shadow-sm cursor-pointer">
                <i class="fa-solid fa-plus"></i>
                <span>➕ Thêm Bình Luận</span>
              </button>
            </div>
            
            <div v-for="(cmt, idx) in post.comments" :key="idx"
              class="p-3.5 rounded-xl bg-slate-900/50 border border-slate-800/80 space-y-2.5">
              <div class="flex items-center gap-2.5 flex-wrap">
                <span class="w-6 h-6 rounded-full bg-indigo-500/20 text-indigo-300 text-xs flex items-center justify-center font-bold">#{{ idx+1 }}</span>
                <input v-model="cmt.username" type="text" placeholder="username" class="glass-input rounded-lg px-2.5 py-1 text-xs w-32 font-semibold text-slate-200">
                
                <!-- Emotion Badge -->
                <select v-model="cmt.emotion" class="glass-input rounded-lg px-2 py-1 text-xs bg-slate-800 text-indigo-300 font-medium">
                  <option value="funny">😂 Hài hước</option>
                  <option value="shocked">💥 Bẻ lái sốc</option>
                  <option value="awkward">💀 Cạn lời</option>
                  <option value="sarcastic">😏 Châm biếm</option>
                  <option value="insightful">✨ Thông minh</option>
                </select>

                <!-- SFX & Meme selector (14 Gifs) -->
                <div class="flex items-center gap-1.5 ml-auto flex-wrap">
                  <span class="text-xs text-slate-500">Meme/SFX:</span>
                  <select v-model="cmt.sfx" class="glass-input rounded-lg px-2 py-1 text-xs bg-slate-800 text-slate-200 font-medium">
                    <option value="none">Không SFX</option>
                    <option value="laugh">😂 Cười nghiêng ngả (laugh.gif)</option>
                    <option value="boom">💥 Mind Blown bẻ lái (boom.gif)</option>
                    <option value="bruh">💀 Cạn lời Facepalm (bruh.gif)</option>
                    <option value="shock">🙀 Sốc hoảng loạn (shock.gif)</option>
                    <option value="huh">🐱 Mèo "Huh?" ngơ ngác (huh.gif)</option>
                    <option value="side_eye">👀 Liếc xéo nghi ngờ (side_eye.gif)</option>
                    <option value="clown">🤡 Tự hóa chú hề (clown.gif)</option>
                    <option value="math">📐 Tính toán hoang mang (math.gif)</option>
                    <option value="applause">👏 Vỗ tay / Nâng ly (applause.gif)</option>
                    <option value="ting">✨ Ting nảy số thông minh (ting.gif)</option>
                    <option value="awkward">😬 Ngại ngùng bối rối (awkward.gif)</option>
                    <option value="facepalm">🤦 Đập trán bất lực (facepalm.gif)</option>
                    <option value="shocked_cat">🙀 Mèo há hốc sốc (shocked_cat.gif)</option>
                    <option value="dancing_cat">💃 Mèo nhảy ăn mừng (dancing_cat.gif)</option>
                  </select>

                  <!-- Nút Xóa Comment nếu thừa -->
                  <button @click="removeComment(idx)" title="Xóa bình luận này"
                    class="w-7 h-7 rounded-lg bg-rose-500/15 hover:bg-rose-500/30 text-rose-400 flex items-center justify-center transition border border-rose-500/30">
                    <i class="fa-solid fa-trash-can text-xs"></i>
                  </button>
                </div>
              </div>

              <textarea v-model="cmt.content" rows="2"
                class="w-full glass-input rounded-xl p-2.5 text-xs focus:outline-none placeholder-slate-500"
                :placeholder="'Bình luận ' + (idx+1) + '...'"></textarea>

              <!-- Avatar Picker Row for Commenter -->
              <div class="flex items-center gap-2 pt-0.5 text-[11px] text-slate-400 flex-wrap">
                <img :src="getAvatarThumb(cmt.avatar_url || cmt.avatar_path)" alt="Comment Avatar"
                  class="w-6 h-6 rounded-full border border-slate-700 object-cover bg-slate-800">
                <button @click="openAvatarModal('comment', idx)"
                  class="px-2.5 py-0.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-indigo-300 border border-indigo-500/30 text-[11px] font-semibold flex items-center gap-1 transition">
                  <i class="fa-solid fa-image-portrait"></i>
                  <span>📷 Chọn Avatar</span>
                </button>
                <input v-model="cmt.avatar_url" type="text" placeholder="Link ảnh hoặc để trống để gán ảnh thật"
                  class="flex-1 min-w-[180px] glass-input rounded-lg px-2 py-0.5 text-[10px] font-mono text-slate-300">
              </div>
            </div>
          </div>
        </div>

        <!-- Section 3: Phong Cách Dựng Video & Bố Cục Đa Dạng -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3">
            <h2 class="text-sm font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-2">
              <i class="fa-solid fa-sliders"></i> 3. Phong Cách Dựng Video & Bố Cục (Đa Dạng Hóa)
            </h2>
            <span class="text-xs text-slate-400">Tùy biến chuẩn TikTok & Reels</span>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3.5">
            <!-- Bố cục video (Layout Presets) -->
            <div class="space-y-1.5">
              <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                <i class="fa-solid fa-table-columns text-indigo-400"></i> Bố cục khung hình:
              </label>
              <select v-model="settings.layout_preset" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                <option value="split_screen">📱 TikTok Split-Screen (Hot nhất)</option>
                <option value="center">🎯 Center Focus (Chính giữa)</option>
                <option value="bottom_story">📖 Storyteller (Góc dưới tối giản)</option>
              </select>
            </div>

            <!-- Chế độ nền video -->
            <div class="space-y-1.5">
              <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                <i class="fa-solid fa-film text-pink-400"></i> Chế độ nền video:
              </label>
              <select v-model="settings.bg_mode" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                <option value="split_gameplay">🔥 Split (Gameplay 60FPS + Thẻ dưới)</option>
                <option value="minecraft">🎮 Minecraft Parkour Full 9:16</option>
                <option value="infinity_neon">🌀 3D Infinity Cyberpunk (Neon)</option>
                <option value="infinity_dark">🌑 3D Infinity Dark Luxury (Kim loại)</option>
                <option value="infinity_gold">✨ 3D Infinity Golden Matrix (Vàng)</option>
                <option value="gradient_cyber">🌌 Gradient Dark Cyberpunk (Tĩnh)</option>
                <option value="gradient_lofi">☕ Gradient Lofi Cozy (Ấm cúng)</option>
              </select>
            </div>

            <!-- Giao diện thẻ -->
            <div class="space-y-1.5">
              <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                <i class="fa-solid fa-palette text-amber-400"></i> Giao diện thẻ Threads:
              </label>
              <select v-model="settings.card_theme" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                <option value="light">☀️ Sáng (Chuẩn Threads thật 100%)</option>
                <option value="dark">🌙 Tối (Dark Mode OLED)</option>
                <option value="glass">🔮 Glassmorphism (Kính mờ viền sáng)</option>
                <option value="cyber_neon">⚡ Cyber Neon (Viền hồng dạ quang)</option>
              </select>
            </div>

            <!-- Giọng đọc tác giả -->
            <div class="space-y-1.5">
              <label class="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                <i class="fa-solid fa-microphone text-emerald-400"></i> Giọng đọc dẫn chuyện:
              </label>
              <select v-model="settings.voice" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800 text-slate-200">
                <option value="vi-VN-NamMinhNeural">🎙️ Nam Minh (Nam trầm ấm)</option>
                <option value="vi-VN-HoaiMyNeural">🎙️ Hoài My (Nữ truyền cảm)</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Big Render Action Button: Duyệt Kịch Bản & Render Ngay -->
        <div>
          <button @click="startRender" :disabled="pipelineState.is_running"
            class="w-full py-4 rounded-2xl bg-gradient-to-r from-brand-600 via-indigo-500 to-pink-500 hover:opacity-95 transition font-extrabold text-base shadow-xl shadow-indigo-600/30 flex items-center justify-center gap-3 disabled:opacity-50 text-white cursor-pointer">
            <i v-if="pipelineState.is_running" class="fa-solid fa-spinner fa-spin text-xl"></i>
            <i v-else class="fa-solid fa-circle-check text-xl text-emerald-300"></i>
            <span>{{ pipelineState.is_running ? '⏳ ' + pipelineState.message : '🚀 DUYỆT KỊCH BẢN & BẮT ĐẦU DỰNG VIDEO (RENDER NGAY)' }}</span>
          </button>
          <p class="text-center text-[11px] text-slate-500 mt-2">
            Hệ thống sẽ lấy chính xác kịch bản và phong cách dựng video bạn vừa duyệt để xuất file 60 FPS chuẩn Studio.
          </p>
        </div>

      </div>

      <!-- Right Panel: Live Preview & Player (5 cols) -->
      <div class="lg:col-span-5 flex flex-col space-y-6">
        
        <!-- Video Player / Card Preview Container -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl flex flex-col items-center">
          <div class="w-full flex items-center justify-between mb-4 border-b border-slate-800 pb-3">
            <div class="flex items-center gap-2">
              <button @click="previewMode = 'video'" :class="{'text-indigo-400 font-bold border-b-2 border-indigo-400': previewMode === 'video'}" class="text-xs px-2 py-1">
                <i class="fa-solid fa-play"></i> Xem Video
              </button>
              <button @click="previewMode = 'cards'" :class="{'text-indigo-400 font-bold border-b-2 border-indigo-400': previewMode === 'cards'}" class="text-xs px-2 py-1">
                <i class="fa-solid fa-image"></i> Xem Thẻ Card
              </button>
            </div>
            <span class="text-xs text-slate-500 font-mono">9:16 Vertical</span>
          </div>

          <!-- Video Player Display -->
          <div v-show="previewMode === 'video'" class="w-full flex flex-col items-center">
            <div class="w-[280px] h-[500px] bg-black rounded-2xl overflow-hidden shadow-2xl border-2 border-slate-800 relative flex items-center justify-center group">
              <video v-if="latestVideoUrl" :src="latestVideoUrl" controls autoplay loop
                class="w-full h-full object-cover"></video>
              <div v-else class="text-center p-6 text-slate-500 text-xs space-y-2">
                <i class="fa-solid fa-video-slash text-3xl"></i>
                <div>Chưa có video nào. Nhấn Render để tạo ngay!</div>
              </div>
            </div>

            <!-- Video Actions & Caption -->
            <div v-if="latestVideoUrl" class="w-full mt-4 space-y-3">
              <div class="flex items-center gap-2">
                <a :href="latestVideoUrl" download class="flex-1 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-center text-slate-200 transition">
                  <i class="fa-solid fa-download"></i> Tải Video Về Máy
                </a>
                <button @click="openFinalFolder" class="px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-slate-200">
                  <i class="fa-solid fa-folder"></i>
                </button>
              </div>

              <!-- Caption Box -->
              <div class="p-3 rounded-xl bg-slate-900/80 border border-slate-800/80 text-xs space-y-2">
                <div class="flex items-center justify-between text-slate-400">
                  <span class="font-bold">Caption & Hashtag:</span>
                  <button @click="copyCaption" class="text-indigo-400 hover:text-indigo-300">
                    <i class="fa-regular fa-copy"></i> Sao chép
                  </button>
                </div>
                <div class="text-slate-300 whitespace-pre-line max-h-24 overflow-y-auto font-mono text-[11px]">
                  {{ latestCaption || 'Chưa có caption' }}
                </div>
              </div>
            </div>
          </div>

          <!-- Cards Preview Display -->
          <div v-show="previewMode === 'cards'" class="w-full space-y-4 max-h-[640px] overflow-y-auto pr-1">
            <div class="flex items-center justify-between">
              <span class="text-xs text-indigo-300 font-bold uppercase tracking-wider flex items-center gap-1.5">
                <i class="fa-solid fa-layer-group"></i> Xem trước các thẻ Threads (PNG)
              </span>
              <button @click="refreshCardPreview" class="text-[11px] text-indigo-400 hover:text-indigo-300 flex items-center gap-1">
                <i class="fa-solid fa-arrows-rotate"></i> Cập nhật thẻ
              </button>
            </div>

            <!-- Thẻ Post chính -->
            <div class="space-y-1">
              <div class="text-[11px] text-slate-400 font-medium">Thẻ bài viết của tác giả:</div>
              <img :src="'/api/card/post.png?t=' + cardTimestamp" alt="Post Card" class="w-full rounded-xl shadow-md border border-slate-700/60">
            </div>
            
            <!-- Danh sách thẻ các bình luận -->
            <div v-for="(cmt, idx) in post.comments" :key="idx" class="space-y-1 pt-1">
              <div class="text-[11px] text-slate-400 font-medium flex items-center justify-between">
                <span>Thẻ bình luận #{{ idx + 1 }} (@{{ cmt.username }}):</span>
                <span class="text-[10px] text-slate-500 font-mono">{{ cmt.emotion }} • {{ cmt.sfx }}</span>
              </div>
              <img :src="'/api/card/comment_' + (idx + 1) + '.png?t=' + cardTimestamp" :alt="'Comment ' + (idx + 1)" class="w-full rounded-xl shadow-md border border-slate-700/60">
            </div>
          </div>
        </div>

        <!-- Section 4: Batch Factory (Sản Xuất Hàng Loạt) -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="text-xs font-bold uppercase tracking-wider text-amber-400 flex items-center gap-2">
              <i class="fa-solid fa-industry"></i> Sản Xuất Hàng Loạt (Batch Mode)
            </h3>
            <span class="text-xs text-slate-500">Tự động 100%</span>
          </div>

          <p class="text-xs text-slate-400">
            Hệ thống tự động dùng Gemini AI nghĩ kịch bản khác nhau, cắt nền và render liên tục theo số lượng bạn chọn.
          </p>

          <div class="flex items-center gap-3">
            <button v-for="cnt in [3, 5, 10]" :key="cnt" @click="startBatch(cnt)" :disabled="pipelineState.is_running"
              class="flex-1 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700/60 text-xs font-bold text-slate-200 transition disabled:opacity-40">
              Tạo {{ cnt }} Video
            </button>
          </div>
        </div>

      </div>

    </main>

    <!-- TAB 2: GIÁM SÁT QUY TRÌNH (LIVE PIPELINE & EMOTION INSPECTOR) -->
    <main v-show="currentTab === 'pipeline'" class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
      
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

        <!-- Action Callout when Script is ready but Video is not rendered yet -->
        <div v-if="pipelineState.steps[1] && pipelineState.steps[1].status === 'done' && pipelineState.steps[2] && pipelineState.steps[2].status === 'idle'"
          class="p-4 rounded-xl bg-gradient-to-r from-indigo-950 via-purple-950 to-slate-900 border-2 border-indigo-500/60 flex items-center justify-between gap-4 flex-wrap shadow-xl">
          <div class="space-y-1">
            <h3 class="text-sm font-extrabold text-white flex items-center gap-2">
              <span class="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 text-[11px] font-bold border border-emerald-500/30">
                ✅ ĐÃ XONG BƯỚC 1 & 2
              </span>
              <span>Kịch bản & Thẻ Avatar người thật đã sẵn sàng!</span>
            </h3>
            <p class="text-xs text-indigo-200/80">
              Bước 3 (Thu âm giọng đọc), Bước 4 (Nhạc Lofi & SFX) và Bước 5 (Dựng video 60 FPS) đang chờ bạn bấm bắt đầu.
            </p>
          </div>

          <div class="flex items-center gap-2.5">
            <button @click="currentTab = 'studio'" class="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1.5 text-xs font-semibold">
              <i class="fa-solid fa-pen-to-square text-indigo-400"></i>
              <span>Xem/Sửa Kịch Bản (Tab 1)</span>
            </button>
            <button @click="startRender" :disabled="pipelineState.is_running"
              class="px-5 py-2.5 rounded-xl bg-gradient-to-r from-pink-500 via-rose-500 to-indigo-600 hover:opacity-95 text-white font-extrabold text-xs shadow-lg shadow-pink-500/30 transition flex items-center gap-2 animate-pulse">
              <i class="fa-solid fa-play text-sm"></i>
              <span>BẤM RENDER VIDEO NGAY (BƯỚC 3 -> 6)</span>
            </button>
          </div>
        </div>

        <!-- Quick Action Bar -->
        <div v-else class="flex items-center justify-between pt-1 border-t border-slate-800/60 flex-wrap gap-2 text-xs">
          <div class="flex items-center gap-2">
            <button @click="currentTab = 'studio'" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1.5 font-medium">
              <i class="fa-solid fa-arrow-left text-slate-400"></i>
              <span>Về Tab 1: Studio Sáng Tạo (Xem Kịch Bản & Thẻ)</span>
            </button>
          </div>
          <button @click="startRender" :disabled="pipelineState.is_running"
            class="px-4 py-1.5 rounded-lg bg-gradient-to-r from-pink-600 to-indigo-600 hover:opacity-90 text-white font-bold transition flex items-center gap-2 shadow-md shadow-pink-500/20 disabled:opacity-50">
            <i class="fa-solid fa-play"></i>
            <span>Bấm Render Video Hoàn Chỉnh (60 FPS)</span>
          </button>
        </div>
      </div>

      <!-- 6-Stage Stepper Grid -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
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
    <main v-show="currentTab === 'config'" class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
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
                    <option value="vi-VN-NamMinhNeural">Nam Minh (Trầm)</option>
                    <option value="vi-VN-HoaiMyNeural">Hoài My (Truyền cảm)</option>
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

          <!-- Nhóm 5: AI Prompt & Model -->
          <div class="space-y-3">
            <h3 class="text-xs font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-2">
              <i class="fa-solid fa-brain"></i> 5. Chỉ Dẫn Đạo Diễn Gemini AI (System Instruction)
            </h3>
            <textarea v-model="directorConfig.ai_director.system_instruction" rows="4"
              class="w-full glass-input rounded-xl p-3 text-xs focus:outline-none font-mono text-slate-300"></textarea>
          </div>

        </div>
      </div>
    </main>

    <!-- Modal Chọn Avatar Người Thật Trực Quan -->
    <div v-if="showAvatarModal" class="fixed inset-0 bg-black/85 backdrop-blur-md flex items-center justify-center z-50 p-4">
      <div class="glass-panel max-w-2xl w-full rounded-2xl p-6 shadow-2xl border border-indigo-500/40 space-y-4 max-h-[90vh] flex flex-col">
        <div class="flex items-center justify-between border-b border-slate-800 pb-3">
          <div>
            <h3 class="text-base font-bold text-white flex items-center gap-2">
              <i class="fa-solid fa-image-portrait text-indigo-400"></i>
              <span>Kho Avatar Người Thật Sắc Nét HD</span>
            </h3>
            <p class="text-xs text-slate-400 mt-0.5">
              Đang chọn cho: <strong class="text-indigo-300">{{ avatarTarget.type === 'author' ? 'Tác giả bài đăng' : ('Bình luận #' + (avatarTarget.index + 1)) }}</strong>
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

  </div>

  <script>
    const { createApp } = Vue;

    createApp({
      data() {
        return {
          currentTab: 'studio',
          topicInput: '',
          isGeneratingScript: false,
          isCrawlingTrending: false,
          previewMode: 'cards',
          cardTimestamp: Date.now(),
          showAvatarModal: false,
          avatarTarget: { type: 'author', index: 0 },
          avatarGallery: [],
          avatarTabFilter: 'all',
          trendingTopicsInfo: {
            date: '',
            date_display: '',
            updated_at: '',
            is_cached: false
          },
          isRefreshingTrends: false,
          viralTopics: [],
          post: {
            username: '',
            content: '',
            likes: 0,
            is_verified: true,
            avatar_path: '',
            avatar_url: '',
            emotional_arc: '',
            comments: []
          },
          settings: {
            layout_preset: 'split_screen',
            bg_mode: 'split_gameplay',
            card_theme: 'light',
            voice: 'vi-VN-NamMinhNeural'
          },
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
          directorConfig: null,
          latestVideoUrl: '',
          latestCaption: '',
          pollTimer: null
        }
      },
      computed: {
        filteredAvatars() {
          if (this.avatarTabFilter === 'all') return this.avatarGallery;
          return this.avatarGallery.filter(a => a.category === this.avatarTabFilter);
        }
      },
      mounted() {
        this.loadSettings();
        this.loadCurrentScript();
        this.loadLatestVideo();
        this.loadDailyTrendingTopics(false);
        this.loadDirectorConfig();
        this.loadAvatarGallery();
        this.startStatusPolling();
      },
      beforeUnmount() {
        if (this.pollTimer) clearInterval(this.pollTimer);
      },
      methods: {
        async loadDailyTrendingTopics(force = false) {
          this.isRefreshingTrends = true;
          try {
            const res = await fetch(`/api/trending_topics?force=${force}`);
            const data = await res.json();
            if (data.topics && data.topics.length > 0) {
              this.viralTopics = data.topics;
            }
            this.trendingTopicsInfo = {
              date: data.date || '',
              date_display: data.date_display || '',
              updated_at: data.updated_at || '',
              is_cached: data.is_cached || false
            };
          } catch(e) {
            console.error('Lỗi tải trending topics:', e);
          } finally {
            this.isRefreshingTrends = false;
          }
        },
        startStatusPolling() {
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
          }, 800);
        },
        async createVideoOneClick() {
          this.currentTab = 'pipeline';
          try {
            const res = await fetch('/api/create_video_one_click', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                topic: this.topicInput,
                settings: this.settings
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
          if (av.startsWith('http://') || av.startsWith('https://')) return av;
          const clean = av.replace(/\\/g, '/').split('/').pop();
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
          const sfxOptions = ['laugh', 'boom', 'bruh', 'shock', 'huh', 'side_eye', 'clown', 'math', 'applause', 'ting'];
          const pickedSfx = sfxOptions[Math.floor(Math.random() * sfxOptions.length)];
          this.post.comments.push({
            username: `user_${Math.floor(1000 + Math.random() * 9000)}`,
            content: `Bình luận tương tác số ${idx}...`,
            likes: 1200 + idx * 350,
            avatar_path: '',
            avatar_url: '',
            sfx: pickedSfx,
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
          if (autoRender) this.currentTab = 'pipeline';
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
              if (autoRender) {
                // Tự động tiếp tục dựng toàn bộ video (Bước 3 -> 6) để người dùng có thành phẩm ngay!
                await this.startRender();
              } else {
                this.currentTab = 'studio';
              }
            } else {
              alert('Lỗi quét trending Threads: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          } finally {
            this.isCrawlingTrending = false;
          }
        },
        async loadSettings() {
          try {
            const res = await fetch('/api/settings');
            const data = await res.json();
            this.settings.layout_preset = data.layout_preset || 'split_screen';
            this.settings.bg_mode = data.bg_mode || 'split_gameplay';
            this.settings.card_theme = data.card_theme || 'light';
            this.settings.voice = data.voice || 'vi-VN-NamMinhNeural';
          } catch(e) {}
        },
        async saveSettings() {
          try {
            await fetch('/api/settings', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(this.settings)
            });
            alert('✅ Đã lưu cấu hình nhanh!');
          } catch(e) {
            alert('Lỗi: ' + e);
          }
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
              body: JSON.stringify({ topic: this.topicInput })
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
              body: JSON.stringify({ post: this.post, theme: this.settings.card_theme })
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
            const res = await fetch('/api/render_video', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                post: this.post,
                settings: this.settings
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
          alert('📋 Đã sao chép caption vào bộ nhớ tạm!');
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


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        data = request.json
        layout_preset = data.get("layout_preset", "split_screen")
        bg_mode = data.get("bg_mode", "split_gameplay")
        card_theme = data.get("card_theme", "light")
        voice = data.get("voice", "vi-VN-NamMinhNeural")

        lines = []
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if not any(line.strip().startswith(k + "=") for k in ["LAYOUT_PRESET", "BG_MODE", "CARD_THEME", "TTS_VOICE"]):
                        lines.append(line)
        lines.append(f"LAYOUT_PRESET={layout_preset}\n")
        lines.append(f"BG_MODE={bg_mode}\n")
        lines.append(f"CARD_THEME={card_theme}\n")
        lines.append(f"TTS_VOICE={voice}\n")
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return jsonify({"success": True})
    else:
        layout_preset = "split_screen"
        bg_mode = "split_gameplay"
        card_theme = "light"
        voice = "vi-VN-NamMinhNeural"
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("LAYOUT_PRESET="): layout_preset = line.split("=", 1)[1].strip()
                    elif line.startswith("BG_MODE="): bg_mode = line.split("=", 1)[1].strip()
                    elif line.startswith("CARD_THEME="): card_theme = line.split("=", 1)[1].strip()
                    elif line.startswith("TTS_VOICE="): voice = line.split("=", 1)[1].strip()
        return jsonify({"layout_preset": layout_preset, "bg_mode": bg_mode, "card_theme": card_theme, "voice": voice})


@app.route("/api/script", methods=["GET"])
def api_get_script():
    sample_file = os.path.join(OUTPUT_DIR, "sample_post.json")
    if os.path.exists(sample_file):
        with open(sample_file, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({})


@app.route("/api/generate_script", methods=["POST"])
def api_generate_script():
    data = request.json
    topic = data.get("topic", "").strip() or None
    try:
        tracker.reset()
        tracker.start_step("script", f"Gemini AI đang sáng tạo kịch bản theo chủ đề: '{topic or 'ngẫu nhiên'}'...", 20)

        post = ai_mod.generate_story_with_gemini(
            topic,
            progress_cb=lambda msg, pct: tracker.update_progress(pct, msg)
        )
        sample_file = os.path.join(OUTPUT_DIR, "sample_post.json")
        content_mod.save_post_to_json(post, sample_file)

        # Render trước các thẻ kèm avatar người thật
        tracker.start_step("cards", "Vẽ thẻ xem trước (Avatar người thật)...", 80)
        p_av = getattr(post, "avatar_url", "") or getattr(post, "avatar_path", "")
        card_mod.render_post_card(post.username, post.content, likes=post.likes, is_verified=post.is_verified, avatar_source=p_av)
        for i, c in enumerate(post.comments, 1):
            c_av = getattr(c, "avatar_url", "") or getattr(c, "avatar_path", "")
            c_img = card_mod.render_comment_card(c.username, c.content, likes=c.likes, index=i, reply_to=post.username, avatar_source=c_av)
            card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))

        tracker.finish_step("cards")
        tracker.finish_step("script")
        tracker.update_progress(35, "Đã tạo kịch bản & vẽ thẻ xem trước (Bước 2/6). Sẵn sàng render video!")

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
                        "content": c.content,
                        "likes": c.likes,
                        "avatar_path": getattr(c, "avatar_path", ""),
                        "avatar_url": getattr(c, "avatar_url", ""),
                        "sfx": getattr(c, "sfx", "laugh"),
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
        p_av = getattr(post, "avatar_url", "") or getattr(post, "avatar_path", "")
        p_img = card_mod.render_post_card(
            post.username, post.content, likes=post.likes, is_verified=post.is_verified,
            avatar_source=p_av
        )
        card_mod.save_card(p_img, os.path.join(CARDS_DIR, "post.png"))

        for i, c in enumerate(post.comments, 1):
            c_av = getattr(c, "avatar_url", "") or getattr(c, "avatar_path", "")
            c_img = card_mod.render_comment_card(
                c.username, c.content, likes=c.likes, index=i, reply_to=post.username,
                avatar_source=c_av
            )
            card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))

        tracker.finish_step("cards")
        tracker.finish_step("script")
        tracker.update_progress(35, "Đã quét tin trending và ghép avatar người thật hoàn tất (Bước 2/6). Sẵn sàng render video!")

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
                        "content": c.content,
                        "likes": c.likes,
                        "avatar_path": getattr(c, "avatar_path", ""),
                        "avatar_url": getattr(c, "avatar_url", ""),
                        "sfx": getattr(c, "sfx", "laugh"),
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
    layout_preset = sets.get("layout_preset", "split_screen")
    bg_mode = sets.get("bg_mode", "split_gameplay")
    card_theme = sets.get("card_theme", "light")

    def _full_video_worker():
        try:
            tracker.reset()
            # BƯỚC 1: KỊCH BẢN & CÀO AVATAR THẬT TỪ THREADS
            tracker.start_step("script", f"AI đang sáng tạo kịch bản & phân tích Threads về: '{topic or 'thịnh hành'}'...", 15)
            radar_mod = load_mod("threads_radar", "threads_radar.py")
            post = radar_mod.crawl_trending_threads_story(
                topic=topic,
                progress_cb=lambda msg, pct: tracker.update_progress(pct, msg)
            )

            # BƯỚC 2: RENDER BỘ THẺ THREADS CHUẨN (VỚI AVATAR THẬT TỪ THREADS)
            tracker.start_step("cards", "Đang ghép avatar thật từ Threads và vẽ bộ thẻ...", 30)
            p_av = getattr(post, "avatar_url", "") or getattr(post, "avatar_path", "")
            p_img = card_mod.render_post_card(
                post.username, post.content, likes=post.likes, is_verified=post.is_verified,
                theme=card_theme, avatar_source=p_av
            )
            card_mod.save_card(p_img, os.path.join(CARDS_DIR, "post.png"))
            for i, c in enumerate(post.comments, 1):
                c_av = getattr(c, "avatar_url", "") or getattr(c, "avatar_path", "")
                c_img = card_mod.render_comment_card(
                    c.username, c.content, likes=c.likes, index=i, reply_to=post.username,
                    theme=card_theme, avatar_source=c_av
                )
                card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))
            tracker.finish_step("cards")

            # BƯỚC 3 -> 6: CHẠY TIẾP ĐỂ DỰNG VIDEO THÀNH PHẨM (TTS, AUDIO, COMPOSITE, EXPORT)
            pipeline_mod.run_pipeline(post, bg_mode=bg_mode, card_theme=card_theme, reset_tracker=False, skip_cards_render=True, layout_preset=layout_preset)

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
    """Lấy danh sách chủ đề trending hôm nay (tự động fetch mới mỗi ngày 1 lần hoặc ép buộc fetch mới)."""
    force = request.args.get("force", "false").lower() == "true"
    if request.method == "POST":
        data = request.json or {}
        if data.get("force"):
            force = True
    try:
        radar_mod = load_mod("threads_radar", "threads_radar.py")
        result = radar_mod.get_daily_trending_topics(force_refresh=force)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e),
            "topics": [
                "Drama công ty khi Gen Z đòi nghỉ phép vì 'cần chữa lành'",
                "Đi date chia đôi tiền, chàng trai đòi luôn tiền gửi xe",
                "Đồng nghiệp mượn tiền không trả, còn chặn Threads của mình",
                "Sếp giục làm report cuối tuần, mình từ chối thẳng thừng",
                "Người yêu cũ bất ngờ nhắn tin hỏi thăm sau 2 năm chia tay",
                "Trào lưu 'nghỉ việc đi du lịch bụi' liệu có thực tế không?",
                "Tiền lương vừa về đã 'bay' hết vì 7749 khoản phí"
            ]
        })


@app.route("/api/save_and_render_cards", methods=["POST"])
def api_save_cards():
    data = request.json
    p_data = data.get("post", {})
    theme = data.get("theme", "light")
    try:
        p_av = p_data.get("avatar_url", "") or p_data.get("avatar_path", "")
        p_img = card_mod.render_post_card(
            p_data.get("username", "author"),
            p_data.get("content", ""),
            likes=int(p_data.get("likes", 20000)),
            is_verified=bool(p_data.get("is_verified", False)),
            theme=theme,
            avatar_source=p_av
        )
        card_mod.save_card(p_img, os.path.join(CARDS_DIR, "post.png"))

        for i, c in enumerate(p_data.get("comments", []), 1):
            c_av = c.get("avatar_url", "") or c.get("avatar_path", "")
            c_img = card_mod.render_comment_card(
                c.get("username", "user"),
                c.get("content", ""),
                likes=int(c.get("likes", 1500)),
                index=i,
                reply_to=p_data.get("username", "author"),
                theme=theme,
                avatar_source=c_av
            )
            card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))

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


@app.route("/api/card/<filename>")
def get_card_image(filename):
    return send_from_directory(CARDS_DIR, filename)


@app.route("/api/meme/<filename>")
def get_meme_image(filename):
    return send_from_directory(MEMES_DIR, filename)


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


def _async_render_worker(post_obj, bg_mode, card_theme, layout_preset="split_screen"):
    """Hàm chạy nền cho pipeline để không bao giờ bị timeout trên browser."""
    try:
        pipeline_mod.run_pipeline(post_obj, bg_mode=bg_mode, card_theme=card_theme, layout_preset=layout_preset)
    except Exception as e:
        tracker.set_error(str(e))


@app.route("/api/render_video", methods=["POST"])
def api_render_video():
    data = request.json
    p_data = data.get("post", {})
    sets = data.get("settings", {})

    try:
        cfg = config_mod.load_config()
        roles = cfg.get("voice_casting", {}).get("roles", {})

        comments = []
        for idx, c in enumerate(p_data.get("comments", []), 1):
            r_info = roles.get(f"commenter_{idx}", {})
            comments.append(content_mod.Comment(
                username=c.get("username", "user"),
                content=c.get("content", ""),
                avatar_color=c.get("avatar_color", "#8b5cf6"),
                avatar_path=c.get("avatar_path", ""),
                avatar_url=c.get("avatar_url", ""),
                likes=int(c.get("likes", 1200)),
                sfx=c.get("sfx", "none"),
                emotion=c.get("emotion", "funny"),
                voice_id=r_info.get("voice", "vi-VN-HoaiMyNeural"),
                rate_offset=r_info.get("rate", "+10%"),
                pitch_offset=r_info.get("pitch", "+0Hz"),
                pause_before_ms=int(c.get("pause_before_ms", 200)),
                pause_after_ms=int(c.get("pause_after_ms", 350))
            ))

        author_role = roles.get("author", {})
        post_obj = content_mod.Post(
            username=p_data.get("username", "author"),
            content=p_data.get("content", ""),
            avatar_color=p_data.get("avatar_color", "#6366f1"),
            avatar_path=p_data.get("avatar_path", ""),
            avatar_url=p_data.get("avatar_url", ""),
            likes=int(p_data.get("likes", 25000)),
            is_verified=bool(p_data.get("is_verified", True)),
            voice_id=sets.get("voice") or author_role.get("voice", "vi-VN-NamMinhNeural"),
            rate_offset=author_role.get("rate", "+8%"),
            pitch_offset=author_role.get("pitch", "+0Hz"),
            emotional_arc=p_data.get("emotional_arc", "Tò mò -> Đẩy cao trào -> Cú bẻ lái sốc -> Cạn lời"),
            comments=comments
        )

        layout_preset = sets.get("layout_preset", "split_screen")
        bg_mode = sets.get("bg_mode", "split_gameplay")
        card_theme = sets.get("card_theme", "light")

        # Khởi chạy quy trình render trong background thread
        thread = threading.Thread(target=_async_render_worker, args=(post_obj, bg_mode, card_theme, layout_preset))
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


@app.route("/api/latest_video")
def api_latest_video():
    files = glob.glob(os.path.join(FINAL_DIR, "*.mp4"))
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
    if os.name == "nt":
        os.startfile(FINAL_DIR)
    return jsonify({"success": True})


def start_studio(port=5000, auto_open=True):
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║     🎬 THREADS VIDEO AUTOMATION STUDIO 2.0 (AI DIRECTOR)  ║")
    print("╚" + "═" * 58 + "╝")
    print(f"👉 Studio đang mở tại: http://localhost:{port}")
    if auto_open:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    start_studio()
