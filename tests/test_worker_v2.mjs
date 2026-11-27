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
    this.postRegistry = new Map();
    this.onBatchStep = null;
  }
  async batch(stmts) {
    const snapCommands = this.commands.map((x) => ({ ...x }));
    const snapRequests = this.crawlRequests.map((x) => ({ ...x }));
    const snapNotifications = this.notifications.map((x) => ({ ...x }));
    const snapState = new Map(this.runtimeState);
    try {
      const results = [];
      for (let i = 0; i < stmts.length; i++) {
        if (typeof this.onBatchStep === "function") {
          await this.onBatchStep(i, stmts[i]);
        }
        results.push(await stmts[i].run());
      }
      return results;
    } catch (err) {
      this.commands = snapCommands;
      this.crawlRequests = snapRequests;
      this.notifications = snapNotifications;
      this.runtimeState = snapState;
      throw err;
    }
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
        if (
          norm.startsWith("SELECT run_id, status, created_at") ||
          norm.startsWith("SELECT status, created_at FROM crawl_notifications WHERE run_id = ?") ||
          norm.startsWith("SELECT status FROM crawl_notifications WHERE run_id = ?")
        ) {
          const row = db.notifications.find((n) => n.run_id === this.args[0]);
          return row ? { ...row } : null;
        }
        if (norm.startsWith("SELECT COUNT(*) AS cnt FROM crawl_notifications")) {
          return { cnt: db.notifications.filter((n) => n.status === "delivery_uncertain").length };
        }
        if (norm.startsWith("SELECT status, notified_at FROM crawl_requests WHERE id = ?")) {
          const row = db.crawlRequests.find((c) => c.id === this.args[0]);
          return row ? { status: row.status, notified_at: row.notified_at } : null;
        }
        if (norm.startsWith("SELECT value FROM worker_runtime_state WHERE key = ?")) {
          const v = db.runtimeState.get(this.args[0]);
          return v !== undefined ? { value: v } : null;
        }
        if (norm.startsWith("SELECT * FROM app_heartbeats")) {
          const all = Array.from(db.heartbeats.values()).sort((a, b) => b.last_seen_at.localeCompare(a.last_seen_at));
          return all[0] ? { ...all[0] } : null;
        }
        if (norm.startsWith("SELECT id, status FROM app_commands WHERE dedup_token = ?")) {
          const row = db.commands.find((c) => c.dedup_token === this.args[0]);
          return row ? { ...row } : null;
        }
        if (norm.includes("FROM app_commands WHERE status = 'queued'")) {
          const row = db.commands.filter((c) => c.status === "queued").sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
          return row ? { ...row } : null;
        }
        if (norm === "SELECT * FROM app_commands WHERE id = ?") {
          const row = db.commands.find((c) => c.id === this.args[0]);
          return row ? { ...row } : null;
        }
        if (norm.startsWith("SELECT * FROM crawl_requests WHERE dedup_token = ?")) {
          const row = db.crawlRequests.find((c) => c.dedup_token === this.args[0]);
          return row ? { ...row } : null;
        }
        if (norm.includes("FROM crawl_requests WHERE platform = ? AND lower(trim(keywords)) = ?")) {
          const [plat, kw, cnt] = this.args;
          const row = db.crawlRequests.find(
            (c) => c.platform === plat && c.keywords.trim().toLowerCase() === kw && c.requested_count === cnt && ["queued", "running"].includes(c.status)
          );
          return row ? { ...row } : null;
        }
        if (norm.includes("FROM crawl_requests WHERE status = 'queued'")) {
          const row = db.crawlRequests.filter((c) => c.status === "queued").sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
          return row ? { ...row } : null;
        }
        if (norm === "SELECT * FROM crawl_requests WHERE id = ?") {
          const row = db.crawlRequests.find((c) => c.id === this.args[0]);
          return row ? { ...row } : null;
        }
        if (norm.startsWith("SELECT * FROM market_post_registry WHERE dedup_key = ?")) {
          const row = db.postRegistry.get(this.args[0]);
          return row ? { ...row } : null;
        }
        if (norm.startsWith("SELECT * FROM market_post_registry WHERE platform = ? AND canonical_url = ?")) {
          const [plat, cUrl] = this.args;
          for (const row of db.postRegistry.values()) {
            if (row.platform === plat && row.canonical_url === cUrl) return { ...row };
          }
          return null;
        }
        return null;
      },
      async all() {
        if (norm.includes("FROM app_commands")) {
          return { results: [...db.commands].reverse().slice(0, 8) };
        }
        if (norm.includes("FROM crawl_requests WHERE status IN ('completed', 'failed') AND notified_at IS NULL")) {
          return {
            results: db.crawlRequests.filter((c) => ["completed", "failed"].includes(c.status) && !c.notified_at),
          };
        }
        if (norm.includes("FROM crawl_notifications WHERE status IN ('delivery_uncertain', 'sending')")) {
          return {
            results: db.notifications
              .filter((n) => n.status === "delivery_uncertain" || n.status === "sending")
              .slice()
              .reverse(),
          };
        }
        if (norm.includes("FROM crawl_notifications WHERE status = 'delivery_failed'")) {
          const [nowIso, limit] = this.args;
          return {
            results: db.notifications
              .filter(
                (n) =>
                  n.status === "delivery_failed" &&
                  Number(n.retryable || 0) === 1 &&
                  Number(n.retry_count || 0) < Number(n.max_retries || 5) &&
                  (!n.next_retry_at || String(n.next_retry_at) <= String(nowIso))
              )
              .slice()
              .sort((a, b) => String(a.next_retry_at || a.created_at || "").localeCompare(String(b.next_retry_at || b.created_at || "")))
              .slice(0, Number(limit || 10)),
          };
        }
        if (norm.includes("FROM crawl_notifications WHERE status = 'delivery_uncertain'")) {
          return {
            results: db.notifications.filter((n) => n.status === "delivery_uncertain"),
          };
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
          const [status, result_json, error_message, updated_at, id, claimed_by, attempts, nowIso] = this.args;
          const row = db.commands.find(
            (c) =>
              c.id === id &&
              c.status === "running" &&
              c.claimed_by === claimed_by &&
              c.attempts === attempts &&
              (!nowIso || !c.lease_expires_at || c.lease_expires_at >= nowIso)
          );
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
        if (norm.startsWith("INSERT INTO market_post_registry")) {
          const [dedup_key, platform, post_id, canonical_url, first_run_id, last_run_id, first_seen_at, last_seen_at, latest_metrics_json] = this.args;
          const prev = db.postRegistry.get(dedup_key);
          if (!prev) {
            db.postRegistry.set(dedup_key, {
              dedup_key,
              platform,
              post_id,
              canonical_url,
              first_run_id,
              last_run_id,
              first_seen_at,
              last_seen_at,
              snapshot_count: 1,
              latest_metrics_json,
            });
          } else {
            prev.last_run_id = last_run_id || prev.last_run_id;
            prev.last_seen_at = last_seen_at;
            prev.snapshot_count = Number(prev.snapshot_count || 1) + 1;
          }
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE market_post_registry SET")) {
          const [cUrl, postId, runId, lastSeenAt, nextSnap, latestMetricsJson, dedupKey] = this.args;
          const row = db.postRegistry.get(dedupKey);
          if (!row) return { meta: { changes: 0 } };
          if (cUrl) row.canonical_url = cUrl;
          if (postId) row.post_id = postId;
          if (runId) row.last_run_id = runId;
          row.last_seen_at = lastSeenAt;
          row.snapshot_count = nextSnap;
          row.latest_metrics_json = latestMetricsJson;
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
          const [status, result_summary_json, report_file_id, error_message, updated_at, id, claimed_by, attempts, nowIso] = this.args;
          const row = db.crawlRequests.find(
            (c) =>
              c.id === id &&
              c.status === "running" &&
              c.claimed_by === claimed_by &&
              c.attempts === attempts &&
              (!nowIso || !c.lease_expires_at || c.lease_expires_at >= nowIso)
          );
          if (!row) return { meta: { changes: 0 } };
          row.status = status;
          row.result_summary_json = result_summary_json;
          row.report_file_id = report_file_id;
          row.error_message = error_message;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET notified_at = ? WHERE id = ? AND notified_at IS NULL AND EXISTS")) {
          const [notified_at, id, run_id, matchVal] = this.args;
          const reqRow = db.crawlRequests.find((c) => c.id === id && !c.notified_at);
          if (!reqRow) return { meta: { changes: 0 } };
          const hasNotif = norm.includes("retry_payload_json = ?")
            ? db.notifications.some((n) => n.run_id === run_id && n.status === "sending" && n.retry_payload_json === matchVal)
            : db.notifications.some((n) => n.run_id === run_id && n.status === "sending" && n.created_at === matchVal);
          if (!hasNotif) return { meta: { changes: 0 } };
          reqRow.notified_at = notified_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET notified_at = ? WHERE id = ? AND notified_at IS NULL")) {
          const [notified_at, id] = this.args;
          const row = db.crawlRequests.find((c) => c.id === id && !c.notified_at);
          if (!row) return { meta: { changes: 0 } };
          row.notified_at = notified_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET notified_at = ? WHERE id = ?")) {
          const [notified_at, id] = this.args;
          const row = db.crawlRequests.find((c) => c.id === id);
          if (row) row.notified_at = notified_at;
          return { meta: { changes: row ? 1 : 0 } };
        }
        if (norm.startsWith("INSERT OR IGNORE INTO crawl_notifications")) {
          const run_id = this.args[0];
          const hasWhereExists = norm.includes("WHERE EXISTS (SELECT 1 FROM crawl_requests WHERE id = ? AND notified_at IS NULL)");
          if (hasWhereExists) {
            const reqIdArg = this.args[this.args.length - 1];
            const eligible = db.crawlRequests.some((c) => c.id === reqIdArg && !c.notified_at);
            if (!eligible) return { meta: { changes: 0 } };
          }
          if (db.notifications.some((n) => n.run_id === run_id)) {
            return { meta: { changes: 0 } };
          }
          if (this.args.length >= 6 || (!hasWhereExists && this.args.length >= 5)) {
            const [, record_count, created_at, max_retries, retry_payload_json] = this.args;
            db.notifications.push({
              run_id,
              record_count,
              status: "sending",
              created_at,
              sent_at: null,
              retry_count: 0,
              max_retries,
              next_retry_at: null,
              retryable: 0,
              last_error: null,
              retry_payload_json,
            });
          } else {
            const [, record_count, created_at] = this.args;
            db.notifications.push({
              run_id,
              record_count,
              status: "sending",
              created_at,
              sent_at: null,
              retry_count: 0,
              max_retries: 5,
              next_retry_at: null,
              retryable: 0,
              last_error: null,
              retry_payload_json: null,
            });
          }
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("DELETE FROM crawl_notifications WHERE run_id = ? AND status = 'sending'")) {
          const [run_id] = this.args;
          const before = db.notifications.length;
          db.notifications = db.notifications.filter((n) => !(n.run_id === run_id && n.status === "sending"));
          return { meta: { changes: before - db.notifications.length } };
        }
        if (norm.startsWith("UPDATE crawl_notifications SET status = 'sending'")) {
          const [reclaimNowIso, retryPayloadJson, notifKey, cmpIso] = this.args;
          const row = db.notifications.find(
            (n) =>
              n.run_id === notifKey &&
              n.status === "delivery_failed" &&
              Number(n.retryable || 0) === 1 &&
              Number(n.retry_count || 0) < Number(n.max_retries || 5) &&
              (!n.next_retry_at || String(n.next_retry_at) <= String(cmpIso))
          );
          if (!row) return { meta: { changes: 0 } };
          row.status = "sending";
          row.created_at = reclaimNowIso;
          row.retry_count = Number(row.retry_count || 0) + 1;
          row.next_retry_at = null;
          if (retryPayloadJson !== null && retryPayloadJson !== undefined) {
            row.retry_payload_json = retryPayloadJson;
          }
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_notifications SET status = ?, sent_at = COALESCE(?, sent_at)")) {
          const [status, sentAt, retryCount, nextRetryAt, retryable, lastError, retryPayloadJson, notifKey] = this.args;
          const row = db.notifications.find((n) => n.run_id === notifKey);
          if (!row) return { meta: { changes: 0 } };
          row.status = status;
          if (sentAt !== null && sentAt !== undefined) row.sent_at = sentAt;
          if (retryCount !== null && retryCount !== undefined) row.retry_count = retryCount;
          row.next_retry_at = nextRetryAt;
          row.retryable = Number(retryable ? 1 : 0);
          row.last_error = lastError;
          if (retryPayloadJson !== null && retryPayloadJson !== undefined) {
            row.retry_payload_json = retryPayloadJson;
          }
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
        if (norm.startsWith("UPDATE crawl_notifications SET status = 'delivery_uncertain' WHERE status = 'sending'")) {
          const [cutoffIso] = this.args;
          let changes = 0;
          for (const n of db.notifications) {
            if (n.status === "sending" && (!cutoffIso || n.created_at <= cutoffIso)) {
              n.status = "delivery_uncertain";
              changes++;
            }
          }
          return { meta: { changes } };
        }
        if (norm.startsWith("DELETE FROM crawl_notifications WHERE status = 'sending'")) {
          const [cutoffIso] = this.args;
          const before = db.notifications.length;
          db.notifications = db.notifications.filter(
            (n) => !(n.status === "sending" && (!cutoffIso || n.created_at <= cutoffIso))
          );
          return { meta: { changes: before - db.notifications.length } };
        }
        if (norm.startsWith("UPDATE crawl_notifications SET status = 'delivery_uncertain'")) {
          const [run_id] = this.args;
          const row = db.notifications.find((n) => n.run_id === run_id);
          if (row) row.status = "delivery_uncertain";
          return { meta: { changes: row ? 1 : 0 } };
        }
        if (norm.startsWith("UPDATE crawl_notifications SET status = 'delivery_failed'")) {
          const [run_id] = this.args;
          const row = db.notifications.find((n) => n.run_id === run_id);
          if (row) row.status = "delivery_failed";
          return { meta: { changes: row ? 1 : 0 } };
        }
        if (norm.startsWith("UPDATE crawl_notifications SET status = ? WHERE run_id = ?")) {
          const [nextStatus, run_id] = this.args;
          const row = db.notifications.find((n) => n.run_id === run_id);
          if (row) row.status = nextStatus;
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
      attempt: claimJson.command.attempts,
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
assert.ok(telegramCalls.some((c) => String(c.body).includes("Drive Explorer và gửi file JSON đã được gỡ bỏ")));

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

//    b) Single-message /start uses Reply Keyboard with EXACT 6 Main Menu buttons (zero Inline Main Menu)
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
assert.ok(Array.isArray(startPayload.reply_markup?.keyboard));
assert.equal(startPayload.reply_markup?.inline_keyboard, undefined);
assert.equal(startPayload.reply_markup?.resize_keyboard, true);
assert.equal(startPayload.reply_markup?.is_persistent, true);
const btnTexts = startPayload.reply_markup.keyboard.flat().map((b) => b.text);
assert.deepEqual(btnTexts, [
  "🕷️ Crawl dữ liệu",
  "📊 Phân tích thị trường",
  "⏰ Lịch tự động",
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

// 9. GPT Review Regressions (RED before fix):
//    Issue #1: Newer run report beats older corrected report; corrected only wins within same run
const EXTRA_REPORTS = [
  {
    id: "1Run1OldCorrected",
    name: "market_report_run1_corrected_v2.json",
    mimeType: "application/json",
    size: "1200",
    modifiedTime: "2026-11-26T09:05:00Z",
    body: { run_id: "run-1-old", technical_status: "completed", data_quality_status: "dat", totals: { records: 5, business_usable_records: 5 }, telegram_summary: "OLD CORRECTED RUN 1" },
  },
  {
    id: "1Run2NewOriginal",
    name: "market_report_run2_20261127.json",
    mimeType: "application/json",
    size: "1200",
    modifiedTime: "2026-11-27T09:05:00Z",
    body: { run_id: "run-2-new", technical_status: "completed", data_quality_status: "dat", totals: { records: 12, business_usable_records: 10 }, telegram_summary: "NEWEST RUN 2 REPORT" },
  },
  {
    id: "1Run3FailedReport",
    name: "market_report_run3_failed.json",
    mimeType: "application/json",
    size: "900",
    modifiedTime: "2026-11-27T10:00:00Z",
    body: { run_id: "run-3-failed", technical_status: "failed", error_message: "Login expired", totals: { records: 0, business_usable_records: 0 }, telegram_summary: "RUN 3 FAILED REPORT" },
  },
];
let extraReportsEnabled = false;
let simulateTelegramThrowOnce = false;
let paginationFiles = [];

const origFetch = globalThis.fetch;
globalThis.fetch = async (url, init = {}) => {
  const sUrl = String(url);
  if (extraReportsEnabled && sUrl.startsWith("https://www.googleapis.com/drive/v3/files?")) {
    const u = new URL(sUrl);
    const q = u.searchParams.get("q") || "";
    if (q.includes(`'${REPORTS_ID}' in parents`)) {
      return new Response(JSON.stringify({ files: EXTRA_REPORTS }), { status: 200 });
    }
    if (paginationFiles.length > 0 && q.includes(`'${THREADS_ID}' in parents`)) {
      const pt = u.searchParams.get("pageToken");
      const startIdx = pt ? Number(pt) : 0;
      const slice = paginationFiles.slice(startIdx, startIdx + 100);
      const nextPageToken = startIdx + 100 < paginationFiles.length ? String(startIdx + 100) : undefined;
      return new Response(JSON.stringify({ files: slice, ...(nextPageToken ? { nextPageToken } : {}) }), { status: 200 });
    }
  }
  if (extraReportsEnabled) {
    for (const rptFile of EXTRA_REPORTS) {
      if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${rptFile.id}?alt=media`)) {
        return new Response(JSON.stringify(rptFile.body), { status: 200 });
      }
    }
  }
  if (simulateTelegramThrowOnce && sUrl.startsWith("https://api.telegram.org/")) {
    simulateTelegramThrowOnce = false;
    throw new Error("Simulated Telegram transport timeout");
  }
  return origFetch(url, init);
};

extraReportsEnabled = true;
// Exclude run3 failed temporarily or check newest failed/completed priority: first check Run2 vs old corrected Run1
const run2OnlyCheck = EXTRA_REPORTS.pop(); // temporarily test run2 (2026-11-27) vs run1_corrected (2026-11-26)
const rptNewWinsRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 5020,
      callback_query: { id: "cq_20", from: { id: 999001 }, message: { message_id: 30, chat: { id: 999001, type: "private" } }, data: "rpt:summary" },
    }),
  }),
  env
);
assert.equal(rptNewWinsRes.status, 200);
const latestRptMsg = telegramCalls[telegramCalls.length - 1];
assert.ok(String(latestRptMsg.body).includes("NEWEST RUN 2 REPORT"));
EXTRA_REPORTS.push(run2OnlyCheck);

//    Issue #2: Cron notifies ALL unnotified runs + failed Drive report + direct D1 crawl_requests + delivery_uncertain safety
env.DB.crawlRequests.push({
  id: "crw_direct_d1_fail",
  platform: "shopee",
  keywords: "flash sale",
  requested_count: 10,
  status: "failed",
  claimed_by: "muse-oauth",
  attempts: 1,
  max_attempts: 3,
  requested_by_chat_id: "999001",
  requested_by_source: "telegram",
  error_message: "Shopee session expired",
  notified_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
});
simulateTelegramThrowOnce = true; // Next notification hits transport error -> delivery_uncertain
await worker.scheduled({}, env, {});
assert.ok(env.DB.notifications.some((n) => n.status === "delivery_uncertain"));
assert.ok(env.DB.notifications.some((n) => n.run_id === "run-2-new" && n.status === "sent"));
assert.ok(env.DB.notifications.some((n) => n.run_id === "run-3-failed" && n.status === "sent"));
assert.ok(env.DB.notifications.some((n) => String(n.run_id).includes("crw_direct_d1_fail") && n.status === "sent"));
const directD1Row = env.DB.crawlRequests.find((c) => c.id === "crw_direct_d1_fail");
assert.ok(directD1Row.notified_at);

// Next Cron MUST NOT double-send delivery_uncertain blindly
const callsAfterMultiCron = telegramCalls.length;
await worker.scheduled({}, env, {});
assert.equal(telegramCalls.length, callsAfterMultiCron);

//    Issue #4: Complete endpoints verify ownership, attempt & lease
env.DB.commands.push({
  id: "cmd_owner_guard",
  command_type: "sync_now",
  payload_json: "{}",
  status: "running",
  claimed_by: "desktop-2",
  claimed_at: new Date().toISOString(),
  lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
  attempts: 2,
  max_attempts: 3,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
});
// Stale attempt 1 from desktop-1 -> 409 Conflict
const wrongOwnerCmd = await worker.fetch(
  new Request("https://example.workers.dev/api/app/commands/cmd_owner_guard/complete", {
    method: "POST",
    headers: { Authorization: "Bearer app-video-bearer-secret", "X-App-Client-Id": "desktop-1", "Content-Type": "application/json" },
    body: JSON.stringify({ status: "completed", attempt: 1, result: { total_items: 999 } }),
  }),
  env
);
assert.equal(wrongOwnerCmd.status, 409);
// Stale attempt 1 from same desktop-2 -> 409 Conflict
const wrongAttemptCmd = await worker.fetch(
  new Request("https://example.workers.dev/api/app/commands/cmd_owner_guard/complete", {
    method: "POST",
    headers: { Authorization: "Bearer app-video-bearer-secret", "X-App-Client-Id": "desktop-2", "Content-Type": "application/json" },
    body: JSON.stringify({ status: "completed", attempt: 1, result: { total_items: 999 } }),
  }),
  env
);
assert.equal(wrongAttemptCmd.status, 409);
assert.equal(env.DB.commands.find((c) => c.id === "cmd_owner_guard").status, "running");

// Crawl complete ownership & lease guard
env.DB.crawlRequests.push({
  id: "crw_owner_guard",
  platform: "threads",
  keywords: "",
  requested_count: 20,
  status: "running",
  claimed_by: "muse-worker-2",
  claimed_at: new Date().toISOString(),
  lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
  attempts: 2,
  max_attempts: 3,
  notified_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
});
const wrongOwnerCrawl = await worker.fetch(
  new Request("https://example.workers.dev/api/app/crawl-requests/crw_owner_guard/complete", {
    method: "POST",
    headers: { ...authHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ worker_id: "muse-worker-1", attempt: 1, status: "completed", result_summary: { record_count: 20 } }),
  }),
  env
);
assert.equal(wrongOwnerCrawl.status, 409);

//    Issue #6: Worker paginates >100 files sharing equal modifiedTime via keyset page_token
paginationFiles = Array.from({ length: 105 }, (_, idx) => ({
  id: `1PageFile_${String(idx).padStart(3, "0")}`,
  name: `threads_batch_${String(idx).padStart(3, "0")}.json`,
  mimeType: "application/json",
  size: "300",
  modifiedTime: "2026-11-26T12:00:00Z",
  md5Checksum: `md5p_${idx}`,
}));
const collectedIds = new Set();
let pageToken = "";
for (let page = 0; page < 5; page++) {
  const qs = new URLSearchParams({ limit: "50", ...(pageToken ? { page_token: pageToken } : {}) });
  const pgRes = await worker.fetch(
    new Request(`https://example.workers.dev/api/app/market/files?${qs}`, { headers: authHeaders }),
    env
  );
  assert.equal(pgRes.status, 200);
  const pgJson = await pgRes.json();
  for (const f of pgJson.files) collectedIds.add(f.file_id);
  if (!pgJson.has_more) break;
  pageToken = pgJson.next_page_token || "";
  assert.ok(pageToken);
}
for (const pf of paginationFiles) {
  assert.ok(collectedIds.has(pf.id));
}
assert.equal(collectedIds.size, 108); // 105 Threads files + 3 Reports files

// 10. Threads Trend Intelligence V3.1: trend_report_*.json, Archive/ exclusion, real Telegram summary, dedup, source_error vs no_qualifying_posts
paginationFiles = [];
extraReportsEnabled = false;

const realV3ReportJson = JSON.parse(
  fs.readFileSync(path.join(__dirname, "../tests/fixtures/market/trend_report_v3_20261126.json"), "utf8")
);
const REPORTS_ARCHIVE_ID = "1ReportsArchiveFolderId111";
const THREADS_ARCHIVE_ID = "1ThreadsArchiveFolderId222";
const ARCHIVED_RPT_ID = "1ArchivedTrendReportId333";
const ARCHIVED_THR_ID = "1ArchivedThreadsTrendId444";
const ACTIVE_V3_RPT_ID = "1ActiveV3TrendReportId555";
const DISCOVERY_DOC_ID = "1DiscoverySourcesDocId666";
const CAP_TEST_DOC_ID = "1CapabilityTestDocId777";
const ACTIVE_V3_THR_ID = "1ActiveThreadsTrendId888";

const v3FetchBase = globalThis.fetch;
let v3TestMode = true;
let v3StatusVariant = "real"; // "real" | "source_error" | "no_qualifying_posts"

globalThis.fetch = async (url, init = {}) => {
  if (!v3TestMode) return v3FetchBase(url, init);
  const sUrl = String(url);
  if (sUrl.startsWith("https://www.googleapis.com/drive/v3/files?")) {
    const u = new URL(sUrl);
    const q = u.searchParams.get("q") || "";
    if (q.includes(`'${MARKET_ID}' in parents`) && !q.includes("'Reports'")) {
      return new Response(
        JSON.stringify({
          files: [
            { id: THREADS_ID, name: "Threads", mimeType: "application/vnd.google-apps.folder" },
            { id: REPORTS_ID, name: "Reports", mimeType: "application/vnd.google-apps.folder" },
            { id: "1RootArchiveFolder999", name: "Archive", mimeType: "application/vnd.google-apps.folder" },
          ],
        }),
        { status: 200 }
      );
    }
    if (q.includes(`'${REPORTS_ID}' in parents`) && !q.includes("vnd.google-apps.document")) {
      return new Response(
        JSON.stringify({
          files: [
            { id: REPORTS_ARCHIVE_ID, name: "Archive", mimeType: "application/vnd.google-apps.folder" },
            { id: DISCOVERY_DOC_ID, name: "threads_discovery_sources_20261126.json", mimeType: "application/json", size: "500", modifiedTime: "2026-11-26T15:09:30Z", md5Checksum: "d1" },
            { id: CAP_TEST_DOC_ID, name: "threads_cli_capability_test_20261126.json", mimeType: "application/json", size: "600", modifiedTime: "2026-11-26T15:38:08Z", md5Checksum: "c1" },
            { id: ACTIVE_V3_RPT_ID, name: "trend_report_v3_20261126.json", mimeType: "application/json", size: "9749", modifiedTime: "2026-11-26T15:51:34Z", md5Checksum: "r_v3" },
          ],
        }),
        { status: 200 }
      );
    }
    if (q.includes(`'${THREADS_ID}' in parents`)) {
      return new Response(
        JSON.stringify({
          files: [
            { id: THREADS_ARCHIVE_ID, name: "Archive", mimeType: "application/vnd.google-apps.folder" },
            { id: ACTIVE_V3_THR_ID, name: "threads_trend_20261126.json", mimeType: "application/json", size: "16378", modifiedTime: "2026-11-26T15:51:33Z", md5Checksum: "t_v3" },
          ],
        }),
        { status: 200 }
      );
    }
    if (q.includes(`'${REPORTS_ARCHIVE_ID}' in parents`) || q.includes(`'${THREADS_ARCHIVE_ID}' in parents`) || q.includes("1RootArchiveFolder999")) {
      return new Response(
        JSON.stringify({
          files: [
            { id: ARCHIVED_RPT_ID, name: "trend_report_v3_archived.json", mimeType: "application/json", size: "5000", modifiedTime: "2026-11-26T23:59:59Z", md5Checksum: "arch_r" },
            { id: ARCHIVED_THR_ID, name: "threads_trend_archived.json", mimeType: "application/json", size: "5000", modifiedTime: "2026-11-26T23:59:59Z", md5Checksum: "arch_t" },
          ],
        }),
        { status: 200 }
      );
    }
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${ARCHIVED_RPT_ID}?alt=media`)) {
    return new Response(JSON.stringify({ report: "ARCHIVED MUST NOT BE READ", run_id: "arch-fail", run_status: "source_error", generated_at: "2026-11-27T00:00:00Z", telegram_summary: "ARCHIVED_BAD" }), { status: 200 });
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${DISCOVERY_DOC_ID}?alt=media`)) {
    return new Response(JSON.stringify({ report: "Research Doc", sources: [{ rank: 1, source: "threads-cli" }] }), { status: 200 });
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${CAP_TEST_DOC_ID}?alt=media`)) {
    return new Response(JSON.stringify({ report: "Capability Test", results: [], engagement_verification_map: {} }), { status: 200 });
  }
  if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${ACTIVE_V3_RPT_ID}?alt=media`)) {
    if (v3StatusVariant === "source_error") {
      return new Response(JSON.stringify({
        report: "V3.1 Source Error Report",
        run_id: "threads-trend-v3-err-1",
        run_status: "source_error",
        generated_at: "2026-11-26T23:10:00+07:00",
        error_message: "threads-cli rate limit 429",
        sections: { "1_screening": { scanned_count: 0, qualified_count: 0, rejected_count: 0 } },
        telegram_summary: "TRẠNG THÁI: source_error — Nguồn threads-cli gặp lỗi 429, chưa kết luận thị trường.",
      }), { status: 200 });
    }
    if (v3StatusVariant === "no_qualifying_posts") {
      return new Response(JSON.stringify({
        report: "V3.1 Empty Qualifying Posts Report",
        run_id: "threads-trend-v3-empty-1",
        run_status: "no_qualifying_posts",
        generated_at: "2026-11-26T23:20:00+07:00",
        sections: { "1_screening": { scanned_count: 25, qualified_count: 0, rejected_count: 25 } },
        telegram_summary: "TRẠNG THÁI: no_qualifying_posts — Đã quét 25 bài hợp lệ nhưng không có bài nào đạt chuẩn V3.1.",
      }), { status: 200 });
    }
    return new Response(JSON.stringify(realV3ReportJson), { status: 200 });
  }
  return v3FetchBase(url, init);
};

// Verify `/api/app/market/files` excludes Archive/ folders & files completely
const v3FilesRes = await worker.fetch(new Request("https://example.workers.dev/api/app/market/files", { headers: authHeaders }), env);
assert.equal(v3FilesRes.status, 200);
const v3FilesJson = await v3FilesRes.json();
const v3ListedNames = v3FilesJson.files.map((f) => f.name);
assert.ok(v3ListedNames.includes("threads_trend_20261126.json"));
assert.ok(v3ListedNames.includes("trend_report_v3_20261126.json"));
assert.ok(!v3ListedNames.includes("trend_report_v3_archived.json"));
assert.ok(!v3ListedNames.includes("threads_trend_archived.json"));

// Verify Telegram `rpt:summary` and `rpt:full` display V3.1 real report, ignore Archive/ & research docs, and preserve no-raw policy
const v3RptSummaryRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 6001,
      callback_query: { id: "cq_v3_1", from: { id: 999001 }, message: { message_id: 50, chat: { id: 999001, type: "private" } }, data: "rpt:summary" },
    }),
  }),
  env
);
assert.equal(v3RptSummaryRes.status, 200);
const v3SummaryBody = String(telegramCalls[telegramCalls.length - 1].body);
assert.ok(v3SummaryBody.includes("Vũ Trụ Say Hi"));
assert.ok(v3SummaryBody.includes("Quét 14 | Đạt chuẩn 8"));
assert.ok(v3SummaryBody.includes("Very Hot 4"));
assert.ok(!v3SummaryBody.includes("ARCHIVED_BAD"));

