# Hướng Dẫn Nâng Cấp Muse Helper & Custom Skill Cho Threads V3.3 (Policy r2)

Tài liệu này hướng dẫn cập nhật **Muse Helper**, **Custom Skill (`threads_crawl` v1.2)** và **Custom Connector (`custom.app_video`)** để kích hoạt **Data Contract V3.3 (`3.3-hot-only-r2-20261128` — Hot & Very Hot Only)** và **Target-based Multi-session Crawl** an toàn mà không làm gián đoạn dữ liệu V3.2 lịch sử.  
**Nguyên tắc phân quyền:** Muse chỉ giao tiếp qua **Custom Connector HTTP `/api/crawl/*`**, **không cấp và không sử dụng quyền SQL trực tiếp vào D1**.

---

## 1. Tại sao cần nâng cấp qua Compatibility Gate?

- Compatibility Gate của Worker **chỉ mở khóa V3.3 khi Muse Helper xác nhận đồng thời Skill `threads_crawl >= 1.2` VÀ Policy `3.3-hot-only-r2-20261128`**, kèm đủ 4 năng lực V3.3:
  1. `multi_session` (nhận `session_seq`, `remaining_target`, `checkpoint`, tạo `session_run_id` riêng mỗi phiên)
  2. `lease_renew` (gọi `POST /api/crawl/lease/renew` tối đa 2×30p khi còn tiến độ)
  3. `checkpoint` (dừng trước ngưỡng dự trữ `max(10p, 20% lease)`, lưu Drive `is_checkpoint: true`, gọi `POST /api/crawl/checkpoint` idempotent 5-tuple)
  4. `quality_first_v33` (xác minh đủ `views` + `likes` không ép `0` thành `null`, chỉ lấy **Hot & Very Hot** theo `THREADS_CRAWL_SKILL v1.2` + `3.3-hot-only-r2-20261128`)
- **Từ chối Helper v1.1 & policy cũ:** Mọi handshake gửi `skill_version: "1.1"` hoặc policy cũ `3.3-hot-only-20261128` đều bị giữ ở trạng thái `v33_compatible = false` (kể cả nếu gửi kèm `verified: true` / `v33_compatible: true`).
- Khi chưa đạt gate: mọi tạo request Threads mới (kể cả bỏ trống `contract_version`) trả HTTP `409 (muse_helper_incompatible)`, **không tự fallback ngầm sang V3.2**.

---

## 2. Bước 1 — Cài đặt Custom Skill `threads_crawl` (v1.2) trên Muse Workspace

1. Sao chép file chuẩn từ repo:
   - Nguồn: [THREADS_CRAWL_SKILL.md](./THREADS_CRAWL_SKILL.md)
   - Đích trong workspace của Muse:
     `~/workspace/custom-skills/threads_crawl/SKILL.md`
2. Đảm bảo phần mở đầu Skill ghi rõ:
   - Skill name: `threads_crawl`
   - Skill version: `1.2`
   - Quality Policy: `3.3-hot-only-r2-20261128` (`Very Hot: views >= 50.000 OR likes >= 1.000`; `Hot: views > 5.000 OR likes >= 200` — không ngoại lệ Emerging hoặc Watchlist)
3. Cập nhật prompt/workflow của Muse để **nạp `threads_crawl v1.2` trước khi discovery**, thay cho các rule nghiệp vụ dài dòng bị lặp lại trong từng tin nhắn prompt.

---

## 3. Bước 2 — Cập nhật Muse Custom Connector (`custom.app_video`)

1. Lấy tài liệu API & SHA-256 mới nhất tại [MUSE_CUSTOM_CONNECTOR_API_SPEC.md](./MUSE_CUSTOM_CONNECTOR_API_SPEC.md) và schema [DATA_CONTRACT_V33.json](./DATA_CONTRACT_V33.json).
2. Bổ sung 3 endpoint mới vào connector action schema của Muse (giữ `Authorization: Bearer <MUSE_APP_VIDEO_TOKEN>`, `claimed_by = "muse-ai"`, **không dùng Execute SQL trực tiếp**):
   - `POST /api/crawl/helper/handshake` (gửi kèm `skill_version: "1.2"` và `quality_policy_version: "3.3-hot-only-r2-20261128"`)
   - `POST /api/crawl/lease/renew`
   - `POST /api/crawl/checkpoint`
3. Đảm bảo `/api/crawl/claim`, `/api/crawl/progress`, `/api/crawl/checkpoint`, `/api/crawl/complete`, `/api/crawl/fail` đều truyền `session_seq` và `attempt` của phiên hiện tại.

---

