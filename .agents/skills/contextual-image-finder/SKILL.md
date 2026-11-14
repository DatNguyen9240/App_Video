---
name: contextual-image-finder
description: Expert system for finding, scraping, verifying, and delivering authentic real-life contextual proof images for Threads video automation. Eliminates irrelevant stock photos, logos, and off-topic visuals.
---

# Contextual Image Finder Skill

Kỹ năng chuyên sâu đảm bảo 100% hình ảnh minh họa bằng chứng (Proof Cutaway Card) trong video Threads khớp chuẩn xác tuyệt đối với nội dung câu chuyện, vật phẩm và đối đáp của nhân vật.

---

## 1. Nguyên Tắc Cốt Lõi (Core Principles)

1. **Chuẩn hóa từ khóa ngữ nghĩa (Semantic Query Normalization):**
   - Tuyệt đối không để nguyên các từ chỉ loại tiếng Việt gây hiểu nhầm sang thực vật/thiên nhiên (ví dụ từ `"cây"` trong *"cây rửa xe"*, *"cây lau nhà"*, *"cây cọ"*).
   - Tự động chuyển đổi thành danh từ mua sắm/đời sống thực tế:
     - *"cây rửa xe"* $\rightarrow$ `["vòi xịt rửa xe", "súng rửa xe mini", "máy rửa xe cầm tay"]`
     - *"cây cọ rửa xe"* $\rightarrow$ `["cọ rửa xe ô tô", "bàn chải cọ xe"]`
     - *"cây lau nhà"* $\rightarrow$ `["chổi lau nhà tự vắt", "cây lau nhà thông minh"]`
   - Luôn kèm theo 1-2 từ khóa tiếng Anh cụ thể 2-3 từ (`"car wash spray nozzle"`, `"car wash pressure gun"`).

2. **Phân luồng công cụ tìm kiếm theo ngôn ngữ (Language-Aware Engine Routing):**
   - **Từ khóa tiếng Anh (English):** Ưu tiên **Wikimedia Commons API**. Đây là kho lưu trữ ảnh tư liệu thực tế do con người chụp trên toàn thế giới, độ phân giải cao, không dính quảng cáo hay logo catalog.
   - **Từ khóa tiếng Việt (Vietnamese):** Quét qua **Bing Image Search sạch** (không chèn đuôi *"real photo"* gây loạn ngôn ngữ) để lấy ảnh thực tế từ các bài đánh giá, Shopee, Lazada, review đồ gia dụng đời thực.

3. **Cổng kiểm duyệt thị giác AI nghiêm ngặt (Vision AI Semantic Gate):**
   - Sử dụng **Gemini 3.5 Flash Lite** (`gemini-3.5-flash-lite`) với thời gian phản hồi siêu tốc (<1.2s).
   - Trước khi nhận bất kỳ ảnh nào, AI Vision phải "nhìn" vào ảnh và xác nhận có đúng vật phẩm/tình huống trong chủ đề không.
   - Từ chối ngay lập tức (`NO`) nếu là:
     - Tòa nhà / sân vườn chung cư lạc đề
     - Tài liệu lịch sử, sách báo cũ
     - Hộp bao bì ngũ cốc, thực phẩm không liên quan
     - Logo thương hiệu, sơ đồ kỹ thuật
   - Nếu từ chối, tự động chuyển sang ứng viên ảnh tiếp theo.

4. **Bộ lọc thuật toán phát hiện Logo / Đồ họa phẳng (`is_logo_or_graphic`):**
   - Ảnh đời thực luôn có ánh sáng tự nhiên và dải màu phong phú hàng nghìn màu.
   - Tự động loại bỏ ảnh có nền trắng đơn sắc $> 50\%$ và dải màu $< 2.000$ màu (chặn đứng 100% logo SP OS, sơ đồ phụ tùng).

5. **Nguyên tắc "Thà không hiện còn hơn hiện sai" (No-Irrelevant-Fallback Policy):**
   - Tuyệt đối KHÔNG gán ảnh phong cảnh / tòa nhà bừa bãi khi câu chuyện nói về đồ gia dụng hay xe cộ.
   - Nếu tất cả các ứng viên đều bị AI Vision từ chối: Hệ thống tự động tắt thẻ popup proof trong video để bảo vệ trải nghiệm xem của khán giả.

