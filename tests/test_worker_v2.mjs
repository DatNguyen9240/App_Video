/**
 * Contract & Security Verification for cloudflare-worker/worker.v2.js
 * Runs in Node 22+ with in-memory D1 & mocked Google Drive / Telegram fetch.
 */
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import worker from "../cloudflare-worker/worker.v2.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const migrationSql = fs.readFileSync(
  path.join(__dirname, "../cloudflare-worker/migrations/0001_v2_market_and_commands.sql"),
  "utf8"
);

// Generate a real ephemeral RSA keypair for Drive JWT signing test
const { privateKey } = crypto.generateKeyPairSync("rsa", {
  modulusLength: 2048,
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
  publicKeyEncoding: { type: "spki", format: "pem" },
});

// Minimal in-memory D1 mock supporting the queries used by worker.v2.js
class MockD1Database {
  constructor() {
    this.commands = [];
    this.heartbeats = new Map();
    this.audits = [];
    this.crawlRequests = [];
    this.notifications = [];
    this.runtimeState = new Map();
  }
  prepare(sql) {
    const db = this;
    const norm = sql.replace(/\s+/g, " ").trim();
    return {
      args: [],
      bind(...args) {
        this.args = args;
        return this;
      },
      async first() {
        if (norm.startsWith("SELECT 1 AS ok")) return { ok: 1 };
        if (norm.startsWith("SELECT value FROM worker_runtime_state WHERE key = ?")) {
          const v = db.runtimeState.get(this.args[0]);
          return v !== undefined ? { value: v } : null;
        }
        if (norm.startsWith("SELECT * FROM app_heartbeats")) {
          const all = Array.from(db.heartbeats.values()).sort((a, b) => b.last_seen_at.localeCompare(a.last_seen_at));
          return all[0] || null;
        }
        if (norm.startsWith("SELECT id, status FROM app_commands WHERE dedup_token = ?")) {
          return db.commands.find((c) => c.dedup_token === this.args[0]) || null;
        }
        if (norm.includes("FROM app_commands WHERE status = 'queued'")) {
          return db.commands.filter((c) => c.status === "queued").sort((a, b) => a.created_at.localeCompare(b.created_at))[0] || null;
        }
        if (norm === "SELECT * FROM app_commands WHERE id = ?") {
          return db.commands.find((c) => c.id === this.args[0]) || null;
        }
        if (norm.startsWith("SELECT * FROM crawl_requests WHERE dedup_token = ?")) {
          return db.crawlRequests.find((c) => c.dedup_token === this.args[0]) || null;
        }
        if (norm.includes("FROM crawl_requests WHERE platform = ? AND lower(trim(keywords)) = ?")) {
          const [plat, kw, cnt] = this.args;
          return db.crawlRequests.find(
            (c) => c.platform === plat && c.keywords.trim().toLowerCase() === kw && c.requested_count === cnt && ["queued", "running"].includes(c.status)
          ) || null;
        }
        if (norm.includes("FROM crawl_requests WHERE status = 'queued'")) {
          return db.crawlRequests.filter((c) => c.status === "queued").sort((a, b) => a.created_at.localeCompare(b.created_at))[0] || null;
        }
        if (norm === "SELECT * FROM crawl_requests WHERE id = ?") {
          return db.crawlRequests.find((c) => c.id === this.args[0]) || null;
        }
        return null;
      },
      async all() {
        if (norm.includes("FROM app_commands")) {
          return { results: [...db.commands].reverse().slice(0, 8) };
        }
        if (norm.includes("FROM crawl_requests")) {
          return { results: [...db.crawlRequests].reverse().slice(0, 40) };
        }
        return { results: [] };
      },
      async run() {
        if (norm.startsWith("INSERT INTO worker_runtime_state")) {
          db.runtimeState.set(this.args[0], this.args[1]);
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("INSERT INTO app_commands")) {
          const [id, command_type, payload_json, requested_by_chat_id, telegram_message_id, dedup_token, created_at, updated_at] = this.args;
          db.commands.push({
            id,
            command_type,
            payload_json,
            status: "queued",
            requested_by_chat_id,
            telegram_message_id,
            dedup_token,
            attempts: 0,
            max_attempts: 3,
            result_json: null,
            error_message: null,
            notified_at: null,
            created_at,
            updated_at,
          });
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("INSERT INTO app_heartbeats")) {
          const [client_id, app_status, sync_status, last_sync_at, last_sync_summary_json, market_counts_json, latest_report_json, sync_interval_seconds, app_version, last_seen_at, updated_at] = this.args;
          db.heartbeats.set(client_id, {
            client_id, app_status, sync_status, last_sync_at, last_sync_summary_json, market_counts_json, latest_report_json, sync_interval_seconds, app_version, last_seen_at, updated_at,
          });
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE app_commands SET status = 'running'")) {
          const [claimed_by, claimed_at, lease_expires_at, updated_at, id] = this.args;
          const row = db.commands.find((c) => c.id === id && c.status === "queued");
          if (!row) return { meta: { changes: 0 } };
          row.status = "running";
          row.claimed_by = claimed_by;
          row.claimed_at = claimed_at;
          row.lease_expires_at = lease_expires_at;
          row.attempts += 1;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE app_commands SET status = 'queued'")) {
          const [updated_at, nowIso] = this.args;
          let changes = 0;
          for (const row of db.commands) {
            if (row.status === "running" && row.lease_expires_at && row.lease_expires_at < nowIso && row.attempts < row.max_attempts) {
              row.status = "queued";
              row.claimed_by = null;
              row.claimed_at = null;
              row.lease_expires_at = null;
              row.updated_at = updated_at;
              changes++;
            }
          }
          return { meta: { changes } };
        }
        if (norm.startsWith("UPDATE app_commands SET status = ?, result_json = ?")) {
          const [status, result_json, error_message, updated_at, id] = this.args;
          const row = db.commands.find((c) => c.id === id && (c.status === "running" || c.status === "queued"));
          if (!row) return { meta: { changes: 0 } };
          row.status = status;
          row.result_json = result_json;
          row.error_message = error_message;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE app_commands SET notified_at = ? WHERE id = ?")) {
          const [notified_at, id] = this.args;
          const row = db.commands.find((c) => c.id === id);
          if (row) row.notified_at = notified_at;
          return { meta: { changes: row ? 1 : 0 } };
        }
        if (norm.startsWith("UPDATE app_commands SET status = 'cancelled'")) {
          const [updated_at, id] = this.args;
          const row = db.commands.find((c) => c.id === id && c.status === "queued");
          if (!row) return { meta: { changes: 0 } };
          row.status = "cancelled";
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("INSERT INTO market_sync_audit")) {
          db.audits.push(this.args);
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("INSERT INTO crawl_requests")) {
          const [id, platform, keywords, requested_count, options_json, dedup_token, poll_profile, expected_pickup_at, requested_by_chat_id, requested_by_source, created_at, updated_at] = this.args;
          db.crawlRequests.push({
            id, platform, keywords, requested_count, options_json, status: "queued", dedup_token,
            muse_connector_mode: "d1_polling", poll_profile, expected_pickup_at, attempts: 0, max_attempts: 3,
            requested_by_chat_id, requested_by_source, created_at, updated_at, notified_at: null,
          });
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET status = 'running'")) {
          const [claimed_by, claimed_at, lease_expires_at, updated_at, id] = this.args;
          const row = db.crawlRequests.find((c) => c.id === id && c.status === "queued");
          if (!row) return { meta: { changes: 0 } };
          row.status = "running";
          row.claimed_by = claimed_by;
          row.claimed_at = claimed_at;
          row.lease_expires_at = lease_expires_at;
          row.attempts += 1;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET status = 'queued'")) {
          const [updated_at, nowIso] = this.args;
          let changes = 0;
          for (const row of db.crawlRequests) {
            if (row.status === "running" && row.lease_expires_at && row.lease_expires_at < nowIso && row.attempts < row.max_attempts) {
              row.status = "queued";
              row.claimed_by = null;
              row.claimed_at = null;
              row.lease_expires_at = null;
              row.updated_at = updated_at;
              changes++;
            }
          }
          return { meta: { changes } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET status = 'cancelled'")) {
          const [updated_at, id] = this.args;
          const row = db.crawlRequests.find((c) => c.id === id && c.status === "queued");
          if (!row) return { meta: { changes: 0 } };
          row.status = "cancelled";
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET status = ?, result_summary_json = ?")) {
          const [status, result_summary_json, report_file_id, error_message, updated_at, id] = this.args;
          const row = db.crawlRequests.find((c) => c.id === id && ["running", "queued"].includes(c.status));
          if (!row) return { meta: { changes: 0 } };
          row.status = status;
          row.result_summary_json = result_summary_json;
          row.report_file_id = report_file_id;
          row.error_message = error_message;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET notified_at = ? WHERE id = ?")) {
          const [notified_at, id] = this.args;
          const row = db.crawlRequests.find((c) => c.id === id);
          if (row) row.notified_at = notified_at;
          return { meta: { changes: row ? 1 : 0 } };
        }
        if (norm.startsWith("INSERT OR IGNORE INTO crawl_notifications")) {
          const [run_id, record_count, created_at] = this.args;
          if (db.notifications.some((n) => n.run_id === run_id)) {
            return { meta: { changes: 0 } };
          }
          db.notifications.push({ run_id, record_count, status: "sending", created_at, sent_at: null });
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_notifications SET status = 'sent'")) {
          const [sent_at, run_id] = this.args;
          const row = db.notifications.find((n) => n.run_id === run_id);
          if (row) {
            row.status = "sent";
            row.sent_at = sent_at;
          }
          return { meta: { changes: row ? 1 : 0 } };
        }
        if (norm.startsWith("UPDATE crawl_notifications SET status = 'delivery_uncertain'")) {
          const [run_id] = this.args;
          const row = db.notifications.find((n) => n.run_id === run_id);
          if (row) row.status = "delivery_uncertain";
          return { meta: { changes: row ? 1 : 0 } };
        }
        return { meta: { changes: 0 } };
      },
    };
  }
}

const WORKSPACE_ID = "1F5m2t3uPxSlicE8PQW70xjf7FSC6345g";
const MARKET_ID = "1MarketDataFolderId123456";
const THREADS_ID = "1jJ0ihxv_LtpZFlkAeBO_BLysLZ9XwcE8";
const REPORTS_ID = "1ReportsFolderId987654321";
const CORR_REPORT_ID = "1CorrectedReportV2FileId888";
const CONTRACT_FILE_ID = "1DataContractV2FileId777";
const INSIDE_FILE_ID = "1Ye3_lNlC9lFW7deyVYT8A1HDImbxfXvY";
const INSIDE_HIDDEN_PARENTS_ID = "1HiddenParentsReport9999";
const OUTSIDE_FILE_ID = "1OutsideSecretFileId99999";

let enableReportsFolder = false;
const telegramCalls = [];

globalThis.fetch = async (url, init = {}) => {
  const sUrl = String(url);
  if (sUrl === "https://oauth2.googleapis.com/token") {
    return new Response(JSON.stringify({ access_token: "mock-drive-token" }), { status: 200 });
  }
  if (sUrl.startsWith("https://www.googleapis.com/upload/drive/v3/files")) {
    return new Response(JSON.stringify({ id: "1CreatedGoogleDocId123", name: "market_report_e2e_20261126_corrected_v2_GoogleDocs" }), { status: 200 });
  }
  if (sUrl.startsWith("https://www.googleapis.com/drive/v3/files?")) {
    const u = new URL(sUrl);
    const q = u.searchParams.get("q") || "";
    if (q.includes(`'${WORKSPACE_ID}' in parents`) && q.includes("01_Market_Data")) {
      return new Response(JSON.stringify({ files: [{ id: MARKET_ID, name: "01_Market_Data" }] }), { status: 200 });
    }
    if (q.includes(`'${WORKSPACE_ID}' in parents`)) {
      return new Response(
        JSON.stringify({
          files: [{ id: MARKET_ID, name: "01_Market_Data", mimeType: "application/vnd.google-apps.folder" }],
        }),
        { status: 200 }
      );
    }
    if (q.includes(`'${MARKET_ID}' in parents`) && q.includes("'Reports'")) {
      return new Response(
        JSON.stringify({ files: enableReportsFolder ? [{ id: REPORTS_ID, name: "Reports" }] : [] }),
        { status: 200 }
      );
    }
    if (q.includes(`'${MARKET_ID}' in parents`)) {
      const children = [{ id: THREADS_ID, name: "Threads", mimeType: "application/vnd.google-apps.folder" }];
      if (enableReportsFolder) {
        children.push({ id: REPORTS_ID, name: "Reports", mimeType: "application/vnd.google-apps.folder" });
      }
      return new Response(JSON.stringify({ files: children }), { status: 200 });
    }
    if (q.includes(`'${REPORTS_ID}' in parents`)) {
      if (q.includes("vnd.google-apps.document")) {
        return new Response(JSON.stringify({ files: [] }), { status: 200 });
      }
      return new Response(
        JSON.stringify({
          files: [
            {
              id: CONTRACT_FILE_ID,
              name: "data_contract_v2.json",
              mimeType: "application/json",
              size: "3100",
              modifiedTime: "2026-11-26T09:10:00Z",
              md5Checksum: "contract1",
            },
            {
              id: CORR_REPORT_ID,
              name: "market_report_e2e_20261126_corrected_v2.json",
              mimeType: "application/json",
              size: "2400",
              modifiedTime: "2026-11-26T09:05:00Z",
              md5Checksum: "corr999",
            },
          ],
        }),
        { status: 200 }
      );
    }
    if (q.includes(`'${THREADS_ID}' in parents`)) {
      return new Response(
        JSON.stringify({
          files: [
            {
              id: INSIDE_FILE_ID,
              name: "threads_crawl_pilot_001.json",
              mimeType: "application/json",
              size: "512",
              modifiedTime: "2026-11-26T05:31:33Z",
              md5Checksum: "abc123",
            },
            {
              id: INSIDE_HIDDEN_PARENTS_ID,
              name: "market_report_hidden_parents.json",
              mimeType: "application/json",
              size: "256",
              modifiedTime: "2026-11-26T05:40:00Z",
              md5Checksum: "def456",
            },
          ],
        }),
        { status: 200 }
      );
    }
    return new Response(JSON.stringify({ files: [] }), { status: 200 });
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${INSIDE_FILE_ID}?alt=media`)) {
    return new Response(JSON.stringify({ run_id: "r1", status: "completed", posts: [] }), { status: 200 });
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${CORR_REPORT_ID}?alt=media`)) {
    return new Response(
      JSON.stringify({
        report: "Bao cao phan tich E2E - BAN CORRECTED V2",
        request_id: "crw_1795683337782_1fc1629a",
        run_id: "e2e-crawl-20261126-1559",
        corrects_report: "market_report_e2e_20261126.json",
        technical_status: "completed",
        data_quality_status: "dat mot phan",
        totals: { records: 20, business_usable_records: 9, needs_verification_records: 11 },
        platform_results: {
          threads: { technical_status: "completed", data_quality_status: "dat mot phan", records: 10, business_usable_records: 9, corrections: ["Khôi phục tiếng Việt có dấu"] },
          tiktok: { technical_status: "partial", data_quality_status: "khong dat", records: 5, business_usable_records: 0, remaining_issues: ["Thiếu caption"] },
          shopee: { technical_status: "partial", data_quality_status: "khong dat", records: 5, business_usable_records: 0, remaining_issues: ["Thiếu giá/shop_id"] },
        },
        telegram_summary: "BÁO CÁO E2E — BẢN CORRECTED V2\nUsable: 9/20",
      }),
      { status: 200 }
    );
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${CONTRACT_FILE_ID}?alt=media`)) {
    return new Response(JSON.stringify({ contract_version: "2.0" }), { status: 200 });
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${INSIDE_HIDDEN_PARENTS_ID}?alt=media`)) {
    return new Response(JSON.stringify({ report: { title: "Hidden Parents Report" } }), { status: 200 });
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${INSIDE_HIDDEN_PARENTS_ID}?fields=`)) {
    return new Response(
      JSON.stringify({
        id: INSIDE_HIDDEN_PARENTS_ID,
        name: "market_report_hidden_parents.json",
        mimeType: "application/json",
        size: "256",
        modifiedTime: "2026-11-26T05:40:00Z",
        parents: [],
        trashed: false,
      }),
      { status: 200 }
    );
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${INSIDE_FILE_ID}?fields=`)) {
    return new Response(
      JSON.stringify({
        id: INSIDE_FILE_ID,
        name: "threads_crawl_pilot_001.json",
        mimeType: "application/json",
        size: "512",
        modifiedTime: "2026-11-26T05:31:33Z",
        parents: [THREADS_ID],
        trashed: false,
      }),
      { status: 200 }
    );
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${THREADS_ID}?fields=`)) {
    return new Response(
      JSON.stringify({ id: THREADS_ID, name: "Threads", mimeType: "application/vnd.google-apps.folder", parents: [MARKET_ID], trashed: false }),
      { status: 200 }
    );
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${MARKET_ID}?fields=`)) {
    return new Response(
      JSON.stringify({ id: MARKET_ID, name: "01_Market_Data", mimeType: "application/vnd.google-apps.folder", parents: [WORKSPACE_ID], trashed: false }),
      { status: 200 }
    );
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${WORKSPACE_ID}?fields=`)) {
    return new Response(
      JSON.stringify({ id: WORKSPACE_ID, name: "AI_WORKSPACE", mimeType: "application/vnd.google-apps.folder", parents: [], trashed: false }),
      { status: 200 }
    );
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${OUTSIDE_FILE_ID}?fields=`)) {
    return new Response(
      JSON.stringify({
        id: OUTSIDE_FILE_ID,
        name: "secret.json",
        mimeType: "application/json",
        size: "120",
        parents: ["1UnrelatedRootFolderXYZ"],
        trashed: false,
      }),
      { status: 200 }
    );
  }
  if (sUrl.startsWith("https://www.googleapis.com/drive/v3/files/1UnrelatedRootFolderXYZ?fields=")) {
    return new Response(JSON.stringify({ id: "1UnrelatedRootFolderXYZ", parents: [], trashed: false }), { status: 200 });
  }
  if (sUrl.startsWith("https://api.telegram.org/")) {
    telegramCalls.push({ url: sUrl, body: init.body });
    return new Response(JSON.stringify({ ok: true, result: { message_id: 99 } }), { status: 200 });
  }
  return new Response("Not mocked", { status: 500 });
};