const v3RptFullRes = await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 6002,
      callback_query: { id: "cq_v3_2", from: { id: 999001 }, message: { message_id: 50, chat: { id: 999001, type: "private" } }, data: "rpt:full" },
    }),
  }),
  env
);
assert.equal(v3RptFullRes.status, 200);
const v3FullBody = String(telegramCalls[telegramCalls.length - 1].body);
assert.ok(v3FullBody.includes("XU HƯỚNG NỔI BẬT"));
assert.ok(v3FullBody.includes("Dr.Althea 345 Relief Cream"));
assert.ok(v3FullBody.includes("HÀNH ĐỘNG ƯU TIÊN"));

// Cron notifies V3.1 `trend_report_v3_20261126.json` once with telegram_summary, then deduplicates on second cron
const callsBeforeV3Cron = telegramCalls.length;
await worker.scheduled({}, env, {});
assert.equal(telegramCalls.length, callsBeforeV3Cron + 1);
const v3NotifBody = String(telegramCalls[telegramCalls.length - 1].body);
assert.ok(v3NotifBody.includes("BÁO CÁO XU HƯỚNG THREADS V3.1"));
assert.ok(v3NotifBody.includes("Dr.Althea 345 Relief Cream"));
assert.ok(env.DB.notifications.some((n) => n.run_id === "threads-trend-v3-20261126-2241" && n.status === "sent"));

