# Data Contract V3.3 — Quality-first (Phương án A) & Target-based Multi-session Crawl

- **Contract Version:** `3.3` (`supersedes 3.2` cho các request V3.3; **giữ nguyên tương thích ngược V3.1 / V3.2**)
- **Quality Policy Version:** `3.3-a-20261128`
- **Skill chuẩn bắt buộc:** `THREADS_CRAWL_SKILL.md` (`threads_crawl`, version `1.0`)
- **Schema tham chiếu máy đọc:** [DATA_CONTRACT_V33.json](file:///d:/Tool%20MMO/CrawlVIDEO_ALLIN1/App_Video/cloudflare-worker/DATA_CONTRACT_V33.json)
- **Migration D1 (development):** [0005_v33_quality_and_multisession.sql](file:///d:/Tool%20MMO/CrawlVIDEO_ALLIN1/App_Video/cloudflare-worker/migrations/0005_v33_quality_and_multisession.sql)

---

## 1. Tương thích lịch sử V3.2 & Bất biến Dữ liệu

1. **Giữ nguyên ngữ nghĩa `new_qualified_count`:** Đếm mọi bản ghi `NEW` hợp lệ về mặt kỹ thuật V3.2 (đủ 5 trường provenance Web), bao gồm cả Watchlist.
2. **Bảo toàn 100% request & báo cáo V3.1/V3.2 lịch sử:** Không chạy default migration biến request cũ thành V3.3. Request cũ giữ `target_metric = "new_qualified_count"` và chỉ số lịch sử nguyên bản.
3. **Không ghi đè lịch sử (Immutable History):** File raw Drive, `raw_payload_json`, `crawled_at` / `views_measured_at`, phân loại gốc và báo cáo lịch sử không bị chỉnh sửa. Repair SQLite (`repair_cached_metrics_from_raw_payloads`) chỉ vá các cột cache `NULL` bị đọc thiếu từ `raw_payload_json.metrics`.

---

## 2. Tiêu chuẩn Chất lượng V3.3 (`quality_policy_version = "3.3-a-20261128"`)

Worker (`assessV33QualityInWorker`) và App_Video (`assess_v33_quality` + `extract_canonical_metrics`) đánh giá **server-side từ số liệu nguồn** — không tin nhãn `quality_qualified` do agent tự khai.

Một bản ghi chỉ đạt **`quality_qualified = true`** (tính vào `quality_qualified_count` và mục tiêu `requested_count` của request V3.3) khi thỏa đồng thời:

1. **Phân loại & Nguồn hợp lệ:**
   - `classification = "NEW"` (không tính `DUPLICATE_HISTORY`, `UPDATED_METRICS`, `UNVERIFIED`, `REJECTED`).
   - Đủ `views >= 0` kèm 5 trường provenance Web V3.2 (`views_display`, `views_precision`, `views_source = "threads_web_browser"`, `views_measured_at`, `views_evidence_url`).
   - Có **`likes >= 0`** đã xác minh — **phân biệt nghiêm ngặt `0` với `null`** (`0` hợp lệ; `null` bị loại khỏi mục tiêu).
2. **Ngưỡng định lượng (Quantitative Tier Gate):**
   - **Very Hot:** `views >= 50,000` **hoặc** `likes >= 1,000`
   - **Hot:** `views >= 10,000` **hoặc** `likes >= 300`
   - **Emerging đạt chuẩn:** `views >= 1,000` **VÀ** ít nhất một trong: `likes >= 20` **hoặc** `comments >= 5` **hoặc** `shares + reposts >= 10`
3. **Phạm vi & Nội dung:**
   - Thuộc đúng `vn_only`, `lookback_hours`, `exclusions` của request và có phân tích nội dung phù hợp (`summary_vi` / `why_trending` / `viral_analysis` / `traffic_analysis` / `buyer_intent_signals` / `affiliate_angle`).
   - **Traffic Content (`traffic_content = true`):** **KHÔNG** bắt buộc có `buyer_intent` hay `affiliate`.
4. **Watchlist (`is_watchlist = true`):**
   - Các bài `NEW` đủ provenance V3.2 nhưng dưới ngưỡng Emerging (không đặt sàn views/likes cho Watchlist) vẫn được ghi Registry & snapshots, nhưng **`quality_qualified = false`**, **KHÔNG tính vào mục tiêu N bài** (chưa bật ngoại lệ ở `3.3-a-20261128`).

---

## 3. Target-based Multi-session Crawl & Guardrails

- **Không giới hạn tổng thời gian của cả request.** Mục tiêu N bài hoàn tất khi `accumulated_quality_count >= requested_count` (hoặc dừng minh bạch với lý do kết thúc hợp lệ: `source_exhausted`, `no_qualifying_posts`, `insufficient_data`, `user_cancelled`, `manual_stop`).
- **Lease an toàn theo phiên:**
  - Mặc định **`60 phút`** (`3600s`) cho V3.3 (tùy chọn `30 / 60 / 120 phút`).
  - Dự trữ đóng phiên an toàn: **`max(10 phút, 20% lease)`** (ví dụ `12 phút` với lease 60p).
  - Gia hạn qua `POST /api/crawl/lease/renew`: tối đa **`2 lần × 30 phút`** mỗi phiên, chỉ cho phép khi thực sự có tiến độ crawl mới (`409 no_real_progress` nếu không có tiến độ).
- **Tách riêng bộ đếm phiên & bộ đếm lỗi:**
  - `session_seq`: tăng mỗi phiên bình thường (`1, 2, 3...`), không làm cạn retry budget.
  - `error_attempts`: chỉ tăng khi `/api/crawl/fail` hoặc lease hết hạn bất thường không checkpoint.
- **Chống trùng & Checkpoint liên phiên:**
  - Giữ một `request_id` xuyên suốt; mỗi phiên có `session_run_id` riêng (`<request_id>_s<session_seq>`).
  - Bảng D1 `crawl_request_quality_items(PRIMARY KEY (request_id, dedup_key))` chặn cộng đúp xuyên phiên và khi replay checkpoint.
  - `POST /api/crawl/checkpoint` chỉ đưa request về `paused` (hoặc `blocked` nếu `captcha_blocked` / `rate_limited`) sau khi drain `registry_sync_outbox` xong (`registry_sync_status = 'ok'`). **Không gửi Telegram hoàn tất khi chỉ checkpoint/tạm dừng.**
  - Request ở trạng thái `blocked` bị chặn khỏi `GET /api/crawl-queue/signal` và `POST /api/crawl/claim` (chống vòng lặp vô hạn) cho đến khi người vận hành bấm **Tiếp tục crawl** (`/pause` / `/resume`).
- **Google Drive Reconciliation:** Tự phân biệt artifact checkpoint (`is_checkpoint = true` hoặc `run_status = "paused"`) với bản hoàn tất cuối cùng (`is_checkpoint = false`).

---

## 4. Schema Bổ sung V3.3

### 4.1. Counters (`counters` trên Data JSON, Report JSON & `/complete`)
- Giữ toàn bộ counters V3.2 (`requested_count`, `scanned_count`, `unique_candidate_count`, `new_count`, `new_qualified_count`, `duplicate_current_run_count`, `duplicate_history_count`, `updated_metrics_count`, `similar_content_count`, `missing_views_count`, `unverified_count`, `rejected_count`, `target_achieved`, `stop_reason`).
- Bổ sung V3.3:
  - `quality_qualified_count` (`integer`)
  - `watchlist_count` (`integer`)
  - `traffic_content_count` (`integer`)
  - `target_metric` (`"quality_qualified_count"` với V3.3; `"new_qualified_count"` với V3.2)

### 4.2. Record Fields (bổ sung trên mỗi item)
- `metrics` hoặc root fields (`views`, `likes`, `comments`, `shares`, `reposts`, `quotes`): backend đọc đồng nhất cả hai, bảo toàn `0 !== null`.
- Server-computed: `quality_qualified`, `quality_tier_basis` (`very_hot | hot | emerging | watchlist | unqualified`), `quality_exclusion_reason`, `is_watchlist`.
- Phân tích mở rộng V3.3: `author_followers`, `followers_measured_at`, `views_to_followers_ratio`, `natural_viral`, `buyer_intent`, `buyer_intent_signals`, `traffic_content`, `traffic_analysis` (8 trường cấu trúc).

### 4.3. Helper Compatibility Gate
- Worker chỉ bật mặc định Data Contract V3.3 (`60p`, `quality_qualified_count`, Skill `1.0`) sau khi `muse_helper_compatibility_json` xác nhận Muse Helper hỗ trợ đủ 4 capability `["multi_session", "lease_renew", "checkpoint", "quality_first_v33"]` (qua `POST /api/crawl/helper/handshake` hoặc xác nhận từ App_Video).
- Nếu chưa xác nhận: yêu cầu tường minh `contract_version = "3.3"` trả HTTP **`409` (`muse_helper_incompatible`)**, request mặc định tiếp tục chạy an toàn theo V3.2 (`30p`).
