"""
THREADS VIDEO STUDIO — MODERN WEB UI
=====================================
Giao diện Web Studio hiện đại, trực quan, chuyên nghiệp:
  - Tạo kịch bản Threads hài hước với Gemini AI 1-click
  - Chỉnh sửa trực tiếp bài đăng, bình luận & chọn SFX meme
  - Xem trước thẻ bài đăng (Live Card Preview)
  - Chọn nền: 3D Infinity Tunnel / Minecraft / Gradient
  - Render video Full HD 1080x1920 (9:16)
  - Trình phát video 9:16 trực tiếp trên trình duyệt
  - Quản lý & sao chép Caption kèm Hashtag
  - Chế độ sản xuất hàng loạt (Batch Factory)

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
from flask import Flask, render_template_string, request, jsonify, send_file, send_from_directory

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SRC_DIR    = os.path.join(BASE_DIR, "src")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
FINAL_DIR  = os.path.join(OUTPUT_DIR, "final")
CARDS_DIR  = os.path.join(OUTPUT_DIR, "cards")
AUDIO_DIR  = os.path.join(OUTPUT_DIR, "audio")
ENV_PATH   = os.path.join(BASE_DIR, ".env")

sys.path.insert(0, SRC_DIR)
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

# State
render_status = {
    "is_rendering": False,
    "progress": 0,
    "message": "Sẵn sàng",
    "video_path": None,
    "caption": ""
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Threads Video Automation Studio</title>
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
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    body { font-family: 'Plus Jakarta Sans', sans-serif; }
    .glass-panel {
      background: rgba(15, 23, 42, 0.75);
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
    
    <!-- Top Navigation -->
    <header class="h-16 border-b border-slate-800 glass-panel sticky top-0 z-50 px-6 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-brand-600 to-pink-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
          <i class="fa-solid fa-wand-magic-sparkles text-white text-lg"></i>
        </div>
        <div>
          <h1 class="text-lg font-bold bg-gradient-to-r from-white via-slate-200 to-indigo-300 bg-clip-text text-transparent">
            Threads Video Studio
          </h1>
          <div class="text-xs text-slate-400 flex items-center gap-2">
            <span class="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            Gemini 2.5 Flash • 3D Infinity Tunnel • Full HD 9:16
          </div>
        </div>
      </div>

      <div class="flex items-center space-x-3">
        <button @click="openFinalFolder" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold glass-input hover:bg-slate-700/60 transition flex items-center gap-2 text-slate-300">
          <i class="fa-regular fa-folder-open text-indigo-400"></i> Mở Thư Mục Video
        </button>
        <button @click="saveSettings" class="px-4 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 hover:bg-brand-500 shadow-md shadow-indigo-600/30 transition flex items-center gap-2">
          <i class="fa-solid fa-floppy-disk"></i> Lưu Cấu Hình
        </button>
      </div>
    </header>

    <!-- Main Studio Workspace -->
    <main class="flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
      
      <!-- Left Panel: Controls & Script Editor (7 cols) -->
      <div class="lg:col-span-7 flex flex-col space-y-6">
        
        <!-- Section 1: AI Prompt & Generator -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-2">
              <i class="fa-solid fa-robot"></i> 1. Sáng Tạo Nội Dung Bằng Gemini AI
            </h2>
            <span class="text-xs text-slate-400">Model: gemini-2.5-flash</span>
          </div>

          <div class="space-y-3">
            <div class="relative">
              <input v-model="topicInput" @keyup.enter="generateScript" type="text"
                placeholder="Nhập chủ đề bạn muốn (ví dụ: Chuyện đi phỏng vấn bị HR quay xe, Drama văn phòng...)"
                class="w-full glass-input rounded-xl px-4 py-3 text-sm focus:outline-none pr-32 placeholder-slate-500">
              <button @click="generateScript" :disabled="isGeneratingScript"
                class="absolute right-2 top-2 px-4 py-1.5 rounded-lg bg-gradient-to-r from-brand-600 to-pink-500 hover:opacity-90 transition text-xs font-bold shadow-md shadow-indigo-500/20 flex items-center gap-2 disabled:opacity-50">
                <i v-if="isGeneratingScript" class="fa-solid fa-spinner fa-spin"></i>
                <i v-else class="fa-solid fa-wand-sparkles"></i>
                <span>AI Tạo Bài</span>
              </button>
            </div>

            <!-- Quick Viral Chips -->
            <div class="flex items-center gap-1.5 flex-wrap">
              <span class="text-xs text-slate-500">Chủ đề hot:</span>
              <button v-for="t in viralTopics" :key="t" @click="pickTopic(t)"
                class="text-xs px-2.5 py-1 rounded-full bg-slate-800/80 hover:bg-slate-700/80 text-slate-300 border border-slate-700/50 transition">
                {{ t }}
              </button>
            </div>
          </div>
        </div>

        <!-- Section 2: Script Detail Editor -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3">
            <h2 class="text-sm font-bold uppercase tracking-wider text-pink-400 flex items-center gap-2">
              <i class="fa-solid fa-pen-to-square"></i> 2. Chi Tiết Bài Đăng & Bình Luận
            </h2>
            <button @click="refreshCardPreview" class="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1">
              <i class="fa-solid fa-rotate-right"></i> Xem trước thẻ PNG
            </button>
          </div>

          <!-- Post Editor -->
          <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2.5">
            <div class="flex items-center gap-3">
              <span class="text-xs font-bold text-slate-400">Tác giả post:</span>
              <input v-model="post.username" type="text" class="glass-input rounded-lg px-2.5 py-1 text-xs w-44 font-semibold text-slate-200">
              <label class="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer ml-auto">
                <input type="checkbox" v-model="post.is_verified" class="rounded bg-slate-800 border-slate-700 text-indigo-500 focus:ring-0">
                <span>Tích xanh ✓</span>
              </label>
              <div class="flex items-center gap-1.5 text-xs text-slate-400">
                <span>Tim:</span>
                <input v-model="post.likes" type="number" class="glass-input rounded-lg px-2 py-1 text-xs w-20 text-right">
              </div>
            </div>
            <textarea v-model="post.content" rows="3"
              class="w-full glass-input rounded-xl p-3 text-sm focus:outline-none placeholder-slate-500"
              placeholder="Nội dung bài đăng chính..."></textarea>
          </div>

          <!-- Comments Editor -->
          <div class="space-y-3">
            <div class="text-xs font-bold text-slate-400 uppercase tracking-wide">3 Bình luận tương tác (Punchlines):</div>
            
            <div v-for="(cmt, idx) in post.comments" :key="idx"
              class="p-3.5 rounded-xl bg-slate-900/40 border border-slate-800/60 space-y-2">
              <div class="flex items-center gap-2.5">
                <span class="w-5 h-5 rounded-full bg-indigo-500/20 text-indigo-400 text-xs flex items-center justify-center font-bold">#{{ idx+1 }}</span>
                <input v-model="cmt.username" type="text" placeholder="username" class="glass-input rounded-lg px-2.5 py-1 text-xs w-36 font-semibold">
                
                <!-- SFX selector -->
                <div class="flex items-center gap-1.5 ml-auto">
                  <span class="text-xs text-slate-500">Hiệu ứng SFX:</span>
                  <select v-model="cmt.sfx" class="glass-input rounded-lg px-2.5 py-1 text-xs bg-slate-800 text-slate-200 font-medium">
                    <option value="none">Không SFX</option>
                    <option value="laugh">😂 Cười (laugh)</option>
                    <option value="boom">💥 Bùng nổ (boom)</option>
                    <option value="bruh">💀 Cạn lời (bruh)</option>
                    <option value="ting">✨ Ting chớp (ting)</option>
                  </select>
                </div>
              </div>

              <textarea v-model="cmt.content" rows="2"
                class="w-full glass-input rounded-xl p-2.5 text-xs focus:outline-none placeholder-slate-500"
                :placeholder="'Bình luận ' + (idx+1) + '...'"></textarea>
            </div>
          </div>
        </div>

        <!-- Section 3: Studio Settings -->
        <div class="glass-panel rounded-2xl p-5 shadow-xl space-y-4">
          <h2 class="text-sm font-bold uppercase tracking-wider text-cyan-400 flex items-center gap-2">
            <i class="fa-solid fa-sliders"></i> 3. Cài Đặt Video & Âm Thanh
          </h2>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <!-- Chế độ nền -->
            <div class="space-y-1.5">
              <label class="text-xs font-semibold text-slate-300">Chế độ nền video:</label>
              <select v-model="settings.bg_mode" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800">
                <option value="infinity">🌀 3D Infinity Tunnel (Vô cực)</option>
                <option value="minecraft">🎮 Minecraft Parkour</option>
                <option value="gradient">🎨 Nền màu Gradient nhẹ</option>
              </select>
            </div>

            <!-- Giao diện thẻ -->
            <div class="space-y-1.5">
              <label class="text-xs font-semibold text-slate-300">Giao diện thẻ Threads:</label>
              <select v-model="settings.card_theme" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800">
                <option value="light">☀️ Sáng (Chuẩn Threads thật 100%)</option>
                <option value="dark">🌙 Tối (Dark Mode)</option>
              </select>
            </div>

            <!-- Giọng đọc -->
            <div class="space-y-1.5">
              <label class="text-xs font-semibold text-slate-300">Giọng đọc tiếng Việt:</label>
              <select v-model="settings.voice" class="w-full glass-input rounded-xl px-3 py-2 text-xs bg-slate-800">
                <option value="vi-VN-NamMinhNeural">🎙️ Nam Minh (Giọng Nam trầm ấm)</option>
                <option value="vi-VN-HoaiMyNeural">🎙️ Hoài My (Giọng Nữ truyền cảm)</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Big Render Action Button -->
        <div>
          <button @click="startRender" :disabled="renderState.is_rendering"
            class="w-full py-4 rounded-2xl bg-gradient-to-r from-brand-600 via-indigo-500 to-pink-500 hover:opacity-95 transition font-extrabold text-base shadow-xl shadow-indigo-600/30 flex items-center justify-center gap-3 disabled:opacity-50">
            <i v-if="renderState.is_rendering" class="fa-solid fa-spinner fa-spin text-xl"></i>
            <i v-else class="fa-solid fa-film text-xl"></i>
            <span>{{ renderState.is_rendering ? renderState.message : '🚀 RENDER VIDEO FULL HD (1080x1920) NGAY' }}</span>
          </button>
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
                <div>Chưa có video nào được render. Nhấn nút Render để tạo ngay!</div>
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
          <div v-show="previewMode === 'cards'" class="w-full space-y-3 max-h-[620px] overflow-y-auto pr-1">
            <div class="text-xs text-slate-400 font-semibold">Thẻ bài viết:</div>
            <img :src="'/api/card/post.png?t=' + cardTimestamp" alt="Post Card" class="w-full rounded-xl shadow-md border border-slate-700/40">
            
            <div class="text-xs text-slate-400 font-semibold pt-2">Thẻ bình luận 1:</div>
            <img :src="'/api/card/comment_1.png?t=' + cardTimestamp" alt="Comment 1" class="w-full rounded-xl shadow-md border border-slate-700/40">
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
            <button v-for="cnt in [3, 5, 10]" :key="cnt" @click="startBatch(cnt)" :disabled="renderState.is_rendering"
              class="flex-1 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700/60 text-xs font-bold text-slate-200 transition disabled:opacity-40">
              Tạo {{ cnt }} Video
            </button>
          </div>
        </div>

      </div>

    </main>
  </div>

  <script>
    const { createApp } = Vue;

    createApp({
      data() {
        return {
          topicInput: '',
          isGeneratingScript: false,
          previewMode: 'video',
          cardTimestamp: Date.now(),
          viralTopics: [
            'Phỏng vấn xin việc bá đạo',
            'Sếp giao task vô lý',
            'Người yêu cũ đòi quà',
            'Sinh viên ở trọ bá đạo',
            'Lập trình viên IT cạn lời'
          ],
          post: {
            username: 'congso.drama',
            content: 'Ngày đầu đi làm, sếp hỏi ai biết dùng máy chiếu không. Mình tự tin nhận, loay hoay 15 phút sếp lại gần mới phát hiện mình đang cắm sạc dự phòng vào cổng HDMI.',
            likes: 45200,
            is_verified: true,
            comments: [
              { username: 'hr_thotim', content: 'Đam mê công nghệ nhưng công nghệ từ chối em hả? 😂', sfx: 'laugh' },
              { username: 'it_helpdesk', content: 'Sếp chắc tưởng em đang sạc pin cho máy chiếu 💀', sfx: 'boom' },
              { username: 'teamleader', content: 'Mai cho em ở nhà sạc pin cả đời luôn nha!', sfx: 'bruh' }
            ]
          },
          settings: {
            bg_mode: 'infinity',
            card_theme: 'light',
            voice: 'vi-VN-NamMinhNeural'
          },
          renderState: {
            is_rendering: false,
            message: 'Sẵn sàng'
          },
          latestVideoUrl: '',
          latestCaption: ''
        }
      },
      mounted() {
        this.loadSettings();
        this.loadCurrentScript();
        this.loadLatestVideo();
      },
      methods: {
        pickTopic(t) {
          this.topicInput = t;
          this.generateScript();
        },
        async loadSettings() {
          try {
            const res = await fetch('/api/settings');
            const data = await res.json();
            this.settings.bg_mode = data.bg_mode || 'infinity';
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
            alert('✅ Đã lưu cấu hình thành công!');
          } catch(e) {
            alert('Lỗi lưu cấu hình: ' + e);
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
            } else {
              alert('Lỗi: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi AI: ' + e);
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
          this.renderState.is_rendering = true;
          this.renderState.message = 'Đang render video...';
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
            if (data.success) {
              this.latestVideoUrl = '/api/video/' + data.video_filename;
              this.latestCaption = data.caption;
              this.previewMode = 'video';
              alert('🎉 Render thành công! Video đã sẵn sàng phát.');
            } else {
              alert('Lỗi render: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          } finally {
            this.renderState.is_rendering = false;
            this.renderState.message = 'Sẵn sàng';
          }
        },
        async startBatch(count) {
          if (!confirm(`Bạn có chắc muốn tạo tự động ${count} video liên tiếp?`)) return;
          this.renderState.is_rendering = true;
          this.renderState.message = `Đang sản xuất ${count} video...`;
          try {
            const res = await fetch('/api/batch_render', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ count: count })
            });
            const data = await res.json();
            if (data.success) {
              alert(`🎉 Đã sản xuất thành công ${data.completed.length} video!`);
              this.loadLatestVideo();
            } else {
              alert('Lỗi batch: ' + data.error);
            }
          } catch(e) {
            alert('Lỗi: ' + e);
          } finally {
            this.renderState.is_rendering = false;
            this.renderState.message = 'Sẵn sàng';
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
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        data = request.json
        bg_mode = data.get("bg_mode", "infinity")
        card_theme = data.get("card_theme", "light")
        voice = data.get("voice", "vi-VN-NamMinhNeural")

        lines = []
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if not any(line.strip().startswith(k + "=") for k in ["BG_MODE", "CARD_THEME", "TTS_VOICE"]):
                        lines.append(line)
        lines.append(f"BG_MODE={bg_mode}\n")
        lines.append(f"CARD_THEME={card_theme}\n")
        lines.append(f"TTS_VOICE={voice}\n")
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return jsonify({"success": True})
    else:
        bg_mode = "infinity"
        card_theme = "light"
        voice = "vi-VN-NamMinhNeural"
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("BG_MODE="): bg_mode = line.split("=", 1)[1].strip()
                    elif line.startswith("CARD_THEME="): card_theme = line.split("=", 1)[1].strip()
                    elif line.startswith("TTS_VOICE="): voice = line.split("=", 1)[1].strip()
        return jsonify({"bg_mode": bg_mode, "card_theme": card_theme, "voice": voice})

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
        post = ai_mod.generate_story_with_gemini(topic)
        sample_file = os.path.join(OUTPUT_DIR, "sample_post.json")
        content_mod.save_post_to_json(post, sample_file)

        # Render cards
        card_mod.render_post_card(post.username, post.content, likes=post.likes, is_verified=post.is_verified)
        for i, c in enumerate(post.comments, 1):
            c_img = card_mod.render_comment_card(c.username, c.content, likes=c.likes, index=i, reply_to=post.username)
            card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))

        return jsonify({
            "success": True,
            "post": {
                "username": post.username,
                "content": post.content,
                "likes": post.likes,
                "is_verified": post.is_verified,
                "comments": [
                    {"username": c.username, "content": c.content, "likes": c.likes, "sfx": getattr(c, "sfx", "laugh")}
                    for c in post.comments
                ]
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/save_and_render_cards", methods=["POST"])
def api_save_cards():
    data = request.json
    p_data = data.get("post", {})
    theme = data.get("theme", "light")
    try:
        p_img = card_mod.render_post_card(
            p_data.get("username", "author"),
            p_data.get("content", ""),
            likes=int(p_data.get("likes", 20000)),
            is_verified=bool(p_data.get("is_verified", False)),
            theme=theme
        )
        card_mod.save_card(p_img, os.path.join(CARDS_DIR, "post.png"))

        for i, c in enumerate(p_data.get("comments", []), 1):
            c_img = card_mod.render_comment_card(
                c.get("username", "user"),
                c.get("content", ""),
                likes=int(c.get("likes", 1500)),
                index=i,
                reply_to=p_data.get("username", "author"),
                theme=theme
            )
            card_mod.save_card(c_img, os.path.join(CARDS_DIR, f"comment_{i}.png"))

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/card/<filename>")
def get_card_image(filename):
    return send_from_directory(CARDS_DIR, filename)

@app.route("/api/render_video", methods=["POST"])
def api_render_video():
    data = request.json
    p_data = data.get("post", {})
    sets = data.get("settings", {})

    try:
        comments = []
        for c in p_data.get("comments", []):
            comments.append(content_mod.Comment(
                username=c.get("username", "user"),
                content=c.get("content", ""),
                likes=int(c.get("likes", 1200)),
                sfx=c.get("sfx", "none")
            ))

        post_obj = content_mod.Post(
            username=p_data.get("username", "author"),
            content=p_data.get("content", ""),
            likes=int(p_data.get("likes", 25000)),
            is_verified=bool(p_data.get("is_verified", True)),
            comments=comments
        )

        bg_mode = sets.get("bg_mode", "infinity")
        video_path = pipeline_mod.run_pipeline(post_obj, bg_mode=bg_mode)

        cap_file = video_path.replace(".mp4", "_caption.txt")
        caption_text = ""
        if os.path.exists(cap_file):
            with open(cap_file, "r", encoding="utf-8") as f:
                caption_text = f.read()

        return jsonify({
            "success": True,
            "video_filename": os.path.basename(video_path),
            "caption": caption_text
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/batch_render", methods=["POST"])
def api_batch_render():
    data = request.json
    count = int(data.get("count", 3))
    try:
        batch_mod = load_mod("batch", "07_batch_factory.py")
        # Run batch
        completed = []
        api_key = ai_mod.get_or_prompt_api_key()
        topics = batch_mod.DEFAULT_TOPICS

        for i in range(1, count + 1):
            t = topics[(i - 1) % len(topics)]
            p = ai_mod.generate_story_with_gemini(t, api_key=api_key)
            v = pipeline_mod.run_pipeline(p)
            completed.append(os.path.basename(v))

        return jsonify({"success": True, "completed": completed})
    except Exception as e:
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
    print("║     🎬 THREADS VIDEO AUTOMATION STUDIO (WEB UI)        ║")
    print("╚" + "═" * 58 + "╝")
    print(f"👉 Đang mở Studio tại: http://localhost:{port}")
    if auto_open:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    start_studio()