// Re-running cron MUST NOT send duplicate Telegram notification
await worker.scheduled({}, env, {});
assert.equal(telegramCalls.length, callsBeforeV3Cron + 1);

// Verify `source_error` vs `no_qualifying_posts` distinction in Telegram notifications
v3StatusVariant = "source_error";
await worker.scheduled({}, env, {});
const errNotifBody = String(telegramCalls[telegramCalls.length - 1].body);
assert.ok(errNotifBody.includes("LỖI NGUỒN DỮ LIỆU (source_error)"));
assert.ok(errNotifBody.includes("source_error"));

v3StatusVariant = "no_qualifying_posts";
await worker.scheduled({}, env, {});
const emptyNotifBody = String(telegramCalls[telegramCalls.length - 1].body);
assert.ok(emptyNotifBody.includes("KHÔNG CÓ BÀI ĐẠT CHUẨN (no_qualifying_posts)"));
assert.ok(!emptyNotifBody.includes("LỖI NGUỒN DỮ LIỆU"));

// Verify /start still keeps exact 6 Reply Keyboard buttons and zero /sendDocument ever sent
assert.equal(telegramCalls.some((c) => c.url.includes("/sendDocument")), false);
const v3StartBefore = telegramCalls.length;
await worker.fetch(
  new Request("https://example.workers.dev/telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
    body: JSON.stringify({
      update_id: 6003,
      message: { message_id: 51, from: { id: 999001 }, chat: { id: 999001, type: "private" }, text: "/start" },
    }),
  }),
  env
);
const v3StartBtns = JSON.parse(telegramCalls[v3StartBefore].body).reply_markup.keyboard.flat().map((b) => b.text);
assert.equal(v3StartBtns.length, 6);

