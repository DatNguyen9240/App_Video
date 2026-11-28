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
    this.postSnapshots = new Map();
    this.registryOutbox = [];
    this.failRegistry = false;
    this.failRegistryOnSnapshotCallsRemaining = 0;
    this.snapshotInsertAttempts = 0;
    this.onBeforeRegistryInsert = null;
    this.onBatchStep = null;
    this.failSignalQuery = false;
    this.writeOps = 0;
    this.onBeforeClaimUpdate = null;
  }
  _countSnaps(dedupKey) {
    let c = 0;
    for (const s of this.postSnapshots.values()) {
      if (s.dedup_key === dedupKey) c += 1;
    }
    return c;
  }
  _parseObsTs(v) {
    if (!v || typeof v !== "string") return 0;
    const t = v.trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return Date.parse(`${t}T00:00:00Z`) || 0;
    return Date.parse(t) || 0;
  }
  async batch(stmts) {
    const snapCommands = this.commands.map((x) => ({ ...x }));
    const snapRequests = this.crawlRequests.map((x) => ({ ...x }));
    const snapNotifications = this.notifications.map((x) => ({ ...x }));
    const snapOutbox = this.registryOutbox.map((x) => ({ ...x }));
    const snapState = new Map(this.runtimeState);
    const snapRegistry = new Map(Array.from(this.postRegistry.entries(), ([k, v]) => [k, { ...v }]));
    const snapSnapshots = new Map(Array.from(this.postSnapshots.entries(), ([k, v]) => [k, { ...v }]));
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
      this.registryOutbox = snapOutbox;
      this.runtimeState = snapState;
      this.postRegistry = snapRegistry;
      this.postSnapshots = snapSnapshots;
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
        if (norm.includes("WHEN EXISTS ( SELECT 1 FROM crawl_requests WHERE status = 'running'") && norm.endsWith("AS queued")) {
          if (db.failSignalQuery) {
            throw new Error("Simulated D1 signal outage");
          }
          const [nowIso] = this.args;
          const hasActiveRunning = db.crawlRequests.some(
            (r) => r.status === "running" && (!r.lease_expires_at || String(r.lease_expires_at) >= String(nowIso))
          );
          if (hasActiveRunning) return { queued: 0 };

          const hasPendingOutbox = db.registryOutbox.some((o) =>
            ["pending", "error", "processing", "done"].includes(o.status)
          );
          if (hasPendingOutbox) return { queued: 0 };

          const hasUnfinishedRegistryStatus = (statusVal) =>
            db.crawlRequests
              .filter((r) => r.status === statusVal)
              .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
              .slice(0, 50)
              .some((r) => {
                if (!r.result_summary_json) return false;
                try {
                  const parsed = JSON.parse(r.result_summary_json);
                  const rs = String(parsed?.registry_sync_status || "").toLowerCase();
                  return rs === "pending" || rs === "error";
                } catch {
                  return false;
                }
              });
          if (hasUnfinishedRegistryStatus("completed") || hasUnfinishedRegistryStatus("failed")) {
            return { queued: 0 };
          }

          const hasEligibleThreadsQueued = db.crawlRequests.some(
            (r) =>
              r.status === "queued" &&
              String(r.platform || "").trim().toLowerCase() === "threads" &&
              Number(r.attempts ?? 0) < Number(r.max_attempts ?? 3)
          );
          return { queued: hasEligibleThreadsQueued ? 1 : 0 };
        }
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
        if (
          norm.startsWith("SELECT status, notified_at FROM crawl_requests WHERE id = ?") ||
          norm.startsWith("SELECT id, status, notified_at, result_summary_json FROM crawl_requests WHERE id = ?") ||
          norm.startsWith("SELECT id, status, claimed_by, notified_at, result_summary_json FROM crawl_requests WHERE id = ?") ||
          norm.startsWith("SELECT status, result_summary_json FROM crawl_requests WHERE id = ?")
        ) {
          const row = db.crawlRequests.find((c) => c.id === this.args[0]);
          return row
            ? {
              id: row.id,
              status: row.status,
              claimed_by: row.claimed_by,
              notified_at: row.notified_at,
              result_summary_json: row.result_summary_json,
            }
            : null;
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
        if (norm.startsWith("SELECT id FROM crawl_requests WHERE status = 'running'")) {
          const [nowIso] = this.args;
          const active = db.crawlRequests.find(
            (r) => r.status === "running" && (!r.lease_expires_at || String(r.lease_expires_at) >= String(nowIso))
          );
          return active ? { id: active.id } : null;
        }
        if (norm.startsWith("SELECT id FROM registry_sync_outbox WHERE status IN ('pending', 'error', 'processing', 'done')")) {
          const hit = db.registryOutbox.find((o) => ["pending", "error", "processing", "done"].includes(o.status));
          return hit ? { id: hit.id } : null;
        }
        if (norm.startsWith("SELECT 1 AS blocked FROM ( SELECT result_summary_json FROM crawl_requests")) {
          const blocked = db.crawlRequests
            .filter((r) => ["completed", "failed"].includes(r.status))
            .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
            .slice(0, 50)
            .some((r) => {
              if (!r.result_summary_json) return false;
              try {
                const p = JSON.parse(r.result_summary_json);
                const rs = String(p?.registry_sync_status || "").toLowerCase();
                return rs === "pending" || rs === "error";
              } catch {
                return false;
              }
            });
          return blocked ? { blocked: 1 } : null;
        }
        if (norm.includes("FROM crawl_requests WHERE status = 'queued' AND lower(trim(platform)) = 'threads'")) {
          const row = db.crawlRequests
            .filter(
              (c) =>
                c.status === "queued" &&
                String(c.platform || "").trim().toLowerCase() === "threads" &&
                Number(c.attempts ?? 0) < Number(c.max_attempts ?? 3)
            )
            .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))[0];
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
        if (norm.includes("FROM market_post_registry")) {
          if (db.failRegistry) throw new Error("Simulated D1 registry outage");
        }
        if (norm.startsWith("SELECT COUNT(*) AS cnt FROM market_post_registry")) {
          return { cnt: db.postRegistry.size };
        }
        if (norm.startsWith("SELECT * FROM market_post_registry WHERE dedup_key = ?")) {
          const row = db.postRegistry.get(this.args[0]);
          return row ? { ...row } : null;
        }
        if (norm.startsWith("SELECT dedup_key FROM market_post_registry WHERE dedup_key = ?")) {
          const row = db.postRegistry.get(this.args[0]);
          return row ? { dedup_key: row.dedup_key } : null;
        }
        if (norm.startsWith("SELECT * FROM market_post_registry WHERE platform = ? AND canonical_url = ?")) {
          const [plat, cUrl] = this.args;
          for (const row of db.postRegistry.values()) {
            if (row.platform === plat && row.canonical_url === cUrl) return { ...row };
          }
          return null;
        }
        if (norm.startsWith("SELECT * FROM market_post_registry WHERE platform = ? AND post_id = ?")) {
          const [plat, postId] = this.args;
          for (const row of db.postRegistry.values()) {
            if (row.platform === plat && row.post_id === postId) return { ...row };
          }
          return null;
        }
        if (norm.includes("FROM market_post_snapshots")) {
          if (db.failRegistry) throw new Error("Simulated D1 registry outage");
        }
        if (norm.startsWith("SELECT id FROM market_post_snapshots WHERE snapshot_idempotency_key = ?")) {
          const [idempKey, newKey, runFlag, runId] = this.args;
          if (db.postSnapshots.has(idempKey)) {
            return { id: db.postSnapshots.get(idempKey).id };
          }
          if (runFlag) {
            for (const s of db.postSnapshots.values()) {
              if (s.dedup_key === newKey && s.run_id === runId) {
                return { id: s.id };
              }
            }
          }
          return null;
        }
        if (norm.startsWith("SELECT id FROM market_post_snapshots WHERE (dedup_key = ?")) {
          const [dedupKey, postIdFilter, plat, postIdVal, runId] = this.args;
          for (const s of db.postSnapshots.values()) {
            const matchKey = s.dedup_key === dedupKey || (postIdFilter && s.platform === plat && s.post_id === postIdVal);
            if (matchKey && s.run_id === runId) {
              return { id: s.id };
            }
          }
          return null;
        }
        if (norm.startsWith("SELECT id FROM market_post_snapshots WHERE dedup_key = ? AND run_id = ?")) {
          const [dedupKey, runId, measAt] = this.args;
          for (const s of db.postSnapshots.values()) {
            if (s.dedup_key === dedupKey && s.run_id === runId && String(s.measured_at || "") === String(measAt || "")) {
              return { id: s.id };
            }
          }
          return null;
        }
        if (norm.startsWith("SELECT COUNT(*) AS cnt FROM registry_sync_outbox")) {
          const [reqId] = this.args;
          const matchesStatus = norm.includes("status IN ('pending', 'error')")
            ? (o) => ["pending", "error"].includes(o.status)
            : () => true;
          return {
            cnt: db.registryOutbox.filter((o) => o.request_id === reqId && matchesStatus(o)).length,
          };
        }
        return null;
      },
      async all() {
        if (norm.startsWith("SELECT * FROM market_post_snapshots WHERE dedup_key = ?")) {
          const [dedupKey] = this.args;
          if (db.failRegistry) throw new Error("Simulated D1 registry outage");
          return {
            results: [...db.postSnapshots.values()].filter((s) => s.dedup_key === dedupKey).map((s) => ({ ...s })),
          };
        }
        if (norm.startsWith("SELECT * FROM registry_sync_outbox WHERE request_id = ?")) {
          const reqId = this.args[0];
          const staleCutoff = this.args.length > 1 ? String(this.args[1] || "") : null;
          return {
            results: db.registryOutbox
              .filter(
                (o) =>
                  o.request_id === reqId &&
                  (["pending", "error"].includes(o.status) ||
                    (staleCutoff !== null && o.status === "processing" && String(o.updated_at || "") <= staleCutoff))
              )
              .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
              .map((o) => ({ ...o })),
          };
        }
        if (
          norm.startsWith("SELECT id, status, last_error, updated_at FROM registry_sync_outbox WHERE request_id = ?") ||
          norm.startsWith("SELECT status FROM registry_sync_outbox WHERE request_id = ?")
        ) {
          const [reqId] = this.args;
          return {
            results: db.registryOutbox
              .filter((o) => o.request_id === reqId)
              .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
              .map((o) => ({ ...o })),
          };
        }
        if (norm.startsWith("SELECT DISTINCT request_id FROM registry_sync_outbox")) {
          const seenReqs = [];
          const set = new Set();
          const hasStaleParam = norm.includes("status = 'processing'");
          const staleCutoff = hasStaleParam ? String(this.args[0] || "") : null;
          const limit = Number((hasStaleParam ? this.args[1] : this.args[0]) || 10);
          const ordered = db.registryOutbox
            .filter(
              (o) =>
                o.request_id &&
                (["pending", "error", "done"].includes(o.status) ||
                  (staleCutoff !== null && o.status === "processing" && String(o.updated_at || "") <= staleCutoff))
            )
            .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
          for (const o of ordered) {
            if (!set.has(o.request_id)) {
              set.add(o.request_id);
              seenReqs.push({ request_id: o.request_id });
            }
          }
          return { results: seenReqs.slice(0, limit) };
        }
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
        if (norm.includes("FROM crawl_requests WHERE platform = ? AND lower(trim(keywords)) = ?")) {
          const [plat, kw, cnt] = this.args;
          return {
            results: db.crawlRequests
              .filter(
                (c) =>
                  c.platform === plat &&
                  String(c.keywords || "").trim().toLowerCase() === kw &&
                  c.requested_count === cnt &&
                  ["queued", "running"].includes(c.status)
              )
              .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
              .slice(0, 10)
              .map((c) => ({ ...c })),
          };
        }
        if (norm.includes("FROM crawl_requests WHERE status IN ('queued', 'running')")) {
          return {
            results: db.crawlRequests
              .filter((c) => c.status === "queued" || c.status === "running")
              .map((c) => ({ ...c }))
              .sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || ""))),
          };
        }
        if (norm.includes("FROM crawl_requests")) {
          return { results: [...db.crawlRequests].reverse().slice(0, 40) };
        }
        return { results: [] };
      },
      async run() {
        db.writeOps += 1;
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
        if (norm.includes("INTO market_post_registry") || norm.includes("UPDATE market_post_registry") || norm.includes("market_post_snapshots")) {
          if (db.failRegistry) throw new Error("Simulated D1 registry outage");
        }
        if (norm.startsWith("INSERT OR IGNORE INTO market_post_registry")) {
          const [
            dedup_key, platform, post_id, post_pk, canonical_url, content_hash,
            first_run_id, last_run_id, first_seen_at, last_seen_at,
            latest_views_display, latest_views_precision, latest_views_source,
            latest_views_measured_at, latest_views_evidence_url, latest_metrics_json
          ] = this.args;
          if (typeof db.onBeforeRegistryInsert === "function") {
            await db.onBeforeRegistryInsert(dedup_key);
          }
          if (db.postRegistry.has(dedup_key)) {
            return { meta: { changes: 0 } };
          }
          if (canonical_url) {
            for (const r of db.postRegistry.values()) {
              if (r.platform === platform && r.canonical_url === canonical_url) {
                return { meta: { changes: 0 } };
              }
            }
          }
          db.postRegistry.set(dedup_key, {
            dedup_key,
            platform,
            post_id,
            post_pk,
            canonical_url,
            content_hash,
            first_run_id,
            last_run_id,
            first_seen_at,
            last_seen_at,
            snapshot_count: 1,
            latest_views_display,
            latest_views_precision,
            latest_views_source,
            latest_views_measured_at,
            latest_views_evidence_url,
            latest_metrics_json,
          });
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE market_post_registry SET dedup_key = ?")) {
          const [newKey, postId, postPk, oldKey] = this.args;
          const row = db.postRegistry.get(oldKey);
          if (!row || (newKey !== oldKey && db.postRegistry.has(newKey))) {
            return { meta: { changes: 0 } };
          }
          db.postRegistry.delete(oldKey);
          row.dedup_key = newKey;
          if (postId) row.post_id = postId;
          if (postPk) row.post_pk = postPk;
          db.postRegistry.set(newKey, row);
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE market_post_registry SET snapshot_count = COALESCE((SELECT COUNT(*)")) {
          const [dedupKey] = this.args;
          const row = db.postRegistry.get(dedupKey);
          if (!row) return { meta: { changes: 0 } };
          row.snapshot_count = Math.max(Number(row.snapshot_count || 1), db._countSnaps(dedupKey));
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE market_post_registry SET canonical_url = COALESCE(canonical_url, ?)")) {
          if (norm.includes("first_seen_at = COALESCE(?, first_seen_at)")) {
            if (this.args.length === 6) {
              // Older late snapshot backfill update
              const [cUrl, postId, postPk, contentHash, fsArg, dedupKey] = this.args;
              const row = db.postRegistry.get(dedupKey);
              if (!row) return { meta: { changes: 0 } };
              if (!row.canonical_url && cUrl) row.canonical_url = cUrl;
              if (!row.post_id && postId) row.post_id = postId;
              if (!row.post_pk && postPk) row.post_pk = postPk;
              if (!row.content_hash && contentHash) row.content_hash = contentHash;
              if (fsArg && (!row.first_seen_at || db._parseObsTs(fsArg) < db._parseObsTs(row.first_seen_at))) row.first_seen_at = fsArg;
              row.snapshot_count = Math.max(1, db._countSnaps(dedupKey));
              return { meta: { changes: 1 } };
            }
            // Merge URL -> post_id case (4 args)
            const [cUrl, postPk, fsArg, dedupKey] = this.args;
            const row = db.postRegistry.get(dedupKey);
            if (!row) return { meta: { changes: 0 } };
            if (!row.canonical_url && cUrl) row.canonical_url = cUrl;
            if (!row.post_pk && postPk) row.post_pk = postPk;
            if (fsArg && (!row.first_seen_at || db._parseObsTs(fsArg) < db._parseObsTs(row.first_seen_at))) row.first_seen_at = fsArg;
            row.snapshot_count = Math.max(1, db._countSnaps(dedupKey));
            return { meta: { changes: 1 } };
          }
          // Existing duplicate or rejected snapshot metadata backfill (5 args)
          const [cUrl, postId, postPk, contentHash, dedupKey] = this.args;
          const row = db.postRegistry.get(dedupKey);
          if (!row) return { meta: { changes: 0 } };
          if (!row.canonical_url && cUrl) row.canonical_url = cUrl;
          if (!row.post_id && postId) row.post_id = postId;
          if (!row.post_pk && postPk) row.post_pk = postPk;
          if (!row.content_hash && contentHash) row.content_hash = contentHash;
          row.snapshot_count = Math.max(1, db._countSnaps(dedupKey));
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE market_post_registry SET")) {
          const [
            cUrl, postId, postPk, contentHash, runId,
            fsArg, lsArg,
            vDisp, vPrec, vSrc,
            vmArg,
            vEv, latestMetricsJson, dedupKey
          ] = this.args;
          const row = db.postRegistry.get(dedupKey);
          if (!row) return { meta: { changes: 0 } };
          if (cUrl) row.canonical_url = cUrl;
          if (postId) row.post_id = postId;
          if (postPk) row.post_pk = postPk;
          if (contentHash) row.content_hash = contentHash;
          if (runId) row.last_run_id = runId;
          if (fsArg && (!row.first_seen_at || db._parseObsTs(fsArg) < db._parseObsTs(row.first_seen_at))) row.first_seen_at = fsArg;
          if (lsArg && (!row.last_seen_at || db._parseObsTs(lsArg) >= db._parseObsTs(row.last_seen_at))) row.last_seen_at = lsArg;
          row.snapshot_count = Math.max(1, db._countSnaps(dedupKey));
          if (vDisp) row.latest_views_display = vDisp;
          if (vPrec) row.latest_views_precision = vPrec;
          if (vSrc) row.latest_views_source = vSrc;
          if (vmArg && (!row.latest_views_measured_at || db._parseObsTs(vmArg) >= db._parseObsTs(row.latest_views_measured_at))) row.latest_views_measured_at = vmArg;
          if (vEv) row.latest_views_evidence_url = vEv;
          row.latest_metrics_json = latestMetricsJson;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("DELETE FROM market_post_registry WHERE dedup_key = ?")) {
          const existed = db.postRegistry.delete(this.args[0]);
          return { meta: { changes: existed ? 1 : 0 } };
        }
        if (norm.startsWith("INSERT OR IGNORE INTO market_post_snapshots")) {
          db.snapshotInsertAttempts += 1;
          if (db.failRegistryOnSnapshotCallsRemaining > 0) {
            db.failRegistryOnSnapshotCallsRemaining -= 1;
            if (db.failRegistryOnSnapshotCallsRemaining === 0) {
              throw new Error("Simulated mid-batch D1 snapshot outage");
            }
          }
          const [
            id, snapshot_idempotency_key, dedup_key, platform, post_id, run_id, measured_at,
            likes_count, comments_count, reposts_count, quotes_count, shares_count, views_count,
            views_display, views_precision, views_source, views_evidence_url, classification, created_at
          ] = this.args;
          if (norm.includes("WHERE EXISTS ( SELECT 1 FROM market_post_registry")) {
            const gKey = this.args[19];
            const gRun = this.args[20];
            const gFirst = this.args[21];
            const regRow = db.postRegistry.get(gKey);
            if (!regRow || String(regRow.first_run_id || "") !== String(gRun || "") || String(regRow.first_seen_at || "") !== String(gFirst || "")) {
              return { meta: { changes: 0 } };
            }
          }
          if (db.postSnapshots.has(snapshot_idempotency_key)) {
            return { meta: { changes: 0 } };
          }
          db.postSnapshots.set(snapshot_idempotency_key, {
            id, snapshot_idempotency_key, dedup_key, platform, post_id, run_id, measured_at,
            likes_count, comments_count, reposts_count, quotes_count, shares_count, views_count,
            views_display, views_precision, views_source, views_evidence_url, classification, created_at
          });
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE market_post_snapshots SET dedup_key = ?")) {
          const [newKey, postId, newIdemp, id] = this.args;
          let foundEntry = null;
          for (const [k, snap] of db.postSnapshots.entries()) {
            if (snap.id === id) {
              foundEntry = [k, snap];
              break;
            }
          }
          if (!foundEntry) return { meta: { changes: 0 } };
          const [oldIdemp, snap] = foundEntry;
          db.postSnapshots.delete(oldIdemp);
          snap.dedup_key = newKey;
          if (postId) snap.post_id = postId;
          snap.snapshot_idempotency_key = newIdemp;
          db.postSnapshots.set(newIdemp, snap);
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("DELETE FROM market_post_snapshots WHERE id = ?")) {
          const targetId = this.args[0];
          for (const [k, snap] of db.postSnapshots.entries()) {
            if (snap.id === targetId) {
              db.postSnapshots.delete(k);
              return { meta: { changes: 1 } };
            }
          }
          return { meta: { changes: 0 } };
        }
        if (norm.startsWith("INSERT INTO registry_sync_outbox") && norm.includes("SELECT ?, ?, ?, ?, ?, 'pending'")) {
          const [
            id, request_id, run_id, crawled_at, payload_json, created_at, updated_at,
            guardReqId, guardOwner, guardAttempt, guardNowIso
          ] = this.args;
          const eligible = db.crawlRequests.some(
            (c) =>
              c.id === guardReqId &&
              c.status === "running" &&
              c.claimed_by === guardOwner &&
              c.attempts === guardAttempt &&
              (!c.lease_expires_at || c.lease_expires_at >= guardNowIso)
          );
          if (!eligible) return { meta: { changes: 0 } };
          db.registryOutbox.push({
            id, request_id, run_id, crawled_at, payload_json,
            status: "pending", attempts: 0, last_error: null, created_at, updated_at,
          });
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("INSERT INTO registry_sync_outbox")) {
          const [id, request_id, run_id, crawled_at, payload_json, created_at, updated_at] = this.args;
          db.registryOutbox.push({
            id, request_id, run_id, crawled_at, payload_json,
            status: "pending", attempts: 0, last_error: null, created_at, updated_at,
          });
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("DELETE FROM registry_sync_outbox WHERE request_id = ? AND status = 'done'")) {
          const before = db.registryOutbox.length;
          const [reqId] = this.args;
          db.registryOutbox = db.registryOutbox.filter((o) => !(o.request_id === reqId && o.status === "done"));
          return { meta: { changes: before - db.registryOutbox.length } };
        }
        if (norm.startsWith("DELETE FROM registry_sync_outbox WHERE id = ?")) {
          const before = db.registryOutbox.length;
          db.registryOutbox = db.registryOutbox.filter((o) => o.id !== this.args[0]);
          return { meta: { changes: before - db.registryOutbox.length } };
        }
        if (norm.startsWith("UPDATE registry_sync_outbox SET status = 'processing'")) {
          const [updated_at, id, staleCutoff] = this.args;
          const row = db.registryOutbox.find(
            (o) =>
              o.id === id &&
              (["pending", "error"].includes(o.status) ||
                (o.status === "processing" && String(o.updated_at || "") <= String(staleCutoff || "")))
          );
          if (!row) return { meta: { changes: 0 } };
          row.status = "processing";
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE registry_sync_outbox SET status = 'done'")) {
          const [last_error, updated_at, id] = this.args;
          const row = db.registryOutbox.find((o) => o.id === id && (!norm.includes("AND status = 'processing'") || o.status === "processing"));
          if (!row) return { meta: { changes: 0 } };
          row.status = "done";
          row.last_error = last_error;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE registry_sync_outbox SET status = 'error'")) {
          const [last_error, updated_at, id] = this.args;
          const row = db.registryOutbox.find((o) => o.id === id && (!norm.includes("AND status = 'processing'") || o.status === "processing"));
          if (!row) return { meta: { changes: 0 } };
          row.status = "error";
          row.attempts = Number(row.attempts || 0) + 1;
          row.last_error = last_error;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET result_summary_json = ?, updated_at = ? WHERE id = ? AND status = ? AND attempts = ?")) {
          const [result_summary_json, updated_at, id, status, attempts] = this.args;
          const row = db.crawlRequests.find(
            (c) => c.id === id && c.status === status && Number(c.attempts || 0) === Number(attempts)
          );
          if (!row) return { meta: { changes: 0 } };
          row.result_summary_json = result_summary_json;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET result_summary_json = ?, updated_at = ? WHERE id = ? AND status = 'running'")) {
          const [result_summary_json, updated_at, id, claimed_by, attempts, nowIso] = this.args;
          const row = db.crawlRequests.find(
            (c) =>
              c.id === id &&
              c.status === "running" &&
              c.claimed_by === claimed_by &&
              Number(c.attempts) === Number(attempts) &&
              (!nowIso || !c.lease_expires_at || String(c.lease_expires_at) >= String(nowIso))
          );
          if (!row) return { meta: { changes: 0 } };
          row.result_summary_json = result_summary_json;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (
          norm.startsWith("UPDATE crawl_requests SET result_summary_json = ?, updated_at = ? WHERE id = ?") ||
          norm.startsWith("UPDATE crawl_requests SET result_summary_json = ? WHERE id = ?")
        ) {
          const result_summary_json = this.args[0];
          const id = this.args[this.args.length - 1];
          const row = db.crawlRequests.find((c) => c.id === id);
          if (!row) return { meta: { changes: 0 } };
          row.result_summary_json = result_summary_json;
          if (this.args.length === 3) row.updated_at = this.args[1];
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
          if (typeof db.onBeforeClaimUpdate === "function") {
            await db.onBeforeClaimUpdate(this.args);
          }
          const [claimed_by, claimed_at, lease_expires_at, updated_at, id, nowIsoGuard] = this.args;
          if (norm.includes("AND lower(trim(platform)) = 'threads'")) {
            const hasActiveRun = db.crawlRequests.some(
              (r) => r.status === "running" && (!r.lease_expires_at || String(r.lease_expires_at) >= String(nowIsoGuard))
            );
            const hasOutbox = db.registryOutbox.some((o) => ["pending", "error", "processing", "done"].includes(o.status));
            if (hasActiveRun || hasOutbox) {
              return { meta: { changes: 0 } };
            }
          }
          const row = db.crawlRequests.find(
            (c) =>
              c.id === id &&
              c.status === "queued" &&
              (!norm.includes("AND lower(trim(platform)) = 'threads'") ||
                (String(c.platform || "").trim().toLowerCase() === "threads" &&
                  Number(c.attempts ?? 0) < Number(c.max_attempts ?? 3)))
          );
          if (!row) return { meta: { changes: 0 } };
          row.status = "running";
          row.claimed_by = claimed_by;
          row.claimed_at = claimed_at;
          row.lease_expires_at = lease_expires_at;
          row.attempts = Number(row.attempts || 0) + 1;
          row.updated_at = updated_at;
          return { meta: { changes: 1 } };
        }
        if (norm.startsWith("UPDATE crawl_requests SET status = 'queued', claimed_by = NULL, claimed_at = NULL, lease_expires_at = NULL, error_message = ?")) {
          const [error_message, updated_at, id, claimed_by, attempts, nowIso] = this.args;
          const row = db.crawlRequests.find(
            (c) =>
              c.id === id &&
              c.status === "running" &&
              c.claimed_by === claimed_by &&
              Number(c.attempts) === Number(attempts) &&
              (!nowIso || !c.lease_expires_at || String(c.lease_expires_at) >= String(nowIso))
          );
          if (!row) return { meta: { changes: 0 } };
          row.status = "queued";
          row.claimed_by = null;
          row.claimed_at = null;
          row.lease_expires_at = null;
          row.error_message = error_message;
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
        if (norm.startsWith("UPDATE crawl_requests SET status = 'failed', error_message = 'Execution lease expired before completion'")) {
          const [updated_at, nowIso] = this.args;
          let changes = 0;
          for (const row of db.crawlRequests) {
            if (row.status === "running" && row.lease_expires_at && row.lease_expires_at < nowIso && row.attempts >= row.max_attempts) {
              row.status = "failed";
              row.error_message = "Execution lease expired before completion";
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
assert.ok(v3SummaryBody.includes("Quét 14"));
assert.ok(v3SummaryBody.includes("Mới đạt chuẩn: 8") || v3SummaryBody.includes("Đạt chuẩn 8"));
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

    // 14b: Reply Button "📊 Phân tích thị trường" -> 6 V3.2 structured bullet groups (<= 2500 chars) + Inline sub-actions
    const mktMsg = await sendUserText(8002, "📊 Phân tích thị trường");
    assert.ok(mktMsg.text.length <= 2500, `Expected <= 2500 chars, got ${mktMsg.text.length}`);
    for (const sec of [
      "📊 Chất lượng crawl",
      "🔥 Trend thị trường",
      "🆕 Trend mới & vòng đời",
      "💰 Tín hiệu Affiliate",
      "📈 Biến động so với lượt trước",
      "🎯 Hành động đề xuất",
    ]) {
      assert.ok(mktMsg.text.includes(sec), `Missing V3.2 section: ${sec}`);
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
    assert.ok(notifText.length <= 2500, `Auto notification exceeded 2500 chars: ${notifText.length}`);
    for (const sec of [
      "📊 Chất lượng crawl",
      "🔥 Trend thị trường",
      "🆕 Trend mới & vòng đời",
      "💰 Tín hiệu Affiliate",
      "📈 Biến động so với lượt trước",
      "🎯 Hành động đề xuất",
    ]) {
      assert.ok(notifText.includes(sec), `Direct D1 auto notification missing section: ${sec}`);
    }
    assert.ok(notifText.includes("7/50 (Chưa đạt mục tiêu)"), "Must show 7/50 Chưa đạt mục tiêu");
    assert.ok(notifText.includes("Bài trùng lịch sử: 9 | Cập nhật tương tác: 2"));
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

// 16. Data Contract V3.2: 171 Seed, 2-Laptop Snapshot Idempotency, Concurrent Race Single-Winner, Lease 409 Guard, Fail-Closed 503, >500 Batch & 6-Group <=2500 Telegram
{
  const m0004Path = path.join(__dirname, "../cloudflare-worker/migrations/0004_seed_post_identity_index_171.sql");
  assert.ok(fs.existsSync(m0004Path), "Missing migration 0004_seed_post_identity_index_171.sql");
  const m0004Sql = fs.readFileSync(m0004Path, "utf8");
  const regInserts = (m0004Sql.match(/INSERT (?:OR IGNORE )?INTO market_post_registry/g) || []).length;
  const snapInserts = (m0004Sql.match(/INSERT (?:OR IGNORE )?INTO market_post_snapshots/g) || []).length;
  assert.equal(regInserts, 171, `Expected 171 seed registry inserts, got ${regInserts}`);
  assert.equal(snapInserts, 171, `Expected 171 seed snapshot inserts, got ${snapInserts}`);
  assert.ok(m0004Sql.includes("ON CONFLICT(dedup_key) DO NOTHING"));
  assert.ok(m0004Sql.includes("ON CONFLICT(snapshot_idempotency_key) DO NOTHING"));

  const env16 = {
    DB: new MockD1Database(),
    APP_VIDEO_API_TOKEN: "test-token-16",
    TELEGRAM_OWNER_ID: "999016",
    TELEGRAM_BOT_TOKEN_VIDEO: "tg-bot-token",
    TELEGRAM_WEBHOOK_SECRET: "tg-webhook-secret",
  };
  const tgCalls16 = [];
  const prevFetch16 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.includes("api.telegram.org")) {
      tgCalls16.push({ url: sUrl, body: init.body });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 916 } }), { status: 200 });
    }
    throw new Error("Unexpected fetch: " + sUrl);
  };

  try {
    // 16a: Two laptops syncing the exact same crawl observation create ONLY 1 snapshot
    const laptopAHeaders = {
      Authorization: "Bearer test-token-16",
      "Content-Type": "application/json",
      "X-Client-Id": "laptop-a",
    };
    const laptopBHeaders = {
      Authorization: "Bearer test-token-16",
      "Content-Type": "application/json",
      "X-Client-Id": "laptop-b",
    };
    const sameCrawlPayload = {
      run_id: "run_shared_20261127",
      crawled_at: "2026-11-27T10:15:00Z",
      items: [
        {
          platform: "threads",
          post_id: "SHARED_001",
          canonical_url: "https://threads.net/@myqnam/post/SHARED_001",
          views: 513000,
          views_display: "513K",
          views_precision: "display_approx",
          views_source: "threads_web_browser",
          views_measured_at: "2026-11-27T10:15:00Z",
          views_evidence_url: "https://threads.net/@myqnam/post/SHARED_001",
          likes: 1200,
          comments: 95,
          shares: 88,
          reposts: 41,
        },
      ],
    };
    const syncA = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify(sameCrawlPayload),
      }),
      env16
    );
    const syncAJson = await syncA.json();
    assert.equal(syncAJson.new_count, 1);
    assert.equal(syncAJson.new_qualified_count, 1);
    assert.equal(syncAJson.snapshots_created, 1);

    const syncB = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: laptopBHeaders,
        body: JSON.stringify(sameCrawlPayload),
      }),
      env16
    );
    const syncBJson = await syncB.json();
    assert.equal(syncBJson.new_count, 0);
    assert.equal(syncBJson.snapshots_created, 0);
    assert.equal(syncBJson.snapshots_deduped, 1);
    assert.equal(env16.DB.postRegistry.get("threads:post:SHARED_001").snapshot_count, 1);
    assert.equal(env16.DB.postSnapshots.size, 1);

    // 16b: Concurrent Promise.all race on same new post -> ONLY ONE task wins NEW
    let raceBarrierHit = 0;
    let releaseBarrier;
    const barrierPromise = new Promise((res) => { releaseBarrier = res; });
    env16.DB.onBeforeRegistryInsert = async (key) => {
      if (key === "threads:post:RACE_POST_1") {
        raceBarrierHit += 1;
        if (raceBarrierHit === 1) {
          await Promise.race([barrierPromise, new Promise((r) => setTimeout(r, 30))]);
        } else {
          releaseBarrier();
        }
      }
    };
    const racePost = {
      platform: "threads",
      post_id: "RACE_POST_1",
      canonical_url: "https://threads.net/@race/post/RACE_POST_1",
      views: 9200,
      views_display: "9.2K",
      views_precision: "rounded_display",
      views_source: "threads_web_browser",
      views_measured_at: "2026-11-27T11:00:00Z",
      views_evidence_url: "https://threads.net/@race/post/RACE_POST_1",
      likes: 340,
      shares: 12,
      reposts: 5,
    };
    const [raceRes1, raceRes2] = await Promise.all([
      worker.fetch(
        new Request("https://example.workers.dev/api/app/market/dedup/record", {
          method: "POST",
          headers: laptopAHeaders,
          body: JSON.stringify({ run_id: "race_run_a", crawled_at: "2026-11-27T11:00:00Z", items: [racePost] }),
        }),
        env16
      ),
      worker.fetch(
        new Request("https://example.workers.dev/api/app/market/dedup/record", {
          method: "POST",
          headers: laptopBHeaders,
          body: JSON.stringify({ run_id: "race_run_b", crawled_at: "2026-11-27T11:00:05Z", items: [racePost] }),
        }),
        env16
      ),
    ]);
    env16.DB.onBeforeRegistryInsert = null;
    const rj1 = await raceRes1.json();
    const rj2 = await raceRes2.json();
    assert.equal(rj1.new_count + rj2.new_count, 1, "Exact single winner allowed for concurrent NEW claim");
    assert.equal(rj1.new_qualified_count + rj2.new_qualified_count, 1);

    // 16c: Expired lease on /complete and /dedup/record returns 409 with ZERO registry writes
    const expCrwRes = await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({ platform: "threads", requested_count: 20, keywords: "expired_lease" }),
      }),
      env16
    );
    const expCrw = (await expCrwRes.json()).request;
    const expRow = env16.DB.crawlRequests.find((c) => c.id === expCrw.id);
    expRow.status = "running";
    expRow.claimed_by = "muse-worker-old";
    expRow.attempts = 1;
    expRow.lease_expires_at = new Date(Date.now() - 60000).toISOString();

    const expiredComplete = await worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${expCrw.id}/complete`, {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({
          claimed_by: "muse-worker-old",
          attempt: 1,
          status: "completed",
          result_summary: {
            run_id: "run_expired_1",
            items: [{ platform: "threads", post_id: "EXPIRED_SHOULD_NOT_WRITE", views: 5000 }],
          },
        }),
      }),
      env16
    );
    assert.equal(expiredComplete.status, 409);
    assert.equal(env16.DB.postRegistry.has("threads:post:EXPIRED_SHOULD_NOT_WRITE"), false, "Expired lease must NOT write registry");

    expRow.status = "running";
    expRow.claimed_by = "muse-worker-old";
    expRow.attempts = 1;
    expRow.lease_expires_at = new Date(Date.now() - 60000).toISOString();
    const expiredDedup = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({
          request_id: expCrw.id,
          claimed_by: "muse-worker-old",
          attempt: 1,
          run_id: "run_expired_2",
          items: [{ platform: "threads", post_id: "EXPIRED_RECORD_NO_WRITE", views: 5000 }],
        }),
      }),
      env16
    );
    assert.equal(expiredDedup.status, 409);
    assert.equal(env16.DB.postRegistry.has("threads:post:EXPIRED_RECORD_NO_WRITE"), false, "Expired /dedup/record must NOT write registry");

    // 16d: URL-first post_id discovery promotes dedup_key without creating a duplicate row
    await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({
          run_id: "run_url_only",
          crawled_at: "2026-11-27T08:00:00Z",
          items: [{ platform: "threads", canonical_url: "https://threads.net/@promo/post/PROMO99", views: 1000, likes: 10 }],
        }),
      }),
      env16
    );
    assert.ok(env16.DB.postRegistry.has("threads:url:https://threads.net/@promo/post/PROMO99"));
    const promoRes = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({
          run_id: "run_url_with_id",
          crawled_at: "2026-11-27T10:00:00Z",
          items: [{ platform: "threads", post_id: "PROMO99", canonical_url: "https://threads.net/@promo/post/PROMO99", views: 4500, likes: 55 }],
        }),
      }),
      env16
    );
    const promoJson = await promoRes.json();
    assert.equal(promoJson.new_count, 0, "URL->post_id promotion must NOT double-count as NEW");
    assert.equal(promoJson.updated_count, 1);
    assert.equal(env16.DB.postRegistry.has("threads:url:https://threads.net/@promo/post/PROMO99"), false);
    assert.equal(env16.DB.postRegistry.get("threads:post:PROMO99").snapshot_count, 2);

    // 16e: Over 500 items processed completely in chunked batches without truncation
    const largeBatch = Array.from({ length: 550 }, (_, idx) => ({
      platform: "threads",
      post_id: `BATCH_BIG_${idx}`,
      canonical_url: `https://threads.net/@big/post/BATCH_BIG_${idx}`,
      views: 1500 + idx,
    }));
    const bigRec = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({ run_id: "run_big_550", items: largeBatch }),
      }),
      env16
    );
    const bigRecJson = await bigRec.json();
    assert.equal(bigRecJson.total_processed, 550);
    assert.equal(bigRecJson.new_count, 550);
    const bigCheck = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/check", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({ items: largeBatch }),
      }),
      env16
    );
    const bigCheckJson = await bigCheck.json();
    assert.equal(bigCheckJson.total_checked, 550);
    assert.equal(bigCheckJson.existing_count, 550);

    // 16f: Fail-closed 503 when D1 registry fails (never treated as missing-from-history)
    env16.DB.failRegistry = true;
    const failCheck = await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/check", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({ items: [{ platform: "threads", post_id: "SHARED_001" }] }),
      }),
      env16
    );
    assert.equal(failCheck.status, 503);
    const failCheckJson = await failCheck.json();
    assert.equal(failCheckJson.ok, false);
    assert.equal(failCheckJson.registry_unavailable, true);
    env16.DB.failRegistry = false;

    // 16g: V3.2 Telegram summary enforces <=2500 chars, 6 exact headings, 35/50 + stop_reason + 513K views_display
    const v32Crw = (await (await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests", {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({ platform: "threads", requested_count: 50, keywords: "v32_test" }),
      }),
      env16
    )).json()).request;
    const v32Row = env16.DB.crawlRequests.find((c) => c.id === v32Crw.id);
    v32Row.status = "running";
    v32Row.claimed_by = "muse-v32";
    v32Row.attempts = 1;
    v32Row.requested_by_chat_id = "999016";

    const beforeV32Tg = tgCalls16.length;
    const v32Complete = await worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${v32Crw.id}/complete`, {
        method: "POST",
        headers: laptopAHeaders,
        body: JSON.stringify({
          claimed_by: "muse-v32",
          attempt: 1,
          status: "completed",
          result_summary: {
            contract_version: "3.2",
            run_id: "run_v32_35of50",
            run_status: "partial",
            technical_status: "completed",
            data_quality_status: "dat_mot_phan",
            counters: {
              requested_count: 50,
              scanned_count: 140,
              unique_candidate_count: 120,
              new_count: 40,
              new_qualified_count: 35,
              duplicate_history_count: 50,
              updated_metrics_count: 10,
              missing_views_count: 8,
              unverified_count: 15,
              rejected_count: 10,
              similar_content_count: 4,
              target_achieved: false,
              stop_reason: "sources_exhausted",
            },
            sections: {
              "3_trends": [
                { theme: "Son dưỡng môi không màu", why_hot: "Nhiều creator so sánh độ ẩm 8h", lifecycle_state: "growing" },
                { theme: "Kẹp tóc kim loại Y2K", why_hot: "Bùng nổ clip phối đồ", lifecycle_state: "new" },
              ],
              "4_top_viral": [
                { post: "@myqnam: test son dưỡng 8 tiếng", views: 513000, views_display: "513K", views_precision: "display_approx", likes: 12000, comments: 430 },
              ],
              "7_affiliate_priorities": [
                { product: "Son dưỡng Bioderma Atoderm", priority: "Cao", evidence: "Nhiều bình luận hỏi link dưới bài @myqnam" },
              ],
              "9_growth_tracking": [
                { post: "@sandeal.shopee (Son Romand)", change_summary: "Views 5K → 18K (+260%), Likes 140 → 520" },
              ],
              "10_priority_actions": [
                { action: "Quay clip test son dưỡng 8 tiếng" },
                { action: "Gắn giỏ hàng combo son dưỡng" },
                { action: "Theo dõi thêm hashtag #kệptócY2K" },
                { action: "Hành động thứ 4 không được xuất hiện vượt giới hạn 3" },
              ],
            },
            telegram_summary:
              "📊 Chất lượng crawl\n" +
              "• Chỉ đạt 35/50 bài mới đạt chuẩn do nguồn search chạm giới hạn.\n" +
              "🔥 Trend thị trường\n" +
              "• Son dưỡng giữ ẩm qua đêm được hỏi link mua rất nhiều.\n" +
              "• Kẹp tóc Y2K tăng nhiệt trở lại nhờ outfit thu đông.\n" +
              "• Bullet chi tiết từ Muse được giữ nguyên không bị cắt bớt " + "X".repeat(600) + "\n" +
              "🆕 Trend mới & vòng đời\n" +
              "• [Mới] Kẹp tóc Y2K mới xuất hiện phiên này.\n" +
              "💰 Tín hiệu Affiliate\n" +
              "• Son dưỡng Bioderma 4g có intent mua trực tiếp cao.\n" +
              "📈 Biến động so với lượt trước\n" +
              "• Bài @sandeal.shopee tăng 5K → 18K views.\n" +
              "🎯 Hành động đề xuất\n" +
              "• Quay clip test giữ ẩm 8h để gắn link.",
          },
        }),
      }),
      env16
    );
    assert.equal(v32Complete.status, 200);
    const v32Notif = tgCalls16.slice(beforeV32Tg).find((c) => c.url.includes("/sendMessage"));
    assert.ok(v32Notif, "Expected V3.2 completion Telegram notification");
    const v32Text = JSON.parse(v32Notif.body).text;
    assert.ok(v32Text.length <= 2500, `Expected V3.2 notification <= 2500 chars, got ${v32Text.length}`);
    for (const sec of [
      "📊 Chất lượng crawl",
      "🔥 Trend thị trường",
      "🆕 Trend mới & vòng đời",
      "💰 Tín hiệu Affiliate",
      "📈 Biến động so với lượt trước",
      "🎯 Hành động đề xuất",
    ]) {
      assert.ok(v32Text.includes(sec), `V3.2 notification missing group: ${sec}`);
    }
    assert.ok(v32Text.includes("35/50 (Chưa đạt mục tiêu • stop: sources_exhausted)"));
    assert.ok(v32Text.includes("513K") && !v32Text.includes("513.000"), "Views must show 513K display format, not 513.000");
    assert.ok(v32Text.includes("@sandeal.shopee"));
  } finally {
    globalThis.fetch = prevFetch16;
  }
}

// 17. Data Integrity Hardening (GPT 5c2f1f1 Review):
//     Outbox /complete recovery, strict V3.2 qualification, monotonic late snapshots, atomic snapshot_count,
//     URL->post_id re-sync without duplicate snapshots, and 0004 canonical snapshot_idempotency_key congruence
{
  const env17 = {
    DB: new MockD1Database(),
    APP_VIDEO_API_TOKEN: "test-token-17",
    TELEGRAM_OWNER_ID: "999017",
    TELEGRAM_BOT_TOKEN_VIDEO: "tg-bot-token",
    TELEGRAM_WEBHOOK_SECRET: "tg-webhook-secret",
  };
  const h17 = {
    Authorization: "Bearer test-token-17",
    "Content-Type": "application/json",
    "X-Client-Id": "muse-integrity",
  };
  const tgCalls17 = [];
  const prevFetch17 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.includes("api.telegram.org")) {
      tgCalls17.push({ url: sUrl, body: init.body });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 917 } }), { status: 200 });
    }
    throw new Error("Unexpected fetch: " + sUrl);
  };

  try {
    // 17a: Mid-batch outage on /complete writes registry_sync_outbox + sets registry_sync_status=error;
    //      retry on SAME /complete MUST recover (NOT return already_finalized) and finish notification
    const crw17 = (await (await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests", {
        method: "POST",
        headers: h17,
        body: JSON.stringify({ platform: "threads", requested_count: 150, keywords: "recovery_test" }),
      }),
      env17
    )).json()).request;
    const rRow17 = env17.DB.crawlRequests.find((c) => c.id === crw17.id);
    rRow17.status = "running";
    rRow17.claimed_by = "muse-rec-1";
    rRow17.attempts = 1;
    rRow17.requested_by_chat_id = "999017";

    const recItems150 = Array.from({ length: 150 }, (_, i) => ({
      platform: "threads",
      post_id: `REC_${i}`,
      canonical_url: `https://threads.net/@rec/post/REC_${i}`,
      views: 5000 + i,
      views_display: "5K",
      views_precision: "rounded_display",
      views_source: "threads_web_browser",
      views_measured_at: "2026-11-27T11:30:00Z",
      views_evidence_url: `https://threads.net/@rec/post/REC_${i}`,
      likes: 100 + i,
    }));

    // Fail on the 101st snapshot insert (chunk #2!)
    env17.DB.failRegistryOnSnapshotCallsRemaining = 101;
    const compBody = {
      claimed_by: "muse-rec-1",
      attempt: 1,
      status: "completed",
      result_summary: {
        run_id: "run_rec_150",
        crawled_at: "2026-11-27T11:30:00Z",
        items: recItems150,
      },
    };

    const failComp = await worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${crw17.id}/complete`, {
        method: "POST",
        headers: h17,
        body: JSON.stringify(compBody),
      }),
      env17
    );
    assert.equal(failComp.status, 503);
    const failJson = await failComp.json();
    assert.equal(failJson.recovery_pending, true);
    const unfinishedOutbox17 = env17.DB.registryOutbox.filter((o) => o.status !== "done");
    const doneOutbox17 = env17.DB.registryOutbox.filter((o) => o.status === "done");
    assert.equal(unfinishedOutbox17.length, 1, "Chunk #2 (50 items) must remain durable in registry_sync_outbox");
    assert.equal(unfinishedOutbox17[0].status, "error");
    assert.equal(doneOutbox17.length, 1, "Chunk #1 stays marked done until all chunks finalize");
    const refreshedErrRow = env17.DB.crawlRequests.find((c) => c.id === crw17.id);
    const savedErrSummary = JSON.parse(refreshedErrRow.result_summary_json);
    assert.equal(savedErrSummary.registry_sync_status, "error");
    assert.equal(refreshedErrRow.notified_at, null, "Notification waits until registry recovery succeeds");

    // Retry SAME /complete -> MUST recover chunk #2 instead of skipping with already_finalized!
    const beforeRecTg = tgCalls17.length;
    const retryComp = await worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${crw17.id}/complete`, {
        method: "POST",
        headers: h17,
        body: JSON.stringify(compBody),
      }),
      env17
    );
    assert.equal(retryComp.status, 200);
    const retryJson = await retryComp.json();
    assert.equal(retryJson.already_finalized, false);
    assert.equal(retryJson.recovered_registry_sync, true);
    assert.equal(env17.DB.registryOutbox.length, 0);
    const refreshedOkRow = env17.DB.crawlRequests.find((c) => c.id === crw17.id);
    assert.equal(JSON.parse(refreshedOkRow.result_summary_json).registry_sync_status, "ok");
    assert.equal(retryJson.dedup_stats.total_processed, 150);
    assert.equal(retryJson.dedup_stats.new_qualified_count, 150);
    for (let i = 0; i < 150; i++) {
      assert.ok(env17.DB.postRegistry.has(`threads:post:REC_${i}`), `Missing recovered post REC_${i}`);
    }
    assert.ok(tgCalls17.slice(beforeRecTg).some((c) => c.url.includes("/sendMessage")), "Expected delayed completion notification after recovery");

    // Subsequent retry now sees registry_sync_status=ok and returns already_finalized=true cleanly
    const idempComp = await worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${crw17.id}/complete`, {
        method: "POST",
        headers: h17,
        body: JSON.stringify(compBody),
      }),
      env17
    );
    assert.equal((await idempComp.json()).already_finalized, true);

    // 17b: Scheduled cron drains pending/errored outbox even if caller never retries
    const crwCron = (await (await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests", {
        method: "POST",
        headers: h17,
        body: JSON.stringify({ platform: "threads", requested_count: 10, keywords: "cron_outbox" }),
      }),
      env17
    )).json()).request;
    const cronRow = env17.DB.crawlRequests.find((c) => c.id === crwCron.id);
    cronRow.status = "running";
    cronRow.claimed_by = "muse-cron";
    cronRow.attempts = 1;
    env17.DB.failRegistryOnSnapshotCallsRemaining = 1;
    const failCronComp = await worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${crwCron.id}/complete`, {
        method: "POST",
        headers: h17,
        body: JSON.stringify({
          claimed_by: "muse-cron",
          attempt: 1,
          status: "completed",
          result_summary: {
            run_id: "run_cron_rec",
            items: [{
              platform: "threads",
              post_id: "CRON_REC_1",
              views: 8000,
              views_display: "8K",
              views_precision: "rounded_display",
              views_source: "threads_web_browser",
              views_measured_at: "2026-11-27T11:35:00Z",
              views_evidence_url: "https://threads.net/@c/post/CRON_REC_1",
            }],
          },
        }),
      }),
      env17
    );
    assert.equal(failCronComp.status, 503);
    assert.equal(env17.DB.registryOutbox.length, 1);
    await worker.scheduled({}, env17, {});
    assert.equal(env17.DB.registryOutbox.length, 0);
    const refreshedCronRow = env17.DB.crawlRequests.find((c) => c.id === crwCron.id);
    assert.equal(JSON.parse(refreshedCronRow.result_summary_json).registry_sync_status, "ok");
    assert.ok(env17.DB.postRegistry.has("threads:post:CRON_REC_1"));

    // 17c: Numeric views alone is NOT enough for new_qualified_count (requires all 5 V3.2 fields)
    const qualTestItems = [
      { platform: "threads", post_id: "QUAL_NO_SRC", views: 25000, views_display: "25K", views_precision: "rounded_display", views_measured_at: "2026-11-27T10:00:00Z", views_evidence_url: "https://threads.net/@a/post/1" },
      { platform: "threads", post_id: "QUAL_BAD_SRC", views: 25000, views_display: "25K", views_precision: "rounded_display", views_source: "threads_cli_search", views_measured_at: "2026-11-27T10:00:00Z", views_evidence_url: "https://threads.net/@a/post/2" },
      { platform: "threads", post_id: "QUAL_NO_DISP", views: 25000, views_precision: "rounded_display", views_source: "threads_web_browser", views_measured_at: "2026-11-27T10:00:00Z", views_evidence_url: "https://threads.net/@a/post/3" },
      { platform: "threads", post_id: "QUAL_BAD_PREC", views: 25000, views_display: "25K", views_precision: "guessed", views_source: "threads_web_browser", views_measured_at: "2026-11-27T10:00:00Z", views_evidence_url: "https://threads.net/@a/post/4" },
      { platform: "threads", post_id: "QUAL_NO_MEAS", views: 25000, views_display: "25K", views_precision: "rounded_display", views_source: "threads_web_browser", views_evidence_url: "https://threads.net/@a/post/5" },
      { platform: "threads", post_id: "QUAL_NO_EV", views: 25000, views_display: "25K", views_precision: "rounded_display", views_source: "threads_web_browser", views_measured_at: "2026-11-27T10:00:00Z" },
      { platform: "threads", post_id: "QUAL_FULL_OK", views: 25000, views_display: "25K", views_precision: "rounded_display", views_source: "threads_web_browser", views_measured_at: "2026-11-27T10:00:00Z", views_evidence_url: "https://threads.net/@a/post/QUAL_FULL_OK" },
    ];
    const qualRes = await (await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: h17,
        body: JSON.stringify({ run_id: "run_qual_strict", crawled_at: "2026-11-27T10:00:00Z", items: qualTestItems }),
      }),
      env17
    )).json();
    assert.equal(qualRes.new_count, 7);
    assert.equal(qualRes.new_qualified_count, 1, "Only the single post meeting all 5 V3.2 provenance rules qualifies");
    assert.equal(qualRes.unverified_count, 6);

    // 17d: Late older snapshot MUST NOT overwrite newer latest_metrics_json or last_seen_at; snapshot_count stays atomic
    await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: h17,
        body: JSON.stringify({
          run_id: "run_newer_t2",
          crawled_at: "2026-11-27T12:00:00Z",
          items: [{
            platform: "threads",
            post_id: "LATE_GUARD_1",
            canonical_url: "https://threads.net/@lg/post/LATE_GUARD_1",
            views: 50000,
            views_display: "50K",
            views_precision: "rounded_display",
            views_source: "threads_web_browser",
            views_measured_at: "2026-11-27T12:00:00Z",
            views_evidence_url: "https://threads.net/@lg/post/LATE_GUARD_1",
            likes: 1200,
          }],
        }),
      }),
      env17
    );
    const lateOldRes = await (await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: h17,
        body: JSON.stringify({
          run_id: "run_older_t1",
          crawled_at: "2026-11-25T08:00:00Z",
          items: [{
            platform: "threads",
            post_id: "LATE_GUARD_1",
            canonical_url: "https://threads.net/@lg/post/LATE_GUARD_1",
            views: 1200,
            views_display: "1.2K",
            views_precision: "rounded_display",
            views_source: "threads_web_browser",
            views_measured_at: "2026-11-25T08:00:00Z",
            views_evidence_url: "https://threads.net/@lg/post/LATE_GUARD_1",
            likes: 40,
          }],
        }),
      }),
      env17
    )).json();
    assert.equal(lateOldRes.updated_count, 0, "Older late snapshot must not report updating current metrics");
    const lgRow = env17.DB.postRegistry.get("threads:post:LATE_GUARD_1");
    assert.equal(lgRow.snapshot_count, 2);
    assert.equal(lgRow.first_seen_at, "2026-11-25T08:00:00Z");
    assert.equal(lgRow.last_seen_at, "2026-11-27T12:00:00Z");
    assert.equal(JSON.parse(lgRow.latest_metrics_json).views, 50000);
    assert.equal(JSON.parse(lgRow.latest_metrics_json).likes, 1200);

    // 17e: URL -> post_id promotion + re-sync of original run does NOT duplicate snapshot
    await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: h17,
        body: JSON.stringify({
          run_id: "run_url_first_old",
          crawled_at: "2026-11-26T09:00:00Z",
          items: [{ platform: "threads", canonical_url: "https://threads.net/@u2p/post/UP22", views: 2000, likes: 30 }],
        }),
      }),
      env17
    );
    await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: h17,
        body: JSON.stringify({
          run_id: "run_url_first_new",
          crawled_at: "2026-11-27T09:00:00Z",
          items: [{ platform: "threads", post_id: "UP22", canonical_url: "https://threads.net/@u2p/post/UP22", views: 9000, likes: 120 }],
        }),
      }),
      env17
    );
    // Re-sync old run now carrying discovered post_id="UP22"
    const resyncOld = await (await worker.fetch(
      new Request("https://example.workers.dev/api/app/market/dedup/record", {
        method: "POST",
        headers: h17,
        body: JSON.stringify({
          run_id: "run_url_first_old",
          crawled_at: "2026-11-26T09:00:00Z",
          items: [{ platform: "threads", post_id: "UP22", canonical_url: "https://threads.net/@u2p/post/UP22", views: 2000, likes: 30 }],
        }),
      }),
      env17
    )).json();
    assert.equal(resyncOld.snapshots_created, 0, "Re-sync of promoted URL-first run must NOT duplicate old snapshot");
    assert.equal(resyncOld.snapshots_deduped, 1);
    assert.equal(env17.DB.postRegistry.get("threads:post:UP22").snapshot_count, 2);

    // 17f: Verify 0004 canonical snapshot_idempotency_key format matches Worker V2 + Muse SQL guide
    const m0004Sql17 = fs.readFileSync(path.join(__dirname, "../cloudflare-worker/migrations/0004_seed_post_identity_index_171.sql"), "utf8");
    assert.ok(m0004Sql17.includes("threads:post:DgC-8Jun454|threads-poc-001-20261125|2026-11-25|:140::::"));
  } finally {
    globalThis.fetch = prevFetch17;
  }
}


