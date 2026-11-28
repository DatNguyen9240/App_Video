# THREADS_CRAWL_SKILL — Tiêu chuẩn thu thập và nghiên cứu nội dung Threads Việt Nam

- Phiên bản Skill được duyệt: 1.0 (V3.3 — 28/11/2026).
- Chiến lược: Strategy 3.3, gồm Quality-first (Phương án A), Traffic Content, Checkpoint/Resume đa phiên.
- Hợp đồng dữ liệu mục tiêu: Data Contract V3.3 (bảo toàn tương thích ngược toàn bộ V3.2 lịch sử).
- Chính sách ngưỡng chất lượng: `quality_policy_version = "3.3-a-20261128"`.
- Mục đích: tài liệu nghiệp vụ duy nhất để Muse nạp khi thực hiện crawl Threads. Muse đóng gói theo custom skill `threads_crawl` tại `~/workspace/custom-skills/threads_crawl/SKILL.md` và xác nhận tương thích qua Worker trước khi chạy request V3.3.

## 1. Mục tiêu và nguyên tắc

Thu thập N bài **mới đạt chuẩn chất lượng (`quality_qualified_count`)**, không phải N bản ghi thô hay Watchlist. Nghiên cứu cả nội dung kéo traffic (`traffic_content`), xây dựng người theo dõi, nội dung tạo giá trị, nội dung viral tự nhiên (`natural_viral`) và nội dung Affiliate / nhu cầu mua hàng (`buyer_intent`). Traffic Content không bắt buộc phải có sản phẩm, Affiliate hoặc Buyer Intent.

Ưu tiên người dùng bình thường và creator nhỏ, nội dung có khả năng lan truyền vượt quy mô tài khoản. Không dùng bài kém chất lượng hoặc Watchlist để lấp chỉ tiêu.

Không giới hạn tổng thời gian của cả request. Tuy nhiên, từng phiên (session) phải tuân thủ lease an toàn (mặc định 60 phút; tùy chọn 30/60/120 phút), giữ dự trữ `max(10 phút, 20% lease)` để lưu Drive + gọi `/api/crawl/checkpoint` hoặc `/api/crawl/complete`, và có thể gia hạn tối đa 2 lần × 30 phút qua `/api/crawl/lease/renew` khi thực sự có tiến độ mới. Không tạo vòng lặp tự động vô hạn; khi gặp CAPTCHA, rate limit hoặc cạn nguồn hợp lệ phải dừng an toàn và báo đúng nguyên nhân.

## 2. Đầu vào mỗi lượt

Đọc cấu hình trong prompt / payload `/api/crawl/claim` của Worker: `request_id`, `contract_version`, `skill_version`, `quality_policy_version`, `target_metric`, `requested_count`, `accumulated_quality_count`, `remaining_target`, `session_seq`, `crawl_mode`, `topics`, `keywords`, `lookback_hours`, `exclusions`, và `checkpoint` (các truy vấn đã quét từ phiên trước nếu là Resume). Chỉ áp dụng skill này cho Threads.

- Mặc định: thị trường Việt Nam (`vn_only`); ưu tiên bài trong 24 giờ, mở rộng tối đa 48 giờ khi `lookback_hours=24`, hoặc theo `48/72/168` giờ nếu được chọn rõ.
- Mặc định mode `balanced` (định hướng 40% Natural Viral, 40% Affiliate, 20% Market Trend, không ép cứng tỷ lệ).
- Các lựa chọn khác: `natural_viral`, `traffic_content`, `affiliate`, `buyer_intent`, `market_trend`. (`growth_tracking` chưa bật).
- Topic được chọn giới hạn nội dung tìm kiếm; từ khóa tùy chỉnh bổ sung phạm vi. Không tự chuyển sang ngành hàng không liên quan để đủ N.

## 3. Nhóm chủ đề và hướng tìm kiếm

