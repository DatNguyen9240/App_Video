# Muse Custom Connector API Specification (`custom.app_video`) — Phase 2 Complete

Tài liệu chính thức cho **Muse Custom Connector** kết nối Cloudflare Worker **`app-video-scheduler`**.  
Toàn bộ SQL, Atomic Claim, Lease, Durable Outbox (`registry_sync_outbox`), Data Contract V3.2 và Telegram Gate chạy tập trung ở Worker — Muse helper **không cần gọi Cloudflare Execute SQL trực tiếp**.

---

## 1. Kết nối & Xác thực

- **Provider:** `app_video`
- **Allowed host (`api_hosts`):** `["app-video-scheduler.doctortphat.workers.dev"]`
- **Base URL:** `https://app-video-scheduler.doctortphat.workers.dev`
- **Xác thực cho `/api/crawl/*`:** Header `Authorization: Bearer <MUSE_APP_VIDEO_TOKEN>` (lưu trong Secure Store của Muse).
- **Header khuyến nghị:** `User-Agent: Muse-Custom-Connector/1.0`, `Content-Type: application/json` (cho `POST`).
- **Tách biệt quyền:**
  - `MUSE_APP_VIDEO_TOKEN` chỉ truy cập được `/api/crawl/*` và chỉ thao tác trên các request `platform = 'threads'`.
  - **Bắt buộc owner phía server:** Worker luôn ép `claimed_by = "muse-ai"` cho mọi thao tác (`claim`, `progress`, `complete`, `fail`); mọi request tự khai `claimed_by` / `worker_id` khác `"muse-ai"` bị từ chối **`403 Forbidden`**.
  - Mọi yêu cầu sang `/api/app/*` bằng token của Muse bị từ chối `401`.
  - `GET /api/crawl-queue/signal` giữ nguyên công khai (chỉ trả `{"queued": true|false}`, không cần token) cho Hook script bash.

---

## 2. Danh sách Endpoint

### 2.1. `GET /api/crawl-queue/signal` (Hook Script công khai — Chỉ đọc)
- **Xác thực:** Không.
- **Response `200` (`Cache-Control: no-store`):** `{"queued": true}` hoặc `{"queued": false}`.
- Trả `true` chỉ khi có request `threads` ở trạng thái `queued` (`attempts < max_attempts`), **không** có request nào đang `running` còn lease, và **không** còn `registry_sync_outbox` / `registry_sync_status` `pending|error`.
- **Lỗi D1:** HTTP `503` (Hook phải giữ `silent`, không coi là `false`).

### 2.2. `GET /api/crawl/health` (Chỉ đọc)
- **Headers:** `Authorization: Bearer <token>`
- **Response `200` (`Cache-Control: no-store`):**
  ```json
  {
    "ok": true,
    "auth_scope": "muse_crawl",
    "phase": "phase_2_active",
    "worker": "app-video-scheduler",
    "version": "2.2.0",
    "db_ok": true,
    "registry_ready": true,
    "registry_count": 171,
    "contract_version": "3.2",
    "server_time": "2026-11-27T07:30:00.000Z"
  }
  ```

### 2.3. `POST /api/crawl/registry/check` (Chỉ đọc — Pre-Discovery V3.2)
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
- **Response `200`:**
  ```json
  {
    "ok": true,
    "read_only": true,
    "existing": { "...": { "dedup_key": "...", "first_seen_at": "...", "last_seen_at": "...", "snapshot_count": 1 } },
    "existing_keys": ["..."],
    "existing_count": 1,
    "new_count": 0,
    "total_checked": 1
  }
  ```
- **Lỗi `503` (`registry_unavailable: true`):** **Fail-closed** — Dừng phân loại `NEW`, tuyệt đối không coi là bài mới.

### 2.4. `POST /api/crawl/claim` (Atomic Claim + Single-Flight, Lease 30 phút)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:** `{"request_id": "crw_..."}` *(Khuyến nghị truyền tường minh `request_id` từ Manual Prompt để tránh claim nhầm request khác; có thể truyền `{}` để fallback FIFO request Threads cũ nhất khi không chỉ định ID. `claimed_by` cố định phía Worker là `"muse-ai"`, tùy chọn `lease_seconds` mặc định `1800`).*
- **Quy tắc server:**
  1. Tự động chạy `expireStaleLeases` trước khi claim.
  2. **Strict Targeted Claim (`request_id`):** Khi caller truyền `request_id`:
     - Tuyệt đối **không fallback** sang request khác trong hàng đợi.
     - `404` nếu không tồn tại; `403` nếu không phải `platform = 'threads'`; `409 Conflict` nếu request không ở `queued` hoặc đã hết `max_attempts`.
  3. **Single-flight:** Nếu đang có request nào `status = 'running'` còn lease, hoặc còn `registry_sync_outbox` / `registry_sync_status IN ('pending', 'error')`, Worker trả `request: null` (`single_flight_blocked: true`).
  4. Chỉ nhận request có `platform = 'threads'`, `status = 'queued'`, `attempts < max_attempts`. Các nền tảng `all`, `tiktok`, `shopee` hiện **chưa hỗ trợ tự động hoàn tất request đầy đủ** qua `/api/crawl/*` (chỉ hỗ trợ đồng bộ dữ liệu Drive).