// 18. Pre-deploy Regression: Concurrent Cron vs /complete Retry Outbox Race & Telegram Pending/Error Suppression
{
  const tgCalls18 = [];
  const env18 = {
    APP_VIDEO_API_TOKEN: "secret18",
    TELEGRAM_OWNER_ID: "999018",
    TELEGRAM_BOT_TOKEN_VIDEO: "bot18-token",
    TELEGRAM_WEBHOOK_SECRET: "whsec18",
    GOOGLE_SERVICE_ACCOUNT_JSON: JSON.stringify({ client_email: "sa@test.iam.gserviceaccount.com", private_key: privateKey }),
    GOOGLE_DRIVE_FOLDER_ID: "drive-root-18",
    DB: new MockD1Database(),
  };
  const h18 = {
    Authorization: "Bearer secret18",
    "Content-Type": "application/json",
    "X-App-Client-Id": "laptop-18",
  };

  let mockDriveReports18 = [];
  const prevFetch18 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.includes("oauth2.googleapis.com/token")) {
      return new Response(JSON.stringify({ access_token: "ya29.mock18", expires_in: 3600 }), { status: 200 });
    }
    if (u.includes("www.googleapis.com/drive/v3/files") && !u.includes("alt=media")) {
      const urlObj = new URL(u);
      const q = urlObj.searchParams.get("q") || "";
      if (q.includes("'01_Market_Data'")) {
        return new Response(JSON.stringify({ files: [{ id: "market-18", name: "01_Market_Data" }] }), { status: 200 });
      }
      if (q.includes("'Reports'")) {
        return new Response(JSON.stringify({ files: [{ id: "reports-18", name: "Reports" }] }), { status: 200 });
      }
      if (q.includes("'reports-18' in parents")) {
        if (q.includes("vnd.google-apps.document")) {
          return new Response(JSON.stringify({ files: [] }), { status: 200 });
        }
        return new Response(JSON.stringify({ files: mockDriveReports18.map((r) => r.file) }), { status: 200 });
      }
      return new Response(JSON.stringify({ files: [] }), { status: 200 });
    }
    if (u.includes("www.googleapis.com/drive/v3/files/") && u.includes("alt=media")) {
      const m = mockDriveReports18.find((r) => u.includes(encodeURIComponent(r.file.id)));
      return new Response(JSON.stringify(m ? m.data : {}), { status: m ? 200 : 404 });
    }
    if (u.startsWith("https://api.telegram.org/")) {
      const body = init.body ? JSON.parse(init.body) : {};
      tgCalls18.push({ url: u, body });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 18000 + tgCalls18.length } }), { status: 200 });
    }
    throw new Error(`Unexpected fetch in test 18: ${u}`);
  };

  try {
    // 18a-1: Concurrent Promise.all between /complete retry and scheduled() Cron on a failed 2-chunk outbox (150 items)
    const crw18a = (await (await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests", {
        method: "POST",
        headers: h18,
        body: JSON.stringify({ platform: "threads", requested_count: 150, keywords: "concurrent_outbox_18a" }),
      }),
      env18
    )).json()).request;
    const row18a = env18.DB.crawlRequests.find((c) => c.id === crw18a.id);
    row18a.status = "running";
    row18a.claimed_by = "muse-18a";
    row18a.attempts = 1;
    row18a.requested_by_chat_id = "999018";

    const items18a = Array.from({ length: 150 }, (_, i) => ({
      platform: "threads",
      post_id: `CONC18A_${i}`,
      canonical_url: `https://threads.net/@c18/post/CONC18A_${i}`,
      views: 9000 + i,
      views_display: "9K",
      views_precision: "rounded_display",
      views_source: "threads_web_browser",
      views_measured_at: "2026-11-27T11:45:00Z",
      views_evidence_url: `https://threads.net/@c18/post/CONC18A_${i}`,
      likes: 150 + i,
    }));

    env18.DB.failRegistryOnSnapshotCallsRemaining = 101;
    const compBody18a = {
      claimed_by: "muse-18a",
      attempt: 1,
      status: "completed",
      result_summary: {
        run_id: "run_conc_18a",
        crawled_at: "2026-11-27T11:45:00Z",
        new_qualified_count: 999, // Deliberately stale client count; D1 authoritative count (150) must override
        items: items18a,
      },
    };
    const firstFail = await worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${crw18a.id}/complete`, {
        method: "POST",
        headers: h18,
        body: JSON.stringify(compBody18a),
      }),
      env18
    );
    assert.equal(firstFail.status, 503);
    assert.equal(tgCalls18.length, 0, "Failed completion must NOT send Telegram while registry_sync_status=error");

    // Yield mid-batch inside chunk #2 so scheduled() and /complete retry truly race concurrently
    let releasePause;
    let enteredPause = false;
    const pauseGate = new Promise((r) => { releasePause = r; });
    env18.DB.snapshotInsertAttempts = 0;
    env18.DB.onBatchStep = async (stepIdx, stmt) => {
      if (!enteredPause && stepIdx === 0) {
        enteredPause = true;
        await pauseGate;
      }
    };

    const tgBefore18a = tgCalls18.length;
    const retryPromise = worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${crw18a.id}/complete`, {
        method: "POST",
        headers: h18,
        body: JSON.stringify(compBody18a),
      }),
      env18
    );
    // Let retryPromise claim chunk #2 ('processing') and enter pauseGate
    await new Promise((r) => setTimeout(r, 20));
    const cronPromise = worker.scheduled({}, env18, {});
    // Let cron inspect chunk #2 while it's 'processing', then release retryPromise
    await new Promise((r) => setTimeout(r, 20));
    releasePause();
    const [retryResp18a] = await Promise.all([retryPromise, cronPromise]);
    env18.DB.onBatchStep = null;

    assert.equal(retryResp18a.status, 200);
    const retryJson18a = await retryResp18a.json();
    assert.equal(retryJson18a.dedup_stats.new_qualified_count, 150);
    assert.equal(retryJson18a.dedup_stats.total_processed, 150);
    assert.equal(env18.DB.snapshotInsertAttempts, 50, "Chunk #2 (50 items) must execute ONCE even under concurrent Cron + retry");
    assert.equal(env18.DB.registryOutbox.length, 0, "All outbox rows must be drained & cleaned");
    const savedRow18a = env18.DB.crawlRequests.find((c) => c.id === crw18a.id);
    const savedSum18a = JSON.parse(savedRow18a.result_summary_json);
    assert.equal(savedSum18a.registry_sync_status, "ok");
    assert.equal(savedSum18a.new_qualified_count, 150, "Authoritative D1 new_qualified_count must be 150, not stale 999");
    assert.equal(savedSum18a.dedup_stats.total_processed, 150);
    const tgSent18a = tgCalls18.slice(tgBefore18a).filter((c) => c.url.includes("/sendMessage"));
    assert.equal(tgSent18a.length, 1, "Concurrent Cron + retry must deliver ONLY ONE Telegram report");

    // 18a-2: Two fresh chunks processed concurrently by /complete retry and scheduled() Cron -> NO lost partial chunk totals!
    const crw18Split = (await (await worker.fetch(
      new Request("https://example.workers.dev/api/app/crawl-requests", {
        method: "POST",
        headers: h18,
        body: JSON.stringify({ platform: "threads", requested_count: 150, keywords: "split_race_18" }),
      }),
      env18
    )).json()).request;
    const rowSplit = env18.DB.crawlRequests.find((c) => c.id === crw18Split.id);
    rowSplit.status = "completed";
    rowSplit.claimed_by = "muse-split";
    rowSplit.attempts = 1;
    rowSplit.requested_by_chat_id = "999018";
    rowSplit.updated_at = new Date().toISOString();
    rowSplit.result_summary_json = JSON.stringify({ run_id: "run_split_18", registry_sync_status: "error" });
    env18.DB.registryOutbox.push(
      {
        id: "rout_split_1",
        request_id: crw18Split.id,
        run_id: "run_split_18",
        crawled_at: "2026-11-27T11:50:00Z",
        payload_json: JSON.stringify(Array.from({ length: 100 }, (_, i) => ({
          platform: "threads",
          post_id: `SPL1_${i}`,
          views: 6000,
          views_display: "6K",
          views_precision: "rounded_display",
          views_source: "threads_web_browser",
          views_measured_at: "2026-11-27T11:50:00Z",
          views_evidence_url: `https://threads.net/@s/post/SPL1_${i}`,
        }))),
        status: "pending",
        attempts: 0,
        last_error: null,
        created_at: "2026-11-27T11:50:00.000Z",
        updated_at: "2026-11-27T11:50:00.000Z",
      },
      {
        id: "rout_split_2",
        request_id: crw18Split.id,
        run_id: "run_split_18",
        crawled_at: "2026-11-27T11:50:00Z",
        payload_json: JSON.stringify(Array.from({ length: 50 }, (_, i) => ({
          platform: "threads",
          post_id: `SPL2_${i}`,
          views: 7000,
          views_display: "7K",
          views_precision: "rounded_display",
          views_source: "threads_web_browser",
          views_measured_at: "2026-11-27T11:50:00Z",
          views_evidence_url: `https://threads.net/@s/post/SPL2_${i}`,
        }))),
        status: "pending",
        attempts: 0,
        last_error: null,
        created_at: "2026-11-27T11:50:01.000Z",
        updated_at: "2026-11-27T11:50:01.000Z",
      }
    );

    let releaseSplitPause;
    let pausedSplit = false;
    const splitGate = new Promise((r) => { releaseSplitPause = r; });
    env18.DB.onBatchStep = async (stepIdx) => {
      if (!pausedSplit && stepIdx === 0) {
        pausedSplit = true;
        await splitGate;
      }
    };
    const tgBeforeSplit = tgCalls18.length;
    const pSplitRetry = worker.fetch(
      new Request(`https://example.workers.dev/api/app/crawl-requests/${crw18Split.id}/complete`, {
        method: "POST",
        headers: h18,
        body: JSON.stringify({ claimed_by: "muse-split", attempt: 1, status: "completed", result_summary: { run_id: "run_split_18" } }),
      }),
      env18
    );
    await new Promise((r) => setTimeout(r, 20));
    // Cron claims chunk #2 while retry holds chunk #1; cron finishes chunk #2 first and MUST see inProgress (not finalize 50/150!)
    const pSplitCron = worker.scheduled({}, env18, {});
    await new Promise((r) => setTimeout(r, 20));
    releaseSplitPause();
    const [resSplitRetry] = await Promise.all([pSplitRetry, pSplitCron]);
    env18.DB.onBatchStep = null;

    assert.equal(resSplitRetry.status, 200);
    const savedSplitRow = env18.DB.crawlRequests.find((c) => c.id === crw18Split.id);
    const savedSplitSum = JSON.parse(savedSplitRow.result_summary_json);
    assert.equal(savedSplitSum.registry_sync_status, "ok");
    assert.equal(savedSplitSum.dedup_stats.new_qualified_count, 150, "Worker picking up final done chunk MUST sum BOTH chunk #1 (100) + chunk #2 (50)");
    assert.equal(savedSplitSum.dedup_stats.total_processed, 150);
    assert.equal(env18.DB.registryOutbox.length, 0);
    assert.equal(tgCalls18.slice(tgBeforeSplit).filter((c) => c.url.includes("/sendMessage") || c.url.includes("/editMessageText")).length, 1);

    // 18b: Telegram MUST NOT send notification via notifyNewCrawl (Direct-D1 OR Drive report) while registry_sync_status is pending/error
    const crwPendingErr = {
      id: "crw_gate_err",
      platform: "threads",
      keywords: "gate_err_kw",
      requested_count: 10,
      status: "completed",
      notified_at: null,
      requested_by_chat_id: "999018",
      updated_at: "2026-11-27T11:51:00.000Z",
      result_summary_json: JSON.stringify({
        run_id: "run_gate_err",
        new_qualified_count: 0,
        registry_sync_status: "error",
      }),
    };
    const crwPendingOnly = {
      id: "crw_gate_pend",
      platform: "threads",
      keywords: "gate_pend_kw",
      requested_count: 10,
      status: "completed",
      notified_at: null,
      requested_by_chat_id: "999018",
      updated_at: "2026-11-27T11:51:10.000Z",
      result_summary_json: JSON.stringify({
        run_id: "run_gate_pend",
        new_qualified_count: 0,
        registry_sync_status: "pending",
      }),
    };
    const crwReadyAfter = {
      id: "crw_gate_ready",
      platform: "threads",
      keywords: "gate_ready_kw",
      requested_count: 10,
      status: "completed",
      notified_at: null,
      requested_by_chat_id: "999018",
      updated_at: "2026-11-27T11:51:20.000Z",
      result_summary_json: JSON.stringify({
        run_id: "run_gate_ready",
        new_qualified_count: 10,
        registry_sync_status: "ok",
      }),
    };
    env18.DB.crawlRequests.push(crwPendingErr, crwPendingOnly, crwReadyAfter);
    mockDriveReports18 = [
      {
        file: { id: "drv_gate_err", name: "threads_report_gate_err.json", mimeType: "application/json", size: "1024", modifiedTime: new Date().toISOString() },
        data: {
          request_id: "crw_gate_err",
          run_id: "run_gate_err",
          crawled_at: new Date().toISOString(),
          run_status: "completed",
          counters: { new_qualified_count: 10, requested_count: 10 },
        },
      },
    ];

    const tgBeforeGate = tgCalls18.length;
    await worker.scheduled({}, env18, {});
    const tgSentGate = tgCalls18.slice(tgBeforeGate).filter((c) => c.url.includes("/sendMessage") || c.url.includes("/editMessageText"));
    assert.equal(tgSentGate.length, 1, "Only crw_gate_ready should notify; crw_gate_err & crw_gate_pend must stay blocked");
    assert.ok(tgSentGate[0].body.text.includes("crw_gate_ready"));
    assert.equal(env18.DB.crawlRequests.find((c) => c.id === "crw_gate_err").notified_at, null);
    assert.equal(env18.DB.crawlRequests.find((c) => c.id === "crw_gate_pend").notified_at, null);
    const d1CursorAfterGate = env18.DB.runtimeState.get("notify_d1_cursor");
    assert.notEqual(
      d1CursorAfterGate,
      "2026-11-27T11:51:20.000Z|crw_gate_ready",
      "notify_d1_cursor MUST NOT advance past blocked crw_gate_err/crw_gate_pend rows"
    );

    // Once crw_gate_err & crw_gate_pend recover to registry_sync_status=ok, next scheduled() delivers them ONCE cleanly
    env18.DB.crawlRequests.find((c) => c.id === "crw_gate_err").result_summary_json = JSON.stringify({
      run_id: "run_gate_err",
      new_qualified_count: 10,
      registry_sync_status: "ok",
    });
    env18.DB.crawlRequests.find((c) => c.id === "crw_gate_pend").result_summary_json = JSON.stringify({
      run_id: "run_gate_pend",
      new_qualified_count: 8,
      registry_sync_status: "ok",
    });
    const tgBeforeRecovery = tgCalls18.length;
    await worker.scheduled({}, env18, {});
    const tgSentRec = tgCalls18.slice(tgBeforeRecovery).filter((c) => c.url.includes("/sendMessage") || c.url.includes("/editMessageText"));
    assert.equal(tgSentRec.length, 2, "Both recovered requests must now notify cleanly without skipping");
    assert.ok(env18.DB.crawlRequests.find((c) => c.id === "crw_gate_err").notified_at);
    assert.ok(env18.DB.crawlRequests.find((c) => c.id === "crw_gate_pend").notified_at);

    // 18c: Verify wrangler.toml points to worker.v2.js (never legacy worker.js)
    const wranglerToml = fs.readFileSync(path.join(__dirname, "../cloudflare-worker/wrangler.toml"), "utf8");
    assert.ok(/^main\s*=\s*"worker\.v2\.js"/m.test(wranglerToml), "wrangler.toml main must point to worker.v2.js");
    assert.ok(/database_name\s*=\s*"app-video-db"/m.test(wranglerToml), "wrangler.toml must bind app-video-db");
  } finally {
    globalThis.fetch = prevFetch18;
  }
}