| Nhóm | Ví dụ hướng discovery (tìm ứng viên) |
|---|---|
| Traffic Content | Meme, chơi chữ, plot twist, random thoughts, relatable, hot takes, tuổi thơ, hoài niệm, châm biếm, reaction, POV, storytelling, trend remix |
| Đời sống và giá trị | Gia đình, tình yêu, công việc, thu nhập, trải nghiệm thật, mẹo hữu ích |
| Affiliate | Review, so sánh, trải nghiệm dùng thật, tìm sản phẩm, săn sale/voucher, kinh nghiệm Affiliate |
| Buyer Intent | Xin link, hỏi giá, xin tư vấn/review, nhu cầu mua cụ thể, câu hỏi chọn sản phẩm |
| Ngành hàng | Mỹ phẩm/làm đẹp, thời trang, nhà cửa/gia dụng, công nghệ, mẹ và bé, ăn uống, sức khỏe, thú cưng, deal/voucher |
| Market Trend | Chủ đề chính thức đang thịnh hành, chủ đề mới phát hiện từ nhiều bài độc lập |

## 4. Kiểm tra công cụ, handshake tương thích và nhận request

1. Xác minh Custom Connector (`GET /api/crawl/health`) và gửi khai báo tương thích Helper (`POST /api/crawl/helper/handshake` hoặc kèm metadata helper trong `/api/crawl/claim`: `helper_version`, `supported_contract_versions`, `skill_version: "1.0"`).
2. Kiểm tra `threads-cli`, Cloud Browser và phiên Threads Web đã đăng nhập `@toilaai267`. Nếu phiên hết hạn hoặc gặp CAPTCHA -> yêu cầu người dùng tự xử lý qua Browser Takeover trước khi claim; không hỏi mật khẩu/OTP trong chat.
3. Gọi `POST /api/crawl/claim` với chính xác `request_id` trong prompt (`{"request_id": "..."}`, kèm `lease_seconds` nếu cần). Nếu là Resume sau checkpoint, Worker trả `session_seq`, `accumulated_quality_count`, `remaining_target` và `checkpoint`.
4. Tạo `session_run_id` duy nhất cho phiên hiện tại (ví dụ `<base_run_id>_s<session_seq>` hoặc `run_threads_YYYYMMDD_HHmm_s<session_seq>`), gắn nhất quán trong Data JSON, Report JSON và API `/progress`, `/checkpoint`, `/complete`. Không bật Hook, Scheduled Task hoặc AI polling.

## 5. Pipeline thực thi bắt buộc

1. **Discovery:** Tìm ứng viên theo `crawl_mode` và `topics`, tránh quét lặp lại các truy vấn đã nằm trong `checkpoint.tried_queries`.
2. **Lọc trước:** Loại bài ngoài thị trường/phạm vi `lookback_hours`, spam, hoặc vi phạm `exclusions` đang bật (người nổi tiếng/fandom/quảng cáo đơn thuần — giữ người thường nhắc tới người nổi tiếng và thương hiệu nhỏ có giá trị). Không loại chỉ vì likes thấp khi chưa đo views.
3. **Registry-check:** Gọi `POST /api/crawl/registry/check` (<= 100 items/lượt). Fail closed nếu HTTP 503. Phân biệt bài `NEW`, `DUPLICATE_HISTORY`, `UPDATED_METRICS`.
4. **Xác minh (Cloud Browser `@toilaai267` + `threads-cli`):** Bắt buộc xác minh cả `views` và `likes` (0 hợp lệ khác hoàn toàn với `null`), `comments`, `reposts`, `quotes`, `shares` riêng biệt (không cộng shares vào reposts), `author_followers`, và đầy đủ 5 trường chứng cứ views V3.2 (`views_display`, `views_precision`, `views_source="threads_web_browser"`, `views_measured_at`, `views_evidence_url`). Bài thiếu `views` hoặc `likes` đánh dấu `UNVERIFIED`.
5. **Phân tầng & Tiêu chuẩn chất lượng V3.3 (Quality Policy `3.3-a-20261128`):** Áp dụng mục 6. Worker sẽ tự tính và kiểm chứng lại hoàn toàn từ số liệu nguồn.
6. **Cập nhật & Gia hạn an toàn:** Định kỳ gọi `POST /api/crawl/progress`. Khi gần chạm ngưỡng dự trữ `max(10 phút, 20% lease)` mà chưa đủ `remaining_target`:
   - Nếu đang có tiến độ thực sự và còn lượt gia hạn (`lease_renew_count < 2`): có thể gọi `POST /api/crawl/lease/renew` (+30 phút/lần, tối đa 2 lần/phiên).
   - Nếu hết ngân sách phiên (đã gia hạn tối đa hoặc chuẩn bị đến ngưỡng dự trữ) hoặc gặp `rate_limited` / `captcha_blocked`: **dừng thu thập ngay**, lưu artifact phiên lên Drive với `is_checkpoint: true`, gọi `POST /api/crawl/checkpoint` để đồng bộ D1 Registry và nhả phiên về `paused` (hoặc `blocked`) cho người dùng bấm **Tiếp tục crawl**.
