/**
 * App Video Scheduler Worker — V2 Review Edition
 * ===============================================
 * Preserves all V1 bindings, secrets, Cron (`notifyNewCrawl`), `crawl_notifications`,
 * and `/telegram` webhook behavior while adding:
 *   1. Authenticated App_Video Bridge API (`/api/app/*`) scoped strictly to `AI_WORKSPACE/01_Market_Data/`
 *   2. D1 Remote Command Queue (`app_commands`) + Online/Offline Heartbeat (`app_heartbeats`)
 *   3. Crawl Center Queue (`crawl_requests`) for Muse Cloudflare OAuth Connector D1 polling
 *      - Production poll cadence: 6h (`production_6h`); limited test cadence: 5m (`test_5m`)
 *      - Atomic claim, lease timeout, deduplication, queued cancellation, honest waiting state
 *   4. Telegram V2: Inline Keyboard hierarchical menus, `editMessageText`, single primary
 *      progress message, Drive Explorer + open Drive link + direct JSON `sendDocument`,
 *      standardized Muse Market Analysis Report summary + full report export
 *
 * NOTE: Keep `worker.js` as V1 backup. Review + run `migrations/0001_v2_market_and_commands.sql`
 * before deploying.
 */

const DEFAULT_WORKSPACE_FOLDER_ID = "1F5m2t3uPxSlicE8PQW70xjf7FSC6345g";
const LEGACY_THREADS_FOLDER_ID = "1jJ0ihxv_LtpZFlkAeBO_BLysLZ9XwcE8";
const MARKET_DATA_DIR_NAME = "01_Market_Data";
const MAX_MARKET_JSON_BYTES = 10 * 1024 * 1024;
const MAX_TELEGRAM_DOC_BYTES = 20 * 1024 * 1024;
const ONLINE_HEARTBEAT_TTL_MS = 90 * 1000;

const ALLOWED_COMMANDS = new Set([
    "app_status",
    "sync_now",
    "last_sync_result",
    "market_stats"
]);
const ALLOWED_CRAWL_PLATFORMS = new Set(["threads", "tiktok", "shopee", "all"]);
const POLL_PROFILE_SECONDS = {
    production_6h: 6 * 3600,
    test_5m: 5 * 60
};

function jsonResponse(data, status = 200, extraHeaders = {}) {
    return new Response(JSON.stringify(data), {
        status,
        headers: {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            ...extraHeaders
        }
    });
}

function timingSafeEqualStr(a, b) {
    if (typeof a !== "string" || typeof b !== "string") return false;
    const enc = new TextEncoder();
    const ba = enc.encode(a);
    const bb = enc.encode(b);
    const maxLen = Math.max(ba.length, bb.length);
    let diff = ba.length ^ bb.length;
    for (let i = 0; i < maxLen; i++) {
        diff |= (ba[i] || 0) ^ (bb[i] || 0);
    }
    return diff === 0;
}

function isValidDriveId(id) {
    return typeof id === "string" && /^[a-zA-Z0-9_-]{10,128}$/.test(id);
}

function formatVnTime(isoOrDate) {
    if (!isoOrDate) return "Chưa có";
    const dt = new Date(isoOrDate);
    if (Number.isNaN(dt.getTime())) return "Không xác định";
    return dt.toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" });
}

function inferPlatformHint(pathOrName) {
    const lower = String(pathOrName || "").toLowerCase();
    if (lower.includes("threads")) return "threads";
    if (lower.includes("tiktok")) return "tiktok";
    if (lower.includes("shopee")) return "shopee";
    return null;
}

function genId(prefix) {
    return `${prefix}_${Date.now()}_${crypto.randomUUID().slice(0, 8)}`;
}

function normalizePollProfile(raw, env) {
    const candidate = String(raw || env?.MUSE_POLL_PROFILE || "production_6h").trim();
    return candidate === "test_5m" ? "test_5m" : "production_6h";
}

function formatPollProfileLabel(profile) {
    return profile === "test_5m"
        ? "Test giới hạn (5 phút/lần)"
        : "Production (6 giờ/lần)";
}

function formatCrawlStatusLabel(status) {
    const map = {
        queued: "⏳ Đang chờ Muse tiếp nhận (chưa claim)",
        running: "🕷️ Muse đã claim & đang crawl",
        completed: "✅ Hoàn thành",
        failed: "❌ Thất bại",
        cancelled: "🚫 Đã hủy khi đang chờ"
    };
    return map[status] || status;
}

// ─── Worker Runtime State Helpers ────────────────────────────────────────────

async function getRuntimeState(env, key, defaultVal = null) {
    try {
        const row = await env.DB.prepare("SELECT value FROM worker_runtime_state WHERE key = ?")
            .bind(key)
            .first();
        return row ? row.value : defaultVal;
    } catch {
        return defaultVal;
    }
}

async function setRuntimeState(env, key, value) {
    try {
        await env.DB.prepare(`
            INSERT INTO worker_runtime_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        `).bind(key, value === null || value === undefined ? null : String(value), new Date().toISOString()).run();
    } catch {
        // Ignore if pre-migration
    }
}

async function computeExpectedPickup(env, explicitProfile = null) {
    const storedProfile = await getRuntimeState(env, "muse_poll_profile", null);
    const profile = normalizePollProfile(explicitProfile || storedProfile, env);
    const intervalSec = POLL_PROFILE_SECONDS[profile] || POLL_PROFILE_SECONDS.production_6h;
    const lastPollIso = await getRuntimeState(env, "muse_last_poll_at", null);
    const confirmedRaw = await getRuntimeState(env, "muse_schedule_confirmed", null);
    const scheduleConfirmed = confirmedRaw === "1" && Boolean(lastPollIso);

    // Do NOT invent a fake next pickup time unless Muse's automatic schedule is actually confirmed & polled
    let expectedIso = null;
    if (scheduleConfirmed && lastPollIso) {
        const parsedLast = Date.parse(lastPollIso);
        if (Number.isFinite(parsedLast)) {
            const nowMs = Date.now();
            const candidate = parsedLast + intervalSec * 1000;
            expectedIso = new Date(candidate > nowMs ? candidate : nowMs + intervalSec * 1000).toISOString();
        }
    }
    return {
        poll_profile: profile,
        poll_interval_seconds: intervalSec,
        muse_last_poll_at: lastPollIso,
        expected_pickup_at: expectedIso,
        muse_schedule_confirmed: scheduleConfirmed,
        profile_label: formatPollProfileLabel(profile)
    };
}

// ─── Google Drive Auth & Scoped Helpers ──────────────────────────────────────

async function getDriveToken(env, scope = "https://www.googleapis.com/auth/drive.readonly") {
    const sa = JSON.parse(env.GOOGLE_SERVICE_ACCOUNT_JSON);
    const encoder = new TextEncoder();

    const b64url = bytes =>
        btoa(String.fromCharCode(...bytes))
            .replace(/\+/g, "-")
            .replace(/\//g, "_")
            .replace(/=+$/, "");

    const encode = value => b64url(encoder.encode(JSON.stringify(value)));
    const now = Math.floor(Date.now() / 1000);

    const header = encode({ alg: "RS256", typ: "JWT" });
    const payload = encode({
        iss: sa.client_email,
        scope,
        aud: "https://oauth2.googleapis.com/token",
        iat: now,
        exp: now + 3600
    });

    const pem = sa.private_key
        .replace(/-----BEGIN PRIVATE KEY-----/g, "")
        .replace(/-----END PRIVATE KEY-----/g, "")
        .replace(/\s/g, "");

    const key = await crypto.subtle.importKey(
        "pkcs8",
        Uint8Array.from(atob(pem), c => c.charCodeAt(0)),
        { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
        false,
        ["sign"]
    );

    const unsigned = `${header}.${payload}`;
    const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, encoder.encode(unsigned));
    const assertion = `${unsigned}.${b64url(new Uint8Array(signature))}`;

    const response = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        body: new URLSearchParams({
            grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
            assertion
        })
    });

    if (!response.ok) throw new Error(`Google Auth HTTP ${response.status}`);
    const data = await response.json();
    return data.access_token;
}

async function driveGetJson(token, pathAndQuery) {
    const res = await fetch(`https://www.googleapis.com/drive/v3/${pathAndQuery}`, {
        headers: { Authorization: `Bearer ${token}` }
    });
    if (!res.ok) throw new Error(`Drive HTTP ${res.status}`);
    return res.json();
}

function getWorkspaceRootId(env) {
    const id = env.GOOGLE_DRIVE_FOLDER_ID || DEFAULT_WORKSPACE_FOLDER_ID;
    if (!isValidDriveId(id)) throw new Error("Invalid GOOGLE_DRIVE_FOLDER_ID");
    return id;
}

async function resolveMarketDataFolderId(token, env) {
    if (isValidDriveId(env.MARKET_DATA_FOLDER_ID)) {
        return env.MARKET_DATA_FOLDER_ID;
    }
    const rootId = getWorkspaceRootId(env);
    const params = new URLSearchParams({
        q: `'${rootId}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder' and name = '${MARKET_DATA_DIR_NAME}'`,
        fields: "files(id,name)",
        pageSize: "5",
        supportsAllDrives: "true",
        includeItemsFromAllDrives: "true"
    });
    const res = await driveGetJson(token, `files?${params}`);
    if (res.files && res.files.length > 0) return res.files[0].id;
    return rootId;
}

async function isDescendantOfFolder(token, itemId, targetAncestorId, maxDepth = 6) {
    if (!isValidDriveId(itemId) || !isValidDriveId(targetAncestorId)) return { allowed: false, meta: null };
    if (itemId === targetAncestorId) {
        const meta = await driveGetJson(
            token,
            `files/${encodeURIComponent(itemId)}?fields=id,name,mimeType,size,modifiedTime,createdTime,md5Checksum,parents,trashed&supportsAllDrives=true`
        );
        return { allowed: !meta.trashed, meta };
    }

    let firstMeta = null;
    const queue = [{ id: itemId, depth: 0 }];
    const visited = new Set();

    while (queue.length > 0) {
        const current = queue.shift();
        if (visited.has(current.id) || current.depth > maxDepth) continue;
        visited.add(current.id);

        let meta;
        try {
            meta = await driveGetJson(
                token,
                `files/${encodeURIComponent(current.id)}?fields=id,name,mimeType,size,modifiedTime,createdTime,md5Checksum,parents,trashed&supportsAllDrives=true`
            );
        } catch (err) {
            if (current.depth === 0) throw err;
            continue;
        }
        if (!firstMeta) firstMeta = meta;
        if (meta.trashed) return { allowed: false, meta: firstMeta };

        const parents = Array.isArray(meta.parents) ? meta.parents : [];
        if (parents.includes(targetAncestorId)) return { allowed: true, meta: firstMeta };
        for (const p of parents) {
            if (!visited.has(p)) queue.push({ id: p, depth: current.depth + 1 });
        }
    }

    // Fallback: level-parallel top-down subtree walk handles Drive shares where `parents` metadata is hidden
    if (firstMeta && !firstMeta.trashed) {
        let frontier = [{ id: targetAncestorId, depth: 0 }];
        const subVisited = new Set();
        const maxSubDepth = Math.min(maxDepth, 4);
        while (frontier.length > 0) {
            const batch = frontier.filter(n => !subVisited.has(n.id) && n.depth <= maxSubDepth);
            if (!batch.length) break;
            batch.forEach(n => subVisited.add(n.id));
            const nextFrontier = [];
            const listings = await Promise.all(
                batch.map(async n => {
                    const files = [];
                    let pageToken = null;
                    do {
                        const listing = await listFolderChildren(token, n.id, pageToken, 100);
                        files.push(...(listing.files || []));
                        pageToken = listing.nextPageToken || null;
                    } while (pageToken);
                    return { node: n, files };
                })
            );
            for (const { node, files } of listings) {
                for (const f of files) {
                    if (f.id === itemId) return { allowed: true, meta: firstMeta };
                    if (f.mimeType === "application/vnd.google-apps.folder" && node.depth < maxSubDepth) {
                        nextFrontier.push({ id: f.id, depth: node.depth + 1 });
                    }
                }
            }
            frontier = nextFrontier;
        }
    }
    return { allowed: false, meta: firstMeta };
}