// 19. Muse Hook Signal (GET /api/crawl-queue/signal)
{
  const env19 = {
    APP_VIDEO_API_TOKEN: "app-secret-19",
    DB: new MockD1Database(),
  };

  const callSignal = async (headers = {}, method = "GET") => {
    const writesBefore = env19.DB.writeOps;
    const res = await worker.fetch(
      new Request("https://example.workers.dev/api/crawl-queue/signal", { method, headers }),
      env19
    );
    const text = await res.text();
    let json = null;
    try { json = JSON.parse(text); } catch { }
    return {
      status: res.status,
      headers: res.headers,
      text,
      json,
      writesDelta: env19.DB.writeOps - writesBefore,
    };
  };

  // 19a: Empty queue -> 200 {"queued":false}, Cache-Control: no-store, strictly boolean key, 0 D1 writes, no CORS *
  const emptyRes = await callSignal();
  assert.equal(emptyRes.status, 200);
  assert.equal(emptyRes.headers.get("Cache-Control"), "no-store");
  assert.equal(emptyRes.headers.get("Access-Control-Allow-Origin"), null);
  assert.deepEqual(emptyRes.json, { queued: false });
  assert.deepEqual(Object.keys(emptyRes.json), ["queued"]);
  assert.equal(emptyRes.writesDelta, 0, "Signal endpoint must never write to D1");

  // 19b: Non-Threads queued requests (all, tiktok, shopee) MUST NOT trigger queued=true
  const now19 = new Date().toISOString();
  env19.DB.crawlRequests.push(
    { id: "crw_all_19", platform: "all", keywords: "secret_kw_all", requested_count: 50, status: "queued", attempts: 0, max_attempts: 3, created_at: now19, updated_at: now19 },
    { id: "crw_tk_19", platform: "tiktok", keywords: "secret_kw_tk", requested_count: 30, status: "queued", attempts: 0, max_attempts: 3, created_at: now19, updated_at: now19 },
    { id: "crw_sh_19", platform: "shopee", keywords: "secret_kw_sh", requested_count: 20, status: "queued", attempts: 0, max_attempts: 3, created_at: now19, updated_at: now19 }
  );
  const nonThreadsRes = await callSignal();
  assert.equal(nonThreadsRes.status, 200);
  assert.deepEqual(nonThreadsRes.json, { queued: false });
  assert.equal(nonThreadsRes.writesDelta, 0);

  // 19c: Eligible Threads queued request -> {"queued":true}, leaves status='queued' intact, leaks ZERO sensitive fields
  env19.DB.crawlRequests.push({
    id: "crw_threads_19_a",
    platform: "threads",
    keywords: "sensitive_brand_kw",
    requested_count: 88,
    requested_by_chat_id: "999999",
    status: "queued",
    attempts: 0,
    max_attempts: 3,
    created_at: "2026-11-27T12:00:00.000Z",
    updated_at: "2026-11-27T12:00:00.000Z",
  });
  const threadsRes = await callSignal();
  assert.equal(threadsRes.status, 200);
  assert.equal(threadsRes.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(threadsRes.json, { queued: true });
  assert.ok(!threadsRes.text.includes("crw_threads_19_a"));
  assert.ok(!threadsRes.text.includes("sensitive_brand_kw"));
  assert.ok(!threadsRes.text.includes("999999"));
  assert.ok(!threadsRes.text.includes("88"));
  assert.equal(threadsRes.writesDelta, 0);
  assert.equal(env19.DB.crawlRequests.find((c) => c.id === "crw_threads_19_a").status, "queued", "Must NOT claim request");

  // 19d: Active running crawl (valid future lease, even non-Threads) -> MUST return {"queued":false}
  const futureLease = new Date(Date.now() + 20 * 60 * 1000).toISOString();
  const runningRow19 = {
    id: "crw_running_19",
    platform: "all",
    keywords: "active_run",
    requested_count: 10,
    status: "running",
    claimed_by: "muse-vm",
    attempts: 1,
    max_attempts: 3,
    lease_expires_at: futureLease,
    created_at: "2026-11-27T12:00:01.000Z",
    updated_at: "2026-11-27T12:00:01.000Z",
  };
  env19.DB.crawlRequests.push(runningRow19);
  const whileRunning = await callSignal();
  assert.equal(whileRunning.status, 200);
  assert.deepEqual(whileRunning.json, { queued: false }, "Active running task must suppress new wake signal");
  assert.equal(whileRunning.writesDelta, 0);

  // 19e: Expired lease on running task MUST stop blocking queued Threads request (read-only),
  //      and after expireStaleLeases requeues an expired Threads task it becomes signal=true (or false if max_attempts exhausted)
  runningRow19.lease_expires_at = new Date(Date.now() - 60 * 1000).toISOString();
  const expiredBlockRes = await callSignal();
  assert.equal(expiredBlockRes.status, 200);
  assert.deepEqual(expiredBlockRes.json, { queued: true }, "Expired running lease must not wedge queued Threads requests");
  assert.equal(expiredBlockRes.writesDelta, 0);
  // Clean up runningRow19 before testing standalone expired Threads reclaim
  env19.DB.crawlRequests = env19.DB.crawlRequests.filter((c) => c.id !== "crw_running_19");

  // Unfinished registry work (registry_sync_outbox OR registry_sync_status pending/error) MUST block signal -> false
  env19.DB.registryOutbox.push({
    id: "rout_block_19",
    request_id: "crw_prev_19",
    status: "error",
    payload_json: "[]",
    created_at: now19,
    updated_at: now19,
  });
  assert.deepEqual((await callSignal()).json, { queued: false }, "Pending/errored outbox must block next crawl signal");
  env19.DB.registryOutbox = [];

  const regBlockedReq = {
    id: "crw_reg_err_19",
    platform: "threads",
    keywords: "done_needs_reg",
    requested_count: 10,
    status: "completed",
    attempts: 1,
    max_attempts: 3,
    result_summary_json: JSON.stringify({ registry_sync_status: "error" }),
    created_at: "2026-11-27T12:00:02.000Z",
    updated_at: "2026-11-27T12:00:02.000Z",
  };
  env19.DB.crawlRequests.push(regBlockedReq);
  assert.deepEqual((await callSignal()).json, { queued: false }, "registry_sync_status=error in completed request must block signal");
  regBlockedReq.result_summary_json = JSON.stringify({ registry_sync_status: "ok" });
  assert.deepEqual((await callSignal()).json, { queued: true }, "Signal resumes true once registry_sync_status=ok");

  // 19f: Consecutive requests (nhieu request lien tiep):
  //      req1 + req2 queued -> true -> req1 running -> false -> req1 completed -> immediately true for req2 -> req2 completed -> false
  env19.DB.crawlRequests.push({
    id: "crw_threads_19_b",
    platform: "threads",
    keywords: "second_threads_req",
    requested_count: 50,
    status: "queued",
    attempts: 0,
    max_attempts: 3,
    created_at: "2026-11-27T12:00:03.000Z",
    updated_at: "2026-11-27T12:00:03.000Z",
  });
  assert.deepEqual((await callSignal()).json, { queued: true });
  const reqA19 = env19.DB.crawlRequests.find((c) => c.id === "crw_threads_19_a");
  reqA19.status = "running";
  reqA19.lease_expires_at = new Date(Date.now() + 10 * 60 * 1000).toISOString();
  reqA19.attempts = 1;
  assert.deepEqual((await callSignal()).json, { queued: false }, "Drops to false during req1 execution even with req2 queued");
  reqA19.status = "completed";
  reqA19.result_summary_json = JSON.stringify({ registry_sync_status: "ok" });
  assert.deepEqual((await callSignal()).json, { queued: true }, "Immediately flips false->true for req2 without waiting 600s");
  const reqB19 = env19.DB.crawlRequests.find((c) => c.id === "crw_threads_19_b");
  reqB19.status = "running";
  reqB19.lease_expires_at = new Date(Date.now() + 10 * 60 * 1000).toISOString();
  reqB19.attempts = 1;
  assert.deepEqual((await callSignal()).json, { queued: false });
  reqB19.status = "completed";
  reqB19.result_summary_json = JSON.stringify({ registry_sync_status: "ok" });
  assert.deepEqual((await callSignal()).json, { queued: false });

  // Expired standalone Threads request reclaimed by scheduled() expireStaleLeases
  const reclaimTarget = {
    id: "crw_reclaim_19",
    platform: "threads",
    keywords: "reclaim_me",
    requested_count: 25,
    status: "running",
    claimed_by: "dead-worker",
    attempts: 1,
    max_attempts: 3,
    lease_expires_at: new Date(Date.now() - 120 * 1000).toISOString(),
    created_at: "2026-11-27T12:00:04.000Z",
    updated_at: "2026-11-27T12:00:04.000Z",
  };
  env19.DB.crawlRequests.push(reclaimTarget);
  await worker.scheduled({}, env19, {});
  assert.equal(reclaimTarget.status, "queued");
  assert.deepEqual((await callSignal()).json, { queued: true });
  // Exhausted attempts -> failed -> signal false
  reclaimTarget.status = "running";
  reclaimTarget.attempts = 3;
  reclaimTarget.lease_expires_at = new Date(Date.now() - 120 * 1000).toISOString();
  await worker.scheduled({}, env19, {});
  assert.equal(reclaimTarget.status, "failed");
  assert.deepEqual((await callSignal()).json, { queued: false });

  // 19g: D1 outage MUST return HTTP 503 (NEVER {"queued":false}) with Cache-Control: no-store
  env19.DB.failSignalQuery = true;
  const errRes19 = await callSignal();
  assert.equal(errRes19.status, 503);
  assert.equal(errRes19.headers.get("Cache-Control"), "no-store");
  assert.equal(errRes19.json?.queued, undefined, "503 must NOT contain queued field");
  assert.ok(!errRes19.text.includes("Simulated D1"), "503 must NOT leak raw D1 internals");
  env19.DB.failSignalQuery = false;

  // 19h: HTTP method restriction, optional secret guard, and per-IP burst rate limiting
  const postRes19 = await callSignal({}, "POST");
  assert.equal(postRes19.status, 405);
  assert.equal(postRes19.headers.get("Allow"), "GET");
  assert.equal(postRes19.headers.get("Cache-Control"), "no-store");

  const env19Tok = { ...env19, MUSE_SIGNAL_TOKEN: "sig-secret-19" };
  const rUnauthSig = await worker.fetch(new Request("https://example.workers.dev/api/crawl-queue/signal"), env19Tok);
  assert.equal(rUnauthSig.status, 401);
  assert.equal(rUnauthSig.headers.get("Cache-Control"), "no-store");
  const rAuthSig = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl-queue/signal", { headers: { "X-Signal-Token": "sig-secret-19" } }),
    env19Tok
  );
  assert.equal(rAuthSig.status, 200);

  const env19Rate = { ...env19, SIGNAL_RATE_LIMIT_PER_MIN: "5" };
  for (let i = 0; i < 5; i++) {
    const okRl = await worker.fetch(
      new Request("https://example.workers.dev/api/crawl-queue/signal", { headers: { "CF-Connecting-IP": "198.51.100.77" } }),
      env19Rate
    );
    assert.equal(okRl.status, 200);
  }
  const blockedRl = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl-queue/signal", { headers: { "CF-Connecting-IP": "198.51.100.77" } }),
    env19Rate
  );
  assert.equal(blockedRl.status, 429);
  assert.equal(blockedRl.headers.get("Retry-After") !== null, true);
  assert.equal(blockedRl.headers.get("Cache-Control"), "no-store");
}


