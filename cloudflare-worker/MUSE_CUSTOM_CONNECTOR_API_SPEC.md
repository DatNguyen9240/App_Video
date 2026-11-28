# Muse Custom Connector API Specification (`custom.app_video`) — V3.3 Multi-session & Hot-Only Quality

Tài liệu chính thức cho **Muse Custom Connector** kết nối Cloudflare Worker **`app-video-scheduler`**.  
Toàn bộ SQL, Atomic Claim, Multi-session Checkpoint/Lease Renew, Server-side V3.3 Hot-Only Quality Verification, Durable Outbox (`registry_sync_outbox`), và Telegram Gate chạy tập trung ở Worker — Muse helper **không cần gọi Cloudflare Execute SQL trực tiếp**.

---

## 1. Kết nối & Xác thực

- **Provider:** `app_video`
- **Allowed host (`api_hosts`):** `["app-video-scheduler.doctortphat.workers.dev"]`
- **Base URL:** `https://app-video-scheduler.doctortphat.workers.dev`
- **Xác thực cho `/api/crawl/*`:** Header `Authorization: Bearer <MUSE_APP_VIDEO_TOKEN>` (lưu trong Secure Store của Muse).
- **Header khuyến nghị:** `User-Agent: Muse-Custom-Connector/1.0`, `Content-Type: application/json` (cho `POST`).
- **Tách biệt quyền:**
  - `MUSE_APP_VIDEO_TOKEN` chỉ truy cập được `/api/crawl/*` và chỉ thao tác trên các request `platform = 'threads'`.
  - **Bắt buộc owner phía server:** Worker luôn ép `claimed_by = "muse-ai"` cho mọi thao tác (`claim`, `progress`, `lease/renew`, `checkpoint`, `complete`, `fail`); mọi request tự khai `claimed_by` / `worker_id` khác `"muse-ai"` bị từ chối **`403 Forbidden`**.
  - Mọi yêu cầu sang `/api/app/*` bằng token của Muse bị từ chối `401`.
  - `GET /api/crawl-queue/signal` giữ cơ chế bảo vệ theo `MUSE_SIGNAL_TOKEN` (header `X-Signal-Token` hoặc `Authorization: Bearer`, nếu cấu hình) và chỉ trả `{"queued": true|false}`.

---

## 2. Danh sách Endpoint

### 2.1. `GET /api/crawl-queue/signal` (Chỉ đọc)
- **Xác thực:** Header `X-Signal-Token` / `Authorization: Bearer` (nếu cấu hình `MUSE_SIGNAL_TOKEN`).
- **Response `200` (`Cache-Control: no-store`):** `{"queued": true}` hoặc `{"queued": false}`.
- Trả `true` chỉ khi có request `threads` ở trạng thái `queued` không bị `execution_phase = 'blocked'`, còn retry budget (V3.3 kiểm `error_attempts < max_attempts` nên resume sạch không bị kẹt), **không** có request nào đang `running` còn lease, và **không** còn `registry_sync_outbox` / `registry_sync_status` `pending|error`.
- **Lỗi D1:** HTTP `503`.

### 2.2. `GET /api/crawl/health` (Chỉ đọc — kèm trạng thái tương thích V3.3)
- **Headers:** `Authorization: Bearer <token>`
- **Response `200` (`Cache-Control: no-store`):**
  ```json
  {
    "ok": true,
    "auth_scope": "muse_crawl",
    "phase": "phase_2_active",
    "worker": "app-video-scheduler",
    "version": "2.3.0",
    "db_ok": true,
    "registry_ready": true,
    "registry_count": 171,
    "contract_version": "3.3",
    "legacy_contract_version": "3.2",
    "skill_name": "threads_crawl",
    "skill_version": "1.1",
    "quality_policy_version": "3.3-hot-only-20261128",
    "helper_compatibility": {
      "v33_compatible": true,
      "default_contract_version": "3.3",
      "required_capabilities": ["multi_session", "lease_renew", "checkpoint", "quality_first_v33"]
    },
    "server_time": "2026-11-28T10:00:00.000Z"
  }
  ```

