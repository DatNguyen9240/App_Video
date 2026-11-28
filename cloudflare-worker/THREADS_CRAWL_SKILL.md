# THREADS_CRAWL_SKILL — Tiêu chuẩn thu thập và nghiên cứu nội dung Threads Việt Nam

- Phiên bản Skill được duyệt: **1.1** (`V3.3 Hot & Very Hot Only` — 28/11/2026).
- Chiến lược: Strategy 3.3, gồm **Hot & Very Hot Only**, `natural_viral`, `traffic_content`, `affiliate`, `buyer_intent`, `market_trend`, và Checkpoint/Resume đa phiên không giới hạn tổng thời gian.
- Hợp đồng dữ liệu mục tiêu: Data Contract V3.3 (bảo toàn 100% tương thích ngược dữ liệu V3.2 lịch sử).
- Chính sách ngưỡng chất lượng: `quality_policy_version = "3.3-hot-only-20261128"`.
- Mục đích: tài liệu nghiệp vụ duy nhất để Muse nạp khi thực hiện crawl Threads. Muse đóng gói theo custom skill `threads_crawl` tại `~/workspace/custom-skills/threads_crawl/SKILL.md` và xác nhận tương thích qua Worker trước khi chạy request V3.3.

## 1. Chính sách chất lượng bắt buộc (Hot & Very Hot Only)

Thu thập N bài **mới đạt chuẩn `Hot` hoặc `Very Hot` (`quality_qualified_count`)**, không phải N bản ghi thô. Ngưỡng định lượng bắt buộc từ **số liệu thật tại nguồn** (Worker tự kiểm chứng lại, không tin nhãn do agent tự gán):

- **Very Hot:** `views >= 50.000` **hoặc** `likes >= 1.000`.
- **Hot:** `views >= 10.000` **hoặc** `likes >= 300`.

**Quy tắc loại trừ dứt khoát:**
- **KHÔNG có ngoại lệ Emerging hoặc Watchlist.**
- **KHÔNG thu thập, KHÔNG phân tích sâu và KHÔNG lưu bài dưới ngưỡng Hot** (kể cả Emerging hoặc bài ít tương tác như `23 views / 0 likes`) vào kho nội dung nghiên cứu mới.
- Chỉ giữ metadata audit tối thiểu nếu cần cho Registry, chống trùng xuyên phiên và đối soát Contract.
- Nghiên cứu đầy đủ cả nội dung kéo traffic (`traffic_content`), viral tự nhiên (`natural_viral`), xây dựng follower, và nội dung Affiliate / nhu cầu mua hàng (`buyer_intent`). Traffic Content không bắt buộc phải có sản phẩm, Affiliate hoặc Buyer Intent.
- Ưu tiên người dùng bình thường và creator nhỏ có lượt xem vượt quy mô tài khoản; hạn chế người nổi tiếng, ca sĩ, thương hiệu lớn và quảng cáo đơn thuần (trừ khi người thường nhắc tới hoặc đúng nhu cầu thương mại).

**Thời lượng không giới hạn & Lease kỹ thuật nội bộ:**
- **Không giới hạn tổng thời gian của một request.** Không tồn tại tùy chọn thời lượng crawl cho người dùng.
- Worker dùng lease kỹ thuật nội bộ (`3600s` mặc định + gia hạn tối đa 2 lần × 30 phút qua `/api/crawl/lease/renew` khi thực sự có tiến độ mới) chỉ để chống kẹt dead-worker.
- Giữ dự trữ đóng phiên `max(10 phút, 20% lease)` để lưu artifact Drive (`is_checkpoint: true`) + gọi `/api/crawl/checkpoint` và nhả phiên về `paused`/`blocked` khi chưa đủ N bài. Không tự ý gọi `/complete` vì hết thời gian một phiên.
- Không tạo vòng lặp tự động vô hạn; không bật Hook, Scheduled Task hoặc AI polling.

## 2. Đầu vào mỗi lượt

