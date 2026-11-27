# Hướng dẫn SQL trực tiếp cho Muse trên Cloudflare D1 (Cloudflare Connector) — Data Contract V3.2

> **Cập nhật kiến trúc (Phase 2 Active):** Với Custom Connector `custom.app_video` (`MUSE_APP_VIDEO_TOKEN`), Muse gọi trực tiếp bộ API nghiệp vụ trên Worker theo tài liệu [MUSE_CUSTOM_CONNECTOR_API_SPEC.md](./MUSE_CUSTOM_CONNECTOR_API_SPEC.md) (`/api/crawl/health`, `/api/crawl/registry/check`, `/api/crawl/claim`, `/api/crawl/progress`, `/api/crawl/complete`, `/api/crawl/fail`). Worker là nơi duy nhất thực thi SQL xuống D1. Tài liệu dưới đây giữ lại làm đặc tả đối chiếu quy tắc V3.2 & câu lệnh SQL bên trong Worker.

---

## 1. Bảng D1 liên quan

- `crawl_requests` (được quản lý trong `0001_v2_market_and_commands.sql`)
- `market_post_registry` (`0003_cross_run_dedup_registry.sql`) — nguồn nhận diện bài viết dùng chung xuyên máy & xuyên lượt crawl (`171` bài lịch sử seed từ `0004_seed_post_identity_index_171.sql`)
- `market_post_snapshots` (`0003_cross_run_dedup_registry.sql`) — lịch sử số liệu từng lần đo với `snapshot_idempotency_key` duy nhất
- `registry_sync_outbox` (`0003_cross_run_dedup_registry.sql`) — hàng đợi outbox bền vững khi cập nhật completion + registry

### Quy ước khóa chuẩn (`dedup_key`, `canonical_url`, `snapshot_idempotency_key`)
- Chuẩn hóa URL (`canonical_url`): đổi host `threads.com`, `www.threads.com`, `www.threads.net` -> `threads.net`, bỏ query/fragment (`?igshid=...`) và dấu `/` cuối.
- `dedup_key`:
  - Có `post_id`: `<platform>:post:<post_id>` (ưu tiên cao nhất, ví dụ `threads:post:DgKGiD8EiuO`)
  - Thiếu `post_id`, có `item_id`: `<platform>:item:<item_id>`
  - Chỉ có URL: `<platform>:url:<canonical_url>`
- `snapshot_idempotency_key` (bắt buộc dựa trên **lượt đo thực tế**, tuyệt đối **không** đưa `client_id` hay thời điểm sync vào khóa):
  ```text
  <dedup_key>|<run_id>|<measured_at>|<views>:<likes>:<comments>:<shares>:<reposts>:<quotes>
  ```
  (Nếu giá trị metric là `NULL` thì để rỗng, ví dụ `threads:post:DgC-8Jun454|threads-poc-001-20261125|2026-11-25|:140::::`).

---

## 2. Kiểm tra Registry trước Discovery (Fail-Closed)

Trước khi xác nhận bài là `NEW`, Muse tra cứu theo batch `<= 100` khóa/lần bằng cả `dedup_key`, `canonical_url` và `post_id`:

```sql
SELECT dedup_key, platform, post_id, post_pk, canonical_url, content_hash,
       first_run_id, last_run_id, first_seen_at, last_seen_at,
       snapshot_count, latest_views_display, latest_views_precision,
       latest_views_source, latest_views_measured_at, latest_views_evidence_url,
       latest_metrics_json
FROM market_post_registry
WHERE dedup_key IN (?, ?, ...)
   OR (platform = 'threads' AND canonical_url IN (?, ?, ...))
   OR (platform = 'threads' AND post_id IN (?, ?, ...));
```

**Quy tắc Fail-Closed bắt buộc:**
- Nếu Cloudflare Connector / D1 trả lỗi timeout, HTTP error hoặc SQL error: **DỪNG** phân loại `NEW` và báo lỗi registry (`registry_unavailable`), tuyệt đối **không** coi lỗi truy vấn là "chưa từng có trong lịch sử".

---

## 3. Điều kiện bắt buộc để tính `new_qualified_count` (V3.2)