### 2.2b. `POST /api/crawl/helper/handshake` (Báo cáo tương thích Muse Helper V3.3)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "helper_name": "muse-ai",
    "helper_version": "muse-helper/3.3.1",
    "supported_contracts": ["3.2", "3.3"],
    "skill_name": "threads_crawl",
    "skill_version": "1.1",
    "capabilities": ["multi_session", "lease_renew", "checkpoint", "quality_first_v33"],
    "note": "Helper ready for V3.3 Hot & Very Hot multi-session crawl"
  }
  ```
- **Quy tắc gate:** Worker chỉ bật `v33_compatible = true` khi có đủ 4 capabilities bắt buộc hoặc xác nhận thủ công hợp lệ. Trước khi sẵn sàng, tạo request `contract_version = "3.3"` sẽ trả HTTP **`409` (`muse_helper_incompatible`)**, còn request mặc định giữ nguyên V3.2.

### 2.3. `POST /api/crawl/registry/check` (Chỉ đọc — Pre-Discovery V3.2/V3.3)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body** (tối đa `100` khóa/items mỗi lượt):
  ```json
  {
    "platform": "threads",
    "dedup_keys": ["threads:post:DgC-8Jun454"],
    "canonical_urls": ["https://threads.net/@kocminhchau/post/DgG8LIGicmA"],
    "post_ids": ["DgC-8Jun454"]
  }
  ```
  *(Hoặc dùng `{"items": [{"platform": "threads", "post_id": "...", "canonical_url": "..."}]}`.)*
- **Response `200`:** trả `existing`, `existing_keys`, `existing_count`, `new_count`, `total_checked`.
- **Lỗi `503` (`registry_unavailable: true`):** **Fail-closed** — Dừng phân loại `NEW`, tuyệt đối không coi là bài mới.

### 2.4. `POST /api/crawl/claim` (Atomic Claim + Single-Flight + Multi-session Resume)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:** `{"request_id": "crw_..."}` *(Truyền tường minh `request_id` từ Manual Prompt / Resume Prompt; `claimed_by` cố định là `"muse-ai"`; lease kỹ thuật nội bộ mặc định `3600s` cho V3.3 — không giới hạn tổng thời gian request).*
- **Quy tắc server:**
  1. Tự động chạy `expireStaleLeases` trước khi claim (phiên V3.3 hết hạn tăng `error_attempts`, nhả về `paused`, KHÔNG tự đánh dấu `completed`).
  2. **Strict Targeted Claim (`request_id`):** `404` nếu không tồn tại; `403` nếu không phải `threads`; `409` nếu đang `running`/`completed`/`blocked` (`request_blocked`) hoặc hết `max_attempts`.
  3. **Single-flight:** Nếu đang có request nào `status = 'running'` còn lease, hoặc còn `registry_sync_outbox` / `registry_sync_status IN ('pending', 'error')`, trả `request: null` (`single_flight_blocked: true`). Không pick request có `execution_phase = 'blocked'`.
  4. Chỉ nhận `platform = 'threads'`. `all`, `tiktok`, `shopee` hiện **chưa hỗ trợ tự động hoàn tất request đầy đủ**.
- **Response `200` khi nhận được việc:** trả thêm:
  - `contract_version`, `skill_version` (`"1.1"`), `quality_policy_version` (`"3.3-hot-only-20261128"`), `target_metric`
  - `session_seq` (`1, 2, 3...`), `error_attempts`, `lease_renew_count` (`0`), `max_lease_renewals` (`2`)
  - `accumulated_quality_count`, `accumulated_new_qualified_count`, `remaining_target`
  - `checkpoint` (payload checkpoint phiên trước nếu là Resume)

### 2.5. `POST /api/crawl/progress` (Cập nhật tiến độ thực tế)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:** gồm `request_id`, `claimed_by: "muse-ai"`, `attempt`, `session_seq`, `run_id`, `stage`, `message`, `counters` (gồm cả `quality_qualified_count`, `below_hot_count`, `traffic_content_count` nếu có).
- **Guard bắt buộc:** Kiểm tra `request_id`, `claimed_by`, `attempt`, `session_seq`, `status = 'running'`, và lease còn hạn (`409` nếu lỗi/stale).

### 2.5b. `POST /api/crawl/lease/renew` (Gia hạn lease kỹ thuật nội bộ — V3.3)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "request_id": "crw_...",
    "claimed_by": "muse-ai",
    "attempt": 1,
    "session_seq": 1,
    "progress_delta": 20
  }
  ```
- **Quy tắc & Guard bắt buộc:**
  1. Kiểm tra `request_id`, `claimed_by = "muse-ai"`, `attempt`, `session_seq`, `status = 'running'`, lease chưa hết hạn.
  2. Tối đa **`2` lần gia hạn/phiên** (`lease_renew_count < 2`); mỗi lần cộng **`+1800s` (`+30 phút`)**. Vượt quá trả **`409` (`max_lease_renewals_reached`)**.
  3. **Bắt buộc có tiến độ crawl mới thật sự** (tăng `scanned_count` / `quality_qualified_count` so với lần gia hạn trước hoặc `progress_delta > 0`), nếu không trả **`409` (`no_real_progress`)**.