// 20. Muse Custom Connector POC Phase 1 (Scoped MUSE_APP_VIDEO_TOKEN + Read-Only /api/crawl/*)
{
  const env20 = {
    APP_VIDEO_API_TOKEN: "app-admin-secret-20",
    MUSE_APP_VIDEO_TOKEN: "muse-scoped-secret-20",
    DB: new MockD1Database(),
  };

  // Seed 2 registry records
  env20.DB.postRegistry.set("threads:post:POC_SEED_1", {
    dedup_key: "threads:post:POC_SEED_1",
    platform: "threads",
    post_id: "POC_SEED_1",
    canonical_url: "https://threads.net/@seed/post/POC_SEED_1",
    first_seen_at: "2026-11-25",
    last_seen_at: "2026-11-25",
    snapshot_count: 1,
    latest_metrics_json: JSON.stringify({ likes: 140 }),
  });
  env20.DB.postRegistry.set("threads:post:POC_SEED_2", {
    dedup_key: "threads:post:POC_SEED_2",
    platform: "threads",
    post_id: "POC_SEED_2",
    canonical_url: "https://threads.net/@seed/post/POC_SEED_2",
    first_seen_at: "2026-11-26",
    last_seen_at: "2026-11-26",
    snapshot_count: 2,
    latest_metrics_json: JSON.stringify({ views: 50000 }),
    latest_views_display: "50K",
    latest_views_precision: "rounded_display",
  });

  // 20a: Fail closed if MUSE_APP_VIDEO_TOKEN is unconfigured (never falls back to APP_VIDEO_API_TOKEN!)
  const envNoMuseTok = { APP_VIDEO_API_TOKEN: "app-admin-secret-20", DB: env20.DB };
  const unconfHealth = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/health", {
      headers: { Authorization: "Bearer app-admin-secret-20" },
    }),
    envNoMuseTok
  );
  assert.equal(unconfHealth.status, 503);

  // 20b: Strict token isolation between APP_VIDEO_API_TOKEN and MUSE_APP_VIDEO_TOKEN
  assert.equal(
    (await worker.fetch(new Request("https://example.workers.dev/api/crawl/health"), env20)).status,
    401
  );
  assert.equal(
    (await worker.fetch(
      new Request("https://example.workers.dev/api/crawl/health", {
        headers: { Authorization: "Bearer app-admin-secret-20" },
      }),
      env20
    )).status,
    401,
    "APP_VIDEO_API_TOKEN must NOT authenticate /api/crawl/*"
  );
  for (const appPath of [
    "/api/app/health",
    "/api/app/market/files",
    "/api/app/crawl-requests",
    "/api/app/market/dedup/check",
  ]) {
    const crossRes = await worker.fetch(
      new Request(`https://example.workers.dev${appPath}`, {
        headers: { Authorization: "Bearer muse-scoped-secret-20" },
      }),
      env20
    );
    assert.equal(crossRes.status, 401, `MUSE_APP_VIDEO_TOKEN MUST NOT access ${appPath}`);
  }

  // 20c: GET /api/crawl/health succeeds read-only with MUSE_APP_VIDEO_TOKEN; rejects POST
  const writesBeforeHealth = env20.DB.writeOps;
  const healthRes = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/health", {
      headers: { Authorization: "Bearer muse-scoped-secret-20" },
    }),
    env20
  );
  assert.equal(healthRes.status, 200);
  assert.equal(healthRes.headers.get("Cache-Control"), "no-store");
  const healthJson = await healthRes.json();
  assert.equal(healthJson.ok, true);
  assert.equal(healthJson.auth_scope, "muse_crawl");
  assert.equal(healthJson.db_ok, true);
  assert.equal(healthJson.registry_ready, true);
  assert.equal(healthJson.registry_count, 2);
  assert.equal(env20.DB.writeOps - writesBeforeHealth, 0, "GET /api/crawl/health must be strictly read-only");

  const healthPost = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/health", {
      method: "POST",
      headers: { Authorization: "Bearer muse-scoped-secret-20" },
    }),
    env20
  );
  assert.equal(healthPost.status, 405);
  assert.equal(healthPost.headers.get("Allow"), "GET");

  // 20d: POST /api/crawl/registry/check supports both {post_ids, canonical_urls, dedup_keys} & {items} read-only
  const writesBeforeCheck = env20.DB.writeOps;
  const regCheck1 = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/registry/check", {
      method: "POST",
      headers: {
        Authorization: "Bearer muse-scoped-secret-20",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        platform: "threads",
        post_ids: ["POC_SEED_1", "POC_UNSEEN_99"],
        canonical_urls: ["https://threads.net/@seed/post/POC_SEED_2"],
      }),
    }),
    env20
  );
  assert.equal(regCheck1.status, 200);
  assert.equal(regCheck1.headers.get("Cache-Control"), "no-store");
  const regCheck1Json = await regCheck1.json();
  assert.equal(regCheck1Json.ok, true);
  assert.equal(regCheck1Json.read_only, true);
  assert.equal(regCheck1Json.existing_count, 2);
  assert.equal(regCheck1Json.new_count, 1);
  assert.equal(regCheck1Json.total_checked, 3);
  assert.equal(env20.DB.writeOps - writesBeforeCheck, 0, "POST /api/crawl/registry/check must be strictly read-only");

  // Also works with { items: [...] } format
  const regCheck2 = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/registry/check", {
      method: "POST",
      headers: {
        Authorization: "Bearer muse-scoped-secret-20",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        items: [{ platform: "threads", post_id: "POC_SEED_1" }],
      }),
    }),
    env20
  );
  assert.equal(regCheck2.status, 200);
  assert.equal((await regCheck2.json()).existing_count, 1);

  // Rejects >100 items, GET method (405), unimplemented Phase 2 write routes (404), and fails closed 503 on D1 outage
  const tooManyRes = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/registry/check", {
      method: "POST",
      headers: {
        Authorization: "Bearer muse-scoped-secret-20",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ post_ids: Array.from({ length: 101 }, (_, i) => `K_${i}`) }),
    }),
    env20
  );
  assert.equal(tooManyRes.status, 400);

  const checkGet = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/registry/check", {
      headers: { Authorization: "Bearer muse-scoped-secret-20" },
    }),
    env20
  );
  assert.equal(checkGet.status, 405);

  const phase2ClaimEmpty = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/claim", {
      method: "POST",
      headers: { Authorization: "Bearer muse-scoped-secret-20" },
    }),
    env20
  );
  assert.equal(phase2ClaimEmpty.status, 200);
  assert.equal((await phase2ClaimEmpty.json()).request, null);

  const unknownCrawlRoute = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/arbitrary-sql", {
      method: "POST",
      headers: { Authorization: "Bearer muse-scoped-secret-20" },
    }),
    env20
  );
  assert.equal(unknownCrawlRoute.status, 404, "Arbitrary /api/crawl/* paths must return 404");

  env20.DB.failRegistry = true;
  assert.equal(
    (await worker.fetch(
      new Request("https://example.workers.dev/api/crawl/health", {
        headers: { Authorization: "Bearer muse-scoped-secret-20" },
      }),
      env20
    )).status,
    503
  );
  const regErr = await worker.fetch(
    new Request("https://example.workers.dev/api/crawl/registry/check", {
      method: "POST",
      headers: {
        Authorization: "Bearer muse-scoped-secret-20",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ post_ids: ["POC_SEED_1"] }),
    }),
    env20
  );
  assert.equal(regErr.status, 503);
  assert.equal((await regErr.json()).registry_unavailable, true);
  env20.DB.failRegistry = false;

  // Hook Signal remains unaffected (public boolean)
  const sig20 = await worker.fetch(new Request("https://example.workers.dev/api/crawl-queue/signal"), env20);
  assert.equal(sig20.status, 200);
  assert.deepEqual(await sig20.json(), { queued: false });
}


