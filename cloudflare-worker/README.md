# Cloudflare Worker (`app-video-scheduler`) V2 — Review, Live E2E & Rollback Guide

> **Safety Notice:** The original live V1 Worker is preserved untouched at [`worker.js`](file:///d:/Tool%20MMO/CrawlVIDEO_ALLIN1/App_Video/cloudflare-worker/worker.js). Do **not** deploy to production until [`worker.v2.js`](file:///d:/Tool%20MMO/CrawlVIDEO_ALLIN1/App_Video/cloudflare-worker/worker.v2.js) and [`migrations/0001_v2_market_and_commands.sql`](file:///d:/Tool%20MMO/CrawlVIDEO_ALLIN1/App_Video/cloudflare-worker/migrations/0001_v2_market_and_commands.sql) are reviewed and approved.

---

## 1. Compatibility Review vs. Running V1 Worker (`worker.js`)
- **Preserved 100%:**
  - `GET /` → `"App Video Scheduler: ONLINE"`
  - `POST /telegram` + `X-Telegram-Bot-Api-Secret-Token` + `TELEGRAM_OWNER_ID` private chat check
  - Existing D1 `DB` binding (`app-video-db`) and `crawl_notifications` table + `notifyNewCrawl(env)` Cron
  - Existing secrets/vars: `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_DRIVE_FOLDER_ID`, `TELEGRAM_BOT_TOKEN_VIDEO`, `TELEGRAM_OWNER_ID`, `TELEGRAM_WEBHOOK_SECRET`
  - Existing V1 reply texts (`/start`, `/status`, `/drive`, `/crawl`, `/notifytest`)
- **Migration Safety (`0001_v2_market_and_commands.sql`):**
  - Strictly uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`.
  - Does **not** alter, drop, or touch `crawl_notifications` or any existing data.

---

## 2. Muse AI OAuth Connector D1 Polling Contract
Because Muse AI connects via official Cloudflare OAuth Connector (no inbound API):
- **Cadence:** `test_5m` (5 minutes, limited test only) vs. `production_6h` (6 hours default, combined scheduled crawl + queue check).
- **Atomic Claim SQL (for Muse Connector on `app-video-db`):**
  ```sql
  -- Update last poll timestamp for ETA tracking
  INSERT INTO worker_runtime_state (key, value, updated_at)
  VALUES ('muse_last_poll_at', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
  ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;

  -- Atomic claim of oldest queued request with 30-min lease
  UPDATE crawl_requests
  SET status = 'running',
      claimed_by = 'muse-ai',
      claimed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
      lease_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '+30 minutes'),
      attempts = attempts + 1,
      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  WHERE id = (
    SELECT id FROM crawl_requests WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1
  ) AND status = 'queued'
  RETURNING *;
  ```
- **Honesty rule:** Requests remain `queued` ("Đang chờ Muse tiếp nhận") until Muse actually claims them (`status = 'running'`).

---

## 3. Pre-Deploy Backup & Deployment Steps

1. **Backup Current Live Worker:**
   - Save current deployment ID / download current bundle from Cloudflare Dashboard or CLI:
     ```bash
     npx wrangler deployments list
     ```
   - Verify local [`worker.js`](file:///d:/Tool%20MMO/CrawlVIDEO_ALLIN1/App_Video/cloudflare-worker/worker.js) matches production V1.

2. **Apply Additive D1 Migration to `app-video-db`:**
   ```bash
   npx wrangler d1 execute app-video-db --remote --file=cloudflare-worker/migrations/0001_v2_market_and_commands.sql
   ```

3. **Configure Secrets & Vars (Never Print or Commit Secrets):**
   - Set `APP_VIDEO_API_TOKEN` on Cloudflare:
     ```bash
     npx wrangler secret put APP_VIDEO_API_TOKEN
     ```
   - Put the **exact same token** into local `App_Video/.env`:
     ```env
     CLOUDFLARE_WORKER_URL=https://app-video-scheduler.doctortphat.workers.dev
     CLOUDFLARE_APP_TOKEN=<exact_match_with_APP_VIDEO_API_TOKEN>
     ```

4. **Deploy Reviewed `worker.v2.js`:**
   ```bash
   npx wrangler deploy cloudflare-worker/worker.v2.js --config cloudflare-worker/wrangler.toml
   ```

5. **Update Telegram Webhook `allowed_updates` (Required for Inline Keyboard `callback_query`):**
   ```bash
   curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN_VIDEO>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://app-video-scheduler.doctortphat.workers.dev/telegram",
       "secret_token": "<TELEGRAM_WEBHOOK_SECRET>",
       "allowed_updates": ["message", "callback_query"]
     }'
   ```

---

## 4. Live E2E Verification Checklist
1. Open local App_Video (`python app_ui.py`) → Tab `4. Market Intelligence` → click **Kiểm tra Cloudflare** (`db_ok: true`, `drive_ok: true`).
2. Click **Sync Now** → verify files from `AI_WORKSPACE/01_Market_Data/` sync into local SQLite + raw folder; restart `app_ui.py` → verify zero duplicate records.
3. In Telegram, send `/start` → test `📡 App_Video Status` (`ONLINE`), `🔄 Sync App_Video` (single progress message updates via `editMessageText`), `📊 Báo cáo phân tích` + `📥 Gửi báo cáo đầy đủ (.json)`, `📁 Drive Explorer` + `🔗 Mở trên Google Drive` + `📥 Gửi JSON trực tiếp`.
4. **Offline Queue + `pending_ack` Verification:**
   - Stop `app_ui.py` → click `🔄 Sync App_Video` in Telegram (verifies D1 stores `queued` while offline).
   - Start `app_ui.py` → verifies outbound poller claims & executes once (`completed`) and updates Telegram.
5. **Crawl Center Verification:**
   - Submit crawl request in App_Video or Telegram → verify status is `queued` (never fake `running`), verify deduplication on duplicate click, verify `Hủy` works while `queued`.

---

## 5. Rollback Procedure
If any live issue occurs after deploying `worker.v2.js`:
```bash
# Option A: Instant Cloudflare deployment rollback
npx wrangler rollback

# Option B: Re-deploy preserved V1 backup directly
npx wrangler deploy cloudflare-worker/worker.js
```
Note: Additive D1 tables (`app_commands`, `app_heartbeats`, `market_sync_audit`, `crawl_requests`, `worker_runtime_state`) do not affect V1 (`crawl_notifications`), so rolling back the Worker script is instantaneous and lossless.