const env = {
  DB: new MockD1Database(),
  GOOGLE_SERVICE_ACCOUNT_JSON: JSON.stringify({ client_email: "sa@test.iam.gserviceaccount.com", private_key: privateKey }),
  GOOGLE_DRIVE_FOLDER_ID: WORKSPACE_ID,
  TELEGRAM_BOT_TOKEN_VIDEO: "123:tg-bot-token",
  TELEGRAM_OWNER_ID: "999001",
  TELEGRAM_WEBHOOK_SECRET: "tg-webhook-secret",
  APP_VIDEO_API_TOKEN: "app-video-bearer-secret",
};

// 1. V1 root health preserved
const rootRes = await worker.fetch(new Request("https://example.workers.dev/"), env);
assert.equal(rootRes.status, 200);
assert.equal(await rootRes.text(), "App Video Scheduler: ONLINE");

// 2. Auth guards fail closed
const unauthRes = await worker.fetch(new Request("https://example.workers.dev/api/app/health"), env);
assert.equal(unauthRes.status, 401);

const badAuthRes = await worker.fetch(
  new Request("https://example.workers.dev/api/app/health", { headers: { Authorization: "Bearer wrong" } }),
  env
);
assert.equal(badAuthRes.status, 401);

// 3. Authenticated health & scoped Drive listing
const authHeaders = { Authorization: "Bearer app-video-bearer-secret", "X-App-Client-Id": "desktop-1" };
const healthRes = await worker.fetch(new Request("https://example.workers.dev/api/app/health", { headers: authHeaders }), env);
assert.equal(healthRes.status, 200);
const healthJson = await healthRes.json();
assert.equal(healthJson.ok, true);
assert.equal(healthJson.market_data_folder_id, MARKET_ID);

