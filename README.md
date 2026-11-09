# 🎬 Threads Video Automation Studio (Gemini AI + 3D Infinity Tunnel 9:16)

Hệ thống tự động hóa sản xuất video ngắn chuẩn **TikTok / YouTube Shorts / Instagram Reels (1080x1920)** từ bài đăng mạng xã hội Threads, tích hợp trí tuệ nhân tạo **Google Gemini AI (gemini-2.5-flash)** và **Giao diện Web Studio hiện đại**.

---

## 🌟 Tính năng nổi bật

* 🌐 **Giao diện Web Studio hiện đại (`app_ui.py`):** Thiết kế Dark Glassmorphism sang trọng, trực quan, tích hợp trình phát video 9:16 ngay trên trình duyệt, xem trước thẻ bài đăng thời gian thực (Live Card Preview) và nút bấm 1-click tiện lợi.
* 🤖 **Sáng tạo kịch bản với Gemini AI:** Nhập bất kỳ chủ đề nào (công sở, người yêu cũ, IT, sinh viên...), AI sẽ tự nghĩ ra tình huống ngắn có cú twist bất ngờ + 3 bình luận châm biếm bẻ lái.
* 🎨 **Thẻ Threads đẹp chuẩn 100% như thật:**
  * Hỗ trợ cả **Light Theme** (nền trắng tinh khôi bo góc mềm mại, đổ bóng 3D nổi bật) và **Dark Theme**.
  * Đầy đủ logo `@threads` chính hãng, avatar chân thực, tên người dùng, thời gian đăng, dấu tích xanh ✓ và nhãn `Trả lời @tác_giả`.
  * Bộ 4 icon vector chuẩn Threads: Thả tim ♡, Bình luận 🗨, Đăng lại 🔁, Chia sẻ ✈ (căn thẳng trục ngang từng pixel).
* 🌀 **3 Chế độ nền video đa dạng:**
  * **3D Infinity Tunnel (Vô cực 3D - Mặc định):** Hiệu ứng đường hầm neon chuyển động thôi miên cực kỳ cuốn hút, chuẩn xu hướng TikTok, render siêu nhanh (<0.5s)!
  * **Gameplay Minecraft Parkour:** Dành cho phong cách chạy nhảy gameplay thư giãn.
  * **Nền màu Gradient chuyển động:** Tinh tế, nhẹ nhàng.
* 🎙️ **Động cơ giọng đọc 3 tầng tự động phục hồi (Không bao giờ lỗi):**
  * Tầng 1: Microsoft Edge TTS (`Nam Minh` / `Hoài My`).
  * Tầng 2: Google Translate TTS (`gTTS`).
  * Tầng 3: Windows SAPI5 Speech Engine (Chạy offline cục bộ, không cần mạng).
  * Mọi file MP3 đều được FFmpeg tự động kiểm định và chuẩn hóa 44100Hz Stereo.
* 💥 **Hiệu ứng âm thanh Meme (SFX) & Sticker Pop-up:** Tự động phát tiếng cười giòn (`laugh`), tiếng bùng nổ cú sốc (`boom`), tiếng `bruh` và hiển thị sticker emoji (😂, 💥, 💀) căn chính giữa màn hình ngay dưới thẻ bình luận.
* 📝 **Tự động xuất Caption & Hashtags:** Mỗi video xuất ra sẽ đi kèm 1 file `_caption.txt` chứa sẵn Tiêu đề + Caption + 7 Hashtag thịnh hành, chỉ việc copy-paste đăng mạng xã hội.
* 🏭 **Chế độ sản xuất hàng loạt (Batch Factory):** 1 click tự động tạo 3, 5 hoặc 10 video với các chủ đề khác nhau để lên lịch đăng cả tuần!

---

## 🚀 Cách sử dụng

### Cách 1: Sử dụng Giao diện Web Studio (Khuyên dùng - Đẹp & Trực quan)
```bash
python app_ui.py
```
👉 Trình duyệt sẽ tự động mở tại `http://localhost:5000`. Bạn có thể nhập chủ đề, xem trước thẻ, render video và xem video phát trực tiếp trên màn hình!

### Cách 2: Sử dụng Menu Terminal
```bash
python main.py
```
```text
  [1] 🌟 MỞ GIAO DIỆN WEB STUDIO ĐỒ HỌA (Đẹp mắt, trực quan, có Player 9:16)
  [2] 🤖 Dùng Gemini AI tạo 1 video theo chủ đề (Terminal)
  [3] 🏭 Sản xuất HÀNG LOẠT video tự động (Batch Mode 3, 5, 10 video)
  [4] 🚀 Render video ngay từ kịch bản hiện tại (sample_post.json)
  [5] ⚙️ Cài đặt (Đổi Nền Vô Cực / Minecraft, Đổi Theme Thẻ, Đổi Giọng)
  [6] 📝 Xem & chỉnh sửa trực tiếp nội dung bài đăng (JSON)
  [7] 📂 Mở thư mục video thành phẩm (output/final/)
  [0] ❌ Thoát
```

---

## ⚙️ Cấu hình API Key & Giọng đọc (`.env`)

Tạo hoặc mở file `.env` ở thư mục gốc để cấu hình:
```env
# API Key của Gemini (Lấy miễn phí tại https://aistudio.google.com/app/apikey)
GEMINI_API_KEY=AIzaSy...

# Chế độ nền: 'infinity' (đường hầm vô cực 3D), 'minecraft' (gameplay), 'gradient' (nền màu)
BG_MODE=infinity

# Giao diện thẻ Threads: 'light' (chuẩn Threads thật) hoặc 'dark' (chế độ tối)
CARD_THEME=light

# Giọng đọc: 'vi-VN-NamMinhNeural' (Nam) hoặc 'vi-VN-HoaiMyNeural' (Nữ)
TTS_VOICE=vi-VN-NamMinhNeural

# Tốc độ đọc
TTS_RATE=+10%
```

---

## 📁 Thư mục kết quả (`output/final/`)

* `final_meme_pro_YYYYMMDD_HHMMSS.mp4`: Video hoàn chỉnh (1080x1920 Full HD).
* `final_meme_pro_YYYYMMDD_HHMMSS_caption.txt`: Tiêu đề, caption và hashtag viral sẵn sàng đăng tải.