// 11. Second-Round GPT Review Regressions (#1-#8 merged with V3.1):
//     Issue #1: Inter-run sorting uses crawl execution time ONLY; modifiedTime only breaks ties within same run
{
  const env11 = {
    ...env,
    DB: new MockD1Database(),
    NOTIFY_BATCH_SIZE: "10",
    TELEGRAM_RETRY_BASE_MS: "10",
    TELEGRAM_RETRY_MAX_WAIT_MS: "40",
  };
  const rptOldTouched = {
    id: "1RptOldTouched",
    name: "trend_report_old_touched.json",
    mimeType: "application/json",
    size: "1200",
    modifiedTime: "2026-11-30T23:59:59Z", // newer file edit time must NOT override newer crawl run!
    body: {
      run_id: "crawl-old-run",
      ended_at: "2026-11-26T08:00:00Z",
      generated_at: "2026-11-26T08:05:00Z",
      run_status: "completed",
      telegram_summary: "OLD CRAWL RETOUCHED",
    },
  };
  const rptNewOrig = {
    id: "1RptNewOrig",
    name: "trend_report_new_run.json",
    mimeType: "application/json",
    size: "1200",
    modifiedTime: "2026-11-27T12:05:00Z",
    body: {
      run_id: "crawl-new-run",
      ended_at: "2026-11-27T12:00:00Z",
      generated_at: "2026-11-27T12:05:00Z",
      run_status: "completed",
      telegram_summary: "NEW CRAWL ORIG",
    },
  };
  const rptNewRev2 = {
    id: "1RptNewRev2",
    name: "trend_report_new_run_corrected_v2.json",
    mimeType: "application/json",
    size: "1300",
    modifiedTime: "2026-11-27T12:15:00Z",
    body: {
      run_id: "crawl-new-run",
      corrects_report: "trend_report_new_run.json",
      ended_at: "2026-11-27T12:00:00Z",
      generated_at: "2026-11-27T12:15:00Z",
      run_status: "completed",
      technical_status: "completed",
      data_quality_status: "đạt",
      telegram_summary: "NEW CRAWL CORRECTED WINS",
    },
  };
  const batch25Files = Array.from({ length: 25 }, (_, i) => ({
    id: `1BatchRpt_${String(i + 1).padStart(2, "0")}`,
    name: `trend_report_batch_${String(i + 1).padStart(2, "0")}.json`,
    mimeType: "application/json",
    size: "900",
    modifiedTime: `2026-11-26T1${Math.floor(i / 10)}:${String((i % 10) * 5).padStart(2, "0")}:00Z`,
    body: {
      run_id: `batch-run-${String(i + 1).padStart(2, "0")}`,
      generated_at: `2026-11-26T1${Math.floor(i / 10)}:${String((i % 10) * 5).padStart(2, "0")}:00Z`,
      run_status: "completed",
      telegram_summary: `Batch summary #${i + 1}`,
    },
  }));

  let activeDriveReports = [rptOldTouched, rptNewOrig, rptNewRev2];
  let drivePaginationMode = "ok"; // "repeated" | "missing_token" | "many_pages"
  let tg429Remaining = 0;

  const prevFetch11 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.startsWith("https://www.googleapis.com/drive/v3/files?")) {
      const u = new URL(sUrl);
      const q = u.searchParams.get("q") || "";
      if (q.includes(`'${REPORTS_ID}' in parents`) && !q.includes("vnd.google-apps.document")) {
        return new Response(JSON.stringify({ files: activeDriveReports }), { status: 200 });
      }
      if (drivePaginationMode !== "ok" && q.includes(`'${THREADS_ID}' in parents`)) {
        if (drivePaginationMode === "repeated") {
          return new Response(JSON.stringify({
            files: [{ id: "1DupPageA", name: "threads_dup_a.json", mimeType: "application/json", size: "100", modifiedTime: "2026-11-26T15:00:00Z" }],
            nextPageToken: "tok_dup",
          }), { status: 200 });
        }
        if (drivePaginationMode === "missing_token") {
          return new Response(JSON.stringify({
            files: [{ id: "1MissTokA", name: "threads_miss_a.json", mimeType: "application/json", size: "100", modifiedTime: "2026-11-26T15:00:00Z" }],
            has_more: true,
          }), { status: 200 });
        }
        if (drivePaginationMode === "many_pages") {
          const pt = Number(u.searchParams.get("pageToken") || "0");
          return new Response(JSON.stringify({
            files: [{ id: `1ManyPg_${pt}`, name: `threads_many_${pt}.json`, mimeType: "application/json", size: "100", modifiedTime: "2026-11-26T15:00:00Z" }],
            nextPageToken: String(pt + 1),
          }), { status: 200 });
        }
      }
    }
    for (const f of activeDriveReports) {
      if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${f.id}?alt=media`)) {
        return new Response(JSON.stringify(f.body), { status: 200 });
      }
    }
    if (tg429Remaining > 0 && sUrl.startsWith("https://api.telegram.org/bot") && sUrl.includes("/sendMessage")) {
      tg429Remaining -= 1;
      telegramCalls.push({ url: sUrl, body: init.body, mock429: true });
      return new Response(JSON.stringify({ ok: false, error_code: 429, parameters: { retry_after: 0.02 }, description: "Too Many Requests" }), { status: 429 });
    }
    return prevFetch11(url, init);
  };

  try {
    // Test Issue #1: rpt:summary picks crawl-new-run corrected over crawl-old-run even though old has newer modifiedTime
    const tgBefore11 = telegramCalls.length;
    const rptRes11 = await worker.fetch(
      new Request("https://example.workers.dev/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
        body: JSON.stringify({
          update_id: 7001,
          callback_query: { id: "cq_11_1", from: { id: 999001 }, message: { message_id: 71, chat: { id: 999001, type: "private" } }, data: "rpt:summary" },
        }),
      }),
      env11
    );
    assert.equal(rptRes11.status, 200);
    const msg11 = String(telegramCalls[telegramCalls.length - 1].body);
    assert.ok(msg11.includes("NEW CRAWL CORRECTED WINS"));
    assert.ok(!msg11.includes("OLD CRAWL RETOUCHED"));
    // Issue #8: rpt:full surfaces all 3 statuses distinctly
    await worker.fetch(
      new Request("https://example.workers.dev/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
        body: JSON.stringify({
          update_id: 7002,
          callback_query: { id: "cq_11_2", from: { id: 999001 }, message: { message_id: 71, chat: { id: 999001, type: "private" } }, data: "rpt:full" },
        }),
      }),
      env11
    );
    const full11 = String(telegramCalls[telegramCalls.length - 1].body);
    assert.ok(full11.includes("run_status: completed"));
    assert.ok(full11.includes("technical_status: completed"));
    assert.ok(full11.includes("data_quality_status: đạt"));

    // Issue #2: 25-file Drive backlog processed cleanly in batches of 10 via durable cursor without missing #21..#25
    activeDriveReports = batch25Files;
    await worker.scheduled({}, env11, {});
    assert.equal(env11.DB.notifications.filter(n => n.run_id.startsWith("batch-run-")).length, 10);
    assert.ok(env11.DB.runtimeState.get("notify_drive_cursor"));
    await worker.scheduled({}, env11, {});
    assert.equal(env11.DB.notifications.filter(n => n.run_id.startsWith("batch-run-")).length, 20);
    await worker.scheduled({}, env11, {});
    assert.equal(env11.DB.notifications.filter(n => n.run_id.startsWith("batch-run-")).length, 25);
    // Re-running cron creates zero duplicates
    await worker.scheduled({}, env11, {});
    assert.equal(env11.DB.notifications.filter(n => n.run_id.startsWith("batch-run-")).length, 25);

    // Issue #2 Unified Key: /complete sets shared run_id key; subsequent Drive report for same run_id does NOT double-send
    env11.DB.crawlRequests.push({
      id: "crw_unified_shared",
      platform: "threads",
      keywords: "",
      requested_count: 20,
      status: "running",
      claimed_by: "muse-unified",
      claimed_at: new Date().toISOString(),
      lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
      attempts: 1,
      max_attempts: 3,
      requested_by_chat_id: "999001",
      notified_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
    const compUniRes = await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests/crw_unified_shared/complete", {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({
          worker_id: "muse-unified",
          attempt: 1,
          status: "completed",
          result_summary: { run_id: "shared-run-uni-1", record_count: 15, qualified_count: 8, run_status: "completed" },
        }),
      }),
      env11
    );
    assert.equal(compUniRes.status, 200);
    assert.ok(env11.DB.notifications.some(n => n.run_id === "shared-run-uni-1" && n.status === "sent"));
    assert.ok(env11.DB.crawlRequests.find(c => c.id === "crw_unified_shared").notified_at);

    activeDriveReports = [{
      id: "1SharedDriveRpt",
      name: "trend_report_shared.json",
      mimeType: "application/json",
      size: "900",
      modifiedTime: "2026-11-28T01:00:00Z",
      body: { request_id: "crw_unified_shared", run_id: "shared-run-uni-1", generated_at: "2026-11-28T01:00:00Z", run_status: "completed", telegram_summary: "SHOULD NOT DOUBLE SEND" },
    }];
    const callsBeforeDupUni = telegramCalls.length;
    await worker.scheduled({}, env11, {});
    assert.equal(telegramCalls.length, callsBeforeDupUni);

    // Issue #3: Mandatory attempt on /complete (400 if omitted) + atomic lease check inside UPDATE (409 if expired)
    env11.DB.crawlRequests.push({
      id: "crw_req_attempt",
      platform: "threads",
      keywords: "",
      requested_count: 10,
      status: "running",
      claimed_by: "muse-w1",
      claimed_at: new Date().toISOString(),
      lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
      attempts: 1,
      max_attempts: 3,
      notified_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
    const noAttemptRes = await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests/crw_req_attempt/complete", {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({ worker_id: "muse-w1", status: "completed", result_summary: { record_count: 5 } }),
      }),
      env11
    );
    assert.equal(noAttemptRes.status, 400);
    assert.equal(env11.DB.crawlRequests.find(c => c.id === "crw_req_attempt").status, "running");

    // Expire lease -> 409 Conflict (atomic UPDATE guard blocks expired lease)
    env11.DB.crawlRequests.find(c => c.id === "crw_req_attempt").lease_expires_at = new Date(Date.now() - 10_000).toISOString();
    const expiredLeaseRes = await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests/crw_req_attempt/complete", {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({ worker_id: "muse-w1", attempt: 1, status: "completed", result_summary: { record_count: 5 } }),
      }),
      env11
    );
    assert.equal(expiredLeaseRes.status, 409);

    // Issue #4: Telegram 429 retry with backoff succeeds -> 'sent'; stuck 'sending' warning + resolution buttons
    tg429Remaining = 1;
    activeDriveReports = [{
      id: "1Rpt429Retry",
      name: "trend_report_429.json",
      mimeType: "application/json",
      size: "800",
      modifiedTime: "2026-11-28T02:00:00Z",
      body: { run_id: "run-429-recovered", generated_at: "2026-11-28T02:00:00Z", run_status: "completed", telegram_summary: "Recovered after 429" },
    }];
    const callsBefore429 = telegramCalls.length;
    await worker.scheduled({}, env11, {});
    assert.equal(telegramCalls.length - callsBefore429, 2); // 1 HTTP 429 + 1 retry 200
    assert.ok(env11.DB.notifications.some(n => n.run_id === "run-429-recovered" && n.status === "sent"));

    // Simulate stuck 'sending' (>5 min old) -> sys:status warns and provides remediation callbacks
    env11.DB.notifications.push({
      run_id: "run-stuck-sending-1",
      record_count: 10,
      status: "sending",
      created_at: new Date(Date.now() - 15 * 60_000).toISOString(),
      sent_at: null,
    });
    await worker.fetch(
      new Request("https://example.workers.dev/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
        body: JSON.stringify({
          update_id: 7003,
          callback_query: { id: "cq_11_3", from: { id: 999001 }, message: { message_id: 72, chat: { id: 999001, type: "private" } }, data: "sys:status" },
        }),
      }),
      env11
    );
    const sysStuckBody = String(telegramCalls[telegramCalls.length - 1].body);
    assert.ok(sysStuckBody.includes("Cảnh báo trạng thái sending bị treo"));
    assert.ok(sysStuckBody.includes("Phương án xử lý"));
    assert.ok(sysStuckBody.includes("notif:stuck:uncertain"));
    // Resolving stuck 'sending' -> transitions safely to 'delivery_uncertain'
    await worker.fetch(
      new Request("https://example.workers.dev/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
        body: JSON.stringify({
          update_id: 7004,
          callback_query: { id: "cq_11_4", from: { id: 999001 }, message: { message_id: 72, chat: { id: 999001, type: "private" } }, data: "notif:stuck:uncertain" },
        }),
      }),
      env11
    );
    assert.equal(env11.DB.notifications.find(n => n.run_id === "run-stuck-sending-1").status, "delivery_uncertain");

    // Issue #6: Worker Drive pagination partial_error preserves cursor & reports partial_error explicitly
    for (const [mode, expectedErr] of [
      ["repeated", "repeated_page_token"],
      ["missing_token", "missing_page_token"],
      ["many_pages", "page_limit_exceeded"],
    ]) {
      drivePaginationMode = mode;
      const pRes = await worker.fetch(
        new Request("https://example.workers.dev/api/app/market/files?cursor=2026-11-26T00:00:00Z&max_pages=2", { headers: authHeaders }),
        env11
      );
      assert.equal(pRes.status, 200);
      const pJson = await pRes.json();
      assert.equal(pJson.status, "partial_error");
      assert.equal(pJson.partial_error, expectedErr);
      assert.equal(pJson.next_cursor, "2026-11-26T00:00:00Z"); // cursor NOT advanced on partial_error!
    }
  } finally {
    globalThis.fetch = prevFetch11;
  }
}

// 12. Third-Round GPT Review Regressions (#1, #2, #4):
//     #1: Atomic delivery_failed reclaim (429 retry_after respected, max_retries enforced, 408/503 -> delivery_uncertain, 400 permanent)
//     #2: Concurrent Promise.all dedupe across Drive report, D1 direct, API /complete, and V1 fallback
//     #4: expireStaleLeases runs independently even if notifyNewCrawl throws
{
  const env12 = {
    ...env,
    DB: new MockD1Database(),
    NOTIFY_BATCH_SIZE: "20",
    TELEGRAM_MAX_ATTEMPTS: "1",
    TELEGRAM_MAX_RETRIES: "2",
    TELEGRAM_RETRY_BASE_MS: "10",
    TELEGRAM_IMMEDIATE_RETRY_MAX_MS: "0", // force deferred next_retry_at reclaim path in tests
  };

  let driveReports12 = [];
  let v1Files12 = [];
  let telegramMode = "ok"; // "429_then_ok" | "429_always" | "408" | "503" | "400" | "ok"
  let tgCalls12 = [];
  let force429Remaining = 0;

  const prevFetch12 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.startsWith("https://oauth2.googleapis.com/token")) {
      return new Response(JSON.stringify({ access_token: "mock-token-12" }), { status: 200 });
    }
    if (sUrl.startsWith("https://www.googleapis.com/drive/v3/files?")) {
      const q = new URL(sUrl).searchParams.get("q") || "";
      if (q.includes(`'${REPORTS_ID}' in parents`) && !q.includes("vnd.google-apps.document")) {
        return new Response(JSON.stringify({ files: driveReports12 }), { status: 200 });
      }
      if (q.includes(`'${THREADS_ID}' in parents`)) {
        return new Response(JSON.stringify({ files: v1Files12 }), { status: 200 });
      }
      return prevFetch12(url, init);
    }
    for (const f of [...driveReports12, ...v1Files12]) {
      if (sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${f.id}?alt=media`)) {
        return new Response(JSON.stringify(f.body), { status: 200 });
      }
    }
    if (sUrl.startsWith("https://api.telegram.org/bot")) {
      tgCalls12.push({ url: sUrl, body: init.body });
      if (sUrl.includes("/sendMessage") || sUrl.includes("/editMessageText")) {
        if (telegramMode === "429_then_ok" && force429Remaining > 0) {
          force429Remaining -= 1;
          return new Response(
            JSON.stringify({ ok: false, error_code: 429, parameters: { retry_after: 0.08 }, description: "Too Many Requests" }),
            { status: 429 }
          );
        }
        if (telegramMode === "429_always") {
          return new Response(
            JSON.stringify({ ok: false, error_code: 429, parameters: { retry_after: 0.01 }, description: "Too Many Requests" }),
            { status: 429 }
          );
        }
        if (telegramMode === "408") {
          return new Response(JSON.stringify({ ok: false, error_code: 408, description: "Request Timeout" }), { status: 408 });
        }
        if (telegramMode === "503") {
          return new Response(JSON.stringify({ ok: false, error_code: 503, description: "Service Unavailable" }), { status: 503 });
        }
        if (telegramMode === "400") {
          return new Response(JSON.stringify({ ok: false, error_code: 400, description: "Bad Request" }), { status: 400 });
        }
      }
      return new Response(JSON.stringify({ ok: true, result: { message_id: 9001 } }), { status: 200 });
    }
    return prevFetch12(url, init);
  };

  try {
    // 12a-1: Definite-unsent HTTP 429 schedules next_retry_at respecting retry_after; blocked before next_retry_at, atomically reclaimed after
    telegramMode = "429_then_ok";
    force429Remaining = 1;
    driveReports12 = [{
      id: "1RptReclaim429",
      name: "trend_report_reclaim429.json",
      mimeType: "application/json",
      size: "600",
      modifiedTime: "2026-11-28T03:00:00Z",
      body: { run_id: "run-reclaim-429", generated_at: "2026-11-28T03:00:00Z", run_status: "completed", telegram_summary: "Retryable 429 test" },
    }];
    await worker.scheduled({}, env12, {});
    const row429 = env12.DB.notifications.find((n) => n.run_id === "run-reclaim-429");
    assert.ok(row429);
    assert.equal(row429.status, "delivery_failed");
    assert.equal(row429.retryable, 1);
    assert.equal(row429.retry_count, 0);
    assert.ok(row429.next_retry_at && Date.parse(row429.next_retry_at) > Date.now());
    const callsAfterFirst429 = tgCalls12.length;

    // Must NOT reclaim before next_retry_at
    await worker.scheduled({}, env12, {});
    assert.equal(tgCalls12.length, callsAfterFirst429);
    assert.equal(row429.status, "delivery_failed");

    // Wait for retry_after (~80ms) to elapse, then cron reclaims & succeeds
    await new Promise((r) => setTimeout(r, 110));
    await worker.scheduled({}, env12, {});
    assert.equal(tgCalls12.length, callsAfterFirst429 + 1);
    assert.equal(row429.status, "sent");
    assert.equal(row429.retryable, 0);
    assert.equal(row429.retry_count, 1);

    // 12a-2: Bounded max_retries=2 stops retrying once exhausted
    telegramMode = "429_always";
    driveReports12 = [{
      id: "1RptExhaust",
      name: "trend_report_exhaust.json",
      mimeType: "application/json",
      size: "600",
      modifiedTime: "2026-11-28T03:10:00Z",
      body: { run_id: "run-exhaust-429", generated_at: "2026-11-28T03:10:00Z", run_status: "completed", telegram_summary: "Exhaust max_retries" },
    }];
    await worker.scheduled({}, env12, {}); // initial fail (retry_count=0, retryable=1)
    await new Promise((r) => setTimeout(r, 25));
    await worker.scheduled({}, env12, {}); // reclaim #1 (retry_count=1, retryable=1)
    await new Promise((r) => setTimeout(r, 25));
    await worker.scheduled({}, env12, {}); // reclaim #2 (retry_count=2 >= max_retries=2 -> retryable=0)
    const rowEx = env12.DB.notifications.find((n) => n.run_id === "run-exhaust-429");
    assert.equal(rowEx.status, "delivery_failed");
    assert.equal(rowEx.retry_count, 2);
    assert.equal(rowEx.retryable, 0);
    const callsAtExhaust = tgCalls12.length;
    await new Promise((r) => setTimeout(r, 25));
    await worker.scheduled({}, env12, {}); // must NOT reclaim again
    assert.equal(tgCalls12.length, callsAtExhaust);

    // 12a-3: HTTP 408 and HTTP 503 MUST become delivery_uncertain (NEVER retried)
    for (const [errCode, runTag] of [["408", "run-err-408"], ["503", "run-err-503"]]) {
      telegramMode = errCode;
      driveReports12 = [{
        id: `1Rpt_${errCode}`,
        name: `trend_report_${errCode}.json`,
        mimeType: "application/json",
        size: "600",
        modifiedTime: "2026-11-28T03:20:00Z",
        body: { run_id: runTag, generated_at: "2026-11-28T03:20:00Z", run_status: "completed", telegram_summary: `Uncertain HTTP ${errCode}` },
      }];
      const beforeUnc = tgCalls12.length;
      await worker.scheduled({}, env12, {});
      assert.equal(tgCalls12.length, beforeUnc + 1);
      const uncRow = env12.DB.notifications.find((n) => n.run_id === runTag);
      assert.equal(uncRow.status, "delivery_uncertain");
      assert.equal(uncRow.retryable, 0);
      await new Promise((r) => setTimeout(r, 25));
      await worker.scheduled({}, env12, {}); // must NEVER retry delivery_uncertain
      assert.equal(tgCalls12.length, beforeUnc + 1);
    }

    // 12a-4: Permanent 400 -> non-retryable delivery_failed (NEVER retried)
    telegramMode = "400";
    driveReports12 = [{
      id: "1Rpt_400",
      name: "trend_report_400.json",
      mimeType: "application/json",
      size: "600",
      modifiedTime: "2026-11-28T03:30:00Z",
      body: { run_id: "run-perm-400", generated_at: "2026-11-28T03:30:00Z", run_status: "completed", telegram_summary: "Permanent 400" },
    }];
    const before400 = tgCalls12.length;
    await worker.scheduled({}, env12, {});
    assert.equal(tgCalls12.length, before400 + 1);
    const permRow = env12.DB.notifications.find((n) => n.run_id === "run-perm-400");
    assert.equal(permRow.status, "delivery_failed");
    assert.equal(permRow.retryable, 0);
    await worker.scheduled({}, env12, {});
    assert.equal(tgCalls12.length, before400 + 1);

    // 12b: Concurrent Promise.all dedupe across Drive report, D1 direct completion, API /complete, and V1 fallback
    telegramMode = "ok";
    const raceRunId = "run-concurrent-4way";
    const raceReqId = "crw_race_api_d1";
    const raceD1DirectId = "crw_race_d1_only";
    env12.DB.crawlRequests.push(
      {
        id: raceReqId,
        platform: "threads",
        keywords: "",
        requested_count: 20,
        status: "running",
        claimed_by: "muse-racer",
        claimed_at: new Date().toISOString(),
        lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
        attempts: 1,
        max_attempts: 3,
        requested_by_chat_id: "999001",
        notified_at: null,
        updated_at: "2026-11-28T04:00:00Z",
      },
      {
        id: raceD1DirectId,
        platform: "threads",
        keywords: "",
        requested_count: 20,
        status: "completed",
        claimed_by: "muse-direct",
        claimed_at: "2026-11-28T03:59:00Z",
        attempts: 1,
        max_attempts: 3,
        result_summary_json: JSON.stringify({ run_id: raceRunId, qualified_count: 8, run_status: "completed", telegram_summary: "D1 direct race" }),
        requested_by_chat_id: "999001",
        notified_at: null,
        updated_at: "2026-11-28T04:01:00Z",
      }
    );
    driveReports12 = [{
      id: "1RptRaceDrive",
      name: "trend_report_race.json",
      mimeType: "application/json",
      size: "700",
      modifiedTime: "2026-11-28T04:01:05Z",
      body: {
        run_id: raceRunId,
        request_id: raceReqId,
        generated_at: "2026-11-28T04:01:05Z",
        run_status: "completed",
        telegram_summary: "Drive report race",
      },
    }];
    v1Files12 = [{
      id: "1V1RaceFile",
      name: "threads_crawl_race.json",
      mimeType: "application/json",
      size: "700",
      createdTime: "2026-11-28T04:01:06Z",
      modifiedTime: "2026-11-28T04:01:06Z",
      body: {
        run_id: raceRunId,
        request_id: raceReqId,
        status: "completed",
        started_at: new Date(Date.now() - 10_000).toISOString(),
        ended_at: new Date().toISOString(),
        posts: [{ id: "p_race_1" }],
      },
    }];
    const tgBeforeRace = tgCalls12.length;
    await Promise.all([
      worker.fetch(
        new Request(`https://example.workers.dev/api/app/crawl-requests/${raceReqId}/complete`, {
          method: "POST",
          headers: { ...authHeaders, "Content-Type": "application/json" },
          body: JSON.stringify({
            status: "completed",
            attempt: 1,
            claimed_by: "muse-racer",
            result_summary: { run_id: raceRunId, qualified_count: 8, run_status: "completed", telegram_summary: "API complete race" },
          }),
        }),
        env12
      ),
      worker.scheduled({}, env12, {}),
      worker.scheduled({}, env12, {}),
      worker.scheduled({}, env12, {}),
    ]);
    assert.equal(tgCalls12.length - tgBeforeRace, 1);

    // 12c: expireStaleLeases MUST still execute independently if notifyNewCrawl throws
    const expiredIso = new Date(Date.now() - 10 * 60_000).toISOString();
    env12.DB.commands.push({
      id: "cmd_expired_12",
      command_type: "sync_now",
      status: "running",
      claimed_by: "app-crashed",
      lease_expires_at: expiredIso,
      attempts: 1,
      max_attempts: 3,
      updated_at: expiredIso,
    });
    env12.DB.crawlRequests.push({
      id: "crw_expired_12",
      platform: "threads",
      status: "running",
      claimed_by: "muse-crashed",
      lease_expires_at: expiredIso,
      attempts: 1,
      max_attempts: 3,
      updated_at: expiredIso,
    });
    const origPrepare12 = env12.DB.prepare.bind(env12.DB);
    env12.DB.prepare = (sql) => {
      if (String(sql).includes("FROM crawl_notifications") && String(sql).includes("status = 'delivery_failed'")) {
        return {
          bind() { return this; },
          async all() { throw new Error("Simulated notifyNewCrawl fatal D1 failure"); },
        };
      }
      return origPrepare12(sql);
    };
    let schedFailed = false;
    try {
      await worker.scheduled({}, env12, {});
    } catch (err) {
      schedFailed = String(err?.message || "").includes("notify");
    } finally {
      env12.DB.prepare = origPrepare12;
    }
    assert.equal(schedFailed, true);
    const recoveredCmd = env12.DB.commands.find((c) => c.id === "cmd_expired_12");
    const recoveredCrw = env12.DB.crawlRequests.find((c) => c.id === "crw_expired_12");
    assert.equal(recoveredCmd.status, "queued");
    assert.equal(recoveredCmd.claimed_by, null);
    assert.equal(recoveredCrw.status, "queued");
    assert.equal(recoveredCrw.claimed_by, null);
  } finally {
    globalThis.fetch = prevFetch12;
  }
}