const listRes = await worker.fetch(new Request("https://example.workers.dev/api/app/market/files", { headers: authHeaders }), env);
assert.equal(listRes.status, 200);
const listJson = await listRes.json();
assert.equal(listJson.files.length, 2);
assert.equal(listJson.files[0].platform_hint, "threads");

// 4. Scope protection rejects files outside 01_Market_Data, allows inside files even when parents[] omitted
const outsideRes = await worker.fetch(
  new Request(`https://example.workers.dev/api/app/market/files/${OUTSIDE_FILE_ID}`, { headers: authHeaders }),
  env
);
assert.equal(outsideRes.status, 403);

const insideRes = await worker.fetch(
  new Request(`https://example.workers.dev/api/app/market/files/${INSIDE_FILE_ID}`, { headers: authHeaders }),
  env
);
assert.equal(insideRes.status, 200);

const hiddenParentsInsideRes = await worker.fetch(
  new Request(`https://example.workers.dev/api/app/market/files/${INSIDE_HIDDEN_PARENTS_ID}`, { headers: authHeaders }),
  env
);
assert.equal(hiddenParentsInsideRes.status, 200);

// 5. Telegram inline command -> D1 queue -> App_Video claim & complete -> Telegram notified once
const tgReq = new Request("https://example.workers.dev/telegram", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret",
  },
  body: JSON.stringify({
    update_id: 5001,
    callback_query: {
      id: "cq_1",
      from: { id: 999001 },
      message: { message_id: 12, chat: { id: 999001, type: "private" } },
      data: "cmd:sync_now",
    },
  }),
});
const tgRes = await worker.fetch(tgReq, env);
assert.equal(tgRes.status, 200);
assert.equal(env.DB.commands.length, 1);
assert.equal(env.DB.commands[0].status, "queued");