// 21. Muse Custom Connector Phase 2 (/api/crawl/claim, /progress, /complete, /fail):
//     Authorization, Concurrent Single-Flight Claim, Progress Guard, Stale Lease, Retry, Outbox Recovery, Idempotency & Telegram Gate
{
  const tgCalls21 = [];
  const prevFetch21 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.startsWith("https://api.telegram.org/")) {
      tgCalls21.push({ url: sUrl, body: init.body ? JSON.parse(init.body) : {} });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 21000 + tgCalls21.length } }), { status: 200 });
    }
    throw new Error(`Unexpected fetch in test 21: ${sUrl}`);
  };

  const env21 = {
    APP_VIDEO_API_TOKEN: "app-admin-secret-21",
    MUSE_APP_VIDEO_TOKEN: "muse-crawl-secret-21",
    TELEGRAM_BOT_TOKEN_VIDEO: "bot21-token",
    TELEGRAM_OWNER_ID: "999021",
    DB: new MockD1Database(),
  };
  const hMuse21 = {
    Authorization: "Bearer muse-crawl-secret-21",
    "Content-Type": "application/json",
  };
  const hApp21 = {
    Authorization: "Bearer app-admin-secret-21",
    "Content-Type": "application/json",
  };

  try {
    // Seed mixed queue: older non-Threads ('all', 'tiktok') + 2 Threads requests (one with requested_by_chat_id)
    env21.DB.crawlRequests.push(
      { id: "crw_21_all", platform: "all", keywords: "all_kw", requested_count: 10, status: "queued", attempts: 0, max_attempts: 3, created_at: "2026-11-27T13:00:00.000Z", updated_at: "2026-11-27T13:00:00.000Z" },
      { id: "crw_21_tk", platform: "tiktok", keywords: "tk_kw", requested_count: 10, status: "queued", attempts: 0, max_attempts: 3, created_at: "2026-11-27T13:00:01.000Z", updated_at: "2026-11-27T13:00:01.000Z" },
      { id: "crw_21_th1", platform: "threads", keywords: "th1_kw", requested_count: 20, status: "queued", attempts: 0, max_attempts: 3, requested_by_chat_id: "999021", created_at: "2026-11-27T13:00:02.000Z", updated_at: "2026-11-27T13:00:02.000Z" },
      { id: "crw_21_th2", platform: "threads", keywords: "th2_kw", requested_count: 150, status: "queued", attempts: 0, max_attempts: 3, requested_by_chat_id: "999021", created_at: "2026-11-27T13:00:03.000Z", updated_at: "2026-11-27T13:00:03.000Z" }
    );

    // 21a: Authorization & Scope Isolation on Phase 2 endpoints
    for (const p of ["/api/crawl/claim", "/api/crawl/progress", "/api/crawl/complete", "/api/crawl/fail"]) {
      assert.equal((await worker.fetch(new Request(`https://example.workers.dev${p}`, { method: "POST", headers: hApp21, body: "{}" }), env21)).status, 401);
      assert.equal((await worker.fetch(new Request(`https://example.workers.dev${p}`, { method: "POST", body: "{}" }), env21)).status, 401);
      assert.equal((await worker.fetch(new Request(`https://example.workers.dev${p}`, { method: "GET", headers: hMuse21 }), env21)).status, 405);
    }

    // 21b: Concurrency + Single-Flight Claim (5 simultaneous calls all see candidate queued, ONLY 1 wins crw_21_th1)
    let claimGateResolve;
    let waitingCount = 0;
    const claimGate = new Promise((r) => { claimGateResolve = r; });
    env21.DB.onBeforeClaimUpdate = async () => {
      waitingCount += 1;
      if (waitingCount === 5) claimGateResolve();
      await claimGate;
    };
    const claimCalls = await Promise.all(
      Array.from({ length: 5 }, () =>
        worker.fetch(
          new Request("https://example.workers.dev/api/crawl/claim", { method: "POST", headers: hMuse21, body: "{}" }),
          env21
        ).then((r) => r.json())
      )
    );
    env21.DB.onBeforeClaimUpdate = null;

    const winners = claimCalls.filter((c) => c.request !== null);
    assert.equal(winners.length, 1, "Exact 1 caller must win atomic single-flight claim");
    assert.equal(winners[0].request.id, "crw_21_th1", "Must pick oldest Threads request, ignoring all/tiktok");
    assert.equal(winners[0].request.claimed_by, "muse-ai");
    assert.equal(winners[0].request.attempt, 1);
    const leaseMs = Date.parse(winners[0].request.lease_expires_at) - Date.parse(winners[0].request.claimed_at);
    assert.ok(leaseMs >= 1790 * 1000 && leaseMs <= 1810 * 1000, "Default lease must be 30 minutes (1800s)");

    // Single-flight blocks subsequent claim while crw_21_th1 is running
    const blockedClaim = await (await worker.fetch(
      new Request("https://example.workers.dev/api/crawl/claim", { method: "POST", headers: hMuse21, body: "{}" }),
      env21
    )).json();
    assert.equal(blockedClaim.request, null);
    assert.equal(blockedClaim.single_flight_blocked, true);

    // 21c: Progress guard (wrong owner/attempt/non-threads -> 403/409; valid owner+attempt+lease -> 200 without Telegram)
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/progress", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({ request_id: "crw_21_all", claimed_by: "muse-ai", attempt: 1, counters: { scanned_count: 5 } }),
      }), env21)).status,
      403,
      "MUSE_APP_VIDEO_TOKEN must not mutate non-threads requests"
    );
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/progress", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({ request_id: "crw_21_th1", claimed_by: "rogue-agent", attempt: 1, counters: { scanned_count: 5 } }),
      }), env21)).status,
      403,
      "MUSE_APP_VIDEO_TOKEN must reject caller declaring non-muse-ai owner"
    );
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/progress", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({ request_id: "crw_21_th1", claimed_by: "muse-ai", attempt: 2, counters: { scanned_count: 5 } }),
      }), env21)).status,
      409
    );
    const progOk = await worker.fetch(new Request("https://example.workers.dev/api/crawl/progress", {
      method: "POST", headers: hMuse21,
      body: JSON.stringify({
        request_id: "crw_21_th1",
        claimed_by: "muse-ai",
        attempt: 1,
        run_id: "run_prog_21",
        stage: "enriching_views",
        message: "Đang quét 12/20 bài Threads",
        counters: { scanned_count: 35, new_qualified_count: 12, duplicate_history_count: 15 },
      }),
    }), env21);
    assert.equal(progOk.status, 200);
    const progJson = await progOk.json();
    assert.equal(progJson.progress_metrics.new_qualified_count, 12);
    assert.equal(progJson.progress_metrics.scanned_count, 35);
    assert.equal(tgCalls21.length, 0, "Progress updates must never send Telegram");

    // 21d: Fail retry policy (attempt 1 < max_attempts -> queued) + Stale Lease guard + Exhausted attempts -> failed
    const failRetry1 = await worker.fetch(new Request("https://example.workers.dev/api/crawl/fail", {
      method: "POST", headers: hMuse21,
      body: JSON.stringify({
        request_id: "crw_21_th1",
        claimed_by: "muse-ai",
        attempt: 1,
        error_message: "Temporary Playwright navigation timeout",
        retryable: true,
      }),
    }), env21);
    assert.equal(failRetry1.status, 200);
    const fail1Json = await failRetry1.json();
    assert.equal(fail1Json.status, "queued");
    assert.equal(fail1Json.retried, true);
    assert.equal(fail1Json.retries_remaining, 2);
    assert.equal(tgCalls21.length, 0, "Retryable requeue must NOT notify failure on Telegram");
    // Repeat fail with stale attempt 1 now rejected 409
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/fail", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({ request_id: "crw_21_th1", claimed_by: "muse-ai", attempt: 1, error_message: "Dup" }),
      }), env21)).status,
      409
    );

    // Re-claim -> gets attempt 2; expire lease -> progress/complete/fail MUST reject 409 & expireStaleLeases requeues it
    const claim2 = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/claim", { method: "POST", headers: hMuse21, body: "{}" }), env21)).json();
    assert.equal(claim2.request.id, "crw_21_th1");
    assert.equal(claim2.request.attempt, 2);

    const th1Row = env21.DB.crawlRequests.find((c) => c.id === "crw_21_th1");
    th1Row.lease_expires_at = new Date(Date.now() - 60 * 1000).toISOString();
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/progress", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({ request_id: "crw_21_th1", claimed_by: "muse-ai", attempt: 2, counters: { scanned_count: 1 } }),
      }), env21)).status,
      409,
      "Expired lease must reject progress write with 409"
    );
    assert.equal(th1Row.status, "queued", "Expired lease write check invokes expireStaleLeases -> queued");

    // Re-claim attempt 3 (final attempt) -> fail -> terminal 'failed' + sends Telegram ONCE + idempotent retry returns already_finalized
    const claim3 = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/claim", { method: "POST", headers: hMuse21, body: "{}" }), env21)).json();
    assert.equal(claim3.request.attempt, 3);
    const tgBeforeFail = tgCalls21.length;
    const failTerm = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/fail", {
      method: "POST", headers: hMuse21,
      body: JSON.stringify({ request_id: "crw_21_th1", claimed_by: "muse-ai", attempt: 3, error_message: "Threads blocked account" }),
    }), env21)).json();
    assert.equal(failTerm.status, "failed");
    assert.equal(failTerm.already_finalized, false);
    assert.equal(tgCalls21.length - tgBeforeFail, 1, "Terminal failure must notify Telegram once");
    const failIdemp = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/fail", {
      method: "POST", headers: hMuse21,
      body: JSON.stringify({ request_id: "crw_21_th1", claimed_by: "muse-ai", attempt: 3, error_message: "Threads blocked account" }),
    }), env21)).json();
    assert.equal(failIdemp.already_finalized, true);
    assert.equal(tgCalls21.length - tgBeforeFail, 1, "Idempotent fail retry must NOT duplicate Telegram");

    // 21e: Complete + Outbox Recovery + Signal/Claim Lock + Idempotency + Telegram Gate on crw_21_th2 (150 items)
    const claimTh2 = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/claim", { method: "POST", headers: hMuse21, body: "{}" }), env21)).json();
    assert.equal(claimTh2.request.id, "crw_21_th2");
    assert.equal(claimTh2.request.attempt, 1);

    const items21 = Array.from({ length: 150 }, (_, i) => ({
      platform: "threads",
      post_id: `P21_${i}`,
      canonical_url: `https://threads.net/@p21/post/P21_${i}`,
      views: 15000 + i,
      views_display: "15K",
      views_precision: "rounded_display",
      views_source: "threads_web_browser",
      views_measured_at: "2026-11-27T14:20:00Z",
      views_evidence_url: `https://threads.net/@p21/post/P21_${i}`,
      likes: 100 + i,
    }));

    env21.DB.failRegistryOnSnapshotCallsRemaining = 101;
    const compPayload21 = {
      request_id: "crw_21_th2",
      claimed_by: "muse-ai",
      attempt: 1,
      report_file_id: "drv_rpt_21",
      result_summary: {
        contract_version: "3.2",
        run_id: "run_p2_21",
        crawled_at: "2026-11-27T14:20:00Z",
        run_status: "completed",
        counters: { requested_count: 150, scanned_count: 180, new_qualified_count: 150 },
      },
      items: items21,
    };

    const tgBeforeComp = tgCalls21.length;
    const failComp21 = await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
      method: "POST", headers: hMuse21, body: JSON.stringify(compPayload21),
    }), env21);
    assert.equal(failComp21.status, 503);
    assert.equal((await failComp21.json()).recovery_pending, true);
    assert.equal(tgCalls21.length - tgBeforeComp, 0, "MUST NOT send Telegram when registry_sync_status=error");
    assert.equal(env21.DB.crawlRequests.find((c) => c.id === "crw_21_th2").notified_at ?? null, null);

    // While crw_21_th2 has pending/error outbox, another queued Threads request MUST be blocked by both signal & claim
    env21.DB.crawlRequests.push({
      id: "crw_21_th3_wait",
      platform: "threads",
      keywords: "wait_kw",
      requested_count: 10,
      status: "queued",
      attempts: 0,
      max_attempts: 3,
      created_at: "2026-11-27T14:21:00.000Z",
      updated_at: "2026-11-27T14:21:00.000Z",
    });
    assert.deepEqual(await (await worker.fetch(new Request("https://example.workers.dev/api/crawl-queue/signal"), env21)).json(), { queued: false });
    const blockedByOutboxClaim = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/claim", { method: "POST", headers: hMuse21, body: "{}" }), env21)).json();
    assert.equal(blockedByOutboxClaim.request, null);
    assert.equal(blockedByOutboxClaim.reason, "registry_sync_pending");

    // Recovery via POST /api/crawl/complete -> recovers outbox, syncs authoritative 150/150 counters, notifies Telegram ONCE!
    const recComp21 = await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
      method: "POST", headers: hMuse21, body: JSON.stringify(compPayload21),
    }), env21);
    assert.equal(recComp21.status, 200);
    const recJson21 = await recComp21.json();
    assert.equal(recJson21.recovered_registry_sync, true);
    assert.equal(recJson21.registry_sync_status, "ok");
    assert.equal(recJson21.dedup_stats.new_qualified_count, 150);
    assert.equal(recJson21.dedup_stats.total_processed, 150);
    const th2SavedSum = JSON.parse(env21.DB.crawlRequests.find((c) => c.id === "crw_21_th2").result_summary_json);
    assert.equal(th2SavedSum.counters.new_qualified_count, 150, "counters synced with authoritative D1 dedup_stats");
    assert.equal(tgCalls21.length - tgBeforeComp, 1, "Telegram sent exactly once after recovery");
    assert.equal(env21.DB.registryOutbox.length, 0);

    // Next signal now unblocks for crw_21_th3_wait!
    assert.deepEqual(await (await worker.fetch(new Request("https://example.workers.dev/api/crawl-queue/signal"), env21)).json(), { queued: true });

    // Idempotent retry on crw_21_th2 -> already_finalized=true, no 2nd Telegram
    const idempComp21 = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
      method: "POST", headers: hMuse21, body: JSON.stringify(compPayload21),
    }), env21)).json();
    assert.equal(idempComp21.already_finalized, true);
    assert.equal(tgCalls21.length - tgBeforeComp, 1);

    // Mismatched attempt/owner on finalized request rejected 409
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
        method: "POST", headers: hMuse21, body: JSON.stringify({ ...compPayload21, attempt: 2 }),
      }), env21)).status,
      409
    );

    // 21f: Hardened pre-deploy checks:
    // (1) Server enforces owner='muse-ai' on claim/progress/complete/fail (403 on other declared owner, 409 on non-muse-ai DB row)
    // (2) Completion rejects any non-Threads item (tiktok/shopee/non-threads URL/dedup_key)
    // (3) Completion rejects new/qualified > 0 without items/records OR empty {}, while accepting valid V3.2 zero-post run
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/claim", {
        method: "POST", headers: hMuse21, body: JSON.stringify({ worker_id: "other-bot" }),
      }), env21)).status,
      403
    );
    const otherOwnerReq = {
      id: "crw_21_other_owner",
      platform: "threads",
      keywords: "owned_by_other",
      requested_count: 10,
      status: "running",
      claimed_by: "other-desktop-worker",
      attempts: 1,
      max_attempts: 3,
      lease_expires_at: new Date(Date.now() + 1800 * 1000).toISOString(),
      created_at: "2026-11-27T15:00:00.000Z",
      updated_at: "2026-11-27T15:00:00.000Z",
    };
    env21.DB.crawlRequests.push(otherOwnerReq);
    // Even with omitted claimed_by (so server defaults to muse-ai) or with claimed_by='other-desktop-worker', MUSE_APP_VIDEO_TOKEN cannot touch it!
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/progress", {
        method: "POST", headers: hMuse21, body: JSON.stringify({ request_id: "crw_21_other_owner", attempt: 1, counters: { scanned_count: 3 } }),
      }), env21)).status,
      409
    );
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
        method: "POST", headers: hMuse21, body: JSON.stringify({ request_id: "crw_21_other_owner", claimed_by: "other-desktop-worker", attempt: 1, items: [] }),
      }), env21)).status,
      403
    );
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/fail", {
        method: "POST", headers: hMuse21, body: JSON.stringify({ request_id: "crw_21_other_owner", claimed_by: "other-desktop-worker", attempt: 1 }),
      }), env21)).status,
      403
    );

    // Prepare valid active request owned by muse-ai for completion payload checks (mark th3 claimed first)
    // Finish/remove otherOwnerReq so single-flight lets crw_21_th3_wait get claimed
    otherOwnerReq.status = "completed";
    const claim3Wait = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/claim", {
      method: "POST", headers: hMuse21, body: "{}",
    }), env21)).json();
    assert.equal(claim3Wait.request.id, "crw_21_th3_wait");

    // Non-Threads items rejected 400 (platform='tiktok', shopee URL, non-threads dedup_key)
    for (const badItem of [
      { platform: "tiktok", post_id: "TK_99" },
      { canonical_url: "https://shopee.vn/product/12345" },
      { dedup_key: "shopee:item:999" },
    ]) {
      const badComp = await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({
          request_id: "crw_21_th3_wait",
          attempt: 1,
          result_summary: { contract_version: "3.2", run_id: "run_bad_plat" },
          items: [badItem],
        }),
      }), env21);
      assert.equal(badComp.status, 400, `Expected 400 for non-Threads item ${JSON.stringify(badItem)}`);
    }

    // Claims new posts > 0 with missing/insufficient records rejected 400
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({
          request_id: "crw_21_th3_wait",
          attempt: 1,
          result_summary: { contract_version: "3.2", run_id: "run_no_recs", counters: { new_count: 5, new_qualified_count: 5 } },
        }),
      }), env21)).status,
      400,
      "Must reject completion claiming new posts without items/records"
    );
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({
          request_id: "crw_21_th3_wait",
          attempt: 1,
          result_summary: { contract_version: "3.2", run_id: "run_partial_recs", counters: { new_qualified_count: 5 } },
          items: [{ post_id: "ONLY_ONE_1" }],
        }),
      }), env21)).status,
      400,
      "Must reject completion claiming 5 new posts with only 1 record"
    );
    assert.equal(
      (await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
        method: "POST", headers: hMuse21,
        body: JSON.stringify({ request_id: "crw_21_th3_wait", attempt: 1, result_summary: {} }),
      }), env21)).status,
      400,
      "Must reject empty completion missing V3.2 contract & items[]"
    );

    // Valid V3.2 zero-post completion ('no_qualifying_posts' + contract_version '3.2' + items: []) MUST SUCCEED 200
    const zeroValidComp = await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
      method: "POST", headers: hMuse21,
      body: JSON.stringify({
        request_id: "crw_21_th3_wait",
        attempt: 1,
        result_summary: {
          contract_version: "3.2",
          run_id: "run_zero_v32_ok",
          run_status: "no_qualifying_posts",
          data_quality_status: "khong_dat",
          counters: {
            requested_count: 10,
            scanned_count: 30,
            new_count: 0,
            new_qualified_count: 0,
            rejected_count: 30,
            stop_reason: "no_qualifying_posts",
          },
        },
        items: [],
      }),
    }), env21);
    assert.equal(zeroValidComp.status, 200);
    const zeroJson = await zeroValidComp.json();
    assert.equal(zeroJson.status, "completed");
    assert.equal(zeroJson.registry_sync_status, "ok");
  } finally {
    globalThis.fetch = prevFetch21;
  }
}