Đọc cấu hình trong prompt / payload `/api/crawl/claim` của Worker: `request_id`, `contract_version`, `skill_version`, `quality_policy_version`, `target_metric`, `requested_count`, `accumulated_quality_count`, `remaining_target`, `session_seq`, `crawl_mode`, `topics`, `keywords`, `lookback_hours`, `exclusions`, và `checkpoint` (`tried_queries` đã quét từ phiên trước nếu là Resume). Chỉ áp dụng skill này cho Threads.

- Mặc định: thị trường Việt Nam (`vn_only`); ưu tiên bài trong 24 giờ, mở rộng tối đa 48 giờ khi `lookback_hours=24`, hoặc theo `48/72/168` giờ nếu được chọn rõ.
- Mặc định mode `balanced` (định hướng 40% Natural Viral, 40% Affiliate, 20% Market Trend, không ép cứng tỷ lệ).
- Các mode khác: `natural_viral`, `traffic_content`, `affiliate`, `buyer_intent`, `market_trend` (`growth_tracking` chưa bật).
- Topic được chọn giới hạn phạm vi tìm kiếm; từ khóa tùy chỉnh bổ sung hướng discovery. Không tự đổi sang ngành hàng không liên quan để đủ N.

## 3. Nhóm chủ đề và hướng tìm kiếm

| Nhóm | Ví dụ hướng discovery (tìm ứng viên Hot & Very Hot) |
|---|---|
| Traffic Content | Meme, chơi chữ, plot twist, random thoughts, relatable, hot takes, tuổi thơ, hoài niệm, châm biếm, reaction, POV, storytelling, trend remix |
| Đời sống và giá trị | Gia đình, tình yêu, công việc, thu nhập, trải nghiệm thật, mẹo hữu ích |
| Affiliate | Review, so sánh, trải nghiệm dùng thật, tìm sản phẩm, săn sale/voucher, kinh nghiệm Affiliate |
| Buyer Intent | Xin link, hỏi giá, xin tư vấn/review, nhu cầu mua cụ thể, câu hỏi chọn sản phẩm |
| Ngành hàng | Mỹ phẩm/làm đẹp, thời trang, nhà cửa/gia dụng, công nghệ, mẹ và bé, ăn uống, sức khỏe, thú cưng, deal/voucher |
| Market Trend | Chủ đề chính thức đang thịnh hành, chủ đề mới phát hiện từ nhiều bài độc lập |

## 4. Kiểm tra công cụ, handshake tương thích và nhận request

1. Xác minh Custom Connector (`GET /api/crawl/health`) và gửi khai báo Helper (`POST /api/crawl/helper/handshake` với `skill_name: "threads_crawl"`, `skill_version: "1.1"`, `capabilities: ["multi_session", "lease_renew", "checkpoint", "quality_first_v33"]`).
2. Kiểm tra `threads-cli`, Cloud Browser và phiên Threads Web đã đăng nhập `@toilaai267`. Nếu phiên hết hạn hoặc gặp CAPTCHA -> dừng và yêu cầu người dùng xử lý qua Browser Takeover trước khi tiếp tục; không hỏi mật khẩu/OTP trong chat.
3. Gọi `POST /api/crawl/claim` với chính xác `request_id` trong prompt (`{"request_id": "..."}`). Nếu là Resume sau checkpoint, đọc `session_seq`, `accumulated_quality_count`, `remaining_target` và `checkpoint`.
4. Tạo `session_run_id` duy nhất cho phiên hiện tại (ví dụ `<request_id>_s<session_seq>`), gắn nhất quán trong Data JSON, Report JSON và API `/progress`, `/checkpoint`, `/complete`.

## 5. Pipeline thực thi bắt buộc