// App_Video claims & completes
const claimRes = await worker.fetch(
  new Request("https://example.workers.dev/api/app/commands/claim", {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ lease_seconds: 120 }),
  }),
  env
);
const claimJson = await claimRes.json();
assert.equal(claimJson.command.command_type, "sync_now");

const completeRes = await worker.fetch(
  new Request(`https://example.workers.dev/api/app/commands/${claimJson.command.id}/complete`, {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({
      status: "completed",
      result: { files_checked: 1, files_downloaded: 1, records_inserted: 50, records_deduped: 0, total_items: 50 },
    }),
  }),
  env
);
assert.equal((await completeRes.json()).status, "completed");
assert.ok(telegramCalls.some((c) => (c.url.includes("/editMessageText") || c.url.includes("/sendMessage")) && String(c.body).includes("APP_VIDEO ĐÃ ĐỒNG BỘ XONG")));

// 6. Removed Telegram Drive Explorer / JSON Send rejects cleanly + Crawl Center (`crawl:go:all:100`, dedup, atomic claim, cancel)
const legacyDrvSendReq = new Request("https://example.workers.dev/telegram", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
  body: JSON.stringify({
    update_id: 5002,
    callback_query: {
      id: "cq_2",
      from: { id: 999001 },
      message: { message_id: 13, chat: { id: 999001, type: "private" } },
      data: `drv:send:${INSIDE_FILE_ID}`,
    },
  }),
});
assert.equal((await worker.fetch(legacyDrvSendReq, env)).status, 200);
assert.equal(telegramCalls.some((c) => c.url.includes("/sendDocument")), false);
assert.ok(telegramCalls.some((c) => String(c.body).includes("Drive Explorer và gửi file JSON đã được bỏ")));