## 4. Bước 3 — Quy trình Vòng đời 1 Phiên V3.3 cho Helper Agent

1. **Khởi động & Handshake:**
   - Gọi `GET /api/crawl/health` -> xác nhận `db_ok: true`, `registry_ready: true`, `required_skill_version: "1.2"`, `required_quality_policy_version: "3.3-hot-only-r2-20261128"`.
   - Gọi `POST /api/crawl/helper/handshake`:
     ```json
     {
       "helper_name": "muse-ai",
       "helper_version": "muse-helper/3.3.2",
       "supported_contracts": ["3.2", "3.3"],
       "skill_name": "threads_crawl",
       "skill_version": "1.2",
       "quality_policy_version": "3.3-hot-only-r2-20261128",
       "capabilities": ["multi_session", "lease_renew", "checkpoint", "quality_first_v33"]
     }
     ```
2. **Claim & Kiểm tra ngữ cảnh phiên:**
   - Gọi `POST /api/crawl/claim` kèm `{"request_id": "<id>"}`.
   - Đọc `session_seq`, `remaining_target`, `accumulated_quality_count`, `lease_expires_at`, và `checkpoint` (`tried_queries` để không quét lại truy vấn cũ). Không còn giới hạn tổng thời gian request hay bộ chọn thời lượng từ người dùng.
   - Luôn tính ngưỡng dự trữ **`reserve = max(10 phút, 20% lease)`** trước `lease_expires_at`.
3. **Thực thi & Áp dụng Hot & Very Hot Only (Policy r2):**
   - Gọi `POST /api/crawl/registry/check` (<= 100 items/lượt, fail-closed nếu 503).
   - Xác minh cả `views` (5 trường provenance Web V3.2) và `likes` (`0` là hợp lệ khác `null`).
   - Chỉ nhận vào nghiên cứu bài **`Very Hot`** (`views >= 50.000` OR `likes >= 1.000`) hoặc **`Hot`** (`views > 5.000` OR `likes >= 200`). Bài dưới ngưỡng Hot (kể cả đúng `5.000 views & 199 likes`) **không thu thập/phân tích sâu vào kho nghiên cứu mới**.
   - Đánh dấu `traffic_content: true` + `traffic_analysis` (8 trường) cho các bài viral kéo traffic không cần ép Buyer Intent.
4. **Gia hạn Lease kỹ thuật (khi cần & đang có tiến độ thật):**
   - Trước khi hết lease nếu vẫn đang quét thuận lợi và `lease_renew_count < 2`: gọi `POST /api/crawl/lease/renew` kèm `progress_delta > 0`.
5. **Checkpoint (khi chạm dự trữ, hết 2 lần gia hạn, gặp CAPTCHA hoặc Rate Limit):**
   - **KHÔNG gọi `/api/crawl/complete` với `stop_reason: "time_budget"`.**
   - Lưu file Data JSON & Report JSON của phiên lên Drive với `is_checkpoint: true`, `session_seq`, `run_status: "paused"` (hoặc `"blocked"` nếu CAPTCHA).
   - Gọi `POST /api/crawl/checkpoint` với `items`, `pause_reason` (`"session_budget_reached"`, `"max_extensions_reached"`, `"captcha_blocked"`, `"rate_limited"`), và `checkpoint: { tried_queries, cursor, note }`.
6. **Hoàn tất cuối cùng:**
   - Chỉ gọi `POST /api/crawl/complete` khi tổng tích lũy đủ `requested_count` bài `Hot`/`Very Hot` mới HOẶC cạn nguồn thật sự (`stop_reason: "sources_exhausted"` / `"no_qualifying_posts"` / `"insufficient_data"`).

---

## 5. Kích hoạt & Rollback An toàn

- **Kích hoạt chính thức:** Sau khi Muse Helper gọi `/api/crawl/helper/handshake` đúng `skill_version: "1.2"` & `quality_policy_version: "3.3-hot-only-r2-20261128"` (hoặc người vận hành bấm **Xác nhận Helper V3.3** trong Crawl Center), Worker tự động khóa mọi request Threads mới mặc định sang `contract_version = "3.3"`.
- **Rollback tức thời (Zero-downtime):**
  - Không cần sửa code hay xóa D1: chọn `Contract: V3.2 (Legacy)` trong Crawl Center hoặc gọi `/request_crawl threads ... contract=3.2` trên Telegram để chuyển qua lại ngay lập tức.
  - Dữ liệu V3.2/V3.3 đã lưu trong `crawl_requests`, `crawl_request_sessions`, `crawl_request_quality_items`, SQLite và Drive hoàn toàn tách biệt, không xung đột.
