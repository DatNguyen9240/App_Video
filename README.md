# 🎬 Threads Video Automation Studio 2.2 (AI Director & 1-Click Viral Automation)

Hệ thống tự động hóa sản xuất video ngắn chuẩn **TikTok / YouTube Shorts / Instagram Reels (1080x1920 60 FPS)** từ bài đăng mạng xã hội Threads, tích hợp trí tuệ nhân tạo **Google Gemini AI (gemini-3.5-flash)**, **Bộ Cào Avatar Người Thật Theo Chủ Đề (Topic-Context Crawler)**, **Kho 14 Meme GIF Hoạt Hình Động**, **Quy trình 1-Click tự động từ A-Z** và **Giao diện Web Studio hiện đại**.

---

## 🌟 Tính năng nổi bật thế hệ 2.2

* 🚀 **Quy trình Tự Động Hóa 1-Click (Zero Effort):**
  * Chỉ cần nhập 1 chủ đề (hoặc bấm 1 chip trend gợi ý hôm nay), hệ thống tự động chạy xuyên suốt cả 6 bước: Sáng tạo kịch bản ➔ Dò quét cào Avatar thật trên Threads ➔ Render thẻ chuẩn ➔ Diễn xuất TTS đa vai ➔ Lồng nhạc Lofi & SFX ➔ FFmpeg dựng phim 60 FPS ➔ Xuất video thành phẩm và tự phát trên Player.

* 🎭 **Cào & Ghép Avatar Người Thật Theo Đúng Ngữ Cảnh (Topic-Context Dynamic Avatar):**
  * Tự động nhận diện chủ đề: Công sở, Sinh viên phòng trọ, IT Lập trình, Tình cảm người yêu cũ, Đời sống...
  * Ưu tiên cào ảnh đại diện thật từ `threads.net`, kèm kho chân dung người thật chuẩn HD dự phòng an toàn 100%, loại bỏ hoàn toàn avatar giả hay chữ cái.

* 🤣 **Kho 14 Meme GIF Hoạt Hình Động Chuẩn Trend Việt Nam:**
  * Tích hợp trọn bộ Meme GIF chuyển động loop mượt mà: `laugh.gif` (cười sặc sụa), `boom.gif` (mind blown bẻ lái), `bruh.gif` (cạn lời), `huh.gif` (mèo ngơ ngác), `side_eye.gif` (liếc xéo nghi ngờ), `shock.gif`, `clown.gif`, `math.gif`, `ting.gif`, `applause.gif`...
  * Hiệu ứng nảy lò xo Bounce-In và Camera Shake 60Hz đồng bộ theo nhịp cảm xúc.

* 🎙️ **Giọng đọc đa vai & Xử lý âm thanh phòng thu (Mastering LUFS -14):**
  * Microsoft Edge TTS tiếng Việt tự nhiên Nam/Nữ đan xen, tự động biến thiên nhịp điệu (Rate & Pitch) theo cảm xúc.
  * Tự động giảm âm lượng nhạc nền khi có giọng đọc (BGM Ducking), chèn khoảng lặng kịch tính (Dramatic Pauses).

* 💎 **Hình ảnh sắc nét chuẩn điện ảnh:**
  * Dựng phim 60 FPS Native siêu mượt, Visually Lossless CRF 18, Color Space BT.709, chuyển động trượt thẻ Ease-Out Slide-Up.

* 📡 **Tab Giám Sát Quy Trình Thời Gian Thực (Live Pipeline Monitor):**
  * Theo dõi trực quan 6 giai đoạn sản xuất (Sáng tạo ➔ Thiết kế thẻ ➔ Diễn xuất TTS ➔ Thiết kế âm thanh ➔ Dựng phim FFmpeg ➔ Đóng gói).
  * Đồ thị nhịp cảm xúc (Emotional Arc Timeline) & Live Terminal Console.

* ⚙️ **Kiến trúc Không Hardcode (Config-Driven Architecture):**
  * Quản lý tập trung mọi thông số tại `config/director_config.json` qua `src/config.py`, có Tab 3 Web UI để chỉnh sửa tức thì.