const crawlQueueReq = new Request("https://example.workers.dev/telegram", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
  body: JSON.stringify({
    update_id: 5003,
    callback_query: {
      id: "cq_3",
      from: { id: 999001 },
      message: { message_id: 14, chat: { id: 999001, type: "private" } },
      data: "crawl:go:all:100",
    },
  }),
});
assert.equal((await worker.fetch(crawlQueueReq, env)).status, 200);
assert.equal(env.DB.crawlRequests.length, 1);
assert.equal(env.DB.crawlRequests[0].platform, "all");
assert.equal(env.DB.crawlRequests[0].requested_count, 100);
assert.equal(env.DB.crawlRequests[0].status, "queued");
assert.equal(env.DB.crawlRequests[0].poll_profile, "production_6h");

// Duplicate request for same active (all, 100) deduplicates cleanly
const dupCrawlRes = await worker.fetch(
  new Request("https://example.workers.dev/api/app/crawl-requests", {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ platform: "all", requested_count: 100, keywords: "" }),
  }),
  env
);
assert.equal((await dupCrawlRes.json()).deduplicated, true);
assert.equal(env.DB.crawlRequests.length, 1);

// Atomic claim by Muse + second claim returns null
const claimCrawl1 = await worker.fetch(
  new Request("https://example.workers.dev/api/app/crawl-requests/claim", {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ worker_id: "muse-oauth", lease_seconds: 1800 }),
  }),
  env
);
const cc1 = await claimCrawl1.json();
assert.equal(cc1.request.status, "running");
assert.equal(cc1.request.claimed_by, "muse-oauth");

