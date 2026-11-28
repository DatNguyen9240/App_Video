# Data Contract V3.3 — Hot & Very Hot Only & Target-based Multi-session Crawl

- **Contract Version:** `3.3` (`supersedes 3.2` cho các request V3.3; **giữ nguyên tương thích ngược V3.1 / V3.2**)
- **Quality Policy Version:** `3.3-hot-only-20261128`
- **Skill chuẩn bắt buộc:** `THREADS_CRAWL_SKILL.md` (`threads_crawl`, version `1.1`)
- **Schema tham chiếu máy đọc:** [DATA_CONTRACT_V33.json](file:///d:/Tool%20MMO/CrawlVIDEO_ALLIN1/App_Video/cloudflare-worker/DATA_CONTRACT_V33.json)
- **Migration D1 (development):** [0005_v33_quality_and_multisession.sql](file:///d:/Tool%20MMO/CrawlVIDEO_ALLIN1/App_Video/cloudflare-worker/migrations/0005_v33_quality_and_multisession.sql)

---

## 1. Tương thích lịch sử V3.2 & Bất biến Dữ liệu

1. **Giữ nguyên ngữ nghĩa `new_qualified_count`:** Đếm mọi bản ghi `NEW` hợp lệ về mặt kỹ thuật cho request V3.2 cũ. Với request V3.3, chỉ bài `Hot` và `Very Hot` mới được đưa vào kho nội dung nghiên cứu mới (`market_items`), nên `new_qualified_count` và `quality_qualified_count` phản ánh đúng số bài đạt chuẩn mới.
2. **Bảo toàn 100% request & báo cáo V3.1/V3.2 lịch sử:** Không chạy default migration ép request cũ chuyển nghĩa sang V3.3. Request cũ giữ `target_metric = "new_qualified_count"` và kết quả lịch sử nguyên bản.
3. **Không ghi đè lịch sử (Immutable History):** File raw Drive, `raw_payload_json`, `crawled_at` / `views_measured_at`, phân loại gốc và báo cáo lịch sử không bị chỉnh sửa.

---

## 2. Tiêu chuẩn Chất lượng V3.3 (`quality_policy_version = "3.3-hot-only-20261128"`)

Worker (`assessV33QualityInWorker` + `classifyQuantitativeV33Tier`) và App_Video (`assess_v33_quality` + `classify_v33_quantitative_tier`) đánh giá **server-side từ số liệu nguồn** — không tin nhãn `quality_qualified` hoặc `trend_tier` do agent tự khai.

Một bản ghi chỉ đạt **`quality_qualified = true`** (tính vào `quality_qualified_count`, mục tiêu `requested_count`, và được lưu vào kho nội dung nghiên cứu mới `market_items`) khi thỏa đồng thời:

1. **Phân loại & Nguồn hợp lệ:**
   - `classification = "NEW"` (không tính `DUPLICATE_HISTORY`, `UPDATED_METRICS`, `UNVERIFIED`, `REJECTED`).
   - Đủ `views >= 0` kèm 5 trường provenance Web V3.2 (`views_display`, `views_precision`, `views_source = "threads_web_browser"`, `views_measured_at`, `views_evidence_url`).
   - Có **`likes >= 0`** đã xác minh — **phân biệt nghiêm ngặt `0` với `null`** (`0` hợp lệ; `null` bị loại).
2. **Ngưỡng định lượng Hot & Very Hot Only:**
   - **Very Hot:** `views >= 50,000` **hoặc** `likes >= 1,000`
   - **Hot:** `views >= 10,000` **hoặc** `likes >= 300`
   - **Mọi bài dưới ngưỡng Hot** (kể cả Emerging hoặc bài như `23 views / 0 likes`): `quality_qualified = false`, `quality_tier_basis = "below_hot"`, `quality_exclusion_reason = "below_hot_threshold"`. **Không có ngoại lệ Emerging hoặc Watchlist.**
3. **Phân luồng Audit-Only (`market_crawl_audit_items`):**
   - Trong lượt V3.3, bài dưới ngưỡng Hot, `UNVERIFIED` hoặc `REJECTED` **KHÔNG được phân tích sâu và KHÔNG lưu vào `market_items` (kho nội dung nghiên cứu mới)**.
   - App_Video chỉ ghi metadata audit tối thiểu vào bảng `market_crawl_audit_items` để phục vụ chống trùng, đối soát counters và hiển thị trong mục thu gọn **Chẩn đoán kỹ thuật**.
4. **Phạm vi & Nội dung:**
   - Thuộc đúng `vn_only`, `lookback_hours`, `exclusions` của request và có phân tích phù hợp (`summary_vi` / `why_trending` / `viral_analysis` / `traffic_analysis` / `buyer_intent_signals` / `affiliate_angle`).
   - **Traffic Content (`traffic_content = true`):** **KHÔNG** bắt buộc có `buyer_intent` hay `affiliate`.

---

## 3. Target-based Multi-session Crawl & Guardrails

- **Không giới hạn tổng thời gian của cả request.** Không còn tùy chọn thời lượng crawl cho người dùng trên UI; `lease_seconds = 3600` chỉ là cơ chế an toàn kỹ thuật nội bộ của Worker.
- Mục tiêu N bài hoàn tất khi `accumulated_quality_count >= requested_count` (hoặc dừng minh bạch với lý do kết thúc hợp lệ: `sources_exhausted`, `no_qualifying_posts`, `insufficient_data`, `user_cancelled`, `manual_stop`).
- **Lease kỹ thuật nội bộ theo phiên:**
  - Mặc định nội bộ **`3600s` (`60 phút`)** cho V3.3.
  - Dự trữ đóng phiên an toàn: **`max(10 phút, 20% lease)`**.
  - Gia hạn qua `POST /api/crawl/lease/renew`: tối đa **`2 lần × 30 phút`** mỗi phiên, chỉ cho phép khi thực sự có tiến độ crawl mới (`409 no_real_progress` nếu không có tiến độ).
- **Tách riêng bộ đếm phiên & bộ đếm lỗi:**
  - `session_seq`: tăng mỗi phiên bình thường (`1, 2, 3...`), không làm cạn retry budget.
  - `error_attempts`: chỉ tăng khi `/api/crawl/fail` hoặc lease hết hạn bất thường không checkpoint.
- **Chống trùng & Checkpoint liên phiên:**
  - Giữ một `request_id` xuyên suốt; mỗi phiên có `session_run_id` riêng (`<request_id>_s<session_seq>`).
  - Bảng D1 `crawl_request_quality_items(PRIMARY KEY (request_id, dedup_key))` chặn cộng đúp xuyên phiên và khi replay checkpoint.
  - `POST /api/crawl/checkpoint` chỉ đưa request về `paused` (hoặc `blocked` nếu `captcha_blocked` / `rate_limited`) sau khi drain `registry_sync_outbox` xong (`registry_sync_status = 'ok'`). **Không gửi Telegram hoàn tất khi chỉ checkpoint/tạm dừng.**
  - **Lease hết hạn bất thường (ví dụ ở `68/100`):** `expireStaleLeases` tự động nhả request V3.3 chưa đủ mục tiêu về `status = 'queued'`, `execution_phase = 'paused'`, tuyệt đối không đánh dấu `completed`.
- **Google Drive Reconciliation:** Tự phân biệt artifact checkpoint (`is_checkpoint = true` hoặc `run_status = "paused"`) với bản hoàn tất cuối cùng (`is_checkpoint = false`).

---

## 4. Schema Bổ sung V3.3

### 4.1. Counters (`counters` trên Data JSON, Report JSON & `/complete`)
- Giữ tương thích counters V3.2 (`requested_count`, `scanned_count`, `unique_candidate_count`, `new_count`, `new_qualified_count`, `duplicate_current_run_count`, `duplicate_history_count`, `updated_metrics_count`, `similar_content_count`, `missing_views_count`, `unverified_count`, `rejected_count`, `target_achieved`, `stop_reason`).
- Bổ sung V3.3:
  - `quality_qualified_count` (`integer` — chỉ đếm bài Hot & Very Hot mới đạt chuẩn)
  - `below_hot_count` (`integer` — số bài bị loại vì dưới ngưỡng Hot)
  - `traffic_content_count` (`integer`)
  - `target_metric` (`"quality_qualified_count"` với V3.3; `"new_qualified_count"` với V3.2)

### 4.2. Record Fields (bổ sung trên mỗi item)
- `metrics` hoặc root fields (`views`, `likes`, `comments`, `shares`, `reposts`, `quotes`): backend đọc đồng nhất cả hai, bảo toàn `0 !== null`.
- Server-computed: `quality_qualified`, `quality_tier_basis` (`very_hot | hot | below_hot | unqualified`), `quality_exclusion_reason`, `trend_tier` (`very_hot | hot` cho nghiên cứu mới).
- Phân tích mở rộng V3.3: `author_followers`, `followers_measured_at`, `views_to_followers_ratio`, `natural_viral`, `buyer_intent`, `buyer_intent_signals`, `traffic_content`, `traffic_analysis` (8 trường cấu trúc).

### 4.3. Helper Compatibility Gate
- Worker chỉ bật mặc định Data Contract V3.3 (`quality_qualified_count`, Skill `1.1`, `3.3-hot-only-20261128`) sau khi `muse_helper_compatibility_json` xác nhận đủ 4 capability `["multi_session", "lease_renew", "checkpoint", "quality_first_v33"]` (qua `POST /api/crawl/helper/handshake` hoặc xác nhận từ App_Video).
- Nếu chưa xác nhận: yêu cầu tường minh `contract_version = "3.3"` trả HTTP **`409` (`muse_helper_incompatible`)**, request mặc định tiếp tục chạy an toàn theo V3.2.