// 22. Telegram Cron Completion Gate, Failed Stream Separation, Cursor Preservation, Independent Reports & Desktop-vs-Complete Snapshot Dedup
{
  const tgCalls22 = [];
  let driveReports22 = [];
  const env22 = {
    APP_VIDEO_API_TOKEN: "app-secret-22",
    MUSE_APP_VIDEO_TOKEN: "muse-secret-22",
    TELEGRAM_OWNER_ID: "999022",
    TELEGRAM_BOT_TOKEN_VIDEO: "bot22-token",
    TELEGRAM_WEBHOOK_SECRET: "whsec22",
    GOOGLE_SERVICE_ACCOUNT_JSON: JSON.stringify({ client_email: "sa@test.iam.gserviceaccount.com", private_key: privateKey }),
    GOOGLE_DRIVE_FOLDER_ID: "drive-root-22",
    NOTIFY_BATCH_SIZE: "25",
    DB: new MockD1Database(),
  };

  const prevFetch22 = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const sUrl = String(url);
    if (sUrl.startsWith("https://oauth2.googleapis.com/token")) {
      return new Response(JSON.stringify({ access_token: "mock-token-22" }), { status: 200 });
    }
    if (sUrl.startsWith("https://www.googleapis.com/drive/v3/files?")) {
      const q = new URL(sUrl).searchParams.get("q") || "";
      if (q.includes("'drive-root-22' in parents") && q.includes("'01_Market_Data'")) {
        return new Response(JSON.stringify({ files: [{ id: "mdata-22", name: "01_Market_Data" }] }), { status: 200 });
      }
      if (q.includes("'mdata-22' in parents") && q.includes("'Reports'")) {
        return new Response(JSON.stringify({ files: [{ id: "reports-22", name: "Reports" }] }), { status: 200 });
      }
      if (q.includes("'reports-22' in parents")) {
        return new Response(JSON.stringify({ files: driveReports22.map((r) => r.file) }), { status: 200 });
      }
      if (q.includes(`'${THREADS_ID}' in parents`)) {
        return new Response(JSON.stringify({ files: [] }), { status: 200 });
      }
      return new Response(JSON.stringify({ files: [] }), { status: 200 });
    }
    const matched = driveReports22.find((r) =>
      sUrl.startsWith(`https://www.googleapis.com/drive/v3/files/${encodeURIComponent(r.file.id)}?alt=media`)
    );
    if (matched) {
      return new Response(JSON.stringify(matched.data), { status: 200 });
    }
    if (sUrl.startsWith("https://api.telegram.org/bot")) {
      tgCalls22.push({ url: sUrl, body: JSON.parse(init.body || "{}") });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 2200 + tgCalls22.length } }), { status: 200 });
    }
    throw new Error("Unexpected fetch22: " + sUrl);
  };

  try {
    // 22a: Request states (queued, running, cancelled, missing registry_sync_status=ok) MUST NOT send completion report,
    // cursor MUST NOT skip waiting requests, independent reports MUST send, and duplicates MUST remain suppressed.
    env22.DB.crawlRequests.push(
      {
        id: "crw_22_queued",
        platform: "threads",
        keywords: "kw_q",
        requested_count: 20,
        status: "queued",
        claimed_by: null,
        attempts: 1,
        max_attempts: 3,
        requested_by_chat_id: "999022",
        notified_at: null,
        created_at: "2026-11-27T10:00:00.000Z",
        updated_at: "2026-11-27T10:00:00.000Z",
        result_summary_json: null,
      },
      {
        id: "crw_22_running",
        platform: "threads",
        keywords: "kw_r",
        requested_count: 20,
        status: "running",
        claimed_by: "muse-ai",
        lease_expires_at: "2026-11-28T10:30:00.000Z",
        attempts: 1,
        max_attempts: 3,
        requested_by_chat_id: "999022",
        notified_at: null,
        created_at: "2026-11-27T10:01:00.000Z",
        updated_at: "2026-11-27T10:01:00.000Z",
        result_summary_json: null,
      },
      {
        id: "crw_22_cancelled",
        platform: "threads",
        keywords: "kw_c",
        requested_count: 20,
        status: "cancelled",
        claimed_by: null,
        attempts: 2,
        max_attempts: 3,
        requested_by_chat_id: "999022",
        notified_at: null,
        created_at: "2026-11-27T10:02:00.000Z",
        updated_at: "2026-11-27T10:02:00.000Z",
        result_summary_json: null,
      }
    );

    driveReports22 = [
      {
        file: { id: "drv_22_cancelled", name: "trend_report_22_cancelled.json", mimeType: "application/json", size: "900", modifiedTime: "2026-11-27T10:05:00.000Z" },
        data: {
          contract_version: "3.2",
          request_id: "crw_22_cancelled",
          run_id: "run_22_cancelled",
          crawled_at: "2026-11-27T10:05:00.000Z",
          run_status: "completed",
          counters: { requested_count: 20, new_qualified_count: 0 },
          telegram_summary: "CANCELLED MUST NEVER NOTIFY",
        },
      },
      {
        file: { id: "drv_22_queued", name: "trend_report_22_queued.json", mimeType: "application/json", size: "900", modifiedTime: "2026-11-27T10:06:00.000Z" },
        data: {
          contract_version: "3.2",
          request_id: "crw_22_queued",
          run_id: "run_22_queued",
          crawled_at: "2026-11-27T10:06:00.000Z",
          run_status: "completed",
          counters: { requested_count: 20, new_qualified_count: 4 },
          telegram_summary: "QUEUED REPORT WAITING COMPLETION",
        },
      },
      {
        file: { id: "drv_22_running", name: "trend_report_22_running.json", mimeType: "application/json", size: "900", modifiedTime: "2026-11-27T10:07:00.000Z" },
        data: {
          contract_version: "3.2",
          request_id: "crw_22_running",
          run_id: "run_22_running",
          crawled_at: "2026-11-27T10:07:00.000Z",
          run_status: "completed",
          counters: { requested_count: 20, new_qualified_count: 6 },
          telegram_summary: "RUNNING REPORT WAITING COMPLETION",
        },
      },
      {
        file: { id: "drv_22_indep", name: "trend_report_22_independent.json", mimeType: "application/json", size: "900", modifiedTime: "2026-11-27T10:08:00.000Z" },
        data: {
          contract_version: "3.2",
          run_id: "run_22_independent",
          crawled_at: "2026-11-27T10:08:00.000Z",
          run_status: "completed",
          counters: { requested_count: 10, new_qualified_count: 3 },
          telegram_summary: "INDEPENDENT REPORT OK",
        },
      },
    ];

    await worker.scheduled({}, env22, {});
    assert.equal(tgCalls22.length, 1, "Only independent report without request_id may notify on first tick");
    assert.ok(tgCalls22[0].body.text.includes("INDEPENDENT REPORT OK"));
    assert.equal(env22.DB.crawlRequests.find((c) => c.id === "crw_22_queued").notified_at, null);
    assert.equal(env22.DB.crawlRequests.find((c) => c.id === "crw_22_running").notified_at, null);
    assert.equal(env22.DB.crawlRequests.find((c) => c.id === "crw_22_cancelled").notified_at, null);
    // Cursor MUST advance only to drv_22_cancelled (strictly BEFORE earliest blocked drv_22_queued!)
    assert.equal(env22.DB.runtimeState.get("notify_drive_cursor"), "2026-11-27T10:05:00.000Z|drv_22_cancelled");

    // Repeated Cron while queued/running stay uncompleted -> 0 new messages
    await worker.scheduled({}, env22, {});
    assert.equal(tgCalls22.length, 1);

    // Transition crw_22_queued to completed V3.2 WITHOUT registry_sync_status='ok' -> MUST STILL stay blocked!
    const qRow22 = env22.DB.crawlRequests.find((c) => c.id === "crw_22_queued");
    qRow22.status = "completed";
    qRow22.result_summary_json = JSON.stringify({ contract_version: "3.2", run_id: "run_22_queued", new_qualified_count: 4 });
    qRow22.updated_at = "2026-11-27T10:09:00.000Z";
    await worker.scheduled({}, env22, {});
    assert.equal(tgCalls22.length, 1, "Completed V3.2 request without registry_sync_status=ok MUST NOT notify");
    assert.equal(qRow22.notified_at, null);

    // Now set registry_sync_status='ok' on crw_22_queued AND complete crw_22_running with registry_sync_status='ok'
    qRow22.result_summary_json = JSON.stringify({
      contract_version: "3.2",
      run_id: "run_22_queued",
      registry_sync_status: "ok",
      new_qualified_count: 4,
    });
    const rRow22 = env22.DB.crawlRequests.find((c) => c.id === "crw_22_running");
    rRow22.status = "completed";
    rRow22.result_summary_json = JSON.stringify({
      contract_version: "3.2",
      run_id: "run_22_running",
      registry_sync_status: "ok",
      new_qualified_count: 6,
    });
    rRow22.updated_at = "2026-11-27T10:10:00.000Z";

    await worker.scheduled({}, env22, {});
    assert.equal(tgCalls22.length, 3, "Both unblocked Drive reports notify ONCE without duplicating independent report");
    assert.ok(tgCalls22.slice(1).some((c) => c.body.text.includes("QUEUED REPORT WAITING COMPLETION")));
    assert.ok(tgCalls22.slice(1).some((c) => c.body.text.includes("RUNNING REPORT WAITING COMPLETION")));
    assert.ok(qRow22.notified_at);
    assert.ok(rRow22.notified_at);
    assert.equal(env22.DB.runtimeState.get("notify_drive_cursor"), "2026-11-27T10:08:00.000Z|drv_22_indep");

    // Re-running scheduled() must NOT duplicate notifications
    await worker.scheduled({}, env22, {});
    assert.equal(tgCalls22.length, 3);

    // _corrected Drive report for an already notified completed V3.2 request MUST still deliver cleanly
    driveReports22.push({
      file: { id: "drv_22_running_corr", name: "trend_report_22_running_corrected.json", mimeType: "application/json", size: "950", modifiedTime: "2026-11-27T10:11:00.000Z" },
      data: {
        contract_version: "3.2",
        request_id: "crw_22_running",
        run_id: "run_22_running",
        corrects_report: "trend_report_22_running.json",
        crawled_at: "2026-11-27T10:07:00.000Z",
        run_status: "completed",
        counters: { requested_count: 20, new_qualified_count: 7 },
        telegram_summary: "RUNNING REPORT CORRECTED DELIVERED",
      },
    });
    await worker.scheduled({}, env22, {});
    assert.equal(tgCalls22.length, 4);
    assert.ok(tgCalls22[3].body.text.includes("RUNNING REPORT CORRECTED DELIVERED"));

    // 22b: Failed request with Drive report -> dedicated failure path only, never completion stream
    env22.DB.crawlRequests.push({
      id: "crw_22_failed",
      platform: "threads",
      keywords: "kw_fail",
      requested_count: 20,
      status: "failed",
      claimed_by: "muse-ai",
      attempts: 3,
      max_attempts: 3,
      error_message: "Threads login wall",
      requested_by_chat_id: "999022",
      notified_at: null,
      created_at: "2026-11-27T10:12:00.000Z",
      updated_at: "2026-11-27T10:12:00.000Z",
      result_summary_json: JSON.stringify({ contract_version: "3.2", run_id: "run_22_failed", run_status: "failed" }),
    });
    driveReports22.push({
      file: { id: "drv_22_failed", name: "trend_report_22_failed.json", mimeType: "application/json", size: "900", modifiedTime: "2026-11-27T10:13:00.000Z" },
      data: {
        contract_version: "3.2",
        request_id: "crw_22_failed",
        run_id: "run_22_failed",
        crawled_at: "2026-11-27T10:13:00.000Z",
        run_status: "failed",
        error_message: "Threads login wall",
        telegram_summary: "SHOULD NOT COME FROM DRIVE COMPLETION STREAM",
      },
    });
    await worker.scheduled({}, env22, {});
    assert.equal(tgCalls22.length, 5, "Failed request notifies ONCE via dedicated D1 failed flow, skipped in Drive completion");
    assert.ok(!tgCalls22[4].body.text.includes("SHOULD NOT COME FROM DRIVE COMPLETION STREAM"));
    assert.ok(tgCalls22[4].body.text.includes("Threads login wall"));
    await worker.scheduled({}, env22, {});
    assert.equal(tgCalls22.length, 5);

    // 22c: Desktop Sync vs Completion API snapshot idempotency (BOTH orders, even when ended_at vs item.measured_at differ)
    const hMuse22 = { Authorization: "Bearer muse-secret-22", "Content-Type": "application/json" };
    const hApp22 = { Authorization: "Bearer app-secret-22", "Content-Type": "application/json" };
    env22.DB.crawlRequests.push({
      id: "crw_22_snap_a",
      platform: "threads",
      keywords: "snap_a",
      requested_count: 1,
      status: "queued",
      attempts: 0,
      max_attempts: 3,
      created_at: "2026-11-27T11:00:00.000Z",
      updated_at: "2026-11-27T11:00:00.000Z",
    });
    const claimSnapA = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/claim", {
      method: "POST", headers: hMuse22, body: "{}",
    }), env22)).json();
    assert.equal(claimSnapA.request.id, "crw_22_snap_a");

    const compSnapARes = await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
      method: "POST",
      headers: hMuse22,
      body: JSON.stringify({
        request_id: "crw_22_snap_a",
        attempt: 1,
        result_summary: { contract_version: "3.2", run_id: "run_22_sync_a", crawled_at: "2026-11-27T14:35:00+07:00", run_status: "completed" },
        items: [{
          platform: "threads",
          post_id: "P22_A",
          canonical_url: "https://threads.net/@u22/post/P22_A",
          measured_at: "2026-11-27T14:25:00+07:00",
          views: 15000,
          views_display: "15K",
          views_precision: "rounded_display",
          views_source: "threads_web_browser",
          views_measured_at: "2026-11-27T14:25:00+07:00",
          views_evidence_url: "https://threads.net/@u22/post/P22_A",
          likes: 200,
        }],
      }),
    }), env22);
    assert.equal(compSnapARes.status, 200);
    const snapCountBeforeDesktopA = env22.DB.postSnapshots.size;

    // Desktop sync arrives with same run_id='run_22_sync_a' and fallback ended_at timestamp ('14:35:00+07:00')
    const desktopResA = await (await worker.fetch(new Request("https://example.workers.dev/api/app/market/dedup/record", {
      method: "POST",
      headers: hApp22,
      body: JSON.stringify({
        run_id: "run_22_sync_a",
        crawled_at: "2026-11-27T14:35:00+07:00",
        items: [{
          platform: "threads",
          post_id: "P22_A",
          canonical_url: "https://threads.net/@u22/post/P22_A",
          measured_at: "2026-11-27T14:35:00+07:00",
          views: 15000,
          likes: 200,
        }],
      }),
    }), env22)).json();
    assert.equal(desktopResA.snapshots_created, 0, "Desktop sync MUST NOT create duplicate snapshot after /complete");
    assert.equal(desktopResA.snapshots_deduped, 1);
    assert.equal(env22.DB.postSnapshots.size, snapCountBeforeDesktopA);
    assert.equal(env22.DB.postRegistry.get("threads:post:P22_A").snapshot_count, 1);

    // Reverse order: Desktop sync first, then /complete second on run_22_sync_b
    const desktopResB = await (await worker.fetch(new Request("https://example.workers.dev/api/app/market/dedup/record", {
      method: "POST",
      headers: hApp22,
      body: JSON.stringify({
        run_id: "run_22_sync_b",
        crawled_at: "2026-11-27T14:55:00+07:00",
        items: [{ platform: "threads", post_id: "P22_B", canonical_url: "https://threads.net/@u22/post/P22_B", likes: 80 }],
      }),
    }), env22)).json();
    assert.equal(desktopResB.snapshots_created, 1);

    env22.DB.crawlRequests.push({
      id: "crw_22_snap_b",
      platform: "threads",
      keywords: "snap_b",
      requested_count: 1,
      status: "queued",
      attempts: 0,
      max_attempts: 3,
      created_at: "2026-11-27T11:10:00.000Z",
      updated_at: "2026-11-27T11:10:00.000Z",
    });
    const claimSnapB = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/claim", {
      method: "POST", headers: hMuse22, body: "{}",
    }), env22)).json();
    assert.equal(claimSnapB.request.id, "crw_22_snap_b");
    const compSnapBRes = await (await worker.fetch(new Request("https://example.workers.dev/api/crawl/complete", {
      method: "POST",
      headers: hMuse22,
      body: JSON.stringify({
        request_id: "crw_22_snap_b",
        attempt: 1,
        result_summary: { contract_version: "3.2", run_id: "run_22_sync_b", crawled_at: "2026-11-27T14:50:00+07:00", run_status: "completed" },
        items: [{
          platform: "threads",
          post_id: "P22_B",
          canonical_url: "https://threads.net/@u22/post/P22_B",
          measured_at: "2026-11-27T14:50:00+07:00",
          likes: 80,
        }],
      }),
    }), env22)).json();
    assert.equal(compSnapBRes.dedup_stats.snapshots_created, 0);
    assert.equal(compSnapBRes.dedup_stats.snapshots_deduped, 1);
    assert.equal(env22.DB.postRegistry.get("threads:post:P22_B").snapshot_count, 1);
  } finally {
    globalThis.fetch = prevFetch22;
  }
}