const claimCrawl2 = await worker.fetch(
  new Request("https://example.workers.dev/api/app/crawl-requests/claim", {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ worker_id: "muse-oauth" }),
  }),
  env
);
assert.equal((await claimCrawl2.json()).request, null);

// Cannot cancel once running (409), CAN cancel queued request
const cancelRunning = await worker.fetch(
  new Request(`https://example.workers.dev/api/app/crawl-requests/${cc1.request.id}/cancel`, {
    method: "POST",
    headers: authHeaders,
  }),
  env
);
assert.equal(cancelRunning.status, 409);

const createSecondCrawl = await worker.fetch(
  new Request("https://example.workers.dev/api/app/crawl-requests", {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ platform: "shopee", requested_count: 20, keywords: "tai nghe", poll_profile: "test_5m" }),
  }),
  env
);
const cr2 = await createSecondCrawl.json();
assert.equal(cr2.request.poll_profile, "test_5m");
const cancelQueued = await worker.fetch(
  new Request(`https://example.workers.dev/api/app/crawl-requests/${cr2.request.id}/cancel`, {
    method: "POST",
    headers: authHeaders,
  }),
  env
);
assert.equal(cancelQueued.status, 200);
assert.equal((await cancelQueued.json()).status, "cancelled");

// 7. Heartbeat (with 5-section report) + Telegram Report (`rpt:summary`, no JSON send / Drive buttons) + Auto Schedule + Settings + System Status
const hbRes = await worker.fetch(
  new Request("https://example.workers.dev/api/app/heartbeat", {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({
      app_status: "online",
      sync_status: "ok",
      last_sync_at: new Date().toISOString(),
      market_counts: { total_items: 50, by_platform: { threads: 50, tiktok: 0, shopee: 0 } },
      latest_report: {
        source_label: "App_Video Normalized",
        trending_highlights: [{ title: "Voucher Shopee 30%" }],
        consumer_needs_problems: [{ title: "Phàn nàn giao hàng chậm" }],
        affiliate_opportunities: [
          { product: "TikTok" },
          { product: "Beacons.ai" },
          { product: "Son dưỡng", evidence_urls: ["https://www.threads.com/@u/post/1"] }
        ],
        content_suggestions: { text_posts: ["So sánh voucher"], short_videos: ["Top 3 deal hot"] },
        data_quality: { total_items: 50, with_purchase_intent: 50 }
      },
      sync_interval_seconds: 300,
    }),
  }),
  env
);
assert.equal(hbRes.status, 200);