async function listFolderChildren(token, folderId, pageToken = null, pageSize = 25) {
    const params = new URLSearchParams({
        q: `'${folderId}' in parents and trashed = false`,
        fields: "nextPageToken,files(id,name,mimeType,size,modifiedTime,createdTime,md5Checksum)",
        orderBy: "folder,modifiedTime desc,name",
        pageSize: String(Math.min(Math.max(Number(pageSize) || 25, 1), 100)),
        supportsAllDrives: "true",
        includeItemsFromAllDrives: "true"
    });
    if (pageToken) params.set("pageToken", pageToken);
    return driveGetJson(token, `files?${params}`);
}

async function listMarketJsonFilesRecursive(token, rootMarketFolderId, cursorIso = null, limit = 50) {
    const results = [];
    let frontier = [{ id: rootMarketFolderId, relPath: "", depth: 0 }];
    const maxDepth = 3;

    while (frontier.length > 0 && results.length < 250) {
        const nextFrontier = [];
        const levelData = await Promise.all(
            frontier.map(async current => {
                const items = [];
                let pageToken = null;
                do {
                    const data = await listFolderChildren(token, current.id, pageToken, 100);
                    items.push(...(data.files || []));
                    pageToken = data.nextPageToken || null;
                } while (pageToken);
                return { current, items };
            })
        );
        for (const { current, items } of levelData) {
            for (const f of items) {
                const isFolder = f.mimeType === "application/vnd.google-apps.folder";
                const childPath = current.relPath ? `${current.relPath}/${f.name}` : f.name;
                if (isFolder) {
                    if (current.depth < maxDepth) {
                        nextFrontier.push({ id: f.id, relPath: childPath, depth: current.depth + 1 });
                    }
                    continue;
                }
                const isJson =
                    (f.name && f.name.toLowerCase().endsWith(".json")) ||
                    f.mimeType === "application/json";
                if (!isJson) continue;
                if (cursorIso && f.modifiedTime && Date.parse(f.modifiedTime) < Date.parse(cursorIso)) continue;
                results.push({
                    file_id: f.id,
                    name: f.name,
                    folder_path: current.relPath || MARKET_DATA_DIR_NAME,
                    mime_type: f.mimeType || "application/json",
                    size: f.size ? Number(f.size) : null,
                    modified_time: f.modifiedTime || null,
                    created_time: f.createdTime || null,
                    md5_checksum: f.md5Checksum || null,
                    platform_hint: inferPlatformHint(childPath)
                });
            }
        }
        frontier = nextFrontier;
    }

    results.sort((a, b) => (Date.parse(a.modified_time || 0) || 0) - (Date.parse(b.modified_time || 0) || 0));
    const sliced = results.slice(0, Math.min(Math.max(Number(limit) || 50, 1), 100));
    const nextCursor = sliced.length > 0 ? sliced[sliced.length - 1].modified_time : cursorIso;
    return { files: sliced, total_matched: results.length, next_cursor: nextCursor };
}

function isNonReportDocName(fileName) {
    const low = String(fileName || "").toLowerCase();
    return low.includes("data_contract") || low.includes("strategy") || low.includes("audit");
}

async function resolveReportsFolderId(token, env) {
    const marketFolderId = await resolveMarketDataFolderId(token, env);
    try {
        const params = new URLSearchParams({
            q: `'${marketFolderId}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder' and name = 'Reports'`,
            fields: "files(id,name)",
            pageSize: "5",
            supportsAllDrives: "true",
            includeItemsFromAllDrives: "true"
        });
        const res = await driveGetJson(token, `files?${params}`);
        if (res.files && res.files.length > 0) return res.files[0].id;
    } catch {}
    return marketFolderId;
}

async function getLatestMuseReportFromDrive(env, targetRunOrRequestId = null) {
    const token = await getDriveToken(env);
    const reportsFolderId = await resolveReportsFolderId(token, env);
    const listing = await listFolderChildren(token, reportsFolderId, null, 50);
    const jsonReports = (listing.files || []).filter(f => {
        const low = String(f.name || "").toLowerCase();
        if (!low.endsWith(".json")) return false;
        if (isNonReportDocName(low)) return false;
        return low.startsWith("market_report") || low.includes("report");
    });
    if (!jsonReports.length) return { reportsFolderId, file: null, data: null };

    // Sort corrected_v2 / _corrected first, then newest modifiedTime
    jsonReports.sort((a, b) => {
        const aCorr = String(a.name || "").toLowerCase().includes("_corrected") ? 1 : 0;
        const bCorr = String(b.name || "").toLowerCase().includes("_corrected") ? 1 : 0;
        if (bCorr !== aCorr) return bCorr - aCorr;
        return (Date.parse(b.modifiedTime || 0) || 0) - (Date.parse(a.modifiedTime || 0) || 0);
    });

    for (const candidate of jsonReports.slice(0, 5)) {
        try {
            const rawRes = await fetch(
                `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(candidate.id)}?alt=media&supportsAllDrives=true`,
                { headers: { Authorization: `Bearer ${token}` } }
            );
            if (!rawRes.ok) continue;
            const parsed = await rawRes.json();
            if (isNonReportDocName(candidate.name) || parsed.contract || parsed.audit || parsed.strategy) continue;
            if (targetRunOrRequestId && parsed.request_id && parsed.request_id !== targetRunOrRequestId && parsed.run_id !== targetRunOrRequestId) {
                continue;
            }
            return { reportsFolderId, file: candidate, data: parsed };
        } catch {}
    }

    // Fallback without strict target filter if none matched request_id
    for (const candidate of jsonReports.slice(0, 3)) {
        try {
            const rawRes = await fetch(
                `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(candidate.id)}?alt=media&supportsAllDrives=true`,
                { headers: { Authorization: `Bearer ${token}` } }
            );
            if (!rawRes.ok) continue;
            const parsed = await rawRes.json();
            if (!parsed.contract && !parsed.audit && !parsed.strategy) {
                return { reportsFolderId, file: candidate, data: parsed };
            }
        } catch {}
    }
    return { reportsFolderId, file: null, data: null };
}

// ─── V1 Preserved Crawl Helpers & Cron ───────────────────────────────────────

async function getLatestCrawl(env) {
    const token = await getDriveToken(env);
    const headers = { Authorization: `Bearer ${token}` };
    const folderId = env.THREADS_FOLDER_ID || LEGACY_THREADS_FOLDER_ID;

    const params = new URLSearchParams({
        q: `'${folderId}' in parents and trashed = false and name contains 'threads_crawl'`,
        fields: "files(id,name,size,createdTime,modifiedTime)",
        orderBy: "createdTime desc",
        pageSize: "1",
        supportsAllDrives: "true",
        includeItemsFromAllDrives: "true"
    });

    const res = await fetch(`https://www.googleapis.com/drive/v3/files?${params}`, { headers });
    if (!res.ok) throw new Error("Drive list failed");

    const list = await res.json();
    const file = list.files?.[0];
    if (!file) return null;
    if (!file.size || Number(file.size) > MAX_MARKET_JSON_BYTES) throw new Error("Invalid file size");

    const dataRes = await fetch(
        `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(file.id)}?alt=media&supportsAllDrives=true`,
        { headers }
    );
    if (!dataRes.ok) throw new Error("Drive download failed");

    const data = await dataRes.json();
    if (!Array.isArray(data.posts)) throw new Error("Invalid crawl data");
    return { ...data, _drive_file: file };
}