// 13. Pre-Deploy Final Guardrails (#1 Atomic batch claim vs mid-tx crash, #2 updatePrimaryProgressMessage no 429/uncertain fallback):
{
  const env13 = {
    ...env,
    DB: new MockD1Database(),
    NOTIFY_BATCH_SIZE: "20",
    TELEGRAM_MAX_ATTEMPTS: "1",
    TELEGRAM_MAX_RETRIES: "3",
    TELEGRAM_RETRY_BASE_MS: "10",
    TELEGRAM_IMMEDIATE_RETRY_MAX_MS: "0",
  };

  let editMode13 = "ok"; // "ok" | "429_then_ok" | "503" | "408" | "throw_timeout" | "400_missing_msg"
  let edit429Remaining = 0;
  let editCalls13 = [];
  let sendCalls13 = [];

  const prevFetch13 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.startsWith("https://oauth2.googleapis.com/token")) {
      return new Response(JSON.stringify({ access_token: "mock-token-13" }), { status: 200 });
    }
    if (sUrl.startsWith("https://www.googleapis.com/drive/v3/files?")) {
      const q = new URL(sUrl).searchParams.get("q") || "";
      if (q.includes(`'${REPORTS_ID}' in parents`) || q.includes(`'${THREADS_ID}' in parents`)) {
        return new Response(JSON.stringify({ files: [] }), { status: 200 });
      }
      return prevFetch13(url, init);
    }
    if (sUrl.startsWith("https://api.telegram.org/bot") && sUrl.includes("/editMessageText")) {
      editCalls13.push({ url: sUrl, body: init.body });
      if (editMode13 === "429_then_ok" && edit429Remaining > 0) {
        edit429Remaining -= 1;
        return new Response(
          JSON.stringify({ ok: false, error_code: 429, parameters: { retry_after: 0.08 }, description: "Too Many Requests" }),
          { status: 429 }
        );
      }
      if (editMode13 === "503") {
        return new Response(JSON.stringify({ ok: false, error_code: 503, description: "Service Unavailable" }), { status: 503 });
      }
      if (editMode13 === "408") {
        return new Response(JSON.stringify({ ok: false, error_code: 408, description: "Request Timeout" }), { status: 408 });
      }
      if (editMode13 === "throw_timeout") {
        throw new Error("Simulated editMessageText socket timeout");
      }
      if (editMode13 === "400_missing_msg") {
        return new Response(JSON.stringify({ ok: false, error_code: 400, description: "Bad Request: message to edit not found" }), { status: 400 });
      }
      return new Response(JSON.stringify({ ok: true, result: { message_id: 777 } }), { status: 200 });
    }
    if (sUrl.startsWith("https://api.telegram.org/bot") && sUrl.includes("/sendMessage")) {
      sendCalls13.push({ url: sUrl, body: init.body });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 8888 } }), { status: 200 });
    }
    return prevFetch13(url, init);
  };

  try {
    // 13a-1: Interruption BETWEEN DB statement 1 (INSERT crawl_notifications) and statement 2 (UPDATE crawl_requests)
    //        inside D1 batch MUST roll back cleanly so notified_at is NEVER set without durable notification row.
    env13.DB.runtimeState.set("tg_primary_status_msg_id", "777");
    env13.DB.crawlRequests.push({
      id: "crw_tx_interrupt",
      platform: "threads",
      keywords: "",
      requested_count: 20,
      status: "running",
      claimed_by: "muse-tx",
      claimed_at: new Date().toISOString(),
      lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
      attempts: 1,
      max_attempts: 3,
      requested_by_chat_id: "999001",
      notified_at: null,
      updated_at: "2026-11-28T05:00:00Z",
    });

    env13.DB.onBatchStep = (stepIdx) => {
      if (stepIdx === 1) throw new Error("Simulated D1 interruption between INSERT and UPDATE");
    };
    let batchInterrupted = false;
    try {
      await worker.fetch(
        new Request("https://example.workers.dev/api/app/crawl-requests/crw_tx_interrupt/complete", {
          method: "POST",
          headers: { ...authHeaders, "Content-Type": "application/json" },
          body: JSON.stringify({
            status: "completed",
            attempt: 1,
            claimed_by: "muse-tx",
            result_summary: { run_id: "run-tx-interrupt", qualified_count: 4, run_status: "completed", telegram_summary: "Atomic tx recovery" },
          }),
        }),
        env13
      );
    } catch (err) {
      batchInterrupted = String(err?.message || "").includes("Simulated D1 interruption");
    } finally {
      env13.DB.onBatchStep = null;
    }
    assert.equal(batchInterrupted, true);
    const reqAfterAbort = env13.DB.crawlRequests.find((c) => c.id === "crw_tx_interrupt");
    assert.equal(reqAfterAbort.notified_at, null);
    assert.equal(env13.DB.notifications.length, 0);
    assert.equal(editCalls13.length, 0);
    assert.equal(sendCalls13.length, 0);

    // Recover cleanly on next Cron without losing the notification
    await worker.scheduled({}, env13, {});
    assert.ok(reqAfterAbort.notified_at);
    const notifRecovered = env13.DB.notifications.find((n) => n.run_id === "run-tx-interrupt");
    assert.ok(notifRecovered);
    assert.equal(notifRecovered.status, "sent");

    // 13a-2: Non-batch sequential fallback interrupted after INSERT before UPDATE cleans up orphan & leaves notified_at NULL
    env13.DB.crawlRequests.push({
      id: "crw_nobatch_interrupt",
      platform: "threads",
      keywords: "",
      requested_count: 20,
      status: "running",
      claimed_by: "muse-nb",
      claimed_at: new Date().toISOString(),
      lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
      attempts: 1,
      max_attempts: 3,
      requested_by_chat_id: "999001",
      notified_at: null,
      updated_at: "2026-11-28T05:05:00Z",
    });
    const origBatch13 = env13.DB.batch.bind(env13.DB);
    const origPrepare13 = env13.DB.prepare.bind(env13.DB);
    env13.DB.batch = undefined;
    env13.DB.prepare = (sql) => {
      if (String(sql).includes("UPDATE crawl_requests") && String(sql).includes("SET notified_at = ?") && String(sql).includes("AND EXISTS")) {
        return {
          bind() { return this; },
          async run() { throw new Error("Simulated non-batch interruption before notified_at"); },
        };
      }
      return origPrepare13(sql);
    };
    let nobatchErr = false;
    try {
      await worker.fetch(
        new Request("https://example.workers.dev/api/app/crawl-requests/crw_nobatch_interrupt/complete", {
          method: "POST",
          headers: { ...authHeaders, "Content-Type": "application/json" },
          body: JSON.stringify({
            status: "completed",
            attempt: 1,
            claimed_by: "muse-nb",
            result_summary: { run_id: "run-nobatch-interrupt", qualified_count: 3, run_status: "completed", telegram_summary: "No-batch fallback rollback" },
          }),
        }),
        env13
      );
    } catch (err) {
      nobatchErr = String(err?.message || "").includes("Simulated non-batch interruption");
    } finally {
      env13.DB.batch = origBatch13;
      env13.DB.prepare = origPrepare13;
    }
    assert.equal(nobatchErr, true);
    const reqNb = env13.DB.crawlRequests.find((c) => c.id === "crw_nobatch_interrupt");
    assert.equal(reqNb.notified_at, null);
    assert.equal(env13.DB.notifications.some((n) => n.run_id === "run-nobatch-interrupt"), false);

    // Next Cron cleanly picks up un-notified crw_nobatch_interrupt without dropping it
    await worker.scheduled({}, env13, {});
    assert.ok(reqNb.notified_at);
    assert.equal(env13.DB.notifications.find((n) => n.run_id === "run-nobatch-interrupt")?.status, "sent");

    // 13b-1: updatePrimaryProgressMessage hits 429 on editMessageText -> MUST NOT fallback to sendMessage;
    //        schedules retry_after and succeeds cleanly on reclaim without ever calling sendMessage.
    editCalls13.length = 0;
    sendCalls13.length = 0;
    editMode13 = "429_then_ok";
    edit429Remaining = 1;
    env13.DB.crawlRequests.push({
      id: "crw_prim_429",
      platform: "threads",
      keywords: "",
      requested_count: 20,
      status: "running",
      claimed_by: "muse-prim",
      claimed_at: new Date().toISOString(),
      lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
      attempts: 1,
      max_attempts: 3,
      requested_by_chat_id: "999001",
      notified_at: null,
      updated_at: "2026-11-28T05:10:00Z",
    });
    const res429Edit = await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests/crw_prim_429/complete", {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({
          status: "completed",
          attempt: 1,
          claimed_by: "muse-prim",
          result_summary: { run_id: "run-prim-429", qualified_count: 6, run_status: "completed", telegram_summary: "Primary 429 test" },
        }),
      }),
      env13
    );
    assert.equal(res429Edit.status, 200);
    assert.equal(editCalls13.length, 1);
    assert.equal(sendCalls13.length, 0); // NO fallback to sendMessage!
    const prim429Row = env13.DB.notifications.find((n) => n.run_id === "run-prim-429");
    assert.equal(prim429Row.status, "delivery_failed");
    assert.equal(prim429Row.retryable, 1);
    assert.ok(prim429Row.next_retry_at && Date.parse(prim429Row.next_retry_at) > Date.now());

    // Wait for retry_after and reclaim via scheduled cron
    await new Promise((r) => setTimeout(r, 110));
    await worker.scheduled({}, env13, {});
    assert.equal(editCalls13.length, 2);
    assert.equal(sendCalls13.length, 0); // Still NO sendMessage!
    assert.equal(prim429Row.status, "sent");
    assert.equal(prim429Row.retryable, 0);

    // 13b-2: updatePrimaryProgressMessage hits 503, 408, or ambiguous throw -> delivery_uncertain, NO fallback, NO retry
    for (const [mode, reqId, runTag] of [
      ["503", "crw_prim_503", "run-prim-503"],
      ["408", "crw_prim_408", "run-prim-408"],
      ["throw_timeout", "crw_prim_throw", "run-prim-throw"],
    ]) {
      editCalls13.length = 0;
      sendCalls13.length = 0;
      editMode13 = mode;
      env13.DB.crawlRequests.push({
        id: reqId,
        platform: "threads",
        keywords: "",
        requested_count: 20,
        status: "running",
        claimed_by: "muse-prim",
        claimed_at: new Date().toISOString(),
        lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
        attempts: 1,
        max_attempts: 3,
        requested_by_chat_id: "999001",
        notified_at: null,
        updated_at: "2026-11-28T05:15:00Z",
      });
      await worker.fetch(
        new Request(`https://example.workers.dev/api/app/crawl-requests/${reqId}/complete`, {
          method: "POST",
          headers: { ...authHeaders, "Content-Type": "application/json" },
          body: JSON.stringify({
            status: "completed",
            attempt: 1,
            claimed_by: "muse-prim",
            result_summary: { run_id: runTag, qualified_count: 2, run_status: "completed", telegram_summary: `Primary ${mode}` },
          }),
        }),
        env13
      );
      assert.equal(editCalls13.length, 1);
      assert.equal(sendCalls13.length, 0); // NO fallback to sendMessage!
      const uncRow = env13.DB.notifications.find((n) => n.run_id === runTag);
      assert.equal(uncRow.status, "delivery_uncertain");
      assert.equal(uncRow.retryable, 0);
      await worker.scheduled({}, env13, {}); // must NOT retry delivery_uncertain
      assert.equal(editCalls13.length, 1);
      assert.equal(sendCalls13.length, 0);
    }

    // 13b-3: Permanent 400 "message to edit not found" DOES safely fall back once to sendMessage and updates primary msg id
    editCalls13.length = 0;
    sendCalls13.length = 0;
    editMode13 = "400_missing_msg";
    env13.DB.crawlRequests.push({
      id: "crw_prim_missing",
      platform: "threads",
      keywords: "",
      requested_count: 20,
      status: "running",
      claimed_by: "muse-prim",
      claimed_at: new Date().toISOString(),
      lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
      attempts: 1,
      max_attempts: 3,
      requested_by_chat_id: "999001",
      notified_at: null,
      updated_at: "2026-11-28T05:20:00Z",
    });
    await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests/crw_prim_missing/complete", {
        method: "POST",
        headers: { ...authHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({
          status: "completed",
          attempt: 1,
          claimed_by: "muse-prim",
          result_summary: { run_id: "run-prim-missing", qualified_count: 5, run_status: "completed", telegram_summary: "Missing primary fallback ok" },
        }),
      }),
      env13
    );
    assert.equal(editCalls13.length, 1);
    assert.equal(sendCalls13.length, 1);
    assert.equal(env13.DB.runtimeState.get("tg_primary_status_msg_id"), "8888");
    assert.equal(env13.DB.notifications.find((n) => n.run_id === "run-prim-missing").status, "sent");
  } finally {
    globalThis.fetch = prevFetch13;
  }
}