- **Response `200` khi nhận được việc:**
  ```json
  {
    "ok": true,
    "request": {
      "id": "crw_...",
      "platform": "threads",
      "keywords": "...",
      "requested_count": 20,
      "options": {},
      "status": "running",
      "display_status": "crawling",
      "display_status_label": "Đang crawl",
      "requested_by_source": "app_video",
      "requested_by_chat_id": null,
      "claimed_by": "muse-ai",
      "claimed_at": "2026-11-27T07:30:00.000Z",
      "lease_expires_at": "2026-11-27T08:00:00.000Z",
      "attempt": 1,
      "attempts": 1,
      "max_attempts": 3
    }
  }
  ```
- **Response `200` khi không có việc / đang bận:**
  ```json
  {
    "ok": true,
    "request": null,
    "single_flight_blocked": true,
    "reason": "active_crawl_in_progress"
  }
  ```
  *(Nếu `request` là `null`, agent kết thúc lặng lẽ, không báo Telegram.)*

### 2.5. `POST /api/crawl/progress` (Cập nhật tiến độ thực tế)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "request_id": "crw_...",
    "claimed_by": "muse-ai",
    "attempt": 1,
    "run_id": "run_threads_20261127_01",
    "stage": "enriching_views",
    "message": "Đang đo views trình duyệt Threads (12/20)",
    "counters": {
      "requested_count": 20,
      "scanned_count": 45,
      "unique_candidate_count": 32,
      "new_qualified_count": 12,
      "duplicate_history_count": 14,
      "missing_views_count": 2,
      "unverified_count": 4,
      "rejected_count": 6
    }
  }
  ```
- **Guard bắt buộc:** Kiểm tra `request_id`, `claimed_by`, `attempt`, `status = 'running'`, `platform = 'threads'` và lease còn hạn. Trả `409 Conflict` nếu sai chủ sở hữu, sai attempt hoặc hết lease (không gửi Telegram).
- **Response `200`:**
  ```json
  {
    "ok": true,
    "request_id": "crw_...",
    "status": "running",
    "claimed_by": "muse-ai",
    "attempt": 1,
    "progress": { "stage": "enriching_views", "message": "...", "updated_at": "..." },
    "progress_metrics": { "requested_count": 20, "new_qualified_count": 12, "..." : "..." }
  }
  ```

### 2.6. `POST /api/crawl/complete` (Hoàn tất, Durable Outbox V3.2 & Telegram Gate)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "request_id": "crw_...",
    "claimed_by": "muse-ai",
    "attempt": 1,
    "report_file_id": "<google_drive_report_file_id>",
    "result_summary": {
      "contract_version": "3.2",
      "run_id": "run_threads_20261127_01",
      "crawled_at": "2026-11-27T07:40:00.000Z",
      "run_status": "completed",
      "counters": { "requested_count": 20, "scanned_count": 60, "target_achieved": true },
      "sections": { "3_trends": [], "4_top_viral": [], "7_affiliate_priorities": [], "9_growth_tracking": [], "10_priority_actions": [] },
      "telegram_summary": "..."
    },
    "items": [
      {
        "platform": "threads",
        "post_id": "Dg...",
        "canonical_url": "https://threads.net/@author/post/Dg...",
        "views": 513000,
        "views_display": "513K",
        "views_precision": "rounded_display",
        "views_source": "threads_web_browser",
        "views_measured_at": "2026-11-27T07:38:00.000Z",
        "views_evidence_url": "https://threads.net/@author/post/Dg...",
        "likes": 1200,
        "comments": 45,
        "reposts": 30,
        "shares": 12
      }
    ]
  }
  ```