Một bài `NEW` chỉ được tính vào `new_qualified_count` (so với `requested_count`) khi thỏa mãn **tất cả**:
1. `classification != 'REJECTED'` và `data_quality != 'rejected'`.
2. `verification_status != 'unverified'`, `data_quality != 'needs_verification'`, `trend_tier != 'unverified'`.
3. `views` là số nguyên không âm (`views >= 0`).
4. Có đủ **5 trường chứng cứ views V3.2 hợp lệ**:
   - `views_display`: chuỗi hợp lệ dạng `^\d+(?:[.,]\d+)?\s*[KMBkmb]?$` (ví dụ `'513K'`, `'3.6K'`).
   - `views_precision` nằm trong `('rounded_display', 'display_approx', 'exact')`.
   - `views_source = 'threads_web_browser'` (đối với Threads).
   - `views_measured_at`: chuỗi ISO-8601 hợp lệ.
   - `views_evidence_url`: URL `https://` hợp lệ trỏ về trang bài lẻ trên `threads.net` hoặc `threads.com`.

Bài có `views` dạng số nhưng thiếu/sai bất kỳ trường chứng cứ nào trong 5 trường trên phải xếp `UNVERIFIED`, **không** được cộng vào `new_qualified_count`.

---

## 4. Ghi Registry & Snapshot Nguyên Tử (Kiểm tra Lease + Single-Winner `NEW`)

### Bước 4.1: Xác minh quyền sở hữu claim & lease còn hạn trước khi ghi

```sql
SELECT id, status, claimed_by, attempts, lease_expires_at
FROM crawl_requests
WHERE id = ?
  AND status = 'running'
  AND claimed_by = ?
  AND attempts = ?
  AND (lease_expires_at IS NULL OR lease_expires_at >= ?);
```

Nếu query trả về 0 dòng (hết lease, mất quyền claim hoặc đã bị task khác thu hồi): **DỪNG NGAY**, không ghi bất kỳ dòng nào vào `market_post_registry` hay `market_post_snapshots`.

### Bước 4.2: Nếu bài từng lưu bằng URL và nay mới có `post_id` — Hợp nhất khóa không làm trùng snapshot

1. Kiểm tra dòng URL và kiểm tra xem khóa `post_id` mục tiêu đã tồn tại riêng chưa:
```sql
SELECT * FROM market_post_registry
WHERE platform = ? AND canonical_url = ? AND dedup_key LIKE '%:url:%'
LIMIT 1;
```
2. Nếu chưa tồn tại dòng `platform:post:<post_id>`:
   - Đổi khóa chính từ `<oldKey>` sang `<newKey>` (`<platform>:post:<post_id>`):
   ```sql
   UPDATE market_post_registry
   SET dedup_key = ?, post_id = COALESCE(?, post_id), post_pk = COALESCE(?, post_pk)
   WHERE dedup_key = ?
     AND NOT EXISTS (SELECT 1 FROM market_post_registry r2 WHERE r2.dedup_key = ?);
   ```
   - Hợp nhất các snapshot cũ gắn `<oldKey>` sang `<newKey>` và giữ chuẩn `snapshot_idempotency_key` (xóa bản trùng nếu đã tồn tại bản mang `<newKey>`):
   ```sql
   DELETE FROM market_post_snapshots
   WHERE dedup_key = ?
     AND EXISTS (
         SELECT 1 FROM market_post_snapshots s2
         WHERE s2.dedup_key = ?
           AND COALESCE(s2.run_id, '') != ''
           AND s2.run_id = market_post_snapshots.run_id
           AND COALESCE(s2.measured_at, '') = COALESCE(market_post_snapshots.measured_at, '')
     );

   UPDATE market_post_snapshots
   SET dedup_key = ?,
       post_id = COALESCE(?, post_id),
       snapshot_idempotency_key = CASE
           WHEN snapshot_idempotency_key LIKE (? || '|%')
               THEN (? || '|' || substr(snapshot_idempotency_key, length(?) + 2))
           ELSE snapshot_idempotency_key
       END
   WHERE dedup_key = ?;
   ```
3. Nếu dòng `platform:post:<post_id>` **đã tồn tại song song** với dòng URL cũ: chạy `DELETE`/`UPDATE` snapshot ở bước trên để dồn hết snapshot về `<newKey>`, cộng dồn `first_seen_at` sớm nhất, cập nhật `snapshot_count`, rồi xóa dòng `oldKey` thừa trong `market_post_registry`.