7. **Hoàn tất request:** Chỉ gọi `POST /api/crawl/complete` khi đã đủ `requested_count` bài chất lượng tích lũy HOẶC đã mở rộng hết nguồn hợp lệ (`stop_reason = "sources_exhausted"` / `"no_qualifying_posts"` / `"source_error"`).

## 6. Tiêu chuẩn chất lượng đo lường được (`quality_policy_version = "3.3-a-20261128"`)

Một bản ghi chỉ đạt **`quality_qualified = true`** (tính vào `quality_qualified_count` và `requested_count`) khi thỏa TẤT CẢ các điều kiện sau:
- Là bài **`NEW`** (không phải `DUPLICATE_HISTORY`, `UPDATED_METRICS`, `UNVERIFIED`, `REJECTED`).
- Đủ **`views >= 0`** kèm 5 trường provenance Web V3.2 **VÀ** **`likes >= 0`** đã xác minh (phân biệt rõ `0` với `null`).
- Đạt ngưỡng tương tác từ **số liệu nguồn** (không tin mù nhãn do agent tự gán):
  - **Very Hot:** `views >= 50000` **hoặc** `likes >= 1000`.
  - **Hot:** `views >= 10000` **hoặc** `likes >= 300`.
  - **Emerging:** `views >= 1000` **VÀ** ít nhất một điều kiện: `likes >= 20` **hoặc** `comments >= 5` **hoặc** `shares + reposts >= 10`.
- Đúng phạm vi request (`vn_only`, `lookback_hours`, `exclusions`) và có phân tích nội dung phù hợp (`why_trending` / `viral_analysis` / `traffic_analysis` 8 thành phần / `buyer_intent_signals` / `affiliate_angle`). Traffic Content không bắt buộc phải có Buyer Intent hay Affiliate.

**Watchlist:** Những bài `NEW` đủ provenance V3.2 nhưng chưa chạm ngưỡng Emerging ở trên thuộc **Watchlist**. Vẫn giữ trong `new_qualified_count` (tương thích V3.2), vẫn lưu D1 Registry & snapshots, nhưng **`quality_qualified = false`**, **KHÔNG tính vào mục tiêu crawl** (chưa bật ngoại lệ ở phiên bản Policy này).

## 7. Đầu ra & Bảo toàn lịch sử

- Data JSON & Report JSON của từng phiên lưu vào `AI_WORKSPACE/01_Market_Data/Threads/` và `Reports/` gắn `request_id`, `session_seq`, `session_run_id`, `is_checkpoint` (`true` với phiên tạm dừng, `false` với kết thúc cuối), `contract_version: "3.3"`, `strategy_version`, `quality_policy_version`.
- `counters` bắt buộc khai báo đủ: `requested_count`, `scanned_count`, `unique_candidate_count`, `new_qualified_count`, `quality_qualified_count`, `watchlist_count`, `duplicate_history_count`, `updated_metrics_count`, `unverified_count`, `rejected_count`, `missing_views_count`, `target_metric: "quality_qualified_count"`, `target_achieved`, `stop_reason`.
- Giữ phương trình đối soát V3.2 nguyên vẹn:
  `unique_candidate_count == new_qualified_count + unverified_count + duplicate_history_count + updated_metrics_count + rejected_count`
  và trong đó `new_qualified_count >= quality_qualified_count + watchlist_count`.