// 14. Telegram UX Upgrade: Reply Keyboard Main Menu, Structured Bulleted Reports, Deduplicated Submenus
{
  const env14 = {
    ...env,
    DB: new MockD1Database(),
  };
  const tgCalls14 = [];
  const prevFetch14 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.startsWith("https://api.telegram.org/bot")) {
      tgCalls14.push({ url: sUrl, body: init.body });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 4444 } }), { status: 200 });
    }
    return prevFetch14(url, init);
  };

  try {
    const MAIN_MENU_SET = new Set([
      "🕷️ Crawl dữ liệu",
      "📊 Phân tích thị trường",
      "⏰ Lịch tự động",
      "📋 Lịch sử tác vụ",
      "📡 Trạng thái hệ thống",
      "⚙️ Cài đặt",
    ]);

    const sendUserText = async (updateId, text) => {
      const before = tgCalls14.length;
      const res = await worker.fetch(
        new Request("https://example.workers.dev/telegram", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
          body: JSON.stringify({
            update_id: updateId,
            message: { message_id: updateId, from: { id: 999001 }, chat: { id: 999001, type: "private" }, text },
          }),
        }),
        env14
      );
      assert.equal(res.status, 200);
      const sent = tgCalls14.slice(before).find((c) => c.url.includes("/sendMessage"));
      assert.ok(sent, `Expected /sendMessage for text ${text}`);
      return JSON.parse(sent.body);
    };

    const sendCallback = async (updateId, data) => {
      const before = tgCalls14.length;
      const res = await worker.fetch(
        new Request("https://example.workers.dev/telegram", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
          body: JSON.stringify({
            update_id: updateId,
            callback_query: {
              id: `cq_14_${updateId}`,
              from: { id: 999001 },
              message: { message_id: 4444, chat: { id: 999001, type: "private" } },
              data,
            },
          }),
        }),
        env14
      );
      assert.equal(res.status, 200);
      const call = tgCalls14.slice(before).find((c) => c.url.includes("/sendMessage") || c.url.includes("/editMessageText"));
      assert.ok(call, `Expected Telegram call for callback ${data}`);
      return JSON.parse(call.body);
    };

    const assertNoMainMenuDuplicateInInline = (payload, label) => {
      assert.ok(Array.isArray(payload.reply_markup?.inline_keyboard), `${label}: must use inline_keyboard`);
      const btns = payload.reply_markup.inline_keyboard.flat();
      for (const b of btns) {
        assert.ok(!MAIN_MENU_SET.has(b.text), `${label}: inline button "${b.text}" duplicates Main Menu`);
        assert.ok(!String(b.callback_data || "").startsWith("drv:"), `${label}: stale Drive Explorer callback`);
        assert.notEqual(b.callback_data, "rpt:send", `${label}: stale JSON send callback`);
      }
      assert.ok(btns.some((b) => b.text === "🏠 Trang chủ" && b.callback_data === "menu:home"), `${label}: missing 🏠 Trang chủ`);
      return btns;
    };

    // 14a: Reply Keyboard on /start
    const homeMsg = await sendUserText(8001, "/start");
    assert.deepEqual(homeMsg.reply_markup.keyboard.flat().map((b) => b.text), Array.from(MAIN_MENU_SET));
    assert.equal(homeMsg.reply_markup.inline_keyboard, undefined);

    // 14b: Reply Button "📊 Phân tích thị trường" -> 6 structured bullet sections + Inline sub-actions
    const mktMsg = await sendUserText(8002, "📊 Phân tích thị trường");
    for (const sec of [
      "📌 Trạng thái",
      "📊 Tổng quan số liệu",
      "🔥 Xu hướng nổi bật",
      "💡 Tín hiệu/cơ hội affiliate",
      "✅ Hành động đề xuất hôm nay",
      "⚠️ Chất lượng dữ liệu / giới hạn",
    ]) {
      assert.ok(mktMsg.text.includes(sec), `Missing section: ${sec}`);
    }
    assert.ok(mktMsg.text.includes("\n• "), "Report must use bullet lines");
    assert.ok(!mktMsg.text.includes("TOÀN THỊ TRƯỜNG:"), "Prose block must be converted into structured bullets");
    const mktBtns = assertNoMainMenuDuplicateInInline(mktMsg, "Phân tích thị trường");
    assert.deepEqual(
      mktBtns.map((b) => b.text),
      ["📄 Xem báo cáo đầy đủ", "🔄 Làm mới báo cáo", "🏠 Trang chủ"]
    );

    const mktFull = await sendCallback(8003, "rpt:full");
    assertNoMainMenuDuplicateInInline(mktFull, "Báo cáo đầy đủ");
    assert.ok(mktFull.reply_markup.inline_keyboard.flat().some((b) => b.callback_data === "rpt:summary"));

    // 14c: Reply Button "🕷️ Crawl dữ liệu" -> Threads -> 50 -> queued request in D1
    const crawlMsg = await sendUserText(8004, "🕷️ Crawl dữ liệu");
    const crawlBtns = assertNoMainMenuDuplicateInInline(crawlMsg, "Crawl dữ liệu");
    assert.deepEqual(
      crawlBtns.map((b) => b.callback_data),
      ["crawl:plat:threads", "crawl:plat:tiktok", "crawl:plat:shopee", "crawl:plat:all", "menu:home"]
    );

    const platMsg = await sendCallback(8005, "crawl:plat:threads");
    const platBtns = assertNoMainMenuDuplicateInInline(platMsg, "Chọn số lượng Crawl");
    assert.deepEqual(
      platBtns.map((b) => b.callback_data),
      ["crawl:go:threads:20", "crawl:go:threads:50", "crawl:go:threads:100", "crawl:menu", "menu:home"]
    );

    const queuedMsg = await sendCallback(8006, "crawl:go:threads:50");
    assertNoMainMenuDuplicateInInline(queuedMsg, "Sau khi tạo Crawl");
    assert.equal(env14.DB.crawlRequests.length, 1);
    assert.equal(env14.DB.crawlRequests[0].platform, "threads");
    assert.equal(env14.DB.crawlRequests[0].requested_count, 50);
    assert.equal(env14.DB.crawlRequests[0].status, "queued");

    // 14d: Reply Button "⏰ Lịch tự động"
    const schedMsg = await sendUserText(8007, "⏰ Lịch tự động");
    const schedBtns = assertNoMainMenuDuplicateInInline(schedMsg, "Lịch tự động");
    assert.deepEqual(schedBtns.map((b) => b.callback_data), ["sched:view", "menu:home"]);

    // 14e: Reply Button "📋 Lịch sử tác vụ"
    const histMsg = await sendUserText(8008, "📋 Lịch sử tác vụ");
    const histBtns = assertNoMainMenuDuplicateInInline(histMsg, "Lịch sử tác vụ");
    assert.deepEqual(histBtns.map((b) => b.callback_data), ["cmd:history", "menu:home"]);

    // 14f: Reply Button "📡 Trạng thái hệ thống"
    const sysMsg = await sendUserText(8009, "📡 Trạng thái hệ thống");
    const sysBtns = assertNoMainMenuDuplicateInInline(sysMsg, "Trạng thái hệ thống");
    assert.deepEqual(sysBtns.map((b) => b.callback_data), ["sys:status", "menu:home"]);

    // 14g: Reply Button "⚙️ Cài đặt" & Manual Sync flow
    const cfgMsg = await sendUserText(8010, "⚙️ Cài đặt");
    const cfgBtns = assertNoMainMenuDuplicateInInline(cfgMsg, "Cài đặt");
    assert.deepEqual(cfgBtns.map((b) => b.callback_data), ["cmd:sync_now", "crawl:toggle_profile", "menu:home"]);

    const syncEnqMsg = await sendCallback(8011, "cmd:sync_now");
    assert.equal(env14.DB.commands.length, 1);
    assert.equal(env14.DB.commands[0].command_type, "sync_now");
    assert.deepEqual(
      syncEnqMsg.reply_markup.inline_keyboard.flat().map((b) => b.callback_data),
      ["menu:home"]
    );

    // 14h: Inline "🏠 Trang chủ" restores Reply Keyboard Main Menu cleanly
    const homeCbMsg = await sendCallback(8012, "menu:home");
    assert.deepEqual(homeCbMsg.reply_markup.keyboard.flat().map((b) => b.text), Array.from(MAIN_MENU_SET));
    assert.equal(homeCbMsg.reply_markup.inline_keyboard, undefined);
  } finally {
    globalThis.fetch = prevFetch14;
  }
}

