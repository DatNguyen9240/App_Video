# 🎬 Threads Video Automation (Gemini AI + Nền Màu / Gameplay 9:16)

Hệ thống tự động hóa sản xuất video ngắn chuẩn **TikTok / YouTube Shorts / Instagram Reels (1080x1920)** từ bài đăng mạng xã hội Threads, tích hợp trí tuệ nhân tạo **Google Gemini AI**.

---

## 🌟 Tính năng nổi bật

* 🤖 **Sáng tạo nội dung bằng Gemini AI:** Nhập bất kỳ chủ đề nào (công sở, người yêu cũ, IT, sinh viên...), AI sẽ tự nghĩ ra tình huống ngắn có cú twist bất ngờ + 3 bình luận châm biếm bẻ lái.
* 🎨 **2 Chế độ nền linh hoạt:**
  * **Nền màu Gradient chuyển động (Mặc định):** Cực kỳ sang trọng, tập trung vào chữ, render siêu nhanh (chỉ mất 8–10 giây), dung lượng siêu nhẹ (~1MB)!
  * **Video Gameplay Minecraft Parkour:** Dành cho ai thích phong cách gameplay chạy nhảy cuốn hút.
* 🎙️ **Giọng đọc tiếng Việt tự nhiên (Nam/Nữ):** Tích hợp Microsoft Edge TTS (Nam Minh / Hoài My) kèm cơ chế tự động chuyển dự phòng sang Google TTS nếu nghẽn mạng (không bao giờ crash).
* 💥 **Hiệu ứng âm thanh Meme (SFX) & Sticker Pop-up:** Tự động phát tiếng cười giòn (`laugh`), tiếng sấm sét cú sốc (`boom` - Vine Boom), tiếng `bruh` và hiển thị sticker emoji (😂, 💥, 💀) đúng vào cuối mỗi câu hài.
* 📝 **Tự động xuất Caption & Hashtags:** Mỗi video xuất ra sẽ đi kèm 1 file `_caption.txt` chứa sẵn Tiêu đề + Caption + 7 Hashtag thịnh hành, chỉ việc copy-paste đăng mạng xã hội.
* 🏭 **Chế độ sản xuất hàng loạt (Batch Factory):** 1 click tự động tạo 3, 5 hoặc 10 video với các chủ đề khác nhau để lên lịch đăng cả tuần!

---

## 🚀 Cách sử dụng

Chạy file điều khiển:
```bash
python main.py
```

### Menu điều khiển:
```text
  [1] 🤖 Dùng Gemini AI tạo 1 video theo chủ đề
  [2] 🏭 Sản xuất HÀNG LOẠT video tự động (Batch Mode 3, 5, 10 video)
  [3] 🚀 Render video ngay từ kịch bản hiện tại (sample_post.json)
  [4] ⚙️ Đổi Chế độ Nền (Nền màu Gradient ↔ Minecraft) & Giọng đọc
  [5] 📝 Xem & chỉnh sửa trực tiếp nội dung bài đăng (JSON)
  [6] 🎨 Test Module 2: Render ảnh thẻ Threads (PNG)
  [7] 📂 Mở thư mục video thành phẩm (output/final/)
  [0] ❌ Thoát
```

---

## ⚙️ Cấu hình API Key & Giọng đọc (`.env`)

Mở file `.env` ở thư mục gốc để tùy chỉnh:
```env
# API Key của Gemini (Lấy miễn phí tại https://aistudio.google.com/app/apikey)
GEMINI_API_KEY=AIzaSy...

# Chế độ nền: 'gradient' (nền màu sang trọng) hoặc 'minecraft' (video gameplay)
BG_MODE=gradient

# Giọng đọc: 'vi-VN-NamMinhNeural' (Nam) hoặc 'vi-VN-HoaiMyNeural' (Nữ)
TTS_VOICE=vi-VN-NamMinhNeural

# Tốc độ đọc
TTS_RATE=+10%
```

---

## 📁 Thư mục kết quả (`output/final/`)

* `final_meme_pro_YYYYMMDD_HHMMSS.mp4`: Video hoàn chỉnh (1080x1920 Full HD).
* `final_meme_pro_YYYYMMDD_HHMMSS_caption.txt`: Tiêu đề, caption và hashtag viral.