// 23. Manual Prompt Trigger + Strict Claim by request_id + Drive Listener Reconciliation + E2E 1622 fixes
{
  const tgCalls = [];
  let driveFilesByFolder = {
    threads: [],
    reports: [],
  };
  let drivePayloadById = {};
  const prevFetch23 = globalThis.fetch;

  const svcKeyPem = (() => {
    const { privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
    return privateKey.export({ type: "pkcs8", format: "pem" });
  })();

  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.startsWith("https://oauth2.googleapis.com/token")) {
      return new Response(JSON.stringify({ access_token: "drv_tok_23", expires_in: 3600 }), { status: 200 });
    }
    if (u.startsWith("https://www.googleapis.com/drive/v3/files/")) {
      const m = u.match(/\/files\/([^/?]+)/);
      const fid = m ? decodeURIComponent(m[1]) : "";
      if (drivePayloadById[fid] !== undefined) {
        return new Response(JSON.stringify(drivePayloadById[fid]), { status: 200 });
      }
      return new Response(JSON.stringify({ error: "Not found" }), { status: 404 });
    }
    if (u.startsWith("https://www.googleapis.com/drive/v3/files")) {
      const parsedUrl = new URL(u);
      const q = parsedUrl.searchParams.get("q") || "";
      if (q.includes("01_Market_Data")) {
        return new Response(JSON.stringify({ files: [{ id: "mdata_root", name: "01_Market_Data", mimeType: "application/vnd.google-apps.folder" }] }), { status: 200 });
      }
      if (q.includes("'mdata_root' in parents")) {
        return new Response(JSON.stringify({
          files: [
            { id: "threads_dir", name: "Threads", mimeType: "application/vnd.google-apps.folder" },
            { id: "reports_dir", name: "Reports", mimeType: "application/vnd.google-apps.folder" },
          ],
        }), { status: 200 });
      }
      if (q.includes("name = 'Reports'") || q.includes("name='Reports'")) {
        return new Response(JSON.stringify({ files: [{ id: "reports_dir", name: "Reports", mimeType: "application/vnd.google-apps.folder" }] }), { status: 200 });
      }
      if (q.includes("'threads_dir' in parents")) {
        return new Response(JSON.stringify({ files: driveFilesByFolder.threads }), { status: 200 });
      }
      if (q.includes("'reports_dir' in parents")) {
        return new Response(JSON.stringify({ files: driveFilesByFolder.reports }), { status: 200 });
      }
      return new Response(JSON.stringify({ files: [] }), { status: 200 });
    }
    if (u.startsWith("https://api.telegram.org/")) {
      let body = {};
      try { body = JSON.parse(init?.body || "{}"); } catch { }
      tgCalls.push({ url: u, body });
      return new Response(JSON.stringify({ ok: true, result: { message_id: 2300 + tgCalls.length } }), { status: 200 });
    }
    return prevFetch23(url, init);
  };

  try {
    const env23 = {
      DB: new MockD1Database(),
      APP_VIDEO_API_TOKEN: "app-token-23",
      MUSE_APP_VIDEO_TOKEN: "muse-token-23",
      TELEGRAM_BOT_TOKEN_VIDEO: "bot-tok-23",
      TELEGRAM_WEBHOOK_SECRET: "tg-sec-23",
      TELEGRAM_OWNER_ID: "99887766",
      MUSE_WEB_URL: "https://muse.ai/",
      THREADS_FOLDER_ID: "legacy_threads_dir",
      GOOGLE_SERVICE_ACCOUNT_JSON: JSON.stringify({
        client_email: "svc@test-project.iam.gserviceaccount.com",
        private_key: svcKeyPem,
      }),
    };

    const findReq = (id) => Array.from(env23.DB.crawlRequests.values()).find(r => r.id === id);
    const countReqs = () => Array.from(env23.DB.crawlRequests.values()).length;

    const appHeaders = {
      "Content-Type": "application/json",
      Authorization: "Bearer app-token-23",
    };
    const museHeaders = {
      "Content-Type": "application/json",
      Authorization: "Bearer muse-token-23",
    };

    // 23A: Shared Manual Prompt Trigger + Display States + Non-duplication + Platform contracts
    const createThreadsResp = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({ platform: "threads", requested_count: 10, keywords: "serum vitamin c" }),
      }),
      env23
    )).json();
    assert.equal(createThreadsResp.ok, true);
    const reqThreads1 = createThreadsResp.request;
    assert.equal(reqThreads1.display_status, "waiting_prompt");
    assert.equal(reqThreads1.display_status_label, "Chờ gửi prompt");
    assert.equal(reqThreads1.strategy_version, "3.3 (sửa đổi 28/11/2026)");
    assert.equal(reqThreads1.contract_version, "3.2");
    assert.equal(reqThreads1.lease_minutes, 30);
    assert.equal(reqThreads1.lease_seconds, 1800);
    assert.equal(reqThreads1.muse_web_url, "https://muse.ai/");

    const assertThreadsStrategy33Prompt = (promptText, expectedReqId, expectedKw = null, expectedCount = null) => {
      const requiredPatterns = [
        new RegExp(`Request ID: ${expectedReqId}`),
        /Platform: threads/,
        expectedCount !== null ? new RegExp(`Requested Count: ${expectedCount} bài mới đạt chuẩn`) : /Requested Count: \d+ bài mới đạt chuẩn/,
        /Strategy Version: 3\.3 \(sửa đổi 28\/11\/2026\)/,
        /Contract Version: 3\.2/,
        /@toilaai267/,
        /Cloud Browser/,
        /threads-cli/,
        /Custom Connector/,
        /đăng nhập lại trước khi claim/,
        new RegExp(`POST /api/crawl/claim[\\s\\S]*\\{"request_id":"${expectedReqId}"\\}`),
        /Không fallback sang request khác/,
        /max\(10 phút, 20% lease\)/,
        /Crawl Strategy 3\.3/,
        /Việt Nam/,
        /24 giờ gần nhất.*48 giờ/,
        /Mở rộng tìm kiếm ngoài trends chính thức/,
        /viral tự nhiên từ người dùng bình thường, creator nhỏ/i,
        /Hạn chế người nổi tiếng, ca sĩ, thương hiệu lớn/i,
        /Không loại cứng người bán hoặc thương hiệu nhỏ/i,
        /POST \/api\/crawl\/registry\/check/,
        /Mở rộng nguồn tìm kiếm nếu chưa đạt mục tiêu/i,
        /Bắt buộc xác minh cả views và likes/,
        /Không bỏ qua bước đo views chỉ vì likes thấp/i,
        /5 trường chứng cứ views theo V3\.2/,
        /Phân biệt rõ số 0 với null/,
        /Không đặt sàn views hoặc likes.*Watchlist/i,
        /Không dừng giữa chừng nếu còn ứng viên phù hợp/,
        /Very Hot: ≥ 1\.000 likes hoặc ≥ 50\.000 views/,
        /Hot: ≥ 300 likes hoặc ≥ 10\.000 views/,
        /Emerging:/,
        /Watchlist:/,
        /Unverified:/,
        /Rejected:/,
        /Followers của tác giả/,
        /Views\/followers/,
        /Natural Viral \(natural_viral\)/,
        /Buyer Intent \(buyer_intent\)/,
        /Nguyên nhân viral và khả năng ứng dụng Affiliate/i,
        /Đo lại (?:các )?bài lịch sử có tiềm năng tăng trưởng.*không tính vào số bài mới/i,
        /Không thu thập toàn bài Watchlist chỉ để đạt mục tiêu/i,
        /POST \/api\/crawl\/progress/,
        /Tuân thủ ngân sách lease\. Không tự gia hạn/,
        /run_id.*Run-centric UI/,
        /Threads\/threads_trend_<YYYYMMDD>_<HHmm>\.json/,
        /Reports\/trend_report_<YYYYMMDD>_<HHmm>\.json/,
        /Reports\/trend_report_<YYYYMMDD>_<HHmm>\.md/,
        /strategy_version="3\.3 \(sửa đổi 28\/11\/2026\)"/,
        /snapshot tương tác tại thời điểm crawl/,
        /Growth Tracking \(9_growth_tracking\)/,
        /POST \/api\/crawl\/complete/,
        /POST \/api\/crawl\/fail/,
        /Không bật Hook, không tạo Scheduled Task, không ghi SQL trực tiếp/,
        /đồng bộ Registry và trạng thái Telegram Notification/,
      ];
      for (const pat of requiredPatterns) {
        assert.match(promptText, pat, `Prompt missing expected Strategy 3.3 requirement: ${pat}`);
      }
      assert.doesNotMatch(promptText, /6–10 phút/);
      if (expectedKw) assert.ok(promptText.includes(expectedKw));
    };

    assertThreadsStrategy33Prompt(reqThreads1.manual_prompt, reqThreads1.id, "serum vitamin c", 10);

    // Listing endpoint also returns unified Strategy 3.3 prompt
    const listReqsResp = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", { headers: appHeaders }),
      env23
    )).json();
    const listedReq1 = listReqsResp.items.find(r => r.id === reqThreads1.id);
    assert.equal(listedReq1.manual_prompt, reqThreads1.manual_prompt);

    // Copy lại Prompt endpoint returns exact prompt without creating extra D1 requests
    const countBeforeCopy = countReqs();
    const promptAgain1 = await (await worker.fetch(
      new Request(`https://worker.example/api/app/crawl-requests/${reqThreads1.id}/prompt`, { headers: appHeaders }),
      env23
    )).json();
    const promptAgain2 = await (await worker.fetch(
      new Request(`https://worker.example/api/app/crawl-requests/${reqThreads1.id}/prompt`, { headers: appHeaders }),
      env23
    )).json();
    assert.equal(countReqs(), countBeforeCopy);
    assert.equal(promptAgain1.manual_prompt, reqThreads1.manual_prompt);
    assert.equal(promptAgain2.manual_prompt, reqThreads1.manual_prompt);

    // Per-platform prompt differences: TikTok, Shopee & ALL must NOT advertise /api/crawl/* completion support
    const createTtResp = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({ platform: "tiktok", requested_count: 15, keywords: "skincare" }),
      }),
      env23
    )).json();
    assert.equal(createTtResp.request.contract_version, "v2");
    assert.match(createTtResp.request.manual_prompt, /CHƯA hỗ trợ tự động hoàn tất request đầy đủ cho TikTok/);
    assert.match(createTtResp.request.manual_prompt, /CHỈ hỗ trợ platform="threads"/);
    assert.match(createTtResp.request.manual_prompt, /KHÔNG dùng các endpoint \/api\/crawl\/\*/);

    const createSpResp = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({ platform: "shopee", requested_count: 15, keywords: "serum" }),
      }),
      env23
    )).json();
    assert.equal(createSpResp.request.contract_version, "v2");
    assert.match(createSpResp.request.manual_prompt, /CHƯA hỗ trợ tự động hoàn tất request đầy đủ cho Shopee/);
    assert.match(createSpResp.request.manual_prompt, /KHÔNG dùng các endpoint \/api\/crawl\/\*/);

    const createAllResp = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({ platform: "all", requested_count: 20, keywords: "review" }),
      }),
      env23
    )).json();
    assert.match(createAllResp.request.manual_prompt, /CHƯA hỗ trợ tự động hoàn tất request đầy đủ cho platform="all"/);

    // Telegram Manual Prompt Trigger uses same shared buildMuseManualPrompt & inline Mở Muse button
    const tgReqRes = await worker.fetch(
      new Request("https://worker.example/telegram", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": "tg-sec-23",
        },
        body: JSON.stringify({
          update_id: 2301,
          message: {
            message_id: 11,
            from: { id: 99887766 },
            chat: { id: 99887766, type: "private" },
            text: "/request_crawl threads 12 retinol",
          },
        }),
      }),
      env23
    );
    assert.equal(tgReqRes.status, 200);
    const tgPromptMsg = tgCalls.find(c => String(c.body?.text || "").includes("Request ID:"));
    assert.ok(tgPromptMsg, "Telegram /request_crawl should output Manual Prompt");
    assert.ok(tgPromptMsg.body.text.length <= 4096, `Telegram message exceeded 4096 chars: ${tgPromptMsg.body.text.length}`);
    assert.ok(tgPromptMsg.body.text.trim().endsWith("</pre>"), "Telegram HTML must cleanly close </pre>");
    assert.match(tgPromptMsg.body.text, /<pre>[\s\S]*Strategy Version: 3\.3 \(sửa đổi 28\/11\/2026\)[\s\S]*Contract Version: 3\.2[\s\S]*<\/pre>/);
    const museBtn = tgPromptMsg.body.reply_markup.inline_keyboard.flat().find(b => b.url === "https://muse.ai/");
    assert.ok(museBtn, "Telegram response must include Mở Muse button");
    const reqThreads2 = Array.from(env23.DB.crawlRequests.values()).find(r => r.keywords === "retinol");
    assert.ok(reqThreads2, "Telegram should create second threads request");

    // Verify Telegram copy callback returns IDENTICAL Strategy 3.3 prompt as App_Video prompt endpoint
    const appSidePromptRes = await (await worker.fetch(
      new Request(`https://worker.example/api/app/crawl-requests/${reqThreads2.id}/prompt`, { headers: appHeaders }),
      env23
    )).json();
    assertThreadsStrategy33Prompt(appSidePromptRes.manual_prompt, reqThreads2.id, "retinol", 12);
    const unescapedTgPre = tgPromptMsg.body.text
      .match(/<pre>([\s\S]*)<\/pre>/)[1]
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&");
    assert.equal(unescapedTgPre, appSidePromptRes.manual_prompt);

    // Re-fetching prompt via Telegram callback does NOT create new request
    const countBeforeTgPrompt = countReqs();
    await worker.fetch(
      new Request("https://worker.example/telegram", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": "tg-sec-23",
        },
        body: JSON.stringify({
          update_id: 2302,
          callback_query: {
            id: "cb_prompt_23",
            from: { id: 99887766 },
            message: { message_id: 12, chat: { id: 99887766, type: "private" } },
            data: `crawl:prompt:${reqThreads2.id}`,
          },
        }),
      }),
      env23
    );
    assert.equal(countReqs(), countBeforeTgPrompt);

    // 23B: Strict POST /api/crawl/claim by request_id (no fallback across queued items)
    // Order: reqThreads1 (older queued), reqThreads2 (newer queued), createTtResp.request (tiktok)
    const missingClaim = await worker.fetch(
      new Request("https://worker.example/api/crawl/claim", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({ request_id: "crw_missing_999" }),
      }),
      env23
    );
    assert.equal(missingClaim.status, 404);
    assert.equal(findReq(reqThreads1.id).status, "queued");
    assert.equal(findReq(reqThreads2.id).status, "queued");

    const wrongPlatClaim = await worker.fetch(
      new Request("https://worker.example/api/crawl/claim", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({ request_id: createTtResp.request.id }),
      }),
      env23
    );
    assert.equal(wrongPlatClaim.status, 403);
    assert.equal(findReq(reqThreads1.id).status, "queued");

    // Target claim explicitly selects newer reqThreads2 instead of older reqThreads1
    const claimNewRes = await worker.fetch(
      new Request("https://worker.example/api/crawl/claim", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({ request_id: reqThreads2.id }),
      }),
      env23
    );
    assert.equal(claimNewRes.status, 200);
    const claimNewJson = await claimNewRes.json();
    assert.equal(claimNewJson.request.id, reqThreads2.id);
    assert.equal(claimNewJson.request.attempt, 1);
    assert.equal(findReq(reqThreads1.id).status, "queued");

    // Single-flight blocks claiming reqThreads1 while reqThreads2 is running
    const claimOtherWhileRunning = await worker.fetch(
      new Request("https://worker.example/api/crawl/claim", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({ request_id: reqThreads1.id }),
      }),
      env23
    );
    assert.equal(claimOtherWhileRunning.status, 200);
    const claimOtherJson = await claimOtherWhileRunning.json();
    assert.equal(claimOtherJson.request, null);
    assert.equal(claimOtherJson.single_flight_blocked, true);
    assert.equal(claimOtherJson.reason, "active_crawl_in_progress");
    assert.equal(findReq(reqThreads1.id).status, "queued");

    // 23C: Drive Listener Reconciliation (Cron) — waiting_pair, validation_failed, stale attempt, valid pair, concurrency race
    const tgCountBeforeReconcile = tgCalls.length;
    const relIso = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();
    const validDrivePost = {
      platform: "threads",
      post_id: "P_DRV_23_1",
      url: "https://www.threads.com/@brand.vn/post/P_DRV_23_1",
      author_handle: "@brand.vn",
      text: "Review serum vitamin C da nhạy cảm",
      views: 15200,
      likes: 420,
      comments: 31,
      reposts: 12,
      shares: 9,
      views_display: "15.2K",
      views_precision: "exact_badge",
      views_source: "threads_web_browser",
      views_measured_at: relIso(-60_000),
      views_evidence_url: "https://www.threads.com/@brand.vn/post/P_DRV_23_1",
      posted_at: relIso(-7200_000),
      classification: "NEW",
    };
    const buildV32Report = (reqId, runId, attempt, qualifiedCount = 1) => ({
      contract_version: "3.2",
      request_id: reqId,
      run_id: runId,
      attempt,
      claimed_by: "muse-ai",
      platform: "threads",
      crawled_at: relIso(-30_000),
      generated_at: relIso(-30_000),
      run_status: qualifiedCount > 0 ? "completed" : "no_qualifying_posts",
      stop_reason: qualifiedCount > 0 ? "target_reached" : "no_qualifying_posts",
      counters: {
        scanned_count: qualifiedCount,
        unique_candidate_count: qualifiedCount,
        new_qualified_count: qualifiedCount,
        updated_metrics_count: 0,
        duplicate_history_count: 0,
        rejected_count: 0,
        unverified_count: 0,
        missing_views_count: 0,
        counters_reconciled: true,
      },
      sections: {
        "1_screening": { scanned: qualifiedCount, new_qualified: qualifiedCount, updated_metrics: 0, rejected: 0, missing_views: 0 },
        "2_market": "Serum vitamin C tăng trưởng ổn định",
        "4_growth": "Tương tác tốt ở phân khúc skincare",
      },
      telegram_summary: `Drive Reconcile Test (${qualifiedCount} bài mới)`,
    });

    // Subcase 1: Data JSON alone (no Report JSON) -> waiting_pair, NOT completed, 0 registry writes, 0 Telegram
    driveFilesByFolder.threads = [
      { id: "drv_data_only", name: "threads_trend_20261128_1000.json", mimeType: "application/json", modifiedTime: relIso(1000) },
    ];
    driveFilesByFolder.reports = [];
    drivePayloadById = {
      drv_data_only: {
        contract_version: "3.2",
        request_id: reqThreads2.id,
        run_id: "run_drv_23",
        attempt: 1,
        claimed_by: "muse-ai",
        platform: "threads",
        counters: { scanned_count: 1, new_qualified_count: 1 },
        items: [validDrivePost],
      },
    };
    await worker.scheduled({}, env23, {});
    const afterSingleFile = findReq(reqThreads2.id);
    assert.equal(afterSingleFile.status, "running");
    assert.equal(JSON.parse(afterSingleFile.result_summary_json).drive_reconcile.status, "waiting_pair");
    assert.equal(env23.DB.postRegistry.size, 0);
    assert.equal(tgCalls.length, tgCountBeforeReconcile);

    // Subcase 2: Pair pointing to UNCLAIMED queued request (reqThreads1) + CANCELLED request -> NEVER completed
    const reqCancelledResp = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({ platform: "threads", requested_count: 5, keywords: "cancel check" }),
      }),
      env23
    )).json();
    await worker.fetch(
      new Request(`https://worker.example/api/app/crawl-requests/${reqCancelledResp.request.id}/cancel`, {
        method: "POST",
        headers: appHeaders,
      }),
      env23
    );
    driveFilesByFolder.threads = [
      { id: "drv_d_queued", name: "threads_trend_20261128_1001.json", mimeType: "application/json", modifiedTime: relIso(2000) },
      { id: "drv_d_cancel", name: "threads_trend_20261128_1002.json", mimeType: "application/json", modifiedTime: relIso(2000) },
    ];
    driveFilesByFolder.reports = [
      { id: "drv_r_queued", name: "trend_report_20261128_1001.json", mimeType: "application/json", modifiedTime: relIso(2000) },
      { id: "drv_r_cancel", name: "trend_report_20261128_1002.json", mimeType: "application/json", modifiedTime: relIso(2000) },
    ];
    drivePayloadById = {
      drv_d_queued: { contract_version: "3.2", request_id: reqThreads1.id, run_id: "run_unclaimed", attempt: 1, claimed_by: "muse-ai", platform: "threads", items: [validDrivePost] },
      drv_r_queued: buildV32Report(reqThreads1.id, "run_unclaimed", 1, 1),
      drv_d_cancel: { contract_version: "3.2", request_id: reqCancelledResp.request.id, run_id: "run_cancelled", attempt: 1, claimed_by: "muse-ai", platform: "threads", items: [validDrivePost] },
      drv_r_cancel: buildV32Report(reqCancelledResp.request.id, "run_cancelled", 1, 1),
    };
    await worker.scheduled({}, env23, {});
    assert.equal(findReq(reqThreads1.id).status, "queued");
    assert.equal(JSON.parse(findReq(reqThreads1.id).result_summary_json).drive_reconcile.status, "validation_failed");
    assert.equal(findReq(reqCancelledResp.request.id).status, "cancelled");
    assert.equal(env23.DB.postRegistry.size, 0);

    // Subcase 3: Stale attempt=1 artifact when reqThreads2 is on attempt=2 -> stale_attempt_rejected
    findReq(reqThreads2.id).attempts = 2;
    driveFilesByFolder.threads = [
      { id: "drv_d_stale", name: "threads_trend_20261128_1003.json", mimeType: "application/json", modifiedTime: relIso(3000) },
    ];
    driveFilesByFolder.reports = [
      { id: "drv_r_stale", name: "trend_report_20261128_1003.json", mimeType: "application/json", modifiedTime: relIso(3000) },
    ];
    drivePayloadById = {
      drv_d_stale: { contract_version: "3.2", request_id: reqThreads2.id, run_id: "run_drv_23", attempt: 1, claimed_by: "muse-ai", platform: "threads", counters: { scanned_count: 1, new_qualified_count: 1 }, items: [validDrivePost] },
      drv_r_stale: buildV32Report(reqThreads2.id, "run_drv_23", 1, 1),
    };
    await worker.scheduled({}, env23, {});
    const afterStale = findReq(reqThreads2.id);
    assert.equal(afterStale.status, "running");
    assert.equal(JSON.parse(afterStale.result_summary_json).drive_reconcile.status, "stale_attempt_rejected");
    assert.equal(env23.DB.postRegistry.size, 0);

    // Subcase 4: Valid attempt=2 Data+Report pair -> reconciles to completed + syncs Registry + sends Telegram ONCE
    driveFilesByFolder.threads = [
      { id: "drv_d_stale", name: "threads_trend_20261128_1003.json", mimeType: "application/json", modifiedTime: relIso(3000) },
      { id: "drv_d_ok", name: "threads_trend_20261128_1008.json", mimeType: "application/json", modifiedTime: relIso(4000) },
    ];
    driveFilesByFolder.reports = [
      { id: "drv_r_stale", name: "trend_report_20261128_1003.json", mimeType: "application/json", modifiedTime: relIso(3000) },
      { id: "drv_r_ok", name: "trend_report_20261128_1008.json", mimeType: "application/json", modifiedTime: relIso(4000) },
    ];
    drivePayloadById.drv_d_ok = {
      contract_version: "3.2",
      request_id: reqThreads2.id,
      run_id: "run_drv_23_att2",
      attempt: 2,
      claimed_by: "muse-ai",
      platform: "threads",
      counters: { scanned_count: 1, new_qualified_count: 1 },
      items: [validDrivePost],
    };
    drivePayloadById.drv_r_ok = buildV32Report(reqThreads2.id, "run_drv_23_att2", 2, 1);

    await worker.scheduled({}, env23, {});
    const completedReq2 = findReq(reqThreads2.id);
    assert.equal(completedReq2.status, "completed");
    assert.equal(JSON.parse(completedReq2.result_summary_json).registry_sync_status, "ok");
    assert.equal(JSON.parse(completedReq2.result_summary_json).drive_reconcile.status, "ok");
    assert.equal(env23.DB.postRegistry.get("threads:post:P_DRV_23_1").snapshot_count, 1);
    const tgSentAfterReconcile = tgCalls.slice(tgCountBeforeReconcile).filter(c => c.url.includes("sendMessage"));
    assert.equal(tgSentAfterReconcile.length, 1);
    assert.ok(completedReq2.notified_at);

    // Second Cron tick produces ZERO extra snapshots or Telegram messages
    const tgTotalAfterTick1 = tgCalls.length;
    await worker.scheduled({}, env23, {});
    assert.equal(env23.DB.postRegistry.get("threads:post:P_DRV_23_1").snapshot_count, 1);
    assert.equal(tgCalls.length, tgTotalAfterTick1);

    // Subcase 5: Concurrent race between POST /api/crawl/complete & worker.scheduled()
    const claimRace = await worker.fetch(
      new Request("https://worker.example/api/crawl/claim", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({ request_id: reqThreads1.id }),
      }),
      env23
    );
    assert.equal(claimRace.status, 200);
    const racePost = {
      ...validDrivePost,
      post_id: "P_RACE_23",
      url: "https://www.threads.com/@race.vn/post/P_RACE_23",
      views_evidence_url: "https://www.threads.com/@race.vn/post/P_RACE_23",
    };
    driveFilesByFolder.threads = [
      { id: "drv_d_race", name: "threads_trend_20261128_1015.json", mimeType: "application/json", modifiedTime: relIso(5000) },
    ];
    driveFilesByFolder.reports = [
      { id: "drv_r_race", name: "trend_report_20261128_1015.json", mimeType: "application/json", modifiedTime: relIso(5000) },
    ];
    drivePayloadById = {
      drv_d_race: {
        contract_version: "3.2",
        request_id: reqThreads1.id,
        run_id: "run_race_23",
        attempt: 1,
        claimed_by: "muse-ai",
        platform: "threads",
        counters: { scanned_count: 1, new_qualified_count: 1 },
        items: [racePost],
      },
      drv_r_race: buildV32Report(reqThreads1.id, "run_race_23", 1, 1),
    };
    const tgBeforeRace = tgCalls.filter(c => c.url.includes("sendMessage")).length;
    const [apiRaceRes] = await Promise.all([
      worker.fetch(
        new Request("https://worker.example/api/crawl/complete", {
          method: "POST",
          headers: museHeaders,
          body: JSON.stringify({
            request_id: reqThreads1.id,
            attempt: 1,
            run_id: "run_race_23",
            report_file_id: "drv_r_race",
            items: [racePost],
            result_summary: buildV32Report(reqThreads1.id, "run_race_23", 1, 1),
          }),
        }),
        env23
      ),
      worker.scheduled({}, env23, {}),
    ]);
    assert.ok([200, 409].includes(apiRaceRes.status));
    assert.equal(findReq(reqThreads1.id).status, "completed");
    assert.equal(env23.DB.postRegistry.get("threads:post:P_RACE_23").snapshot_count, 1);
    const tgAfterRace = tgCalls.filter(c => c.url.includes("sendMessage")).length;
    assert.equal(tgAfterRace - tgBeforeRace, 1);

    // 23D: E2E 1622 fixes — @shopee.vn hostname allowlist, REJECTED vs UPDATED_METRICS, missing_views_count, +07:00 vs Z, 0-qualified notification
    driveFilesByFolder = { threads: [], reports: [] };
    drivePayloadById = {};
    env23.DB.postRegistry.set("threads:post:P1622_REJ", {
      dedup_key: "threads:post:P1622_REJ",
      platform: "threads",
      post_id: "P1622_REJ",
      canonical_url: "https://www.threads.com/@shopee.vn/post/P1622_REJ",
      first_seen_at: "2026-11-27T08:00:00Z",
      last_seen_at: "2026-11-27T15:00:00+07:00",
      latest_metrics_json: JSON.stringify({ views: null, likes: 5, comments: 1, shares: 0 }),
      snapshot_count: 1,
    });
    env23.DB.postRegistry.set("threads:post:P1622_UPD", {
      dedup_key: "threads:post:P1622_UPD",
      platform: "threads",
      post_id: "P1622_UPD",
      canonical_url: "https://www.threads.com/@creator.vn/post/P1622_UPD",
      first_seen_at: "2026-11-27T08:00:00Z",
      last_seen_at: "2026-11-27T15:00:00+07:00", // = 08:00Z
      latest_metrics_json: JSON.stringify({ views: null, likes: 10, comments: 2, shares: 1 }),
      snapshot_count: 1,
    });

    const req1622Create = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({ platform: "threads", requested_count: 10, keywords: "e2e1622" }),
      }),
      env23
    )).json();
    const req1622Id = req1622Create.request.id;
    assert.equal(req1622Create.request.requested_by_chat_id || null, null);
    await worker.fetch(
      new Request("https://worker.example/api/crawl/claim", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({ request_id: req1622Id }),
      }),
      env23
    );

    // Non-Threads hostname (shopee.vn) MUST still be rejected with 400
    const badHostRes = await worker.fetch(
      new Request("https://worker.example/api/crawl/complete", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({
          request_id: req1622Id,
          attempt: 1,
          run_id: "run_bad_host_23",
          items: [{ platform: "threads", post_id: "BAD_H", url: "https://shopee.vn/product/123", text: "bad" }],
          result_summary: buildV32Report(req1622Id, "run_bad_host_23", 1, 1),
        }),
      }),
      env23
    );
    assert.equal(badHostRes.status, 400);

    // Valid @shopee.vn URL + REJECTED reposts:null->0 + UPDATED_METRICS without views + 1 unverified missing views + mixed-TZ older late snapshot
    const tgBefore1622 = tgCalls.length;
    const comp1622 = await worker.fetch(
      new Request("https://worker.example/api/crawl/complete", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({
          request_id: req1622Id,
          attempt: 1,
          run_id: "run_1622_fix",
          items: [
            {
              platform: "threads",
              post_id: "P1622_REJ",
              url: "https://www.threads.com/@shopee.vn/post/P1622_REJ",
              author_handle: "@shopee.vn",
              likes: 5,
              comments: 1,
              shares: 0,
              reposts: 0,
              views: null,
              classification: "REJECTED",
              crawled_at: "2026-11-28T03:30:00Z",
            },
            {
              platform: "threads",
              post_id: "P1622_UPD",
              url: "https://www.threads.com/@creator.vn/post/P1622_UPD",
              author_handle: "@creator.vn",
              likes: 25,
              comments: 2,
              shares: 1,
              views: null,
              classification: "UPDATED_METRICS",
              crawled_at: "2026-11-28T03:30:00Z",
            },
            {
              platform: "threads",
              post_id: "P1622_UPD",
              url: "https://www.threads.com/@creator.vn/post/P1622_UPD",
              author_handle: "@creator.vn",
              likes: 7,
              comments: 1,
              shares: 0,
              views: null,
              classification: "DUPLICATE_HISTORY",
              crawled_at: "2026-11-27T09:00:00+07:00", // = 02:00Z, older than existing 08:00Z!
            },
            {
              platform: "threads",
              post_id: "P1622_MISS",
              url: "https://www.threads.com/@newshop.vn/post/P1622_MISS",
              author_handle: "@newshop.vn",
              likes: 30,
              comments: 5,
              shares: 2,
              views: null,
              classification: "NEW_UNVERIFIED",
              crawled_at: "2026-11-28T03:30:00Z",
            },
          ],
          result_summary: {
            contract_version: "3.2",
            request_id: req1622Id,
            run_id: "run_1622_fix",
            platform: "threads",
            crawled_at: "2026-11-28T03:30:00Z",
            run_status: "no_qualifying_posts",
            stop_reason: "no_qualifying_posts",
            counters: {
              scanned_count: 4,
              unique_candidate_count: 3,
              new_qualified_count: 0,
              updated_metrics_count: 1,
              duplicate_history_count: 0,
              rejected_count: 1,
              unverified_count: 1,
              missing_views_count: 1,
              counters_reconciled: true,
            },
            sections: {
              "1_screening": { scanned: 4, new_qualified: 0, updated_metrics: 1, rejected: 1, missing_views: 1 },
              "3_trends": [{ topic: "Skincare routine tối giản", count: 2 }],
              "9_growth_tracking": ["Tệp follower tăng nhanh ở nhóm beauty"],
            },
            telegram_summary: "Đã quét 4 bài: 0 bài đạt chuẩn mới, 1 cập nhật metric, 1 thiếu views.",
          },
        }),
      }),
      env23
    );
    assert.equal(comp1622.status, 200);
    const comp1622Json = await comp1622.json();
    assert.equal(comp1622Json.dedup_stats.rejected_count, 1);
    assert.equal(comp1622Json.dedup_stats.updated_metrics_count, 1);
    assert.equal(comp1622Json.dedup_stats.new_qualified_count, 0);
    assert.equal(comp1622Json.dedup_stats.missing_views_count, 1);
    assert.equal(comp1622Json.display_status, "completed");
    assert.equal(comp1622Json.telegram_notified, true);
    assert.equal(JSON.parse(findReq(req1622Id).result_summary_json).missing_views_count, 1);
    const regUpd = env23.DB.postRegistry.get("threads:post:P1622_UPD");
    assert.equal(JSON.parse(regUpd.latest_metrics_json).likes, 25);
    assert.equal(Date.parse(regUpd.last_seen_at) >= Date.parse("2026-11-28T03:30:00Z"), true);
    const rejSnaps = Array.from(env23.DB.postSnapshots.values()).filter(s => s.dedup_key === "threads:post:P1622_REJ");
    assert.ok(rejSnaps.some(s => s.classification === "REJECTED"));
    const tgSent1622 = tgCalls.slice(tgBefore1622).filter(c => c.url.includes("sendMessage") || c.url.includes("editMessageText"));
    assert.equal(tgSent1622.length, 1);
    assert.equal(String(tgSent1622[0].body.chat_id), "99887766");
    assert.match(tgSent1622[0].body.text, /🔥 Trend thị trường/);
    assert.match(tgSent1622[0].body.text, /📈 Biến động so với lượt trước/);

    // Pure 0-item no_qualifying_posts V3.2 completion succeeds and delivers structured summary
    const reqZeroCreate = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({ platform: "threads", requested_count: 10, keywords: "zero_valid" }),
      }),
      env23
    )).json();
    await worker.fetch(
      new Request("https://worker.example/api/crawl/claim", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({ request_id: reqZeroCreate.request.id }),
      }),
      env23
    );
    const compZero = await worker.fetch(
      new Request("https://worker.example/api/crawl/complete", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({
          request_id: reqZeroCreate.request.id,
          attempt: 1,
          run_id: "run_zero_v32_23",
          items: [],
          result_summary: {
            contract_version: "3.2",
            request_id: reqZeroCreate.request.id,
            run_id: "run_zero_v32_23",
            platform: "threads",
            crawled_at: "2026-11-28T03:45:00Z",
            run_status: "no_qualifying_posts",
            stop_reason: "no_qualifying_posts",
            counters: {
              scanned_count: 15,
              unique_candidate_count: 0,
              new_qualified_count: 0,
              updated_metrics_count: 0,
              duplicate_history_count: 0,
              rejected_count: 0,
              unverified_count: 0,
              missing_views_count: 0,
              counters_reconciled: true,
            },
            sections: {
              "1_screening": { scanned: 15, new_qualified: 0 },
              "3_trends": [{ topic: "Chưa đủ mẫu đạt chuẩn", count: 0 }],
            },
            telegram_summary: "Đã quét 15 bài nhưng không có bài đạt chuẩn mới.",
          },
        }),
      }),
      env23
    );
    assert.equal(compZero.status, 200);
    const compZeroJson = await compZero.json();
    assert.equal(compZeroJson.status, "completed");
    assert.equal(compZeroJson.registry_sync_status, "ok");
    assert.equal(compZeroJson.telegram_notified, true);

    // 23E: Safe optional Threads lease duration (App_Video + Telegram + POST /api/crawl/claim stored options + clamping)
    const reqDurDefault = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({ platform: "threads", requested_count: 20, keywords: "gia dung thong minh" }),
      }),
      env23
    )).json();
    assert.equal(reqDurDefault.ok, true);
    assert.equal(reqDurDefault.request.lease_seconds, 1800);
    assert.equal(reqDurDefault.request.lease_minutes, 30);

    // Request with 90m duration MUST NOT deduplicate onto queued 30m request and MUST store 5400s
    const reqDur90 = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({
          platform: "threads",
          requested_count: 20,
          keywords: "gia dung thong minh",
          options: { lease_seconds: 5400 },
        }),
      }),
      env23
    )).json();
    assert.equal(reqDur90.ok, true);
    assert.equal(reqDur90.deduplicated, false);
    assert.notEqual(reqDur90.request.id, reqDurDefault.request.id);
    assert.equal(reqDur90.request.lease_seconds, 5400);
    assert.equal(reqDur90.request.lease_minutes, 90);
    assert.match(reqDur90.manual_prompt, /Thời lượng lease: 90 phút \(5400s/);
    assert.match(reqDur90.manual_prompt, /≈ 18p\b/);
    assertThreadsStrategy33Prompt(reqDur90.manual_prompt, reqDur90.request.id, "gia dung thong minh", 20);

    // Oversized lease_seconds clamps strictly to Worker 7200s (120 phút) safety cap
    const reqDurClamped = await (await worker.fetch(
      new Request("https://worker.example/api/app/crawl-requests", {
        method: "POST",
        headers: appHeaders,
        body: JSON.stringify({
          platform: "threads",
          requested_count: 25,
          keywords: "clamp cap test",
          options: { lease_seconds: 99999 },
        }),
      }),
      env23
    )).json();
    assert.equal(reqDurClamped.request.lease_seconds, 7200);
    assert.equal(reqDurClamped.request.lease_minutes, 120);

    // Claiming by {"request_id": reqDur90.id} WITHOUT passing lease_seconds grants stored 5400s (~90m) lease automatically
    const claimBeforeMs = Date.now();
    const claim90 = await (await worker.fetch(
      new Request("https://worker.example/api/crawl/claim", {
        method: "POST",
        headers: museHeaders,
        body: JSON.stringify({ request_id: reqDur90.request.id }),
      }),
      env23
    )).json();
    assert.equal(claim90.ok, true);
    const leaseDeltaSec = Math.round((Date.parse(claim90.request.lease_expires_at) - claimBeforeMs) / 1000);
    assert.ok(leaseDeltaSec >= 5390 && leaseDeltaSec <= 5410, `Expected ~5400s lease, got ${leaseDeltaSec}s`);

    // Telegram /request_crawl optional duration token e.g. 60m + keywords works safely and stays <= 4096 chars
    const tg60Res = await worker.fetch(
      new Request("https://worker.example/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "tg-sec-23" },
        body: JSON.stringify({
          update_id: 2390,
          message: {
            message_id: 91,
            from: { id: 99887766 },
            chat: { id: 99887766, type: "private" },
            text: "/request_crawl threads 20 60m my pham binh dan",
          },
        }),
      }),
      env23
    );
    assert.equal(tg60Res.status, 200);
    const tg60Req = Array.from(env23.DB.crawlRequests.values()).find(r => r.keywords === "my pham binh dan");
    assert.ok(tg60Req, "Telegram /request_crawl should strip optional 60m token into lease option");
    assert.equal(JSON.parse(tg60Req.options_json).lease_seconds, 3600);
    const tg60Msg = tgCalls[tgCalls.length - 1];
    assert.ok(tg60Msg.body.text.length <= 4096);
    assert.match(tg60Msg.body.text, /Thời lượng lease: 60 phút/);
    assert.match(tg60Msg.body.text, /<\/pre>$/);
  } finally {
    globalThis.fetch = prevFetch23;
  }
}

console.log("WORKER_V2_CONTRACT_TESTS_OK");