const mktReportReq = new Request("https://example.workers.dev/telegram", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
  body: JSON.stringify({
    update_id: 5004,
    callback_query: {
      id: "cq_4",
      from: { id: 999001 },
      message: { message_id: 15, chat: { id: 999001, type: "private" } },
      data: "rpt:summary",
    },
  }),
});
assert.equal((await worker.fetch(mktReportReq, env)).status, 200);
assert.ok(telegramCalls.some((c) => (c.url.includes("/editMessageText") || c.url.includes("/sendMessage")) && String(c.body).includes("Voucher Shopee 30%")));

// Report view MUST NOT expose `rpt:send` or `drv:` buttons
const rptMarkupCall = telegramCalls.slice().reverse().find((c) => String(c.body).includes("Voucher Shopee 30%"));
assert.ok(rptMarkupCall);
const rptKbCbs = JSON.parse(rptMarkupCall.body).reply_markup.inline_keyboard.flat().map((b) => b.callback_data || "");
assert.ok(rptKbCbs.includes("rpt:full"));
assert.ok(!rptKbCbs.includes("rpt:send"));
assert.ok(!rptKbCbs.some((cb) => cb.startsWith("drv:")));

// Auto Schedule (`sched:view`), Settings (`cfg:menu` -> includes manual sync), System Status (`sys:status`)
const schedViewRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 5005,
      callback_query: {
        id: "cq_5",
        from: { id: 999001 },
        message: { message_id: 16, chat: { id: 999001, type: "private" } },
        data: "sched:view",
      },
    }),
  }),
  env
);
assert.equal(schedViewRes.status, 200);
assert.ok(telegramCalls.some((c) => String(c.body).includes("Chưa kích hoạt / Chưa xác nhận lịch tự động phía Muse")));

const cfgMenuRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 5006,
      callback_query: {
        id: "cq_6",
        from: { id: 999001 },
        message: { message_id: 17, chat: { id: 999001, type: "private" } },
        data: "cfg:menu",
      },
    }),
  }),
  env
);
assert.equal(cfgMenuRes.status, 200);
const cfgCall = telegramCalls.slice().reverse().find((c) => String(c.body).includes("CÀI ĐẶT HỆ THỐNG"));
assert.ok(cfgCall);
assert.ok(JSON.parse(cfgCall.body).reply_markup.inline_keyboard.flat().some((b) => b.callback_data === "cmd:sync_now" && b.text.includes("Đồng bộ thủ công")));

const sysStatusRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 5012,
      callback_query: {
        id: "cq_12",
        from: { id: 999001 },
        message: { message_id: 18, chat: { id: 999001, type: "private" } },
        data: "sys:status",
      },
    }),
  }),
  env
);
assert.equal(sysStatusRes.status, 200);
assert.ok(telegramCalls.some((c) => String(c.body).includes("Drive Connector Backend: ✅ Connected")));

// Ensure fallback NEVER leaks platform tokens ("TikTok", "Beacons.ai") as products
const mktSummaryCall = telegramCalls.find((c) => String(c.body).includes("Voucher Shopee 30%"));
assert.ok(mktSummaryCall);
assert.ok(!String(mktSummaryCall.body).includes("Beacons.ai"));

// 8. V2 Upgrade Checks:
//    a) Honest schedule / pickup time when Muse has never polled
assert.equal(env.DB.crawlRequests[0].expected_pickup_at, null);
const tgToggleReq = new Request("https://example.workers.dev/telegram", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
  body: JSON.stringify({
    update_id: 5007,
    callback_query: {
      id: "cq_7",
      from: { id: 999001 },
      message: { message_id: 14, chat: { id: 999001, type: "private" } },
      data: "crawl:toggle_profile",
    },
  }),
});
assert.equal((await worker.fetch(tgToggleReq, env)).status, 200);
assert.equal(env.DB.runtimeState.get("muse_schedule_confirmed"), "0");

//    b) Single-message /start uses ONLY Inline Keyboard (zero Reply Keyboard) with EXACT 6 simplified buttons
const tgCallsBeforeStart = telegramCalls.length;
const startRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 5008,
      message: { message_id: 20, from: { id: 999001 }, chat: { id: 999001, type: "private" }, text: "/start" },
    }),
  }),
  env
);
assert.equal(startRes.status, 200);
const startMsgs = telegramCalls.slice(tgCallsBeforeStart).filter((c) => c.url.includes("/sendMessage") || c.url.includes("/editMessageText"));
assert.equal(startMsgs.length, 1);
const startPayload = JSON.parse(startMsgs[0].body);
assert.ok(Array.isArray(startPayload.reply_markup?.inline_keyboard));
assert.equal(startPayload.reply_markup?.keyboard, undefined);
const btnTexts = startPayload.reply_markup.inline_keyboard.flat().map((b) => b.text);
assert.deepEqual(btnTexts, [
  "🕷️ Crawl dữ liệu",
  "📊 Phân tích thị trường",
  "⏱️ Lịch tự động",
  "📋 Lịch sử tác vụ",
  "📡 Trạng thái hệ thống",
  "⚙️ Cài đặt",
]);

//    c) Drive Reports priority works EVEN WHEN App_Video desktop is OFFLINE, and supports rpt:full
enableReportsFolder = true;
env.DB.heartbeats.clear(); // Simulate App_Video OFFLINE

const offlineRptRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 5009,
      callback_query: {
        id: "cq_8",
        from: { id: 999001 },
        message: { message_id: 21, chat: { id: 999001, type: "private" } },
        data: "rpt:summary",
      },
    }),
  }),
  env
);
assert.equal(offlineRptRes.status, 200);
assert.ok(telegramCalls.some((c) => String(c.body).includes("OFFLINE (đọc trực tiếp Drive)") && String(c.body).includes("BẢN CORRECTED V2")));

const fullRptRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 5010,
      callback_query: {
        id: "cq_9",
        from: { id: 999001 },
        message: { message_id: 21, chat: { id: 999001, type: "private" } },
        data: "rpt:full",
      },
    }),
  }),
  env
);
assert.equal(fullRptRes.status, 200);
assert.ok(telegramCalls.some((c) => String(c.body).includes("[THREADS] 9/10 usable")));

assert.equal(telegramCalls.some((c) => c.url.includes("/sendDocument")), false);

//    d) Scheduled notification deduplicates Muse Reports via crawl_notifications
const callsBeforeCron = telegramCalls.length;
await worker.scheduled({}, env, {});
assert.equal(env.DB.notifications.length, 1);
assert.equal(env.DB.notifications[0].run_id, "e2e-crawl-20261126-1559_corrected");
assert.equal(env.DB.notifications[0].status, "sent");
const callsAfterFirstCron = telegramCalls.length;
assert.ok(callsAfterFirstCron > callsBeforeCron);

await worker.scheduled({}, env, {});
assert.equal(telegramCalls.length, callsAfterFirstCron);

console.log("WORKER_V2_CONTRACT_TESTS_OK");