// 15. Cross-Run Dedup Registry (D1), Target Progress 7/50, Unified Direct-D1 Bullet Renderer & Callback Confirmations
{
  const m0003Path = path.join(__dirname, "../cloudflare-worker/migrations/0003_cross_run_dedup_registry.sql");
  assert.ok(fs.existsSync(m0003Path), "Missing migration 0003_cross_run_dedup_registry.sql");
  const m0003Sql = fs.readFileSync(m0003Path, "utf8");
  assert.ok(m0003Sql.includes("CREATE TABLE IF NOT EXISTS market_post_registry"));

  const env15 = {
    DB: new MockD1Database(),
    APP_VIDEO_API_TOKEN: "test-token-15",
    TELEGRAM_OWNER_ID: "999001",
    TELEGRAM_BOT_TOKEN_VIDEO: "tg-bot-token",
    TELEGRAM_WEBHOOK_SECRET: "tg-webhook-secret",
  };
  const authHeaders15 = {
    Authorization: "Bearer test-token-15",
    "Content-Type": "application/json",
    "X-Client-Id": "app-video-test",
  };
  const tgCalls15 = [];
  const prevFetch15 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.includes("api.telegram.org")) {
      tgCalls15.push({ url: sUrl, body: init.body });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 777 } }), { status: 200 });
    }
    throw new Error("Unexpected fetch: " + sUrl);
  };
  try {
    // 15a: Multi-run registry record + check across 3 runs
    const recRun1 = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: authHeaders15,
        body: JSON.stringify({
          run_id: "r1",
          items: [
            { platform: "threads", post_id: "P100", canonical_url: "https://threads.net/@u/post/P100", views: 1000, likes: 50 },
            { platform: "threads", post_id: "P101", canonical_url: "https://threads.net/@u/post/P101", views: null, likes: 12 },
          ],
        }),
      }),
      env15
    );
    const j1 = await recRun1.json();
    assert.equal(j1.new_count, 2);
    assert.equal(j1.duplicate_count, 0);
    assert.equal(j1.updated_count, 0);
    assert.equal(j1.missing_views_count, 1);

    const recRun2 = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: authHeaders15,
        body: JSON.stringify({
          run_id: "r2",
          items: [
            { platform: "threads", post_id: "P100", views: 2500, likes: 120 },
            { platform: "threads", post_id: "P101", views: null, likes: 12 },
          ],
        }),
      }),
      env15
    );
    const j2 = await recRun2.json();
    assert.equal(j2.new_count, 0);
    assert.equal(j2.updated_count, 1);
    assert.equal(j2.duplicate_count, 1);
    assert.equal(j2.missing_views_count, 1);

    const recRun3 = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: authHeaders15,
        body: JSON.stringify({
          run_id: "r3",
          items: [{ platform: "threads", post_id: "P100", views: 2500, likes: 120 }],
        }),
      }),
      env15
    );
    const j3 = await recRun3.json();
    assert.equal(j3.new_count, 0);
    assert.equal(j3.updated_count, 0);
    assert.equal(j3.duplicate_count, 1);
    assert.equal(env15.DB.postRegistry.get("threads:post:P100").snapshot_count, 3);

    const checkRes = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/check", {
        method: "POST",
        headers: authHeaders15,
        body: JSON.stringify({
          items: [
            { platform: "threads", post_id: "P100" },
            { platform: "threads", post_id: "P999" },
          ],
        }),
      }),
      env15
    );
    const checkJson = await checkRes.json();
    assert.equal(checkJson.existing_count, 1);
    assert.equal(checkJson.new_count, 1);
    assert.ok(checkJson.existing["threads:post:P100"]);

    // 15b: Direct D1 crawl completion uses shared 6-section bullet renderer, shows 7/50 + Chưa đạt mục tiêu + cross-run dedup bullets, without repeating numbers across sections
    const crwRes = await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests", {
        method: "POST",
        headers: authHeaders15,
        body: JSON.stringify({ platform: "threads", requested_count: 50, keywords: "skincare" }),
      }),
      env15
    );
    const crwData = await crwRes.json();
    const crwId = crwData.request.id;
    const reqRow = env15.DB.crawlRequests.find((c) => c.id === crwId);
    reqRow.status = "running";
    reqRow.claimed_by = "muse-worker";
    reqRow.attempts = 1;
    reqRow.requested_by_chat_id = "999001";

    const beforeCompleteTg = tgCalls15.length;
    const completeRes = await worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${crwId}/complete`, {
        method: "POST",
        headers: authHeaders15,
        body: JSON.stringify({
          claimed_by: "muse-worker",
          attempt: 1,
          status: "completed",
          result_summary: {
            run_id: "run-tg-shared-15",
            run_status: "completed",
            technical_status: "completed",
            data_quality_status: "dat",
            scanned_count: 30,
            qualified_count: 7,
            rejected_count: 10,
            new_count: 7,
            duplicate_count: 9,
            updated_count: 2,
            missing_views_count: 4,
            telegram_summary:
              "TRẠNG THÁI: Quét 30 | Đạt chuẩn 7 | Bị loại 10\n" +
              "TOÀN THỊ TRƯỜNG: Kem chống nắng vật lý; Tẩy trang cho da nhạy cảm\n" +
              "AFFILIATE: Nước tẩy trang Bioderma 500ml\n" +
              "HÀNH ĐỘNG: Làm video test chống nắng 30s\n" +
              "CHẤT LƯỢNG DỮ LIỆU: 4 bài thiếu views đã tách riêng",
          },
        }),
      }),
      env15
    );
    assert.equal(completeRes.status, 200);

    const notifCall = tgCalls15.slice(beforeCompleteTg).find((c) => c.url.includes("/sendMessage"));
    assert.ok(notifCall, "Expected automatic Telegram notification after /complete");
    const notifText = JSON.parse(notifCall.body).text;
    for (const sec of [
      "📌 Trạng thái",
      "📊 Tổng quan số liệu",
      "🔥 Xu hướng nổi bật",
      "💡 Tín hiệu/cơ hội affiliate",
      "✅ Hành động đề xuất hôm nay",
      "⚠️ Chất lượng dữ liệu / giới hạn",
    ]) {
      assert.ok(notifText.includes(sec), `Direct D1 auto notification missing section: ${sec}`);
    }
    assert.ok(notifText.includes("7/50 (Chưa đạt mục tiêu)"), "Must show 7/50 Chưa đạt mục tiêu");
    assert.ok(notifText.includes("Bài mới: 7 | Bài trùng lịch sử: 9 | Cập nhật tương tác: 2"));
    assert.ok(notifText.includes("Bài thiếu views: 4"));
    assert.ok(!notifText.includes("TOÀN THỊ TRƯỜNG:"), "Must convert summary prose into bullets");

    // Verify GET /api/app/crawl-requests returns progress_metrics matching 7/50
    const listAfter = await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests", { headers: authHeaders15 }),
      env15
    );
    const listJson = await listAfter.json();
    assert.equal(listJson.items[0].progress_metrics.new_qualified_count, 7);
    assert.equal(listJson.items[0].progress_metrics.target_reached, false);
    assert.equal(listJson.items[0].progress_metrics.duplicate_count, 9);
    assert.equal(listJson.items[0].progress_metrics.missing_views_count, 4);

    // 15c: Callback button clicks emit non-empty confirmation toast via answerCallbackQuery
    const beforeCb = tgCalls15.length;
    await worker.fetch(
      new Request("https://example.workers.dev/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-webhook-secret" },
        body: JSON.stringify({
          update_id: 9001,
          callback_query: {
            id: "cq_ack_1",
            from: { id: 999001 },
            message: { message_id: 777, chat: { id: 999001, type: "private" } },
            data: "crawl:plat:threads",
          },
        }),
      }),
      env15
    );
    const ackCall = tgCalls15.slice(beforeCb).find((c) => c.url.includes("/answerCallbackQuery"));
    assert.ok(ackCall, "Expected /answerCallbackQuery call");
    const ackBody = JSON.parse(ackCall.body);
    assert.ok(ackBody.text && ackBody.text.includes("THREADS"), `Expected descriptive callback ack, got: ${ackBody.text}`);
  } finally {
    globalThis.fetch = prevFetch15;
  }
}

console.log("WORKER_V2_CONTRACT_TESTS_OK");