async function notifyNewCrawl(env) {
    // 1. Check latest Muse report on 01_Market_Data/Reports/ first (strictly deduplicated via crawl_notifications)
    try {
        const rep = await getLatestMuseReportFromDrive(env);
        if (rep.file && rep.data) {
            const d = rep.data;
            const techStatus = String(d.technical_status || "completed").toLowerCase();
            if (techStatus === "completed" || techStatus === "partial") {
                const rawRunId = String(d.run_id || rep.file.name || "report").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 96);
                const isCorr = String(rep.file.name || "").toLowerCase().includes("_corrected") || Boolean(d.corrects_report);
                const notifKey = isCorr ? `${rawRunId}_corrected` : rawRunId;
                const totals = d.totals || {};
                const recCount = Number(totals.records ?? 0);
                const usableCount = Number(totals.business_usable_records ?? recCount);

                const claim = await env.DB.prepare(`
                    INSERT OR IGNORE INTO crawl_notifications
                    (run_id, record_count, status, created_at)
                    VALUES (?, ?, 'sending', ?)
                `).bind(notifKey, recCount, new Date().toISOString()).run();

                if (claim.meta.changes === 1) {
                    const summaryText = d.telegram_summary
                        ? `🔔 BÁO CÁO MUSE MỚI (${isCorr ? "BẢN CORRECTED V2" : "MỚI NHẤT"})\n\n${d.telegram_summary}`
                        : `🔔 BÁO CÁO MUSE MỚI\nRun: ${rawRunId}\nKỹ thuật: ${d.technical_status || "completed"} | Chất lượng: ${d.data_quality_status || "N/A"}\nUsable: ${usableCount}/${recCount}`;
                    try {
                        const res = await sendTelegramMessage(env, env.TELEGRAM_OWNER_ID, summaryText, mainInlineKeyboard());
                        if (!res.ok) throw new Error("Delivery failed");
                        await env.DB.prepare("UPDATE crawl_notifications SET status = 'sent', sent_at = ? WHERE run_id = ?")
                            .bind(new Date().toISOString(), notifKey).run();
                    } catch {
                        await env.DB.prepare("UPDATE crawl_notifications SET status = 'delivery_uncertain' WHERE run_id = ?")
                            .bind(notifKey).run();
                    }
                    return;
                }
            }
        }
    } catch {}

    // 2. Preserved V1 fallback check on THREADS_FOLDER_ID
    const data = await getLatestCrawl(env);
    if (!data || data.status !== "completed") return;

    if (Date.parse(data.started_at) < Date.parse("2026-11-26T12:25:00+07:00")) return;
    if (
        typeof data.run_id !== "string" ||
        !/^[a-zA-Z0-9_-]{1,128}$/.test(data.run_id) ||
        !Array.isArray(data.posts)
    ) {
        return;
    }
    if (!Number.isFinite(Date.parse(data.ended_at))) return;

    const claim = await env.DB.prepare(`
        INSERT OR IGNORE INTO crawl_notifications
        (run_id, record_count, status, created_at)
        VALUES (?, ?, 'sending', ?)
    `).bind(data.run_id, data.posts.length, new Date().toISOString()).run();

    if (claim.meta.changes !== 1) return;

    const time = formatVnTime(data.ended_at);
    const text =
        "🔔 MUSE CRAWL HOÀN THÀNH\n\n" +
        "Nền tảng: Threads\n" +
        `Số bài: ${data.posts.length}\n` +
        `Trùng đã loại: ${Number.isSafeInteger(data.duplicates_skipped) ? data.duplicates_skipped : "N/A"}\n` +
        `Hoàn thành: ${time}\n\n` +
        "Auto Sync đang chạy ngầm và sẽ tự đồng bộ về App_Video (hoặc vào ⚙️ Cài đặt → 🔄 Đồng bộ thủ công).";

    try {
        const res = await sendTelegramMessage(env, env.TELEGRAM_OWNER_ID, text, mainInlineKeyboard());
        if (!res.ok) throw new Error("Delivery failed");
    } catch {
        await env.DB.prepare(`
            UPDATE crawl_notifications SET status = 'delivery_uncertain' WHERE run_id = ?
        `).bind(data.run_id).run();
        return;
    }

    await env.DB.prepare(`
        UPDATE crawl_notifications SET status = 'sent', sent_at = ? WHERE run_id = ?
    `).bind(new Date().toISOString(), data.run_id).run();
}

async function expireStaleLeases(env) {
    const nowIso = new Date().toISOString();
    for (const table of ["app_commands", "crawl_requests"]) {
        try {
            await env.DB.prepare(`
                UPDATE ${table}
                SET status = 'queued', claimed_by = NULL, claimed_at = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ? AND attempts < max_attempts
            `).bind(nowIso, nowIso).run();

            await env.DB.prepare(`
                UPDATE ${table}
                SET status = 'failed', error_message = 'Execution lease expired before completion', updated_at = ?
                WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ? AND attempts >= max_attempts
            `).bind(nowIso, nowIso).run();
        } catch {
            // Table may not exist yet prior to migration 0001
        }
    }
}

// ─── Telegram API Helpers (Inline, editMessageText, Single Primary Message) ─

function mainInlineKeyboard() {
    return {
        inline_keyboard: [
            [
                { text: "🕷️ Crawl dữ liệu", callback_data: "crawl:menu" },
                { text: "📊 Phân tích thị trường", callback_data: "rpt:summary" }
            ],
            [
                { text: "⏱️ Lịch tự động", callback_data: "sched:view" },
                { text: "📋 Lịch sử tác vụ", callback_data: "cmd:history" }
            ],
            [
                { text: "📡 Trạng thái hệ thống", callback_data: "sys:status" },
                { text: "⚙️ Cài đặt", callback_data: "cfg:menu" }
            ]
        ]
    };
}

async function sendTelegramMessage(env, chatId, text, replyMarkup = null) {
    const body = { chat_id: chatId, text: String(text).slice(0, 4096) };
    if (replyMarkup) body.reply_markup = replyMarkup;
    const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN_VIDEO}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    });
    const data = await resp.json();
    return { ok: Boolean(resp.ok && data.ok), data };
}

async function editTelegramMessage(env, chatId, messageId, text, replyMarkup = null) {
    const body = {
        chat_id: chatId,
        message_id: Number(messageId),
        text: String(text).slice(0, 4096)
    };
    if (replyMarkup) body.reply_markup = replyMarkup;
    const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN_VIDEO}/editMessageText`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    });
    const data = await resp.json();
    const notModified = !resp.ok && String(data.description || "").includes("message is not modified");
    return { ok: Boolean((resp.ok && data.ok) || notModified), data };
}

async function sendOrEditTelegram(env, chatId, messageId, text, replyMarkup = null) {
    if (messageId) {
        const edited = await editTelegramMessage(env, chatId, messageId, text, replyMarkup);
        if (edited.ok) return edited;
    }
    return sendTelegramMessage(env, chatId, text, replyMarkup);
}

async function updatePrimaryProgressMessage(env, chatId, text, replyMarkup = null) {
    const primaryMsgId = await getRuntimeState(env, "tg_primary_status_msg_id", null);
    if (primaryMsgId) {
        const edited = await editTelegramMessage(env, chatId, primaryMsgId, text, replyMarkup);
        if (edited.ok) return edited;
    }
    const sent = await sendTelegramMessage(env, chatId, text, replyMarkup);
    if (sent.ok && sent.data?.result?.message_id) {
        await setRuntimeState(env, "tg_primary_status_msg_id", String(sent.data.result.message_id));
    }
    return sent;
}

async function answerCallbackQuery(env, callbackQueryId, text = "") {
    if (!callbackQueryId) return;
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN_VIDEO}/answerCallbackQuery`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ callback_query_id: callbackQueryId, text: text.slice(0, 180) })
    });
}