### 2.5c. `POST /api/crawl/checkpoint` (Lưu Checkpoint & Tạm dừng / Chặn an toàn liên phiên)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "request_id": "crw_...",
    "claimed_by": "muse-ai",
    "attempt": 1,
    "session_seq": 1,
    "pause_reason": "session_budget_reached",
    "run_id": "crw_..._s1",
    "crawled_at": "2026-11-28T10:00:00.000Z",
    "items": [],
    "checkpoint": { "cursor": "...", "tried_queries": ["decor phong"] },
    "result_summary": { "counters": { "scanned_count": 65, "quality_qualified_count": 37 } }
  }
  ```
- **Hành vi & Guard:**
  1. Server chấm lại chất lượng từng item theo **Quality Policy `3.3-hot-only-20261128`** (chỉ tính `very_hot`: `views >= 50000 || likes >= 1000` và `hot`: `views >= 10000 || likes >= 300`; mọi bài `< Hot` bị loại `below_hot_threshold` kể cả nếu client gửi sai tier), chèn `registry_sync_outbox` + `crawl_request_quality_items` (`PRIMARY KEY (request_id, dedup_key)` chặn cộng đúp xuyên phiên).
  2. Drain `registry_sync_outbox` tới `registry_sync_status = "ok"` trước khi nhả phiên về `status = "queued"` (`execution_phase = "paused"` hoặc `"blocked"` nếu `pause_reason` là `captcha_blocked` / `rate_limited` / `blocked: true`). Nếu D1 lỗi giữa chừng -> **`503` (`checkpoint_sync_pending: true`)**.
  3. **Không gửi thông báo Telegram hoàn tất khi checkpoint.**
  4. **Idempotent Replay:** Gọi lại với cùng `(request_id, session_seq, attempt)` khi phiên đã checkpoint xong trả `200` (`idempotent_replay: true`, giữ nguyên tiến độ tích lũy).
  5. **Response `200`:** trả `execution_phase`, `pause_reason`, `accumulated_quality_count`, `remaining_target`, `registry_sync_status: "ok"`, `resume_prompt`.

### 2.6. `POST /api/crawl/complete` (Hoàn tất cuối cùng, Durable Outbox & Telegram Gate)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:** giống V3.2, khuyến nghị truyền kèm `session_seq`, `contract_version: "3.3"`, `quality_policy_version: "3.3-hot-only-20261128"`, `is_checkpoint: false`.
- **Ràng buộc bổ sung cho V3.3:**
  1. **Cấm gọi `/complete` với `stop_reason = "time_budget"` hoặc `"session_budget_reached"`** → trả **`400`** (phải gọi `/api/crawl/checkpoint`).
  2. **Target-based Gate:** Worker tính `projected_quality_count = accumulated_quality_count + batch_new_quality` hoàn toàn server-side (chỉ nhận `Hot` & `Very Hot` hợp lệ). Nếu `< requested_count` mà không có `stop_reason` kết thúc hợp lệ (`sources_exhausted`, `no_qualifying_posts`, `insufficient_data`, `user_cancelled`, `manual_stop`), Worker từ chối **`400` (`target_reached: false`)**.
  3. Toàn bộ guard V3.2 (`items` đúng nền tảng `threads`, `DB.batch` outbox, Telegram Gate chỉ gửi 1 lần khi hoàn tất cuối cùng, Idempotent Retry, `503 recovery_pending`) được giữ nguyên.

### 2.7. `POST /api/crawl/fail` (Báo lỗi & Xử lý Retry)
- Giữ nguyên nguyên tắc V3.2 (`retryable` + `error_attempts` cho V3.3 / `attempts` cho V3.2). Khi lỗi là `captcha_blocked` / `rate_limited`, Worker đặt `execution_phase = 'blocked'` để ngăn tự động vòng lặp vô hạn.

---

## 3. Luồng thực thi chuẩn cho Helper của Muse (`custom.app_video` V3.3)

1. `GET /api/crawl/health` + `POST /api/crawl/helper/handshake` (`skill_version: "1.1"`) -> xác nhận V3.3 sẵn sàng.
2. `claim = POST /api/crawl/claim` với `{"request_id": "<request_id>"}`. Ghi nhớ `attempt`, `session_seq`, `remaining_target`, dự trữ **`max(10 phút, 20% lease)`** trước `lease_expires_at`.
3. Nạp `THREADS_CRAWL_SKILL` v1.1, bỏ qua truy vấn trong `checkpoint.tried_queries`, gọi `POST /api/crawl/registry/check` trước khi chấm (<= 100 items/lượt, fail-closed khi `503`).
4. Chỉ thu thập và phân tích sâu bài **`Hot`** (`views >= 10.000` OR `likes >= 300`) và **`Very Hot`** (`views >= 50.000` OR `likes >= 1.000`). Không giữ bài Emerging/Watchlist/dưới ngưỡng Hot vào kho nghiên cứu mới.
5. Định kỳ gọi `POST /api/crawl/progress`; nếu chạm dự trữ khi còn tiến độ và `lease_renew_count < 2`, gọi `POST /api/crawl/lease/renew` (`+30p`).
6. Nếu chạm dự trữ, hết 2 lần gia hạn, gặp CAPTCHA hoặc rate limit mà chưa đủ `requested_count`: ghi file Drive phiên hiện tại với `is_checkpoint: true`, `session_seq`, `run_status: "paused"`, rồi gọi `POST /api/crawl/checkpoint`.
7. Chỉ khi tích lũy đủ `requested_count` bài `Hot`/`Very Hot` mới (hoặc nguồn cạn thực sự): ghi cặp Data JSON + Report JSON cuối cùng (`is_checkpoint: false`, `contract_version: "3.3"`, `quality_policy_version: "3.3-hot-only-20261128"`) lên Drive và gọi `POST /api/crawl/complete`.