### Bước 4.3: Ghi bài `NEW` nguyên tử (Single-Winner Race Protection)

```sql
INSERT INTO market_post_registry (
    dedup_key, platform, post_id, post_pk, canonical_url, content_hash,
    first_run_id, last_run_id, first_seen_at, last_seen_at, snapshot_count,
    latest_views_display, latest_views_precision, latest_views_source,
    latest_views_measured_at, latest_views_evidence_url, latest_metrics_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING;
```

Kiểm tra `meta.changes` (hoặc `SELECT changes()`):
- **Nếu `changes = 1`**: Tác vụ hiện tại là người chiến thắng duy nhất -> tính bài là `NEW` (và cộng `new_qualified_count` nếu đạt đủ Bước 3). Thực thi **cùng trong batch nguyên tử** (nếu chèn snapshot lỗi thì rollback luôn `market_post_registry`) để chèn snapshot đầu tiên:
```sql
INSERT INTO market_post_snapshots (
    id, snapshot_idempotency_key, dedup_key, platform, post_id, run_id, measured_at,
    likes_count, comments_count, reposts_count, quotes_count, shares_count, views_count,
    views_display, views_precision, views_source, views_evidence_url, classification, created_at
)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
WHERE EXISTS (
    SELECT 1 FROM market_post_registry
    WHERE dedup_key = ? AND COALESCE(first_run_id, '') = COALESCE(?, '') AND first_seen_at = ?
)
ON CONFLICT(snapshot_idempotency_key) DO NOTHING;
```
- **Nếu `changes = 0`**: Đã có trong lịch sử (hoặc một task đồng thời vừa ghi ngay trước đó) -> **KHÔNG tính `NEW`**. Chuyển sang Bước 4.4.

### Bước 4.4: Ghi snapshot bài đã tồn tại (Idempotent + Bảo vệ snapshot cũ đến muộn + `snapshot_count` nguyên tử)

1. Chèn snapshot chỉ khi chưa tồn tại cùng `snapshot_idempotency_key` hoặc cùng `(dedup_key, run_id, measured_at)`:
```sql
INSERT INTO market_post_snapshots (
    id, snapshot_idempotency_key, dedup_key, platform, post_id, run_id, measured_at,
    likes_count, comments_count, reposts_count, quotes_count, shares_count, views_count,
    views_display, views_precision, views_source, views_evidence_url, classification, created_at
)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
WHERE NOT EXISTS (
    SELECT 1 FROM market_post_snapshots
    WHERE dedup_key = ?
      AND COALESCE(?, '') != ''
      AND run_id = ?
      AND COALESCE(measured_at, '') = COALESCE(?, '')
)
ON CONFLICT(snapshot_idempotency_key) DO NOTHING;
```
2. Cập nhật `market_post_registry` **đơn điệu theo thời gian đo** (`measured_at`) và tính **`snapshot_count` nguyên tử từ SQL** (snapshot cũ đến muộn **không** ghi đè `last_seen_at`, `last_run_id`, `latest_metrics_json` hay `latest_views_*` mới hơn):
```sql
UPDATE market_post_registry
SET canonical_url = COALESCE(?, canonical_url),
    post_id = COALESCE(?, post_id),
    post_pk = COALESCE(?, post_pk),
    content_hash = COALESCE(?, content_hash),
    first_seen_at = CASE
        WHEN ? IS NOT NULL AND (first_seen_at IS NULL OR ? < first_seen_at) THEN ?
        ELSE first_seen_at
    END,
    last_run_id = CASE
        WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN COALESCE(?, last_run_id)
        ELSE last_run_id
    END,
    last_seen_at = CASE
        WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN ?
        ELSE last_seen_at
    END,
    latest_views_display = CASE
        WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN COALESCE(?, latest_views_display)
        ELSE latest_views_display
    END,
    latest_views_precision = CASE
        WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN COALESCE(?, latest_views_precision)
        ELSE latest_views_precision
    END,
    latest_views_source = CASE
        WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN COALESCE(?, latest_views_source)
        ELSE latest_views_source
    END,
    latest_views_measured_at = CASE
        WHEN ? IS NOT NULL AND (latest_views_measured_at IS NULL OR ? >= latest_views_measured_at) THEN ?
        ELSE latest_views_measured_at
    END,
    latest_views_evidence_url = CASE
        WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN COALESCE(?, latest_views_evidence_url)
        ELSE latest_views_evidence_url
    END,
    latest_metrics_json = CASE
        WHEN ? IS NOT NULL AND (last_seen_at IS NULL OR ? >= last_seen_at) THEN ?
        ELSE latest_metrics_json
    END,
    snapshot_count = COALESCE(
        (SELECT COUNT(*) FROM market_post_snapshots WHERE dedup_key = market_post_registry.dedup_key),
        snapshot_count + 1
    )
WHERE dedup_key = ?;
```