1. **Discovery:** Tìm ứng viên theo `crawl_mode` và `topics`, tránh quét lặp lại các truy vấn trong `checkpoint.tried_queries`.
2. **Lọc sơ bộ & Sàng lọc ngưỡng Hot:** Bỏ qua bài ngoài thị trường/`lookback_hours`, spam, vi phạm `exclusions`, hoặc tín hiệu quá thấp không có khả năng chạm ngưỡng Hot sau khi xác minh Web. Không bỏ qua bước đo `views` chỉ vì `likes` thấp ở bước candidate.
3. **Registry-check:** Gọi `POST /api/crawl/registry/check` (<= 100 items/lượt). Fail-closed nếu HTTP 503. Phân biệt `NEW`, `DUPLICATE_HISTORY`, `UPDATED_METRICS`.
4. **Xác minh (Cloud Browser `@toilaai267` + `threads-cli`):** Bắt buộc xác minh cả `views` và `likes` (`0` hợp lệ khác hoàn toàn với `null`), `comments`, `reposts`, `quotes`, `shares` riêng biệt (không cộng shares vào reposts), `author_followers`, và đầy đủ 5 trường provenance views Web V3.2 (`views_display`, `views_precision`, `views_source="threads_web_browser"`, `views_measured_at`, `views_evidence_url`).
5. **Kiểm tra đạt chuẩn Hot & Very Hot:**
   - Nếu `NEW` + đủ provenance Web (`views >= 0`, `likes >= 0`) + đạt **`Very Hot`** (`views >= 50.000` OR `likes >= 1.000`) hoặc **`Hot`** (`views >= 10.000` OR `likes >= 300`) + phân tích đầy đủ -> đánh dấu `quality_qualified = true` và đưa vào Data JSON nghiên cứu.
   - Nếu dưới ngưỡng Hot hoặc thiếu xác minh -> **KHÔNG phân tích sâu, KHÔNG đưa vào kho nội dung nghiên cứu mới**. Chỉ ghi nhận tối thiểu vào bộ đếm `below_hot_count` / `rejected_count` / `unverified_count` hoặc payload audit gọn để phục vụ đối soát.
6. **Cập nhật & Gia hạn an toàn:** Định kỳ gọi `POST /api/crawl/progress`. Khi chạm dự trữ phiên hoặc gặp `rate_limited` / `captcha_blocked`:
   - Nếu đang có tiến độ thực sự và còn lượt gia hạn (`lease_renew_count < 2`): gọi `POST /api/crawl/lease/renew` (+30 phút/lần).
   - Nếu chuẩn bị hết phiên hoặc bị chặn: **dừng thu thập ngay**, lưu artifact phiên lên Drive với `is_checkpoint: true`, gọi `POST /api/crawl/checkpoint` để đồng bộ D1 Registry và nhả phiên về `paused` (hoặc `blocked`).
7. **Hoàn tất request:** Chỉ gọi `POST /api/crawl/complete` khi đã đủ `requested_count` bài `Hot`/`Very Hot` mới tích lũy HOẶC nguồn hợp lệ đã cạn (`stop_reason = "sources_exhausted"` / `"no_qualifying_posts"` / `"source_error"`).

## 6. Đầu ra & Bảo toàn lịch sử

- Data JSON & Report JSON của từng phiên lưu vào `AI_WORKSPACE/01_Market_Data/Threads/` và `Reports/` gắn `request_id`, `session_seq`, `session_run_id`, `is_checkpoint` (`true` với phiên tạm dừng, `false` với kết thúc cuối), `contract_version: "3.3"`, `strategy_version`, `quality_policy_version: "3.3-hot-only-20261128"`, `skill_version: "1.1"`.
- `counters` bắt buộc khai báo đủ: `requested_count`, `scanned_count`, `unique_candidate_count`, `new_count`, `new_qualified_count`, `quality_qualified_count`, `below_hot_count`, `duplicate_history_count`, `updated_metrics_count`, `unverified_count`, `rejected_count`, `missing_views_count`, `target_metric: "quality_qualified_count"`, `target_achieved`, `stop_reason`.
- Mọi dữ liệu lịch sử V3.2 / V1 / V2 được bảo toàn nguyên vẹn; không ghi đè snapshot cũ.