* 🎨 **Thẻ Threads đẹp chuẩn 100% như thật:**
  * Hỗ trợ cả **Light Theme** (nền trắng tinh khôi bo góc mềm mại, đổ bóng 3D nổi bật) và **Dark Theme**.
  * Logo `@threads`, avatar thật, tên người dùng, tích xanh ✓ và bộ 4 icon vector chuẩn: Thả tim ♡, Bình luận 🗨, Đăng lại 🔁, Chia sẻ ✈.

* 🌀 **3 Chế độ nền video đa dạng:**
  * **3D Infinity Tunnel (Vô cực 3D - Mặc định):** Hiệu ứng đường hầm neon chuyển động thôi miên cực kỳ cuốn hút, chuẩn xu hướng TikTok!
  * **Gameplay Minecraft Parkour:** Dành cho phong cách gameplay thư giãn.
  * **Nền màu Gradient chuyển động:** Tinh tế, nhẹ nhàng.

* 🎙️ **Động cơ giọng đọc 4 tầng tự động phục hồi (Chống sập 100%):**
  * Tầng 1: Microsoft Edge TTS (`Nam Minh` / `Hoài My`) kèm biến thiên Rate & Pitch.
  * Tầng 2: Google Translate TTS (`gTTS`).
  * Tầng 3: Windows SAPI5 Speech Engine (Chạy offline cục bộ).
  * Tầng 4: Bộ tạo Audio Placeholder an toàn (Zero Crash).

---

## 🚀 Cách sử dụng

### Cách 1: Sử dụng Giao diện Web Studio (Khuyên dùng - Đẹp & Trực quan)
```bash
python app_ui.py
```
👉 Trình duyệt sẽ tự động mở tại `http://localhost:5000` với 3 tab:
1. **🎬 Studio Sáng Tạo:** Nhập chủ đề, AI tạo kịch bản, chỉnh sửa post/comments, xem trước thẻ, xem video 9:16.
2. **📡 Giám Sát Quy Trình:** Theo dõi trực tiếp từng bước render, đồ thị nhịp cảm xúc và live console log.
3. **⚙️ Cấu Hình Đạo Diễn:** Điều chỉnh nhịp thở, bảng phân vai, prompt và âm lượng không cần sửa code.

### Cách 2: Sử dụng Menu Terminal
```bash
python main.py
```

---

## 📁 Cấu trúc thư mục dự án

```text
App_Video/
├── config/
│   └── director_config.json    # File cấu hình trung tâm (Prompts, Nhịp thở, Voice, SFX)
├── src/
│   ├── config.py               # Loader cấu hình tập trung (Chống hardcode)
│   ├── pipeline_tracker.py     # Bộ theo dõi tiến trình thời gian thực
│   ├── 01_content.py           # Dataclass Post, Comment & Đồ thị cảm xúc
│   ├── 01_ai_content.py        # Gemini AI sinh kịch bản chuẩn đạo diễn
│   ├── 02_card_gen.py          # Render thẻ Threads chuẩn pixel
│   ├── 03_tts.py               # Diễn xuất giọng nói đa vai & ngắt nghỉ
│   ├── 04_bg_audio.py          # Video nền vô cực 3D & Mix nhạc ducking
│   ├── 05_pipeline.py          # Master Compositor ghép video đa lớp
│   ├── 06_generator.py         # Generator tiện ích
│   ├── 07_batch_factory.py     # Sản xuất hàng loạt 3, 5, 10 video
│   └── _loader.py              # Tiện ích import module
├── assets/                     # SFX meme, avatar, sticker PNG, backgrounds
├── output/
│   ├── cards/                  # Ảnh thẻ PNG
│   ├── audio/                  # File âm thanh từng câu & âm thanh tổng
│   └── final/                  # Video MP4 Full HD + Caption TikTok
├── app_ui.py                   # Flask Web Studio 3 Tab hiện đại
├── main.py                     # Menu điều khiển Console
└── requirements.txt            # Danh sách thư viện Python
```