- **Hành vi nguyên tử & Ràng buộc kiểm tra dữ liệu phía Worker:**
  1. Kiểm tra `request_id`, owner cố định `"muse-ai"` (`403` nếu tự khai owner khác, `409` nếu bản ghi thuộc owner khác), `attempt`, `status = 'running'`, lease còn hạn và `platform = 'threads'` (`403` nếu không phải `threads`).
  2. **Kiểm tra payload Threads & Data Contract V3.2 (trước khi ghi D1):**
     - Toàn bộ phần tử trong `items`/`records` phải thuộc `threads` (`platform` nếu có phải là `'threads'`, không chứa `dedup_key` hay URL của `tiktok`/`shopee`, ngược lại trả **`400`**).
     - Bắt buộc gửi kèm mảng `items`/`records`: **từ chối `400`** nếu báo cáo khai `new_count > 0` / `new_qualified_count > 0` mà thiếu mảng records hoặc số records ít hơn số khai báo.
     - **Cho phép kết quả `0` bài** khi truyền `items: []` (hoặc `records: []`) kèm đúng cấu trúc V3.2: `contract_version: "3.2"`, `run_id` hợp lệ, `run_status` thuộc `('no_qualifying_posts', 'insufficient_data', 'partial', 'completed')` (nếu `'completed'` hoặc `'partial'` phải có `stop_reason` rõ ràng) và bộ đếm `new_count = 0`, `new_qualified_count = 0`.
  3. Chèn các chunk (`<= 100` bài) vào `registry_sync_outbox` và chốt `crawl_requests.status = 'completed'`, `registry_sync_status = 'pending'` **trong cùng một `DB.batch`**.
  4. Drain chunk-atomic từng phần vào `market_post_registry` & `market_post_snapshots` (kiểm tra đủ 5 trường chứng cứ views V3.2 để tính `new_qualified_count`, chống ghi đè metrics mới bằng snapshot cũ, nâng cấp URL → `post_id`).
  5. **Telegram Gate:** Tuyệt đối không gửi Telegram nếu `registry_sync_status` vẫn ở `pending`/`error` hoặc outbox đang `in_progress`. Khi gặp lỗi D1 giữa chừng, trả HTTP **`503`** (`recovery_pending: true`).
  6. **Idempotent Retry:** Nếu cùng `(request_id, "muse-ai", attempt)` gọi lại `POST /api/crawl/complete` khi `registry_sync_status` còn `error`/`pending` → tự động phục hồi các chunk còn lại, chốt `registry_sync_status = "ok"` và gửi Telegram đúng 1 lần. Nếu đã `ok` trước đó → trả `200` (`already_finalized: true`, không nhân đôi).

### 2.7. `POST /api/crawl/fail` (Báo lỗi & Xử lý Retry)
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "request_id": "crw_...",
    "claimed_by": "muse-ai",
    "attempt": 1,
    "error_message": "Threads session blocked by login wall",
    "retryable": true
  }
  ```
- **Quy tắc retry & quyền:**
  1. Kiểm tra `request_id`, `claimed_by`, `attempt`, `status = 'running'`, `platform = 'threads'` và lease còn hạn (`409` nếu sai hoặc hết lease).
  2. Nếu **`retryable !== false` và `attempt < max_attempts`** (mặc định `max_attempts = 3`):
     - Đưa request về **`status = 'queued'`** (xóa `claimed_by`, `claimed_at`, `lease_expires_at`, lưu `error_message`), **không gửi thông báo lỗi sớm lên Telegram**.
     - Trả `200`: `{"ok": true, "status": "queued", "retried": true, "attempts": 1, "max_attempts": 3, "retries_remaining": 2}`.
  3. Nếu **`retryable === false` hoặc `attempt >= max_attempts`**:
     - Chốt **`status = 'failed'`** (`retried: false`), ghi `error_message`, gửi Telegram báo lỗi đúng 1 lần (vẫn qua guard registry & `crawl_notifications`).
  4. Gọi lặp với cùng `(request_id, claimed_by, attempt)` khi đã chốt `failed` → trả `200` (`already_finalized: true`); gọi sau khi request đã requeue/bị worker khác claim → trả `409 Conflict`.

---

## 3. Luồng thực thi chuẩn cho Helper của Muse (`custom.app_video` & Manual Prompt Trigger)

1. `claim = POST /api/crawl/claim` với body `{"request_id": "<request_id_trong_prompt>"}` (hoặc `{}` khi poll không chỉ định). Nếu `claim.request == null` hoặc HTTP `409`/`404` → báo rõ trạng thái và **không tự lấy request khác**.
2. Lưu `request_id = claim.request.id`, `attempt = claim.request.attempt`, `claimed_by = claim.request.claimed_by` (`"muse-ai"`), `requested_by_source = claim.request.requested_by_source`, ngân sách thời gian dự trữ tối thiểu `max(10 phút, 20% lease)` trước `lease_expires_at`.
3. Trước khi crawl / ghi kết quả mới: gọi `POST /api/crawl/registry/check` (batch `<= 100`). Nếu `503` → gọi `POST /api/crawl/fail` (`retryable: true`) và dừng.
4. Trong khi crawl: gọi định kỳ `POST /api/crawl/progress` với `scanned_count`, `new_qualified_count`...
5. Khi xong: ghi **đủ cặp 2 file** lên Google Drive (`Threads/threads_trend_*.json` + `Reports/trend_report_*.json`) cùng có đủ `request_id`, `run_id`, `attempt`, `claimed_by: "muse-ai"`, `platform: "threads"`, `contract_version: "3.2"`, rồi gọi `POST /api/crawl/complete` kèm `items` & `result_summary`. Nếu gặp HTTP `503` (`recovery_pending: true`), retry cùng payload (Worker Cron `*/5` cũng tự phục hồi và đối soát Drive an toàn khi đủ cặp Data + Report khớp `(request_id, run_id, attempt)`).
6. Khi lỗi: gọi `POST /api/crawl/fail` (`retryable: true` cho lỗi tạm thời, `false` cho lỗi vĩnh viễn).