async function sendTelegramDocument(env, chatId, fileName, bytesBuffer, caption = "", mimeType = "application/json") {
    const form = new FormData();
    form.append("chat_id", String(chatId));
    if (caption) form.append("caption", caption.slice(0, 1000));
    form.append("document", new Blob([bytesBuffer], { type: mimeType }), fileName);

    const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN_VIDEO}/sendDocument`, {
        method: "POST",
        body: form
    });
    const data = await resp.json();
    return { ok: Boolean(resp.ok && data.ok), data };
}

// ─── D1 Command Queue & Crawl Center Helpers ─────────────────────────────────

async function getAppOnlineSummary(env) {
    try {
        const hb = await env.DB.prepare(
            "SELECT * FROM app_heartbeats ORDER BY last_seen_at DESC LIMIT 1"
        ).first();
        if (!hb) return { online: false, heartbeat: null, age_sec: null };
        const ageMs = Date.now() - Date.parse(hb.last_seen_at);
        return {
            online: Number.isFinite(ageMs) && ageMs <= ONLINE_HEARTBEAT_TTL_MS,
            heartbeat: hb,
            age_sec: Number.isFinite(ageMs) ? Math.max(0, Math.round(ageMs / 1000)) : null
        };
    } catch {
        return { online: false, heartbeat: null, age_sec: null };
    }
}

async function enqueueCommand(env, commandType, payload, chatId, dedupToken = null, msgId = null) {
    if (!ALLOWED_COMMANDS.has(commandType)) throw new Error("Unsupported command type");
    const nowIso = new Date().toISOString();
    const id = genId("cmd");

    if (dedupToken) {
        const existing = await env.DB.prepare(
            "SELECT id, status FROM app_commands WHERE dedup_token = ?"
        ).bind(dedupToken).first();
        if (existing) return { id: existing.id, status: existing.status, deduplicated: true };
    }

    await env.DB.prepare(`
        INSERT INTO app_commands
        (id, command_type, payload_json, status, requested_by_chat_id, telegram_message_id, dedup_token, attempts, max_attempts, created_at, updated_at)
        VALUES (?, ?, ?, 'queued', ?, ?, ?, 0, 3, ?, ?)
    `).bind(
        id,
        commandType,
        JSON.stringify(payload || {}),
        chatId ? String(chatId) : null,
        msgId ? String(msgId) : null,
        dedupToken,
        nowIso,
        nowIso
    ).run();

    return { id, status: "queued", deduplicated: false };
}

async function createCrawlRequestInD1(env, {
    platform,
    keywords = "",
    requestedCount = 50,
    options = {},
    pollProfile = null,
    chatId = null,
    source = "app_video",
    dedupToken = null
}) {
    const plat = String(platform || "").toLowerCase().trim();
    if (!ALLOWED_CRAWL_PLATFORMS.has(plat)) {
        throw new Error("Platform must be threads, tiktok, shopee, or all");
    }
    const cleanKeywords = String(keywords || "").trim().slice(0, 300);
    const count = Math.min(Math.max(Number.parseInt(requestedCount, 10) || 50, 1), 500);
    await expireStaleLeases(env);

    // 1. Check explicit dedupToken
    if (dedupToken) {
        const byToken = await env.DB.prepare(
            "SELECT * FROM crawl_requests WHERE dedup_token = ?"
        ).bind(dedupToken).first();
        if (byToken) return { deduplicated: true, request: byToken };
    }

    // 2. Active-queue deduplication for identical platform + keywords + count
    const existingActive = await env.DB.prepare(`
        SELECT * FROM crawl_requests
        WHERE platform = ? AND lower(trim(keywords)) = ? AND requested_count = ? AND status IN ('queued', 'running')
        ORDER BY created_at DESC LIMIT 1
    `).bind(plat, cleanKeywords.toLowerCase(), count).first();
    if (existingActive) {
        return { deduplicated: true, request: existingActive };
    }

    const eta = await computeExpectedPickup(env, pollProfile);
    const nowIso = new Date().toISOString();
    const id = genId("crw");

    await env.DB.prepare(`
        INSERT INTO crawl_requests (
            id, platform, keywords, requested_count, options_json, status, dedup_token,
            muse_connector_mode, poll_profile, expected_pickup_at, attempts, max_attempts,
            requested_by_chat_id, requested_by_source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'queued', ?, 'd1_polling', ?, ?, 0, 3, ?, ?, ?, ?)
    `).bind(
        id,
        plat,
        cleanKeywords,
        count,
        JSON.stringify(options || {}),
        dedupToken,
        eta.poll_profile,
        eta.expected_pickup_at,
        chatId ? String(chatId) : null,
        source,
        nowIso,
        nowIso
    ).run();

    const created = await env.DB.prepare("SELECT * FROM crawl_requests WHERE id = ?").bind(id).first();
    return { deduplicated: false, request: created, eta };
}

function formatCommandNotificationText(cmdRow, status, resultObj, errorMessage) {
    const cmdType = cmdRow.command_type;
    if (status === "failed") {
        return (
            `❌ LỆNH ${cmdType.toUpperCase()} THẤT BẠI\n\n` +
            `Mã lệnh: ${cmdRow.id}\n` +
            `Lỗi: ${String(errorMessage || "Unknown error").slice(0, 400)}`
        );
    }

    if (cmdType === "sync_now") {
        const r = resultObj || {};
        return (
            "✅ APP_VIDEO ĐÃ ĐỒNG BỘ XONG\n\n" +
            `Mã lệnh: ${cmdRow.id}\n` +
            `File kiểm tra: ${r.files_checked ?? 0}\n` +
            `File tải mới: ${r.files_downloaded ?? 0}\n` +
            `Bản ghi thêm mới: ${r.records_inserted ?? 0}\n` +
            `Cập nhật: ${r.records_updated ?? 0}\n` +
            `Trùng đã bỏ qua: ${r.records_deduped ?? 0}\n` +
            `Tổng kho local: ${r.total_items ?? "N/A"}\n` +
            `Hoàn thành: ${formatVnTime(r.finished_at || new Date().toISOString())}`
        );
    }

    if (cmdType === "app_status") {
        const r = resultObj || {};
        return (
            "📡 APP_VIDEO STATUS (LIVE)\n\n" +
            `Trạng thái: 🟢 ONLINE\n` +
            `Client: ${r.client_id || "app-video"}\n` +
            `Đồng bộ nền: ${r.sync_status || "idle"} (mỗi ${r.sync_interval_seconds ?? 300}s)\n` +
            `Sync gần nhất: ${formatVnTime(r.last_sync_at)}\n` +
            `Tổng bản ghi local: ${r.total_items ?? 0}`
        );
    }

    if (cmdType === "market_stats" || cmdType === "last_sync_result") {
        const r = resultObj || {};
        const byPlat = r.by_platform || {};
        return (
            "📊 THỐNG KÊ THỊ TRƯỜNG APP_VIDEO\n\n" +
            `Mã lệnh: ${cmdRow.id}\n` +
            `Tổng bản ghi: ${r.total_items ?? 0}\n` +
            `• Threads: ${byPlat.threads ?? 0}\n` +
            `• TikTok: ${byPlat.tiktok ?? 0}\n` +
            `• Shopee: ${byPlat.shopee ?? 0}\n` +
            `Sync gần nhất: ${formatVnTime(r.last_sync_at)}`
        );
    }

    return `✅ Lệnh ${cmdType} (${cmdRow.id}) đã hoàn thành.`;
}

// ─── Authenticated App_Video Bridge API (`/api/app/*`) ───────────────────────

async function handleAppApi(request, env, url) {
    if (!env.APP_VIDEO_API_TOKEN) {
        return jsonResponse({ ok: false, error: "APP_VIDEO_API_TOKEN is not configured" }, 503);
    }

    const authHeader = request.headers.get("Authorization") || "";
    const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7).trim() : "";
    if (!token || !timingSafeEqualStr(token, env.APP_VIDEO_API_TOKEN)) {
        return jsonResponse({ ok: false, error: "Unauthorized" }, 401);
    }

    const clientId = (request.headers.get("X-App-Client-Id") || "app-video-desktop").slice(0, 64);
    const path = url.pathname;

    // 1. GET /api/app/health
    if (request.method === "GET" && path === "/api/app/health") {
        let dbOk = false;
        let driveOk = false;
        let marketFolderId = null;
        try {
            const r = await env.DB.prepare("SELECT 1 AS ok").first();
            dbOk = r?.ok === 1;
        } catch {
            dbOk = false;
        }
        try {
            const driveToken = await getDriveToken(env);
            marketFolderId = await resolveMarketDataFolderId(driveToken, env);
            driveOk = Boolean(marketFolderId);
        } catch {
            driveOk = false;
        }
        return jsonResponse({
            ok: dbOk && driveOk,
            worker: "app-video-scheduler",
            version: "2.1.0",
            db_ok: dbOk,
            drive_ok: driveOk,
            market_data_folder_id: marketFolderId,
            server_time: new Date().toISOString()
        });
    }

    // 2. GET /api/app/market/files
    if (request.method === "GET" && path === "/api/app/market/files") {
        try {
            const cursor = url.searchParams.get("cursor") || null;
            const limit = Number(url.searchParams.get("limit") || "50");
            const driveToken = await getDriveToken(env);
            const marketFolderId = await resolveMarketDataFolderId(driveToken, env);
            const listing = await listMarketJsonFilesRecursive(driveToken, marketFolderId, cursor, limit);
            return jsonResponse({ ok: true, scope_folder_id: marketFolderId, ...listing });
        } catch {
            return jsonResponse({ ok: false, error: "Failed to list Market Data files" }, 502);
        }
    }

    // 3. GET /api/app/market/files/:fileId
    if (request.method === "GET" && path.startsWith("/api/app/market/files/")) {
        const fileId = decodeURIComponent(path.slice("/api/app/market/files/".length)).trim();
        if (!isValidDriveId(fileId)) return jsonResponse({ ok: false, error: "Invalid Drive file ID" }, 400);
        try {
            const driveToken = await getDriveToken(env);
            const marketFolderId = await resolveMarketDataFolderId(driveToken, env);
            const check = await isDescendantOfFolder(driveToken, fileId, marketFolderId);
            if (!check.allowed || !check.meta) {
                return jsonResponse({ ok: false, error: "File is outside scoped 01_Market_Data folder" }, 403);
            }
            const meta = check.meta;
            const isJson = (meta.name && meta.name.toLowerCase().endsWith(".json")) || meta.mimeType === "application/json";
            if (!isJson) return jsonResponse({ ok: false, error: "Only JSON files are permitted" }, 403);
            if (meta.size && Number(meta.size) > MAX_MARKET_JSON_BYTES) {
                return jsonResponse({ ok: false, error: "File exceeds maximum allowed JSON size" }, 413);
            }

            const rawRes = await fetch(
                `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(fileId)}?alt=media&supportsAllDrives=true`,
                { headers: { Authorization: `Bearer ${driveToken}` } }
            );
            if (!rawRes.ok) return jsonResponse({ ok: false, error: `Drive download HTTP ${rawRes.status}` }, 502);
            const bodyText = await rawRes.text();

            try {
                await env.DB.prepare(`
                    INSERT INTO market_sync_audit (id, client_id, drive_file_id, file_name, action, status, details_json, created_at)
                    VALUES (?, ?, ?, ?, 'download', 'ok', ?, ?)
                `).bind(
                    genId("audit"),
                    clientId,
                    fileId,
                    meta.name || "unknown.json",
                    JSON.stringify({ size: bodyText.length, modified_time: meta.modifiedTime || null }),
                    new Date().toISOString()
                ).run();
            } catch {}

            return new Response(bodyText, {
                status: 200,
                headers: {
                    "Content-Type": "application/json; charset=utf-8",
                    "Cache-Control": "no-store",
                    "X-Drive-File-Id": fileId,
                    "X-Drive-File-Name": encodeURIComponent(meta.name || ""),
                    "X-Drive-Modified-Time": meta.modifiedTime || "",
                    "X-Drive-Md5": meta.md5Checksum || ""
                }
            });
        } catch {
            return jsonResponse({ ok: false, error: "Failed to read Drive file" }, 502);
        }
    }

    // 4. POST /api/app/heartbeat
    if (request.method === "POST" && path === "/api/app/heartbeat") {
        let payload = {};
        try { payload = await request.json(); } catch { return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400); }
        const nowIso = new Date().toISOString();
        await env.DB.prepare(`
            INSERT INTO app_heartbeats
            (client_id, app_status, sync_status, last_sync_at, last_sync_summary_json, market_counts_json, latest_report_json, sync_interval_seconds, app_version, last_seen_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                app_status = excluded.app_status,
                sync_status = excluded.sync_status,
                last_sync_at = excluded.last_sync_at,
                last_sync_summary_json = excluded.last_sync_summary_json,
                market_counts_json = excluded.market_counts_json,
                latest_report_json = COALESCE(excluded.latest_report_json, app_heartbeats.latest_report_json),
                sync_interval_seconds = excluded.sync_interval_seconds,
                app_version = excluded.app_version,
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at
        `).bind(
            clientId,
            String(payload.app_status || "online"),
            String(payload.sync_status || "idle"),
            payload.last_sync_at || null,
            JSON.stringify(payload.last_sync_summary || {}),
            JSON.stringify(payload.market_counts || {}),
            payload.latest_report ? JSON.stringify(payload.latest_report) : null,
            Number(payload.sync_interval_seconds || 300),
            String(payload.app_version || "2.3"),
            nowIso,
            nowIso
        ).run();

        return jsonResponse({ ok: true, server_time: nowIso });
    }

    // 5. POST /api/app/commands/claim
    if (request.method === "POST" && path === "/api/app/commands/claim") {
        let payload = {};
        try { payload = await request.json(); } catch { payload = {}; }
        const leaseSec = Math.min(Math.max(Number(payload.lease_seconds) || 120, 30), 600);
        await expireStaleLeases(env);

        for (let attempt = 0; attempt < 2; attempt++) {
            const candidate = await env.DB.prepare(`
                SELECT id, command_type, payload_json, requested_by_chat_id, attempts, max_attempts, created_at
                FROM app_commands
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
            `).first();

            if (!candidate) return jsonResponse({ ok: true, command: null });
            if (!ALLOWED_COMMANDS.has(candidate.command_type)) {
                await env.DB.prepare(`
                    UPDATE app_commands SET status = 'failed', error_message = 'Disallowed command type', updated_at = ?
                    WHERE id = ? AND status = 'queued'
                `).bind(new Date().toISOString(), candidate.id).run();
                continue;
            }

            const now = new Date();
            const nowIso = now.toISOString();
            const leaseExpires = new Date(now.getTime() + leaseSec * 1000).toISOString();

            const claimed = await env.DB.prepare(`
                UPDATE app_commands
                SET status = 'running', claimed_by = ?, claimed_at = ?, lease_expires_at = ?, attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND status = 'queued'
            `).bind(clientId, nowIso, leaseExpires, nowIso, candidate.id).run();

            if (claimed.meta.changes === 1) {
                let parsedPayload = {};
                try { parsedPayload = JSON.parse(candidate.payload_json || "{}"); } catch {}
                return jsonResponse({
                    ok: true,
                    command: {
                        id: candidate.id,
                        command_type: candidate.command_type,
                        payload: parsedPayload,
                        lease_expires_at: leaseExpires,
                        attempts: Number(candidate.attempts || 0) + 1,
                        created_at: candidate.created_at
                    }
                });
            }
        }
        return jsonResponse({ ok: true, command: null });
    }

    // 6. POST /api/app/commands/:id/complete
    if (request.method === "POST" && path.startsWith("/api/app/commands/") && path.endsWith("/complete")) {
        const commandId = path.slice("/api/app/commands/".length, -"/complete".length).trim();
        if (!commandId) return jsonResponse({ ok: false, error: "Missing command ID" }, 400);

        let body = {};
        try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400); }

        const nextStatus = body.status === "failed" ? "failed" : "completed";
        const nowIso = new Date().toISOString();
        const resultJson = JSON.stringify(body.result || {});
        const errMsg = body.error_message ? String(body.error_message).slice(0, 1000) : null;

        const existing = await env.DB.prepare("SELECT * FROM app_commands WHERE id = ?").bind(commandId).first();
        if (!existing) return jsonResponse({ ok: false, error: "Command not found" }, 404);

        if (["completed", "failed", "cancelled"].includes(existing.status)) {
            return jsonResponse({ ok: true, already_finalized: true, status: existing.status });
        }

        const upd = await env.DB.prepare(`
            UPDATE app_commands
            SET status = ?, result_json = ?, error_message = ?, updated_at = ?
            WHERE id = ? AND status IN ('running', 'queued')
        `).bind(nextStatus, resultJson, errMsg, nowIso, commandId).run();

        if (upd.meta.changes === 1 && existing.requested_by_chat_id && !existing.notified_at && env.TELEGRAM_BOT_TOKEN_VIDEO) {
            const text = formatCommandNotificationText(existing, nextStatus, body.result, errMsg);
            const sent = await updatePrimaryProgressMessage(env, existing.requested_by_chat_id, text, mainInlineKeyboard());
            if (sent.ok) {
                await env.DB.prepare("UPDATE app_commands SET notified_at = ? WHERE id = ?")
                    .bind(new Date().toISOString(), commandId).run();
            }
        }

        return jsonResponse({ ok: true, already_finalized: false, status: nextStatus });
    }

    // 7. Crawl Center APIs (Muse polls D1 via official Cloudflare OAuth Connector)
    if (request.method === "GET" && path === "/api/app/crawl-requests") {
        await expireStaleLeases(env);
        const rows = await env.DB.prepare(`
            SELECT * FROM crawl_requests ORDER BY created_at DESC LIMIT 40
        `).all();
        const schedule = await computeExpectedPickup(env);
        return jsonResponse({
            ok: true,
            schedule,
            items: rows.results || []
        });
    }

    if (request.method === "POST" && path === "/api/app/crawl-requests") {
        let body = {};
        try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400); }
        try {
            const res = await createCrawlRequestInD1(env, {
                platform: body.platform,
                keywords: body.keywords || "",
                requestedCount: body.requested_count || 50,
                options: body.options || {},
                pollProfile: body.poll_profile || null,
                source: "app_video",
                dedupToken: body.dedup_token || null
            });
            return jsonResponse({ ok: true, ...res }, res.deduplicated ? 200 : 201);
        } catch (err) {
            return jsonResponse({ ok: false, error: err.message }, 400);
        }
    }

    if (request.method === "POST" && path.startsWith("/api/app/crawl-requests/") && path.endsWith("/cancel")) {
        const crawlId = path.slice("/api/app/crawl-requests/".length, -"/cancel".length).trim();
        if (!crawlId) return jsonResponse({ ok: false, error: "Missing crawl request ID" }, 400);
        const upd = await env.DB.prepare(`
            UPDATE crawl_requests SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'queued'
        `).bind(new Date().toISOString(), crawlId).run();
        if (upd.meta.changes !== 1) {
            return jsonResponse({ ok: false, error: "Request is not in queued state or not found" }, 409);
        }
        return jsonResponse({ ok: true, id: crawlId, status: "cancelled" });
    }

    if (request.method === "POST" && path === "/api/app/crawl-requests/claim") {
        let body = {};
        try { body = await request.json(); } catch { body = {}; }
        const workerId = String(body.worker_id || clientId || "muse-ai").slice(0, 64);
        const leaseSec = Math.min(Math.max(Number(body.lease_seconds) || 1800, 60), 7200);
        const now = new Date();
        const nowIso = now.toISOString();
        await setRuntimeState(env, "muse_last_poll_at", nowIso);
        if (body.schedule_confirmed === true) {
            await setRuntimeState(env, "muse_schedule_confirmed", "1");
        }
        await expireStaleLeases(env);

        for (let attempt = 0; attempt < 2; attempt++) {
            const candidate = await env.DB.prepare(`
                SELECT * FROM crawl_requests WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1
            `).first();
            if (!candidate) return jsonResponse({ ok: true, request: null });

            const leaseExpires = new Date(now.getTime() + leaseSec * 1000).toISOString();
            const claimed = await env.DB.prepare(`
                UPDATE crawl_requests
                SET status = 'running', claimed_by = ?, claimed_at = ?, lease_expires_at = ?, attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND status = 'queued'
            `).bind(workerId, nowIso, leaseExpires, nowIso, candidate.id).run();

            if (claimed.meta.changes === 1) {
                const updatedRow = await env.DB.prepare("SELECT * FROM crawl_requests WHERE id = ?").bind(candidate.id).first();
                return jsonResponse({ ok: true, request: updatedRow });
            }
        }
        return jsonResponse({ ok: true, request: null });
    }

    if (request.method === "POST" && path.startsWith("/api/app/crawl-requests/") && path.endsWith("/complete")) {
        const crawlId = path.slice("/api/app/crawl-requests/".length, -"/complete".length).trim();
        let body = {};
        try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400); }
        const nextStatus = body.status === "failed" ? "failed" : "completed";
        const nowIso = new Date().toISOString();

        const existing = await env.DB.prepare("SELECT * FROM crawl_requests WHERE id = ?").bind(crawlId).first();
        if (!existing) return jsonResponse({ ok: false, error: "Crawl request not found" }, 404);
        if (["completed", "failed", "cancelled"].includes(existing.status)) {
            return jsonResponse({ ok: true, already_finalized: true, status: existing.status });
        }

        const upd = await env.DB.prepare(`
            UPDATE crawl_requests
            SET status = ?, result_summary_json = ?, report_file_id = ?, error_message = ?, updated_at = ?
            WHERE id = ? AND status IN ('running', 'queued')
        `).bind(
            nextStatus,
            JSON.stringify(body.result_summary || {}),
            body.report_file_id || null,
            body.error_message || null,
            nowIso,
            crawlId
        ).run();

        if (upd.meta.changes === 1 && existing.requested_by_chat_id && !existing.notified_at && env.TELEGRAM_BOT_TOKEN_VIDEO) {
            const summary = body.result_summary || {};
            const text = nextStatus === "completed"
                ? `✅ MUSE CRAWL XONG (${existing.platform.toUpperCase()})\nMã: ${crawlId}\nSố bài: ${summary.record_count ?? "N/A"}`
                : `❌ MUSE CRAWL THẤT BẠI (${existing.platform.toUpperCase()})\nMã: ${crawlId}\nLỗi: ${body.error_message || "Unknown"}`;
            const sent = await updatePrimaryProgressMessage(env, existing.requested_by_chat_id, text, mainInlineKeyboard());
            if (sent.ok) {
                await env.DB.prepare("UPDATE crawl_requests SET notified_at = ? WHERE id = ?").bind(nowIso, crawlId).run();
            }
        }
        return jsonResponse({ ok: true, already_finalized: false, status: nextStatus });
    }

    if (request.method === "POST" && path === "/api/app/crawl-schedule") {
        let body = {};
        try { body = await request.json(); } catch { body = {}; }
        const profile = normalizePollProfile(body.poll_profile, env);
        await setRuntimeState(env, "muse_poll_profile", profile);
        await setRuntimeState(env, "muse_schedule_confirmed", "0");
        const eta = await computeExpectedPickup(env, profile);
        return jsonResponse({
            ok: true,
            note: "Đã lưu cấu hình đề xuất trên Worker/D1 — lịch bên phía Muse chưa đổi cho đến khi được xác nhận.",
            ...eta
        });
    }

    return jsonResponse({ ok: false, error: "Not found" }, 404);
}

// ─── Telegram V2 Views (Reports, Drive Explorer, Crawl Center, Task History) ─

const NON_PRODUCT_TOKENS = new Set([
    "tiktok", "threads", "shopee", "facebook", "instagram", "youtube",
    "beacons", "beacons.ai", "linktree", "linktr.ee", "bit.ly", "endless", "tiki", "lazada"
]);

function filterCleanProductItems(list) {
    return (Array.isArray(list) ? list : []).filter(a => {
        const name = String(a?.product || a?.title || a || "").replace(/^[@#]+/, "").trim().toLowerCase();
        return Boolean(name) && !NON_PRODUCT_TOKENS.has(name);
    });
}

async function buildAnalysisReportBundle(env) {
    const appState = await getAppOnlineSummary(env);
    let hbReport = null;
    let hbCounts = {};
    if (appState.heartbeat) {
        try { hbCounts = JSON.parse(appState.heartbeat.market_counts_json || "{}"); } catch {}
        try { hbReport = JSON.parse(appState.heartbeat.latest_report_json || "null"); } catch {}
    }

    // 1. Prioritize latest corrected Muse report directly from Drive (works even when App_Video is OFFLINE)
    let museDrive = { reportsFolderId: null, file: null, data: null };
    try {
        museDrive = await getLatestMuseReportFromDrive(env);
    } catch {}

    const museData = museDrive.data;
    const reportsFolderUrl = museDrive.reportsFolderId
        ? `https://drive.google.com/drive/folders/${museDrive.reportsFolderId}`
        : `https://drive.google.com/drive/folders/${getWorkspaceRootId(env)}`;

    let summaryText = "";
    let fullText = "";
    let sendFileName = `market_report_${Date.now()}.json`;
    let sendJsonPayload = null;

    if (museData && typeof museData === "object") {
        const isCorr = String(museDrive.file?.name || "").toLowerCase().includes("_corrected") || Boolean(museData.corrects_report);
        const totals = museData.totals || {};
        const pr = museData.platform_results || {};
        const headerBadge = isCorr ? "BẢN CORRECTED V2" : "MUSE DRIVE REPORT";
        sendFileName = museDrive.file?.name || "market_report_muse.json";
        sendJsonPayload = museData;

        const platLines = [];
        for (const pKey of ["threads", "tiktok", "shopee"]) {
            const p = pr[pKey];
            if (!p) continue;
            platLines.push(
                `• ${pKey.toUpperCase()}: ${p.business_usable_records ?? 0}/${p.records ?? 0} dùng được` +
                ` | KT: ${p.technical_status || p.status || "—"} | CL: ${p.data_quality_status || "—"}`
            );
        }

        const headerLines = [
            `📊 PHÂN TÍCH THỊ TRƯỜNG (${headerBadge})`,
            `📄 File: ${sendFileName}` + (appState.online ? " | 🖥️ App_Video: 🟢 ONLINE" : " | 🖥️ App_Video: 🔴 OFFLINE (đọc trực tiếp Drive)"),
            `Tổng crawl: ${totals.records ?? "N/A"} | Dùng được (usable): ${totals.business_usable_records ?? "N/A"} | Cần xác minh: ${totals.needs_verification_records ?? "N/A"}`,
            `Kỹ thuật: ${museData.technical_status || "—"} | Chất lượng: ${museData.data_quality_status || "—"}`
        ];
        if (platLines.length) headerLines.push(platLines.join("\n"));

        if (museData.telegram_summary) {
            summaryText = `${headerLines.join("\n")}\n\n${museData.telegram_summary}`;
        } else {
            summaryText = headerLines.join("\n\n");
        }

        // Detailed breakdown for "Xem báo cáo đầy đủ"
        const detailSections = [`📖 BÁO CÁO ĐẦY ĐỦ: ${museData.report || sendFileName}`];
        for (const pKey of ["threads", "tiktok", "shopee"]) {
            const p = pr[pKey];
            if (!p) continue;
            const corrs = Array.isArray(p.corrections) ? p.corrections.slice(0, 3).map(c => `  + ${c}`).join("\n") : "";
            const issues = Array.isArray(p.remaining_issues) ? p.remaining_issues.slice(0, 3).map(i => `  - ${i}`).join("\n") : "";
            detailSections.push(
                `[${pKey.toUpperCase()}] ${p.business_usable_records ?? 0}/${p.records ?? 0} usable (KT: ${p.technical_status || p.status}, CL: ${p.data_quality_status || "—"})` +
                (corrs ? `\nĐã chuẩn hóa:\n${corrs}` : "") +
                (issues ? `\nHạn chế còn lại:\n${issues}` : "")
            );
        }
        const lims = Array.isArray(museData.data_quality_and_limitations)
            ? museData.data_quality_and_limitations.slice(0, 4).map(l => `• ${l}`).join("\n")
            : "";
        if (lims) detailSections.push(`⚠️ Giới hạn dữ liệu:\n${lims}`);
        fullText = detailSections.join("\n\n");
    } else if (hbReport && typeof hbReport === "object") {
        // Fallback strictly when NO Muse report JSON exists in 01_Market_Data/Reports/
        const cleanAff = filterCleanProductItems(hbReport.affiliate_opportunities).slice(0, 3);
        const trends = Array.isArray(hbReport.trending_highlights)
            ? hbReport.trending_highlights.slice(0, 3).map(t => t?.title || t?.topic || t).filter(Boolean)
            : [];
        const needs = Array.isArray(hbReport.consumer_needs_problems)
            ? hbReport.consumer_needs_problems.slice(0, 2).map(n => n?.title || n).filter(Boolean)
            : [];
        const dq = hbReport.data_quality || {};
        summaryText = [
            "📊 PHÂN TÍCH THỊ TRƯỜNG (Dữ liệu App_Video)",
            `Tổng: ${hbCounts.total_items ?? dq.total_items ?? 0} bản ghi | Usable: ${dq.usable_items ?? "N/A"} | Cần xác minh: ${dq.needs_verification_items ?? "N/A"}`,
            trends.length ? `🔥 Xu hướng: ${trends.join("; ")}` : "",
            needs.length ? `💡 Nhu cầu: ${needs.join("; ")}` : "",
            `🛒 Cơ hội đủ điều kiện: ${cleanAff.length ? cleanAff.map(a => a.product).join("; ") : "Chưa có sản phẩm đủ điều kiện"}`
        ].filter(Boolean).join("\n\n");
        fullText = summaryText;
        sendJsonPayload = hbReport;
    } else {
        summaryText = "📊 PHÂN TÍCH THỊ TRƯỜNG\n\nChưa tìm thấy báo cáo nào trong AI_WORKSPACE/01_Market_Data/Reports/.";
        fullText = summaryText;
        sendJsonPayload = { status: "empty", generated_at: new Date().toISOString() };
    }

    const kb = {
        inline_keyboard: [
            [
                { text: "📖 Xem báo cáo đầy đủ", callback_data: "rpt:full" },
                { text: "🔄 Làm mới báo cáo", callback_data: "rpt:summary" }
            ],
            [
                { text: "🏠 Trang chủ", callback_data: "menu:home" }
            ]
        ]
    };
    return {
        text: summaryText,
        fullText,
        reply_markup: kb,
        fullPayload: sendJsonPayload,
        fileName: sendFileName,
        reportsFolderId: museDrive.reportsFolderId,
        driveFileId: museDrive.file?.id || null
    };
}

async function buildAutoScheduleView(env) {
    const eta = await computeExpectedPickup(env);
    const appState = await getAppOnlineSummary(env);
    const syncIntervalSec = appState.heartbeat?.sync_interval_seconds ?? 300;
    const museStatusText = eta.muse_schedule_confirmed
        ? "✅ Đã kích hoạt & xác nhận lịch tự động phía Muse"
        : "⚪ Chưa kích hoạt / Chưa xác nhận lịch tự động phía Muse";
    const lastPollText = eta.muse_last_poll_at
        ? formatVnTime(eta.muse_last_poll_at)
        : "Chưa ghi nhận (Muse chưa poll lần nào)";
    const nextEtaText = eta.expected_pickup_at
        ? formatVnTime(eta.expected_pickup_at)
        : "Chưa xác định (chỉ tính sau khi có lượt poll thật từ Muse)";

    const text =
        "⏱️ LỊCH TỰ ĐỘNG (TRẠNG THÁI THỰC TẾ)\n\n" +
        `• Lịch tự động Muse: ${museStatusText}\n` +
        `• Poll Muse thực tế gần nhất: ${lastPollText}\n` +
        `• Dự kiến nhận việc kế tiếp: ${nextEtaText}\n` +
        `• Hồ sơ D1 đề xuất: ${eta.profile_label} (không tự bật lịch bên Muse)\n` +
        `• Auto Sync App_Video: Chạy ngầm tự động mỗi ${syncIntervalSec}s khi App_Video online`;

    const kb = {
        inline_keyboard: [
            [
                { text: "🔄 Kiểm tra lại", callback_data: "sched:view" },
                { text: "🕷️ Crawl dữ liệu", callback_data: "crawl:menu" }
            ],
            [
                { text: "⚙️ Cài đặt", callback_data: "cfg:menu" },
                { text: "🏠 Trang chủ", callback_data: "menu:home" }
            ]
        ]
    };
    return { text, reply_markup: kb };
}

async function buildSettingsMenu(env) {
    const appState = await getAppOnlineSummary(env);
    const eta = await computeExpectedPickup(env);
    const syncIntervalSec = appState.heartbeat?.sync_interval_seconds ?? 300;
    const lastSyncText = appState.heartbeat?.last_sync_at
        ? formatVnTime(appState.heartbeat.last_sync_at)
        : "Chưa ghi nhận";

    const text =
        "⚙️ CÀI ĐẶT HỆ THỐNG & ĐỒNG BỘ\n\n" +
        `• Auto Sync App_Video: Tự động chạy ngầm (${syncIntervalSec}s/lần)\n` +
        `• App_Video Desktop: ${appState.online ? `🟢 ONLINE (${appState.age_sec}s)` : "🔴 OFFLINE"}\n` +
        `• Đồng bộ gần nhất: ${lastSyncText}\n` +
        `• Hồ sơ chu kỳ đề xuất (D1): ${eta.profile_label}\n` +
        `• Xác nhận lịch phía Muse: ${eta.muse_schedule_confirmed ? "✅ Đã xác nhận" : "⚪ Chưa xác nhận (chỉ tham chiếu)"}`;

    const kb = {
        inline_keyboard: [
            [
                { text: "🔄 Đồng bộ thủ công ngay", callback_data: "cmd:sync_now" },
                { text: "📡 Ping App_Video Live", callback_data: "cmd:app_status" }
            ],
            [
                { text: "🔀 Đổi hồ sơ đề xuất (5p / 6h)", callback_data: "crawl:toggle_profile" }
            ],
            [
                { text: "⏱️ Lịch tự động", callback_data: "sched:view" },
                { text: "🏠 Trang chủ", callback_data: "menu:home" }
            ]
        ]
    };
    return { text, reply_markup: kb };
}

async function buildSystemStatusView(env) {
    let dbStatus = "❌ Chưa kết nối";
    try {
        const result = await env.DB.prepare("SELECT 1 AS ok").first();
        if (result?.ok === 1) dbStatus = "✅ Connected";
    } catch {
        dbStatus = "❌ Connection failed";
    }

    let driveBackendStatus = "❌ Connection failed";
    try {
        const token = await getDriveToken(env);
        const rootId = getWorkspaceRootId(env);
        await driveGetJson(token, `files/${encodeURIComponent(rootId)}?fields=id,name`);
        driveBackendStatus = "✅ Connected (Auto Sync Backend)";
    } catch {}

    const appState = await getAppOnlineSummary(env);
    const eta = await computeExpectedPickup(env);
    const hb = appState.heartbeat;
    let hbCounts = {};
    if (hb?.market_counts_json) {
        try { hbCounts = JSON.parse(hb.market_counts_json); } catch {}
    }

    const text =
        "📡 TRẠNG THÁI HỆ THỐNG\n\n" +
        "• Cloudflare Worker: ✅ Online\n" +
        "• Telegram Bot: ✅ Connected\n" +
        `• D1 Database: ${dbStatus}\n` +
        `• Drive Connector Backend: ${driveBackendStatus}\n` +
        `• App_Video Desktop: ${appState.online ? `🟢 ONLINE (${appState.age_sec}s)` : "🔴 OFFLINE"}\n` +
        `• Đồng bộ ngầm gần nhất: ${hb?.last_sync_at ? formatVnTime(hb.last_sync_at) : "Chưa ghi nhận"} (${hb?.sync_status || "unknown"})\n` +
        `• Kho local App_Video: ${hbCounts.total_items ?? "N/A"} bản ghi\n` +
        `• Lịch tự động Muse: ${eta.muse_schedule_confirmed ? "✅ Đã xác nhận" : "⚪ Chưa kích hoạt / Chưa xác nhận"}\n` +
        `• Muse Poll gần nhất: ${eta.muse_last_poll_at ? formatVnTime(eta.muse_last_poll_at) : "Chưa ghi nhận"}\n` +
        "• Auto Publisher: Chưa triển khai";

    const kb = {
        inline_keyboard: [
            [
                { text: "🔄 Làm mới", callback_data: "sys:status" },
                { text: "⚙️ Cài đặt", callback_data: "cfg:menu" }
            ],
            [
                { text: "🏠 Trang chủ", callback_data: "menu:home" }
            ]
        ]
    };
    return { text, reply_markup: kb };
}

async function buildCrawlCenterMenu(env) {
    await expireStaleLeases(env);
    const eta = await computeExpectedPickup(env);
    const rowsRes = await env.DB.prepare(
        "SELECT id, platform, requested_count, status, requested_by_source, expected_pickup_at FROM crawl_requests ORDER BY created_at DESC LIMIT 6"
    ).all();
    const rows = rowsRes.results || [];
    const summaryLines = rows.map(r => {
        const srcLabel = r.requested_by_source === "telegram" ? "Thủ công TG" : (r.requested_by_source === "app_video" ? "Thủ công App" : (r.requested_by_source || "N/A"));
        return `• [${r.platform}/${r.requested_count}|${srcLabel}] ${formatCrawlStatusLabel(r.status)} (${r.id.slice(-8)})`;
    });

    const inlineRows = [
        [
            { text: "🧵 Threads", callback_data: "crawl:plat:threads" },
            { text: "🎵 TikTok", callback_data: "crawl:plat:tiktok" }
        ],
        [
            { text: "🛒 Shopee", callback_data: "crawl:plat:shopee" },
            { text: "🌐 Tất cả (All)", callback_data: "crawl:plat:all" }
        ]
    ];
    for (const r of rows) {
        if (r.status === "queued") {
            inlineRows.push([{ text: `✖️ Hủy chờ ${r.platform}/${r.requested_count} (${r.id.slice(-8)})`, callback_data: `crawl:cancel:${r.id}` }]);
        }
    }
    inlineRows.push([
        { text: "⏱️ Lịch tự động", callback_data: "sched:view" },
        { text: "🏠 Trang chủ", callback_data: "menu:home" }
    ]);

    const pollRealText = eta.muse_last_poll_at
        ? `${formatVnTime(eta.muse_last_poll_at)} (dự kiến kế tiếp: ${eta.expected_pickup_at ? formatVnTime(eta.expected_pickup_at) : "Chưa có"})`
        : "Chưa ghi nhận lượt poll nào từ Muse (lịch Muse chưa bật)";

    const confirmText = eta.muse_schedule_confirmed
        ? "✅ Đã xác nhận từ lượt poll thực tế của Muse"
        : "⚠️ Lịch phía Muse CHƯA xác nhận thay đổi (đây chỉ là mức cấu hình trên Worker/D1)";

    const text =
        "🕷️ CRAWL DỮ LIỆU — HÀNG ĐỢI D1\n\n" +
        `Chu kỳ cấu hình Worker: ${eta.profile_label}\n` +
        `${confirmText}\n` +
        `Poll thực tế gần nhất: ${pollRealText}\n` +
        "ℹ️ Yêu cầu mới giữ trạng thái CHỜ (queued) cho đến khi Muse thực sự claim.\n\n" +
        (summaryLines.length ? `Gần đây:\n${summaryLines.join("\n")}` : "Chưa có yêu cầu nào.");

    return { text, reply_markup: { inline_keyboard: inlineRows } };
}

async function buildTaskHistoryView(env) {
    await expireStaleLeases(env);
    const cmds = (await env.DB.prepare(
        "SELECT id, command_type, status, created_at FROM app_commands ORDER BY created_at DESC LIMIT 6"
    ).all()).results || [];
    const crawls = (await env.DB.prepare(
        "SELECT id, platform, requested_count, status, requested_by_source, created_at FROM crawl_requests ORDER BY created_at DESC LIMIT 5"
    ).all()).results || [];

    const inlineRows = [];
    const cmdLines = cmds.map(c => {
        if (c.status === "queued") {
            inlineRows.push([{ text: `✖️ Hủy lệnh ${c.command_type} (${c.id.slice(-8)})`, callback_data: `cmd:cancel:${c.id}` }]);
        }
        return `• ${c.command_type} [${c.status}] — ${formatVnTime(c.created_at)}`;
    });
    const crwLines = crawls.map(r => `• Crawl ${r.platform}(${r.requested_count}) [${r.status}|${r.requested_by_source || "manual"}] — ${formatVnTime(r.created_at)}`);

    inlineRows.push([
        { text: "🕷️ Crawl dữ liệu", callback_data: "crawl:menu" },
        { text: "⚙️ Cài đặt", callback_data: "cfg:menu" }
    ]);
    inlineRows.push([{ text: "🏠 Trang chủ", callback_data: "menu:home" }]);

    const text =
        "📋 LỊCH SỬ TÁC VỤ & HÀNG ĐỢI D1\n\n" +
        `Lệnh App_Video:\n${cmdLines.length ? cmdLines.join("\n") : "• Trống"}\n\n` +
        `Yêu cầu Crawl Muse:\n${crwLines.length ? crwLines.join("\n") : "• Trống"}`;

    return { text, reply_markup: { inline_keyboard: inlineRows } };
}

// ─── Telegram Webhook Handler ────────────────────────────────────────────────

async function handleTelegramUpdate(request, env) {
    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.TELEGRAM_WEBHOOK_SECRET || !timingSafeEqualStr(secret || "", env.TELEGRAM_WEBHOOK_SECRET)) {
        return new Response("Forbidden", { status: 403 });
    }
    if (!env.TELEGRAM_BOT_TOKEN_VIDEO) return new Response("Config error", { status: 503 });

    const update = await request.json();

    // 1. Inline Keyboard callback_query
    if (update.callback_query) {
        const cq = update.callback_query;
        const fromId = String(cq.from?.id || "");
        const chatId = String(cq.message?.chat?.id || "");
        const msgId = cq.message?.message_id || null;
        if (!env.TELEGRAM_OWNER_ID || fromId !== env.TELEGRAM_OWNER_ID || chatId !== env.TELEGRAM_OWNER_ID) {
            return new Response("OK");
        }
        const data = String(cq.data || "");
        await answerCallbackQuery(env, cq.id);

        if (data === "menu:home") {
            const appState = await getAppOnlineSummary(env);
            await sendOrEditTelegram(
                env,
                chatId,
                msgId,
                `🤖 APP VIDEO MANAGER V2\nApp_Video: ${appState.online ? "🟢 ONLINE" : "🔴 OFFLINE"}\nChọn thao tác:`,
                mainInlineKeyboard()
            );
            return new Response("OK");
        }

        if (data === "cmd:sync_now" || data === "cmd:app_status" || data === "cmd:market_stats") {
            const cmdType = data.slice(4);
            const appState = await getAppOnlineSummary(env);
            const enq = await enqueueCommand(env, cmdType, { source: "telegram_inline" }, chatId, `tg_upd_${update.update_id}`, msgId);
            const statusText =
                `📨 ĐÃ XẾP LỆNH: ${cmdType}\nMã lệnh: ${enq.id}\nTrạng thái: ${enq.status}\n\n` +
                (appState.online
                    ? "🟢 App_Video đang ONLINE — đang chờ nhận và thực thi..."
                    : "⏳ App_Video đang OFFLINE — lệnh đã lưu hàng đợi D1, sẽ tự chạy khi online.");
            const edited = await sendOrEditTelegram(env, chatId, msgId, statusText, mainInlineKeyboard());
            if (edited.ok && (msgId || edited.data?.result?.message_id)) {
                await setRuntimeState(env, "tg_primary_status_msg_id", String(msgId || edited.data.result.message_id));
            }
            return new Response("OK");
        }

        if (data === "cmd:history" || data === "cmd:queue") {
            const view = await buildTaskHistoryView(env);
            await sendOrEditTelegram(env, chatId, msgId, view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data.startsWith("cmd:cancel:")) {
            const cmdId = data.slice("cmd:cancel:".length);
            const res = await env.DB.prepare(
                "UPDATE app_commands SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'queued'"
            ).bind(new Date().toISOString(), cmdId).run();
            const prefix = res.meta.changes === 1
                ? `✅ Đã hủy lệnh chờ: ${cmdId}\n\n`
                : `⚠️ Không thể hủy ${cmdId} (có thể đang chạy hoặc đã xong).\n\n`;
            const view = await buildTaskHistoryView(env);
            await sendOrEditTelegram(env, chatId, msgId, prefix + view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data === "rpt:summary" || data === "mkt:report") {
            const bundle = await buildAnalysisReportBundle(env);
            await sendOrEditTelegram(env, chatId, msgId, bundle.text, bundle.reply_markup);
            return new Response("OK");
        }

        if (data === "rpt:full") {
            const bundle = await buildAnalysisReportBundle(env);
            await sendOrEditTelegram(env, chatId, msgId, bundle.fullText || bundle.text, bundle.reply_markup);
            return new Response("OK");
        }

        if (data === "sched:view") {
            const view = await buildAutoScheduleView(env);
            await sendOrEditTelegram(env, chatId, msgId, view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data === "cfg:menu") {
            const view = await buildSettingsMenu(env);
            await sendOrEditTelegram(env, chatId, msgId, view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data === "sys:status") {
            const view = await buildSystemStatusView(env);
            await sendOrEditTelegram(env, chatId, msgId, view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data === "rpt:send" || data === "rpt:gdoc" || data === "drv:root" || data.startsWith("drv:")) {
            await sendOrEditTelegram(
                env,
                chatId,
                msgId,
                "ℹ️ Drive Explorer và gửi file JSON đã được bỏ khỏi menu Telegram. Auto Sync tự động đồng bộ ngầm về App_Video (hoặc vào ⚙️ Cài đặt → Đồng bộ thủ công).",
                mainInlineKeyboard()
            );
            return new Response("OK");
        }

        if (data === "crawl:menu") {
            const view = await buildCrawlCenterMenu(env);
            await sendOrEditTelegram(env, chatId, msgId, view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data === "crawl:toggle_profile") {
            const cur = await getRuntimeState(env, "muse_poll_profile", env.MUSE_POLL_PROFILE || "production_6h");
            const next = cur === "test_5m" ? "production_6h" : "test_5m";
            await setRuntimeState(env, "muse_poll_profile", next);
            await setRuntimeState(env, "muse_schedule_confirmed", "0");
            const view = await buildSettingsMenu(env);
            const banner = "ℹ️ Đã đổi hồ sơ tham chiếu trên Worker/D1 — lịch tự động phía Muse CHƯA kích hoạt/chưa đổi nếu chưa có xác nhận từ Muse.\n\n";
            await sendOrEditTelegram(env, chatId, msgId, banner + view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data.startsWith("crawl:plat:")) {
            const plat = data.slice("crawl:plat:".length);
            if (!ALLOWED_CRAWL_PLATFORMS.has(plat)) return new Response("OK");
            const kb = {
                inline_keyboard: [
                    [
                        { text: "20 bài", callback_data: `crawl:go:${plat}:20` },
                        { text: "50 bài", callback_data: `crawl:go:${plat}:50` },
                        { text: "100 bài", callback_data: `crawl:go:${plat}:100` }
                    ],
                    [{ text: "⬅️ Chọn lại nền tảng", callback_data: "crawl:menu" }]
                ]
            };
            await sendOrEditTelegram(
                env,
                chatId,
                msgId,
                `🕷️ CRAWL CENTER — NỀN TẢNG: ${plat.toUpperCase()}\n\nChọn số lượng mục tiêu để đưa vào hàng đợi D1 cho Muse polling:`,
                kb
            );
            return new Response("OK");
        }

        if (data.startsWith("crawl:go:") || data.startsWith("crawl:queue:")) {
            let plat = "threads";
            let count = 50;
            if (data.startsWith("crawl:go:")) {
                const parts = data.split(":");
                plat = parts[2] || "threads";
                count = Number(parts[3] || "50");
            } else {
                plat = data.slice("crawl:queue:".length);
            }
            if (ALLOWED_CRAWL_PLATFORMS.has(plat)) {
                const res = await createCrawlRequestInD1(env, {
                    platform: plat,
                    keywords: "",
                    requestedCount: count,
                    chatId,
                    source: "telegram",
                    dedupToken: `tg_crw_${update.update_id}`
                });
                const reqRow = res.request;
                const etaInfo = reqRow.expected_pickup_at
                    ? `Dự kiến Muse nhận: ${formatVnTime(reqRow.expected_pickup_at)}`
                    : "Dự kiến Muse nhận: Chưa xác định (chưa ghi nhận lượt poll từ Muse)";
                const msg =
                    `📝 ${res.deduplicated ? "ĐÃ CÓ YÊU CẦU TƯƠNG TỰ ĐANG CHỜ" : "ĐÃ XẾP YÊU CẦU CRAWL VÀO D1"}\n\n` +
                    `Mã: ${reqRow.id}\n` +
                    `Nền tảng: ${reqRow.platform.toUpperCase()} | Số lượng: ${reqRow.requested_count}\n` +
                    `Trạng thái: ${formatCrawlStatusLabel(reqRow.status)}\n` +
                    `Nguồn kích hoạt: Thủ công từ Telegram\n` +
                    `Cấu hình Worker: ${formatPollProfileLabel(reqRow.poll_profile)}\n` +
                    etaInfo;
                const kb = {
                    inline_keyboard: [
                        reqRow.status === "queued"
                            ? [{ text: "✖️ Hủy yêu cầu này", callback_data: `crawl:cancel:${reqRow.id}` }]
                            : [],
                        [{ text: "🕷️ Về Crawl dữ liệu mới", callback_data: "crawl:menu" }, { text: "🏠 Trang chủ", callback_data: "menu:home" }]
                    ].filter(r => r.length > 0)
                };
                const edited = await sendOrEditTelegram(env, chatId, msgId, msg, kb);
                if (edited.ok && (msgId || edited.data?.result?.message_id)) {
                    await setRuntimeState(env, "tg_primary_status_msg_id", String(msgId || edited.data.result.message_id));
                }
            }
            return new Response("OK");
        }

        if (data.startsWith("crawl:cancel:")) {
            const crawlId = data.slice("crawl:cancel:".length);
            const upd = await env.DB.prepare(
                "UPDATE crawl_requests SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'queued'"
            ).bind(new Date().toISOString(), crawlId).run();
            const note = upd.meta.changes === 1
                ? `✅ Đã hủy yêu cầu crawl đang chờ: ${crawlId}\n\n`
                : `⚠️ Không thể hủy ${crawlId} (Muse đã claim hoặc đã kết thúc).\n\n`;
            const view = await buildCrawlCenterMenu(env);
            await sendOrEditTelegram(env, chatId, msgId, note + view.text, view.reply_markup);
            return new Response("OK");
        }

        return new Response("OK");
    }

    // 2. Standard Telegram message / Reply Keyboard
    const msg = update.message;
    if (
        !msg ||
        !env.TELEGRAM_OWNER_ID ||
        msg.chat?.type !== "private" ||
        String(msg.from?.id) !== env.TELEGRAM_OWNER_ID ||
        String(msg.chat?.id) !== env.TELEGRAM_OWNER_ID
    ) {
        return new Response("OK");
    }

    const chatId = msg.chat.id;
    const rawInput = (msg.text || "").trim();
    const mapped = {
        "🏠 Trang chủ": "/start",
        "📊 Trạng thái": "/status",
        "⏱️ Lịch tự động": "/schedule",
        "⚙️ Cài đặt": "/settings",
        "🔍 Dữ liệu thị trường": "/market",
        "🕷️ Crawl Center": "/crawlcenter",
        "📋 Hàng đợi lệnh": "/queue",
        "📋 Lịch sử tác vụ": "/queue"
    }[rawInput] || rawInput;

    let reply = "Sử dụng /start hoặc /status.";
    let markup = mainInlineKeyboard();

    if (mapped === "/start") {
        const appState = await getAppOnlineSummary(env);
        const eta = await computeExpectedPickup(env);
        reply =
            "🤖 APP VIDEO MANAGER V2\n\n" +
            `• Cloudflare Worker: ✅ Online\n` +
            `• App_Video Desktop: ${appState.online ? `🟢 ONLINE (${appState.age_sec}s)` : "🔴 OFFLINE (Auto Sync chạy khi mở app)"}\n` +
            `• Lịch tự động Muse: ${eta.muse_schedule_confirmed ? "✅ Đã xác nhận" : "⚪ Chưa kích hoạt / Chưa xác nhận"}\n` +
            `• Muse poll gần nhất: ${eta.muse_last_poll_at ? formatVnTime(eta.muse_last_poll_at) : "Chưa ghi nhận"}\n\n` +
            "Chọn chức năng trong bảng điều khiển bên dưới:";
        const sent = await updatePrimaryProgressMessage(env, chatId, reply, mainInlineKeyboard());
        return new Response(sent.ok ? "OK" : "Delivery failed", { status: sent.ok ? 200 : 502 });
    } else if (mapped === "/schedule") {
        const view = await buildAutoScheduleView(env);
        reply = view.text;
        markup = view.reply_markup;
    } else if (mapped === "/settings") {
        const view = await buildSettingsMenu(env);
        reply = view.text;
        markup = view.reply_markup;
    } else if (mapped === "/sync") {
        const appState = await getAppOnlineSummary(env);
        const enq = await enqueueCommand(env, "sync_now", { source: "telegram_cmd" }, chatId, `tg_msg_${msg.message_id}`, msg.message_id);
        reply =
            `📨 ĐÃ ĐƯA LỆNH ĐỒNG BỘ THỦ CÔNG VÀO HÀNG ĐỢI\nMã lệnh: ${enq.id}\n` +
            (appState.online
                ? "🟢 App_Video đang ONLINE — đang chờ nhận và thực thi."
                : "⏳ App_Video hiện đang OFFLINE — lệnh sẽ chạy tự động khi mở App_Video.");
        const sent = await updatePrimaryProgressMessage(env, chatId, reply, mainInlineKeyboard());
        return new Response(sent.ok ? "OK" : "Delivery failed", { status: sent.ok ? 200 : 502 });
    } else if (mapped === "/market") {
        const bundle = await buildAnalysisReportBundle(env);
        reply = bundle.text;
        markup = bundle.reply_markup;
    } else if (mapped === "/crawlcenter") {
        const view = await buildCrawlCenterMenu(env);
        reply = view.text;
        markup = view.reply_markup;
    } else if (mapped === "/queue") {
        const view = await buildTaskHistoryView(env);
        reply = view.text;
        markup = view.reply_markup;
    } else if (mapped.startsWith("/request_crawl")) {
        const parts = mapped.split(/\s+/);
        const plat = (parts[1] || "").toLowerCase();
        const maybeCount = Number.parseInt(parts[2], 10);
        const count = Number.isFinite(maybeCount) ? maybeCount : 50;
        const keywords = (Number.isFinite(maybeCount) ? parts.slice(3) : parts.slice(2)).join(" ").trim();
        if (!ALLOWED_CRAWL_PLATFORMS.has(plat)) {
            reply = "Cú pháp: /request_crawl <threads|tiktok|shopee|all> [số_lượng] [từ_khóa]";
        } else {
            const res = await createCrawlRequestInD1(env, {
                platform: plat,
                keywords,
                requestedCount: count,
                chatId: String(chatId),
                source: "telegram",
                dedupToken: `tg_crw_msg_${msg.message_id}`
            });
            const reqRow = res.request;
            const etaText = reqRow.expected_pickup_at
                ? formatVnTime(reqRow.expected_pickup_at)
                : "Chưa xác định (chưa ghi nhận lượt poll từ Muse)";
            reply =
                `📝 Đã xếp yêu cầu crawl (${reqRow.platform}/${reqRow.requested_count}) vào D1: ${reqRow.id}\n` +
                `Trạng thái: ${formatCrawlStatusLabel(reqRow.status)}\n` +
                `Dự kiến Muse nhận: ${etaText}`;
        }
    } else if (mapped === "/notifytest") {
        try {
            await notifyNewCrawl(env);
            const last = await env.DB.prepare(`
                SELECT run_id, status, record_count FROM crawl_notifications ORDER BY created_at DESC LIMIT 1
            `).first();
            reply = last
                ? `✅ Notification check\n\nRun: ${last.run_id}\nStatus: ${last.status}\nRecords: ${last.record_count}`
                : "⚠️ Không có lượt crawl đủ điều kiện.";
        } catch {
            reply = "❌ Notification check failed.";
        }
    } else if (mapped === "/crawl") {
        try {
            const data = await getLatestCrawl(env);
            if (!data) {
                reply = "Chưa có kết quả crawl.";
            } else {
                const completed = data.status === "completed";
                reply =
                    "📊 MARKET INTELLIGENCE\n\n" +
                    "Nền tảng: Threads\n" +
                    "Nguồn dữ liệu: Muse\n" +
                    `Trạng thái: ${completed ? "✅ Hoàn thành" : "⚠️ Chưa xác nhận"}\n` +
                    `Số bài: ${data.posts.length}\n` +
                    `Trùng đã loại: ${Number.isSafeInteger(data.duplicates_skipped) ? data.duplicates_skipped : "N/A"}\n` +
                    `Cập nhật: ${formatVnTime(data.ended_at)}`;
            }
        } catch {
            reply = "❌ Không đọc được kết quả crawl.";
        }
    } else if (mapped === "/drive") {
        reply = "ℹ️ Drive Explorer và chức năng gửi file JSON đã được bỏ khỏi menu Telegram. Auto Sync tự động đồng bộ ngầm về App_Video.";
    } else if (mapped === "/status") {
        const view = await buildSystemStatusView(env);
        reply = view.text;
        markup = view.reply_markup;
    }

    const sent = await sendTelegramMessage(env, chatId, reply, markup);
    return new Response(sent.ok ? "OK" : "Delivery failed", { status: sent.ok ? 200 : 502 });
}

// ─── Worker Entrypoint ───────────────────────────────────────────────────────

export default {
    async scheduled(controller, env, ctx) {
        try {
            await notifyNewCrawl(env);
            await expireStaleLeases(env);
        } catch {
            console.error("Scheduled check failed");
            throw new Error("Scheduled check failed");
        }
    },

    async fetch(request, env) {
        const url = new URL(request.url);

        if (request.method === "GET" && url.pathname === "/") {
            return new Response("App Video Scheduler: ONLINE");
        }

        if (url.pathname.startsWith("/api/app/")) {
            return handleAppApi(request, env, url);
        }

        if (url.pathname === "/telegram" && request.method === "POST") {
            try {
                return await handleTelegramUpdate(request, env);
            } catch {
                return new Response("Internal error", { status: 500 });
            }
        }

        return new Response("Not found", { status: 404 });
    }
};