---

## 5. Completion Nguyên Tử Kèm Outbox Phục Hồi (`registry_sync_outbox`)

Khi chốt lượt crawl trực tiếp trên D1, Muse **không** tự đánh dấu xong xuôi nếu phần ghi registry chưa an toàn:

### Cách chuẩn dùng Batch nguyên tử (`crawl_requests` + `registry_sync_outbox`)

1. Thực hiện **trong cùng một batch/giao dịch D1**:
   - Ghi các chunk `<= 100` bài vào `registry_sync_outbox` với điều kiện claim/lease vẫn còn hiệu lực:
   ```sql
   INSERT INTO registry_sync_outbox (
       id, request_id, run_id, crawled_at, payload_json, status, attempts, last_error, created_at, updated_at
   )
   SELECT ?, ?, ?, ?, ?, 'pending', 0, NULL, ?, ?
   WHERE EXISTS (
       SELECT 1 FROM crawl_requests
       WHERE id = ? AND status = 'running' AND claimed_by = ? AND attempts = ?
         AND (lease_expires_at IS NULL OR lease_expires_at >= ?)
   );
   ```
   - Chốt `crawl_requests` (ghi sẵn `"registry_sync_status": "pending"` trong `result_summary_json`):
   ```sql
   UPDATE crawl_requests
   SET status = ?, result_summary_json = ?, report_file_id = ?, error_message = ?, updated_at = ?
   WHERE id = ? AND status = 'running' AND claimed_by = ? AND attempts = ?
     AND (lease_expires_at IS NULL OR lease_expires_at >= ?);
   ```
   *(Nếu `UPDATE` trả `changes = 0`: rollback/hủy toàn bộ, trả xung đột `409`, không ghi registry.)*

2. Sau khi batch commit thành công (`changes = 1`), Muse chạy **Bước 4** cho từng chunk trong `registry_sync_outbox`:
   - Mỗi chunk xong thì `DELETE FROM registry_sync_outbox WHERE id = ?`.
   - Nếu gặp lỗi D1 giữa chừng: giữ nguyên dòng outbox chưa xong và cập nhật trạng thái phục hồi:
   ```sql
   UPDATE registry_sync_outbox
   SET status = 'error', attempts = attempts + 1, last_error = ?, updated_at = ?
   WHERE id = ?;

   UPDATE crawl_requests
   SET result_summary_json = json_set(COALESCE(result_summary_json, '{}'), '$.registry_sync_status', 'error', '$.registry_sync_error', ?),
       updated_at = ?
   WHERE id = ?;
   ```
   - Khi retry (hoặc khi Worker cron quét `registry_sync_outbox`), tiếp tục xử lý nốt các dòng `status IN ('pending', 'error')`, xóa outbox đã xong, và chốt `"registry_sync_status": "ok"`:
   ```sql
   SELECT * FROM registry_sync_outbox
   WHERE request_id = ? AND status IN ('pending', 'error')
   ORDER BY created_at ASC;
   ```
   *(Nếu `crawl_requests` đã `completed` nhưng `registry_sync_status = 'error'`, **tuyệt đối không** bỏ qua như `already_finalized` trước khi drain hết `registry_sync_outbox`.)*

---

## 6. Phối hợp Hook Signal (`GET /api/crawl-queue/signal`) & Cooldown Không Làm Chậm Request Kế Tiếp

Để đồng bộ giữa Worker (`app-video-scheduler`) và Muse Hook (`crawl-queue-signal`), áp dụng chính xác 4 quy ước sau:

1. **Phạm vi Signal & Claim chỉ dành cho Threads:**
   - Worker endpoint `GET /api/crawl-queue/signal` chỉ trả `{"queued": true}` cho request `lower(trim(platform)) = 'threads'` có `status = 'queued'` và `attempts < max_attempts` (**không** xét `all`, `tiktok`, `shopee`).
   - Khi agent Muse được đánh thức bởi hook `crawl-queue-signal`, câu SQL claim trực tiếp trên D1 phải khớp bộ lọc `lower(trim(platform)) = 'threads'` (và kiểm tra không có task `running` còn lease hay `registry_sync_outbox` còn tồn) để tránh đánh thức nhầm hoặc lấy nhầm request nền tảng khác:
   ```sql
   UPDATE crawl_requests
   SET status = 'running',
       claimed_by = 'muse-ai',
       claimed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
       lease_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '+30 minutes'),
       attempts = attempts + 1,
       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
   WHERE id = (
     SELECT id FROM crawl_requests
     WHERE status = 'queued'
       AND lower(trim(platform)) = 'threads'
       AND COALESCE(attempts, 0) < COALESCE(max_attempts, 3)
       AND NOT EXISTS (
         SELECT 1 FROM crawl_requests r_run
         WHERE r_run.status = 'running'
           AND (r_run.lease_expires_at IS NULL OR r_run.lease_expires_at >= strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
       )
       AND NOT EXISTS (
         SELECT 1 FROM registry_sync_outbox
         WHERE status IN ('pending', 'error', 'processing', 'done')
       )
     ORDER BY created_at ASC
     LIMIT 1
   )
     AND status = 'queued'
   RETURNING *;
   ```
2. **Tín hiệu tự hạ về `false` khi đang chạy hoặc registry chưa hoàn tất:**
   - Ngay khi request #1 chuyển `running` (lease còn hạn) hoặc khi hoàn tất nhưng `registry_sync_outbox` / `registry_sync_status` vẫn ở `pending`/`error`, `GET /api/crawl-queue/signal` trả `{"queued": false}` **kể cả khi request #2 đang đợi trong hàng đợi**.
3. **Cơ chế Cooldown 2 tầng phía Muse Hook (tránh wake trùng nhưng không làm chậm request tiếp theo):**
   - **Cạnh lên (`prev_queued = false` → `curr_queued = true`):** **`wake` ngay lập tức**, **không** bị chặn bởi cooldown 600 giây của lần wake trước (do tín hiệu chỉ xuống `false` khi tác vụ trước đã được claim/xử lý và registry đã xong `ok`, nên cạnh lên mới đồng nghĩa request kế tiếp sẵn sàng chạy).
   - **Trạng thái giữ nguyên `true` (`prev_queued = true` → `curr_queued = true`):** Áp dụng `COOLDOWN_SECS = 600` (`now - last_wake_ts < 600` → `silent`) để chặn wake trùng trong cửa sổ ~10–30 giây trước khi agent kịp claim request #1 và giới hạn tối đa 6 wake/giờ nếu agent sập trước khi claim.
   - Nhờ (2) + (3), hai request Threads liên tiếp chạy theo chu kỳ `false → true (wake #1) → false (running/registry) → true (wake #2)` giúp request #2 được xử lý ngay sau ~30–60 giây thay vì chờ 600 giây.
4. **Chuẩn gọi HTTP & Xử lý lỗi phía Script Bash:**
   - Gọi với `Cache-Control: no-store` trên kết nối TLS, chỉ wake khi HTTP status là `200` và JSON khớp `{"queued":true}` (`jq -e '.queued == true'`).
   - Khi HTTP `503` (D1 lỗi), `429` (rate limit), timeout hoặc JSON lỗi: **luôn coi là `fetch_failed` → `silent`** (không ghi đè `prev_queued` thành `false` nếu muốn giữ nguyên cooldown chống bão, hoặc giữ nguyên trạng thái trước đó).
   - Nếu Edge WAF bật Bot Fight, truyền `User-Agent` rõ ràng (ví dụ `Muse-Crawl-Hook/1.0`, tránh UA mặc định bị chặn bởi Cloudflare error `1010`).
