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
 *   4. Telegram V2: Reply Keyboard 6-button main menu + Inline Keyboard sub-actions,
 *      `editMessageText` single primary progress message, structured icon-bulleted
 *      Muse Market Analysis Report (short + full views, Drive Explorer/JSON send removed)
 *
 * NOTE: Keep `worker.js` as V1 backup. Review + run `migrations/0001_v2_market_and_commands.sql`
 * & `migrations/0002_notification_retry_state.sql` before deploying.
 */

const DEFAULT_WORKSPACE_FOLDER_ID = "1F5m2t3uPxSlicE8PQW70xjf7FSC6345g";
const LEGACY_THREADS_FOLDER_ID = "1jJ0ihxv_LtpZFlkAeBO_BLysLZ9XwcE8";
const MARKET_DATA_DIR_NAME = "01_Market_Data";
const MAX_MARKET_JSON_BYTES = 10 * 1024 * 1024;
const ONLINE_HEARTBEAT_TTL_MS = 90 * 1000;
const STUCK_SENDING_THRESHOLD_MS = 5 * 60 * 1000;
const DEFAULT_MAX_DRIVE_LIST_PAGES = 25;
const DEFAULT_NOTIFY_BATCH_SIZE = 10;

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
    if (lower.includes("threads") || lower.startsWith("trend_report") || lower.includes("/trend_report")) return "threads";
    if (lower.includes("tiktok")) return "tiktok";
    if (lower.includes("shopee")) return "shopee";
    return null;
}

function isArchiveFolderName(name) {
    return String(name || "").trim().toLowerCase() === "archive";
}

function isArchiveRelPath(relPath) {
    return String(relPath || "")
        .split(/[\\/]+/)
        .some(seg => seg.trim().toLowerCase() === "archive");
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
        if (current.id !== targetAncestorId && isArchiveFolderName(meta.name)) {
            return { allowed: false, meta: firstMeta };
        }

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
                                if (isArchiveFolderName(n.name)) break;
                        files.push(...(listing.files || []));
                        pageToken = listing.nextPageToken || null;
                    } while (pageToken);
                    return { node: n, files };
                })
            );
            for (const { node, files } of listings) {
                if (isArchiveFolderName(node.name)) continue;
                for (const f of files) {
                    if (isArchiveFolderName(f.name) || isArchiveRelPath(f.name)) continue;
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

async function paginateDriveFolderChildren(token, folderId, maxPages = DEFAULT_MAX_DRIVE_LIST_PAGES, pageSize = 100) {
    const files = [];
    const seenTokens = new Set();
    let pageToken = null;
    const boundedMaxPages = Math.min(Math.max(Number(maxPages) || DEFAULT_MAX_DRIVE_LIST_PAGES, 1), 100);
    for (let page = 0; page < boundedMaxPages; page++) {
        const data = await listFolderChildren(token, folderId, pageToken, pageSize);
        files.push(...(data.files || []));
        const nextPt = data.nextPageToken ? String(data.nextPageToken) : null;
        if (data.has_more === true && !nextPt) {
            return { files, partialError: "missing_page_token", incomplete: true };
        }
        if (!nextPt) {
            return { files, partialError: null, incomplete: false };
        }
        if (nextPt === pageToken || seenTokens.has(nextPt)) {
            return { files, partialError: "repeated_page_token", incomplete: true };
        }
        if (page >= boundedMaxPages - 1) {
            return { files, partialError: "page_limit_exceeded", incomplete: true };
        }
        seenTokens.add(nextPt);
        pageToken = nextPt;
    }
    return { files, partialError: "page_limit_exceeded", incomplete: true };
}

async function listMarketJsonFilesRecursive(token, rootMarketFolderId, cursorIso = null, limit = 50, pageToken = null, maxDrivePages = DEFAULT_MAX_DRIVE_LIST_PAGES) {
    const results = [];
    const allActiveIds = [];
    let frontier = [{ id: rootMarketFolderId, relPath: "", depth: 0 }];
    const maxDepth = 3;
    const cursorMs = cursorIso ? Date.parse(cursorIso) : null;
    let partialError = null;

    let tokMs = null;
    let tokFileId = "";
    if (pageToken && String(pageToken).includes("|")) {
        const sepIdx = String(pageToken).lastIndexOf("|");
        const rawTs = String(pageToken).slice(0, sepIdx);
        tokFileId = String(pageToken).slice(sepIdx + 1);
        const parsedTs = Date.parse(rawTs);
        if (Number.isFinite(parsedTs)) tokMs = parsedTs;
    }

    while (frontier.length > 0) {
        const nextFrontier = [];
        const levelData = await Promise.all(
            frontier.map(async current => {
                const paged = await paginateDriveFolderChildren(token, current.id, maxDrivePages, 100);
                return { current, items: paged.files, partialError: paged.partialError };
            })
        );
        for (const { current, items, partialError: dirErr } of levelData) {
            if (dirErr && !partialError) partialError = dirErr;
            for (const f of items) {
                const isFolder = f.mimeType === "application/vnd.google-apps.folder";
                const childPath = current.relPath ? `${current.relPath}/${f.name}` : f.name;
                if (isFolder) {
                    // Strictly exclude Archive/ directories from Auto Sync & manual discovery
                    if (isArchiveFolderName(f.name) || isArchiveRelPath(childPath)) {
                        continue;
                    }
                    if (current.depth < maxDepth) {
                        nextFrontier.push({ id: f.id, relPath: childPath, depth: current.depth + 1 });
                    }
                    continue;
                }
                if (isArchiveRelPath(current.relPath)) continue;
                const isJson =
                    (f.name && f.name.toLowerCase().endsWith(".json")) ||
                    f.mimeType === "application/json";
                if (!isJson) continue;
                allActiveIds.push(f.id);
                const itemMs = f.modifiedTime ? (Date.parse(f.modifiedTime) || 0) : 0;
                // Inclusive >= on cursorMs ensures multiple files sharing cursor timestamp are never dropped
                if (cursorMs !== null && Number.isFinite(cursorMs) && itemMs > 0 && itemMs < cursorMs) continue;
                if (tokMs !== null && (itemMs < tokMs || (itemMs === tokMs && String(f.id) <= tokFileId))) continue;
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

    // Deterministic composite keyset order: (modified_time ASC, file_id ASC)
    results.sort((a, b) => {
        const diff = (Date.parse(a.modified_time || 0) || 0) - (Date.parse(b.modified_time || 0) || 0);
        if (diff !== 0) return diff;
        return a.file_id < b.file_id ? -1 : (a.file_id > b.file_id ? 1 : 0);
    });
    const effLimit = Math.min(Math.max(Number(limit) || 50, 1), 100);
    const sliced = results.slice(0, effLimit);
    let hasMore = results.length > effLimit;
    const last = sliced.length > 0 ? sliced[sliced.length - 1] : null;
    let nextPageToken = (hasMore && last) ? `${last.modified_time || "1970-01-01T00:00:00Z"}|${last.file_id}` : null;
    if (hasMore && nextPageToken && nextPageToken === pageToken) {
        partialError = partialError || "repeated_page_token";
        hasMore = false;
        nextPageToken = null;
    }
    // Never commit/advance cursor as complete if pagination hit partial_error
    const nextCursor = (!partialError && last) ? last.modified_time : cursorIso;
    return {
        status: partialError ? "partial_error" : "ok",
        partial_error: partialError,
        files: sliced,
        total_matched: results.length,
        has_more: hasMore,
        next_page_token: nextPageToken,
        next_cursor: nextCursor,
        ...(!partialError && !pageToken ? { active_file_ids: allActiveIds } : {})
    };
}

function isNonReportDocName(fileName) {
    const low = String(fileName || "").toLowerCase();
    return (
        low.includes("data_contract") ||
        low.includes("strategy") ||
        low.includes("audit") ||
        low.includes("discovery_sources") ||
        low.includes("capability_test") ||
        low.includes("identity_index") ||
        low.includes("contract_amendment")
    );
}

function getReportStem(fileName) {
    return String(fileName || "")
        .toLowerCase()
        .replace(/\.json$/i, "")
        .replace(/_corrected(_v\d+)?$/i, "");
}

function extractMuseReportStats(d) {
    if (!d || typeof d !== "object") return null;
    const s1 = d.sections?.["1_screening"] || d.section_1_screening || {};
    const s2 = d.sections?.["2_tier_distribution"] || d.section_2_tier_distribution || {};
    const c = (d.counters && typeof d.counters === "object") ? d.counters : (s1.counters && typeof s1.counters === "object" ? s1.counters : {});
    const tiers = s2.tiers || s2 || {};
    const groups = s2.groups || {};
    const isV32 = Boolean(
        d.counters ||
        d.sections?.["9_growth_tracking"] ||
        d.sections?.["10_priority_actions"] ||
        String(d.contract_version || d.schema_version || "").includes("3.2") ||
        c.new_qualified_count !== undefined
    );
    const isV3 = Boolean(isV32 || d.sections || d.run_status || d.section_1_screening);
    const rawRunStatus = d.run_status ? String(d.run_status).trim().toLowerCase() : null;
    const rawTechStatus = d.technical_status ? String(d.technical_status).trim().toLowerCase() : null;
    const rawDqStatus = d.data_quality_status ? String(d.data_quality_status).trim() : null;
    const effStatus = rawRunStatus || rawTechStatus || "completed";

    const requestedCount = pickFirstFiniteInt(c.requested_count, d.requested_count, s1.requested_count);
    const scanned = pickFirstFiniteInt(c.scanned_count, d.scanned_count, s1.scanned_count, s1.scanned_posts, d.totals?.records);
    const uniqueCandidates = pickFirstFiniteInt(c.unique_candidate_count, d.unique_candidate_count, s1.unique_candidate_count);
    const newQualifiedCount = pickFirstFiniteInt(c.new_qualified_count, d.new_qualified_count, s1.new_qualified_count);
    const newCount = pickFirstFiniteInt(c.new_count, d.new_count, s1.new_count);
    const qualified = pickFirstFiniteInt(
        newQualifiedCount,
        c.qualified_count,
        d.qualified_count,
        s1.qualified_count,
        s1.qualified_posts,
        d.totals?.business_usable_records
    );
    const duplicateHistoryCount = pickFirstFiniteInt(
        c.duplicate_history_count,
        d.duplicate_history_count,
        s1.duplicate_history_count,
        c.duplicate_count,
        d.duplicate_count,
        s1.duplicate_count
    );
    const updatedMetricsCount = pickFirstFiniteInt(
        c.updated_metrics_count,
        d.updated_metrics_count,
        s1.updated_metrics_count,
        c.updated_count,
        d.updated_count,
        s1.updated_count
    );
    const similarContentCount = pickFirstFiniteInt(c.similar_content_count, d.similar_content_count, s1.similar_content_count);
    const missingViewsCount = pickFirstFiniteInt(c.missing_views_count, d.missing_views_count, s1.missing_views_count);
    const rejected = pickFirstFiniteInt(c.rejected_count, d.rejected_count, s1.rejected_count, s1.rejected_posts);
    const crossVerified = pickFirstFiniteInt(s1.cross_verified_count, c.cross_verified_count);
    const unverifiedRaw = pickFirstFiniteInt(c.unverified_count, d.unverified_count, s1.unverified_count, tiers.unverified);
    const unverified = unverifiedRaw ?? 0;

    let targetAchieved = null;
    if (typeof c.target_achieved === "boolean") targetAchieved = c.target_achieved;
    else if (typeof d.target_achieved === "boolean") targetAchieved = d.target_achieved;
    else if (typeof s1.target_achieved === "boolean") targetAchieved = s1.target_achieved;
    else if (newQualifiedCount !== null && requestedCount !== null) targetAchieved = newQualifiedCount >= requestedCount;

    const stopReason = c.stop_reason || d.stop_reason || s1.stop_reason || null;

    let countersReconciled = null;
    if (
        uniqueCandidates !== null &&
        newQualifiedCount !== null &&
        unverifiedRaw !== null &&
        duplicateHistoryCount !== null &&
        updatedMetricsCount !== null &&
        rejected !== null
    ) {
        const expected = newQualifiedCount + unverifiedRaw + duplicateHistoryCount + updatedMetricsCount + rejected;
        countersReconciled = uniqueCandidates === expected;
    }

    const trends = d.sections?.["3_trends"] || d.section_3_trending_themes || d.trending_highlights || [];
    const topViral = d.sections?.["4_top_viral"] || d.section_4_top_viral || [];
    const s5 = d.sections?.["5_emerging_and_signals"] || {};
    const emerging = Array.isArray(s5.emerging) ? s5.emerging : (Array.isArray(d.section_5_emerging) ? d.section_5_emerging : []);
    const signals = Array.isArray(s5.signals) ? s5.signals : [];
    const needs = d.sections?.["6_consumer_needs"] || d.section_6_consumer_needs || d.consumer_needs_problems || [];
    const aff = d.sections?.["7_affiliate_priorities"] || d.section_7_affiliate_products || d.affiliate_opportunities || [];
    const contentSuggestions = d.sections?.["8_content_suggestions"] || d.content_suggestions || {};
    const growthTracking = d.sections?.["9_growth_tracking"] || d.section_9_growth_tracking || d.growth_tracking || [];
    const actions =
        d.sections?.["10_priority_actions"] ||
        d.sections?.["9_priority_actions"] ||
        d.section_9_priority_actions ||
        d.priority_actions ||
        [];

    return {
        isV3,
        isV32,
        rawRunStatus,
        rawTechStatus,
        rawDqStatus,
        effStatus,
        s1,
        tiers: {
            very_hot: tiers.very_hot ?? 0,
            hot: tiers.hot ?? 0,
            emerging: tiers.emerging ?? 0,
            watchlist: tiers.watchlist ?? 0,
            unverified
        },
        groups,
        requestedCount,
        scanned,
        uniqueCandidates,
        newCount,
        newQualifiedCount,
        qualified,
        duplicateHistoryCount,
        updatedMetricsCount,
        similarContentCount,
        missingViewsCount,
        rejected,
        crossVerified,
        unverified,
        unverifiedExplicit: unverifiedRaw,
        targetAchieved,
        stopReason,
        countersReconciled,
        trends: Array.isArray(trends) ? trends : [],
        topViral: Array.isArray(topViral) ? topViral : [],
        emerging,
        signals,
        needs: Array.isArray(needs) ? needs : [],
        aff: Array.isArray(aff) ? aff : [],
        growthTracking: Array.isArray(growthTracking) ? growthTracking : [],
        actions: Array.isArray(actions) ? actions : [],
        contentSuggestions,
        verifDetail: d.verification_detail?.note || null,
        dataLimitations: Array.isArray(d.data_quality_and_limitations) ? d.data_quality_and_limitations : [],
        platformResults: d.platform_results && typeof d.platform_results === "object" ? d.platform_results : {},
        totals: d.totals && typeof d.totals === "object" ? d.totals : null
    };
}

function getReportStatusBadge(effStatus, isCorr, isV3, isV32 = false) {
    if (effStatus === "source_error" || effStatus === "failed") {
        return "❌ LỖI NGUỒN DỮ LIỆU (source_error) — Không kết luận về thị trường";
    }
    if (effStatus === "insufficient_data") {
        return "⚠️ THIẾU DỮ LIỆU (insufficient_data) — Chưa đủ cơ sở kết luận xu hướng";
    }
    if (effStatus === "partial") {
        return isV32
            ? "⚠️ ĐẠT MỘT PHẦN MỤC TIÊU (partial) — BÁO CÁO THREADS V3.2"
            : "⚠️ ĐẠT MỘT PHẦN (partial) — Tổng hợp dữ liệu đã thu thập";
    }
    if (effStatus === "no_qualifying_posts") {
        return "⚪ NGUỒN OK — KHÔNG CÓ BÀI ĐẠT CHUẨN (no_qualifying_posts)";
    }
    if (isCorr) return "🔔 BÁO CÁO MUSE (BẢN CORRECTED V2)";
    if (isV32) return "🔔 BÁO CÁO XU HƯỚNG THREADS V3.2";
    return isV3 ? "🔔 BÁO CÁO XU HƯỚNG THREADS V3.1" : "🔔 BÁO CÁO PHÂN TÍCH THỊ TRƯỜNG";
}

function trimBullet(raw, maxLen = 220) {
    const clean = String(raw || "")
        .replace(/^\s*(?:[•\-\*]|\d+[.)])\s*/, "")
        .replace(/\s+/g, " ")
        .trim();
    if (!clean) return "";
    return clean.length > maxLen ? `${clean.slice(0, maxLen - 1).trimEnd()}…` : clean;
}

function canonicalizeSocialUrl(rawUrl) {
    if (!rawUrl || typeof rawUrl !== "string") return null;
    try {
        const trimmed = rawUrl.trim();
        if (!trimmed) return null;
        const parsed = new URL(trimmed);
        let host = (parsed.hostname || "").toLowerCase();
        if (host === "threads.com" || host === "www.threads.com" || host === "www.threads.net" || host === "threads.net") {
            host = "threads.net";
        } else if (host === "tiktok.com" || host === "m.tiktok.com" || host === "www.tiktok.com") {
            host = "www.tiktok.com";
        }
        const cleanPath = (parsed.pathname || "").replace(/\/+$/, "");
        return `${parsed.protocol.toLowerCase()}//${host}${cleanPath}`;
    } catch {
        return String(rawUrl).trim() || null;
    }
}

function buildCanonicalDedupKey(item) {
    if (!item || typeof item !== "object") return null;
    if (item.dedup_key) return String(item.dedup_key).trim();
    const plat = String(item.platform || "threads").toLowerCase().trim() || "threads";
    const postId = item.post_id ? String(item.post_id).trim() : null;
    const itemId = item.item_id || item.video_id ? String(item.item_id || item.video_id).trim() : null;
    const shopId = item.shop_id ? String(item.shop_id).trim() : null;
    const cUrl = canonicalizeSocialUrl(item.canonical_url || item.post_url || item.video_url || item.product_url || item.url);
    if (plat === "shopee") {
        if (shopId && itemId) return `shopee:item:${shopId}:${itemId}`;
        if (itemId) return `shopee:item:${itemId}`;
        if (postId) return `shopee:post:${postId}`;
        if (cUrl) return `shopee:url:${cUrl}`;
    } else if (plat === "tiktok") {
        if (itemId) return `tiktok:item:${itemId}`;
        if (postId) return `tiktok:post:${postId}`;
        if (cUrl) return `tiktok:url:${cUrl}`;
    } else {
        if (postId) return `${plat}:post:${postId}`;
        if (itemId) return `${plat}:item:${itemId}`;
        if (cUrl) return `${plat}:url:${cUrl}`;
    }
    return null;
}

function extractRegistryMetrics(item) {
    if (!item || typeof item !== "object") return {};
    const m = (item.metrics && typeof item.metrics === "object") ? item.metrics : item;
    const pickInt = (...keys) => {
        for (const k of keys) {
            const v = m[k] !== undefined ? m[k] : item[k];
            if (v !== null && v !== undefined && v !== "") {
                const n = Number(v);
                if (Number.isFinite(n)) return Math.trunc(n);
            }
        }
        return null;
    };
    const out = {};
    const views = pickInt("views", "views_count", "play_count");
    const likes = pickInt("likes", "likes_count");
    const comments = pickInt("comments", "comment_count", "comments_count", "replies_or_comments_count");
    const shares = pickInt("shares", "shares_count");
    const reposts = pickInt("reposts", "reposts_count", "repost_count");
    const quotes = pickInt("quotes", "quotes_count", "quote_count");
    const sold = pickInt("sold", "sold_count", "historical_sold");
    const priceRaw = m.price ?? item.price_vnd ?? item.price;
    const price = (priceRaw !== null && priceRaw !== undefined && priceRaw !== "" && Number.isFinite(Number(priceRaw))) ? Number(priceRaw) : null;
    if (views !== null) out.views = views;
    if (likes !== null) out.likes = likes;
    if (comments !== null) out.comments = comments;
    if (shares !== null) out.shares = shares;
    if (reposts !== null) out.reposts = reposts;
    if (quotes !== null) out.quotes = quotes;
    if (sold !== null) out.sold = sold;
    if (price !== null) out.price = price;
    return out;
}

function haveRegistryMetricsChanged(incoming, existing) {
    const keys = ["views", "likes", "comments", "shares", "reposts", "quotes", "sold", "price"];
    for (const k of keys) {
        const nv = incoming[k];
        if (nv !== undefined && nv !== null && nv !== existing[k]) {
            return true;
        }
    }
    return false;
}

function extractRegistryItemMeta(rawItem, defaultRunId = null, defaultMeasuredAt = null) {
    const metrics = extractRegistryMetrics(rawItem);
    const key = buildCanonicalDedupKey(rawItem);
    const plat = String(rawItem?.platform || "threads").toLowerCase().trim() || "threads";
    const cUrl = canonicalizeSocialUrl(
        rawItem?.canonical_url || rawItem?.post_url || rawItem?.video_url || rawItem?.product_url || rawItem?.url
    );
    const postId = rawItem?.post_id ? String(rawItem.post_id).trim() : null;
    const postPk = rawItem?.post_pk ? String(rawItem.post_pk).trim() : null;
    const contentHash = rawItem?.content_hash ? String(rawItem.content_hash).trim() : null;
    const viewsDisplay = rawItem?.views_display ?? rawItem?.metrics?.views_display ?? null;
    const viewsPrecision = rawItem?.views_precision ?? rawItem?.metrics?.views_precision ?? null;
    const viewsSource = rawItem?.views_source ?? rawItem?.metrics?.views_source ?? null;
    const viewsEvidenceUrl = rawItem?.views_evidence_url ?? rawItem?.metrics?.views_evidence_url ?? null;
    const measuredAt = rawItem?.views_measured_at || rawItem?.measured_at || rawItem?.crawled_at || rawItem?.scraped_at || defaultMeasuredAt || null;
    const cls = String(rawItem?.classification || "").trim().toUpperCase();
    const isRejected = cls === "REJECTED" || String(rawItem?.data_quality || "").toLowerCase() === "rejected";
    const hasVerifiedViews = metrics.views !== undefined && metrics.views !== null;
    const isUnverified =
        cls === "UNVERIFIED" ||
        !hasVerifiedViews ||
        String(rawItem?.verification_status || "").toLowerCase() === "unverified";
    const isNewQualified = !isRejected && !isUnverified && hasVerifiedViews;
    const effCls = cls || (isRejected ? "REJECTED" : (isUnverified ? "UNVERIFIED" : "NEW"));
    const itemRunId = rawItem?.run_id || rawItem?.last_seen_run_id || defaultRunId || null;
    return {
        metrics,
        key,
        plat,
        cUrl,
        postId,
        postPk,
        contentHash,
        viewsDisplay: viewsDisplay ? String(viewsDisplay).trim() : null,
        viewsPrecision: viewsPrecision ? String(viewsPrecision).trim() : null,
        viewsSource: viewsSource ? String(viewsSource).trim() : null,
        viewsEvidenceUrl: viewsEvidenceUrl ? String(viewsEvidenceUrl).trim() : null,
        measuredAt: measuredAt ? String(measuredAt).trim() : null,
        isRejected,
        hasVerifiedViews,
        isUnverified,
        isNewQualified,
        classification: effCls,
        runId: itemRunId ? String(itemRunId).trim() : null,
    };
}

function buildSnapshotIdempotencyKey({ dedupKey, runId, measuredAt, metrics }) {
    const effKey = String(dedupKey || "").trim();
    const effRun = String(runId || "no_run").trim();
    const effMeas = String(measuredAt || "").trim();
    const m = metrics || {};
    const mSig = `${m.views ?? ""}:${m.likes ?? ""}:${m.comments ?? ""}:${m.shares ?? ""}:${m.reposts ?? ""}:${m.quotes ?? ""}`;
    return `${effKey}|${effRun}|${effMeas}|${mSig}`;
}

async function lookupRegistryRow(env, key, plat, cUrl, postId) {
    let row = null;
    if (key) {
        row = await env.DB.prepare("SELECT * FROM market_post_registry WHERE dedup_key = ?").bind(key).first();
    }
    if (!row && cUrl) {
        row = await env.DB.prepare("SELECT * FROM market_post_registry WHERE platform = ? AND canonical_url = ? LIMIT 1")
            .bind(plat, cUrl).first();
    }
    if (!row && postId) {
        row = await env.DB.prepare("SELECT * FROM market_post_registry WHERE platform = ? AND post_id = ? LIMIT 1")
            .bind(plat, postId).first();
    }
    return row;
}

async function checkCrossRunDedupInD1(env, rawItems) {
    const items = Array.isArray(rawItems) ? rawItems : [];
    const existing = {};
    const existingKeys = [];
    const chunkSize = 100;

    for (let offset = 0; offset < items.length; offset += chunkSize) {
        const chunk = items.slice(offset, offset + chunkSize);
        for (const rawItem of chunk) {
            const key = buildCanonicalDedupKey(rawItem);
            const plat = String(rawItem?.platform || "threads").toLowerCase().trim() || "threads";
            const cUrl = canonicalizeSocialUrl(rawItem?.canonical_url || rawItem?.post_url || rawItem?.video_url || rawItem?.product_url || rawItem?.url);
            const postId = rawItem?.post_id ? String(rawItem.post_id).trim() : null;
            if (!key && !cUrl && !postId) continue;

            try {
                const row = await lookupRegistryRow(env, key, plat, cUrl, postId);
                if (row) {
                    let latestMetrics = {};
                    try { latestMetrics = JSON.parse(row.latest_metrics_json || "{}"); } catch {}
                    const resolvedKey = key || row.dedup_key;
                    existing[resolvedKey] = {
                        dedup_key: row.dedup_key,
                        post_id: row.post_id || null,
                        canonical_url: row.canonical_url || null,
                        first_seen_at: row.first_seen_at,
                        last_seen_at: row.last_seen_at,
                        snapshot_count: Number(row.snapshot_count || 1),
                        latest_metrics: latestMetrics,
                        latest_views_display: row.latest_views_display || null,
                        latest_views_precision: row.latest_views_precision || null,
                    };
                    existingKeys.push(resolvedKey);
                }
            } catch (err) {
                const re = new Error(`D1_REGISTRY_UNAVAILABLE: ${err?.message || "check failed"}`);
                re.isRegistryError = true;
                throw re;
            }
        }
    }

    return {
        existing,
        existing_keys: existingKeys,
        existing_count: existingKeys.length,
        new_count: Math.max(0, items.length - existingKeys.length),
        total_checked: items.length,
    };
}

async function insertMeasurementSnapshotInD1(env, targetDedupKey, meta, snapCls, nowIso) {
    const idempKey = buildSnapshotIdempotencyKey({
        dedupKey: targetDedupKey,
        runId: meta.runId,
        measuredAt: meta.measuredAt,
        metrics: meta.metrics,
    });
    const snapId = `snp_${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
    const ins = await env.DB.prepare(`
        INSERT OR IGNORE INTO market_post_snapshots
        (id, snapshot_idempotency_key, dedup_key, platform, post_id, run_id, measured_at, likes_count, comments_count, reposts_count, quotes_count, shares_count, views_count, views_display, views_precision, views_source, views_evidence_url, classification, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
        snapId,
        idempKey,
        targetDedupKey,
        meta.plat,
        meta.postId,
        meta.runId,
        meta.measuredAt,
        meta.metrics.likes ?? null,
        meta.metrics.comments ?? null,
        meta.metrics.reposts ?? null,
        meta.metrics.quotes ?? null,
        meta.metrics.shares ?? null,
        meta.metrics.views ?? null,
        meta.viewsDisplay,
        meta.viewsPrecision,
        meta.viewsSource,
        meta.viewsEvidenceUrl,
        snapCls,
        nowIso
    ).run();
    return Number(ins?.meta?.changes || 0) === 1;
}

async function recordCrossRunDedupInD1(env, rawItems, runId = null, opts = {}) {
    const items = Array.isArray(rawItems) ? rawItems : [];
    const nowIso = new Date().toISOString();
    const defaultMeasuredAt = opts?.crawledAt || null;
    const chunkSize = 100;

    let newCount = 0;
    let newQualifiedCount = 0;
    let duplicateCount = 0;
    let updatedCount = 0;
    let missingViewsCount = 0;
    let unverifiedCount = 0;
    let rejectedCount = 0;
    let snapshotsCreated = 0;
    let snapshotsDeduped = 0;

    for (let offset = 0; offset < items.length; offset += chunkSize) {
        const chunk = items.slice(offset, offset + chunkSize);
        for (const rawItem of chunk) {
            const meta = extractRegistryItemMeta(rawItem, runId, defaultMeasuredAt);
            if (!meta.key) continue;
            if (!meta.hasVerifiedViews) {
                missingViewsCount += 1;
            }

            try {
                let row = await lookupRegistryRow(env, meta.key, meta.plat, meta.cUrl, meta.postId);

                if (!row) {
                    const firstSeen = meta.measuredAt || nowIso;
                    const ins = await env.DB.prepare(`
                        INSERT OR IGNORE INTO market_post_registry
                        (dedup_key, platform, post_id, post_pk, canonical_url, content_hash, first_run_id, last_run_id, first_seen_at, last_seen_at, snapshot_count, latest_views_display, latest_views_precision, latest_views_source, latest_views_measured_at, latest_views_evidence_url, latest_metrics_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    `).bind(
                        meta.key,
                        meta.plat,
                        meta.postId,
                        meta.postPk,
                        meta.cUrl,
                        meta.contentHash,
                        meta.runId,
                        meta.runId,
                        firstSeen,
                        meta.measuredAt || nowIso,
                        meta.viewsDisplay,
                        meta.viewsPrecision,
                        meta.viewsSource,
                        meta.measuredAt,
                        meta.viewsEvidenceUrl,
                        JSON.stringify(meta.metrics)
                    ).run();

                    if (Number(ins?.meta?.changes || 0) === 1) {
                        newCount += 1;
                        if (meta.isRejected) rejectedCount += 1;
                        else if (meta.isNewQualified) newQualifiedCount += 1;
                        else unverifiedCount += 1;

                        const snapInserted = await insertMeasurementSnapshotInD1(
                            env,
                            meta.key,
                            meta,
                            meta.classification || "NEW",
                            nowIso
                        );
                        if (snapInserted) snapshotsCreated += 1;
                        else snapshotsDeduped += 1;
                        continue;
                    }

                    row = await lookupRegistryRow(env, meta.key, meta.plat, meta.cUrl, meta.postId);
                    if (!row) {
                        continue;
                    }
                }

                let targetDedupKey = row.dedup_key;
                if (
                    String(row.dedup_key || "").includes(":url:") &&
                    meta.key &&
                    !meta.key.includes(":url:") &&
                    meta.key !== row.dedup_key
                ) {
                    const targetTaken = await env.DB.prepare("SELECT dedup_key FROM market_post_registry WHERE dedup_key = ?")
                        .bind(meta.key).first();
                    if (!targetTaken) {
                        const prom = await env.DB.prepare(`
                            UPDATE market_post_registry
                            SET dedup_key = ?, post_id = COALESCE(?, post_id), post_pk = COALESCE(?, post_pk)
                            WHERE dedup_key = ?
                        `).bind(meta.key, meta.postId, meta.postPk, row.dedup_key).run();
                        if (Number(prom?.meta?.changes || 0) === 1) {
                            await env.DB.prepare(`
                                UPDATE market_post_snapshots
                                SET dedup_key = ?, post_id = COALESCE(?, post_id)
                                WHERE dedup_key = ?
                            `).bind(meta.key, meta.postId, row.dedup_key).run();
                            targetDedupKey = meta.key;
                            row.dedup_key = meta.key;
                        }
                    }
                }

                let prevMetrics = {};
                try { prevMetrics = JSON.parse(row.latest_metrics_json || "{}"); } catch {}
                const changed = haveRegistryMetricsChanged(meta.metrics, prevMetrics);
                const snapCls = meta.isRejected
                    ? "REJECTED"
                    : (meta.isUnverified ? "UNVERIFIED" : (changed ? "UPDATED_METRICS" : "DUPLICATE_HISTORY"));

                const snapInserted = await insertMeasurementSnapshotInD1(env, targetDedupKey, meta, snapCls, nowIso);
                if (!snapInserted) {
                    snapshotsDeduped += 1;
                    duplicateCount += 1;
                    if (meta.cUrl || meta.postId) {
                        await env.DB.prepare(`
                            UPDATE market_post_registry
                            SET canonical_url = COALESCE(?, canonical_url),
                                post_id = COALESCE(?, post_id),
                                post_pk = COALESCE(?, post_pk),
                                content_hash = COALESCE(?, content_hash)
                            WHERE dedup_key = ?
                        `).bind(meta.cUrl, meta.postId, meta.postPk, meta.contentHash, targetDedupKey).run();
                    }
                    continue;
                }

                snapshotsCreated += 1;
                const mergedMetrics = { ...prevMetrics, ...meta.metrics };
                const nextSnap = Number(row.snapshot_count || 1) + 1;
                const effSeenAt = meta.measuredAt || nowIso;
                await env.DB.prepare(`
                    UPDATE market_post_registry
                    SET canonical_url = COALESCE(?, canonical_url),
                        post_id = COALESCE(?, post_id),
                        post_pk = COALESCE(?, post_pk),
                        content_hash = COALESCE(?, content_hash),
                        last_run_id = COALESCE(?, last_run_id),
                        last_seen_at = ?,
                        snapshot_count = ?,
                        latest_views_display = COALESCE(?, latest_views_display),
                        latest_views_precision = COALESCE(?, latest_views_precision),
                        latest_views_source = COALESCE(?, latest_views_source),
                        latest_views_measured_at = COALESCE(?, latest_views_measured_at),
                        latest_views_evidence_url = COALESCE(?, latest_views_evidence_url),
                        latest_metrics_json = ?
                    WHERE dedup_key = ?
                `).bind(
                    meta.cUrl,
                    meta.postId,
                    meta.postPk,
                    meta.contentHash,
                    meta.runId,
                    effSeenAt,
                    nextSnap,
                    meta.viewsDisplay,
                    meta.viewsPrecision,
                    meta.viewsSource,
                    meta.measuredAt,
                    meta.viewsEvidenceUrl,
                    JSON.stringify(mergedMetrics),
                    targetDedupKey
                ).run();

                if (changed) updatedCount += 1;
                else duplicateCount += 1;
            } catch (err) {
                const re = new Error(`D1_REGISTRY_UNAVAILABLE: ${err?.message || "record failed"}`);
                re.isRegistryError = true;
                throw re;
            }
        }
    }

    return {
        new_count: newCount,
        new_qualified_count: newQualifiedCount,
        duplicate_count: duplicateCount,
        duplicate_history_count: duplicateCount,
        updated_count: updatedCount,
        updated_metrics_count: updatedCount,
        missing_views_count: missingViewsCount,
        unverified_count: unverifiedCount,
        rejected_count: rejectedCount,
        snapshots_created: snapshotsCreated,
        snapshots_deduped: snapshotsDeduped,
        total_processed: items.length,
    };
}

function pickFirstFiniteInt(...vals) {
    for (const v of vals) {
        if (v !== null && v !== undefined && v !== "") {
            const n = Number(v);
            if (Number.isFinite(n)) return Math.trunc(n);
        }
    }
    return null;
}

function extractCrossRunDedupStats(obj) {
    if (!obj || typeof obj !== "object") return {};
    const c = (obj.counters && typeof obj.counters === "object") ? obj.counters : {};
    const s1 = obj.sections?.["1_screening"] || obj.section_1_screening || obj.screening || {};
    const ds = (obj.dedup_stats && typeof obj.dedup_stats === "object") ? obj.dedup_stats : obj;
    const newCount = pickFirstFiniteInt(
        ds.new_count,
        c.new_count,
        obj.new_count,
        s1.new_count,
        obj.new_items,
        obj.records_inserted
    );
    const newQualifiedCount = pickFirstFiniteInt(
        ds.new_qualified_count,
        c.new_qualified_count,
        obj.new_qualified_count,
        s1.new_qualified_count,
        obj.qualified_count,
        s1.qualified_count
    );
    const duplicateCount = pickFirstFiniteInt(
        ds.duplicate_history_count,
        ds.duplicate_count,
        c.duplicate_history_count,
        c.duplicate_count,
        obj.duplicate_history_count,
        obj.duplicate_count,
        obj.historical_duplicates,
        s1.duplicate_history_count,
        s1.duplicate_count,
        obj.records_deduped,
        obj.deduped
    );
    const updatedCount = pickFirstFiniteInt(
        ds.updated_metrics_count,
        ds.updated_count,
        c.updated_metrics_count,
        c.updated_count,
        obj.updated_metrics_count,
        obj.updated_count,
        obj.engagement_updated_count,
        obj.growth_count,
        s1.updated_metrics_count,
        s1.updated_count
    );
    const missingViewsCount = pickFirstFiniteInt(
        ds.missing_views_count,
        c.missing_views_count,
        obj.missing_views_count,
        obj.views_missing_count,
        s1.missing_views_count
    );
    const unverifiedCount = pickFirstFiniteInt(
        ds.unverified_count,
        c.unverified_count,
        obj.unverified_count,
        s1.unverified_count
    );
    const similarContentCount = pickFirstFiniteInt(
        ds.similar_content_count,
        c.similar_content_count,
        obj.similar_content_count,
        s1.similar_content_count
    );
    return {
        newCount,
        newQualifiedCount,
        duplicateCount,
        updatedCount,
        missingViewsCount,
        unverifiedCount,
        similarContentCount,
        hasAny: [newCount, newQualifiedCount, duplicateCount, updatedCount, missingViewsCount, unverifiedCount, similarContentCount].some(v => v !== null),
    };
}

function computeCrawlRequestProgress(reqRow) {
    let summary = {};
    try {
        summary = typeof reqRow?.result_summary_json === "string"
            ? JSON.parse(reqRow.result_summary_json || "{}")
            : (reqRow?.result_summary || {});
    } catch {}
    const c = (summary.counters && typeof summary.counters === "object") ? summary.counters : {};
    const s1 = summary?.sections?.["1_screening"] || summary?.screening || {};
    const reqCount = Math.max(1, Number.parseInt(reqRow?.requested_count, 10) || Number.parseInt(c.requested_count, 10) || 50);
    const dedup = extractCrossRunDedupStats(summary);
    const scannedCount = pickFirstFiniteInt(c.scanned_count, summary.scanned_count, summary.scanned_posts, s1.scanned_count, s1.scanned_posts);
    const uniqueCandidateCount = pickFirstFiniteInt(c.unique_candidate_count, summary.unique_candidate_count, s1.unique_candidate_count);
    const explicitQualified = dedup.newQualifiedCount ?? pickFirstFiniteInt(summary.qualified_posts, summary.record_count);
    const qualifiedCount = explicitQualified !== null ? explicitQualified : dedup.newCount;
    const rejectedCount = pickFirstFiniteInt(c.rejected_count, summary.rejected_count, summary.rejected_posts, s1.rejected_count);
    const stopReason = c.stop_reason || summary.stop_reason || s1.stop_reason || null;
    const explicitAchieved =
        typeof c.target_achieved === "boolean"
            ? c.target_achieved
            : (typeof summary.target_achieved === "boolean" ? summary.target_achieved : null);
    const targetReached = explicitAchieved !== null ? explicitAchieved : (qualifiedCount !== null ? qualifiedCount >= reqCount : null);
    return {
        requested_count: reqCount,
        scanned_count: scannedCount,
        unique_candidate_count: uniqueCandidateCount,
        qualified_count: qualifiedCount,
        new_qualified_count: qualifiedCount,
        new_count: dedup.newCount,
        duplicate_history_count: dedup.duplicateCount,
        duplicate_count: dedup.duplicateCount,
        updated_metrics_count: dedup.updatedCount,
        updated_count: dedup.updatedCount,
        missing_views_count: dedup.missingViewsCount,
        unverified_count: dedup.unverifiedCount,
        similar_content_count: dedup.similarContentCount,
        rejected_count: rejectedCount,
        target_achieved: targetReached,
        target_reached: targetReached,
        stop_reason: stopReason,
    };
}

function isPureMetricEchoLine(line) {
    const clean = String(line || "").trim();
    if (!clean) return true;
    return /^(?:qu[eé]t|đạt\s*chuẩn|bị\s*loại|loại|tổng\s*bản\s*ghi|bài\s*mới|trùng\s*lịch\s*sử|thiếu\s*views) /i.test(clean);
}

function splitSummaryClauses(rawText, preserveLines = false) {
    const splitter = preserveLines ? /(?:\r?\n+)/ : /(?:\r?\n|[•\|;]|(?<=\.)\s+)/;
    return String(rawText || "")
        .split(splitter)
        .map(s => trimBullet(s, 220))
        .filter(s => s && !isPureMetricEchoLine(s));
}

function parseTelegramSummaryBlocks(rawSummary) {
    const out = {
        raw: String(rawSummary || "").trim(),
        quality: [],
        market: [],
        lifecycle: [],
        affiliate: [],
        growth: [],
        actions: [],
        matched: false,
    };
    if (!out.raw) return out;
    const lines = out.raw.split(/\r?\n+/).map(l => l.trim()).filter(Boolean);
    let activeBucket = null;

    const matchHeader = (line) => {
        const up = line.replace(/^[•\-*\s]+/, "").toUpperCase();
        if (/^(?:📊\s*)?CHẤT LƯỢNG CRAWL\b/.test(up) || /^(?:📌\s*)?TRẠNG THÁI\b/.test(up) || /^(?:⚠️\s*)?(?:CHẤT LƯỢNG DỮ LIỆU|GIỚI HẠN)\b/.test(up)) {
            return "quality";
        }
        if (/^(?:🔥\s*)?(?:TREND THỊ TRƯỜNG|TOÀN THỊ TRƯỜNG|XU HƯỚNG)/.test(up)) {
            return "market";
        }
        if (/^(?:🆕\s*)?(?:TREND MỚI|VÒNG ĐỜI|MỚI NỔI)\b/.test(up)) {
            return "lifecycle";
        }
        if (/^(?:💰\s*|💡\s*)?(?:TÍN HIỆU\s*AFFILIATE|TÍN HIỆU|AFFILIATE)\b/.test(up)) {
            return "affiliate";
        }
        if (/^(?:📈\s*)?BIẾN ĐỘNG SO VỚI LƯỢT TRƯỚC\b/.test(up) || /^(?:📈\s*)?BIẾN ĐỘNG\b/.test(up)) {
            return "growth";
        }
        if (/^(?:🎯\s*|✅\s*)?(?:HÀNH ĐỘNG ĐỀ XUẤT|ƯU TIÊN|HÀNH ĐỘNG)\b/.test(up)) {
            return "actions";
        }
        return null;
    };

    for (const line of lines) {
        if (/^[━─=]{4,}$/.test(line)) continue;
        const headerBucket = matchHeader(line);
        if (headerBucket) {
            activeBucket = headerBucket;
            out.matched = true;
            const colonIdx = line.indexOf(":");
            if (colonIdx !== -1 && colonIdx < 60) {
                const rest = line.slice(colonIdx + 1).trim();
                if (rest) {
                    const isLegacyInline = !/^(?:📊|🔥|🆕|💰|📈|🎯)/.test(line);
                    out[activeBucket].push(...splitSummaryClauses(rest, !isLegacyInline));
                }
            }
            continue;
        }
        if (activeBucket) {
            const clean = trimBullet(line, 220);
            if (clean && !isPureMetricEchoLine(clean)) {
                out[activeBucket].push(clean);
            }
        }
    }
    return out;
}

function pushUniqueBullet(list, rawLine, seenSet, maxLen = 220, iconPrefix = "•") {
    const clean = trimBullet(rawLine, maxLen);
    if (!clean) return false;
    const normKey = clean.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
    if (!normKey || seenSet.has(normKey)) return false;
    seenSet.add(normKey);
    const hasLeadingIcon = /^[📌📊🔍🎯🆕♻️📈⏳🔗⚠️❗ℹ️🔥🚀💎💰💡📝✅💬❤️👁️•]/u.test(clean);
    list.push(hasLeadingIcon ? (clean.startsWith("•") ? clean : `• ${clean}`) : `${iconPrefix} ${clean}`);
    return true;
}

function formatTrendLifecycleTag(rawState) {
    const s = String(rawState || "").trim().toLowerCase();
    if (!s) return "";
    const map = {
        new: "[Mới]",
        continuing: "[Tiếp diễn]",
        growing: "[Tăng trưởng]",
        declining: "[Suy giảm]",
        insufficient_evidence: "[Chưa đủ bằng chứng]",
    };
    return map[s] || `[${s}]`;
}

function formatViewsLabel(tv) {
    if (!tv || typeof tv !== "object") return "— views";
    if (tv.views_display && String(tv.views_display).trim()) {
        const approxTag = String(tv.views_precision || "").toLowerCase() === "display_approx" ? " (≈ hiển thị)" : "";
        return `${String(tv.views_display).trim()}${approxTag} views`;
    }
    if (tv.views !== null && tv.views !== undefined && Number.isFinite(Number(tv.views))) {
        return `${Number(tv.views).toLocaleString("vi-VN")} views`;
    }
    return "chưa xác minh views";
}

function fitTelegramSummarySections(header, sections, maxChars = 2500) {
    const render = () =>
        [
            header,
            ...sections.map((s) => `${s.title}\n${s.bullets.join("\n")}`),
        ].join("\n\n");

    let text = render();
    if (text.length <= maxChars) return text;

    // First compact overly long single bullets before dropping items
    for (const s of sections) {
        s.bullets = s.bullets.map((b) => (b.length > 165 ? `${b.slice(0, 164).trimEnd()}…` : b));
    }
    text = render();
    if (text.length <= maxChars) return text;

    let guard = 0;
    while (text.length > maxChars && guard < 100) {
        guard += 1;
        let maxIdx = -1;
        let maxLen = 1;
        for (let i = 0; i < sections.length; i++) {
            if (sections[i].bullets.length > maxLen) {
                maxLen = sections[i].bullets.length;
                maxIdx = i;
            }
        }
        if (maxIdx === -1) break;
        sections[maxIdx].bullets.pop();
        text = render();
    }

    if (text.length > maxChars) {
        for (let pass = 0; pass < 4 && text.length > maxChars; pass++) {
            const limit = Math.max(48, 120 - pass * 25);
            for (const s of sections) {
                s.bullets = s.bullets.map((b) => (b.length > limit ? `${b.slice(0, limit - 1).trimEnd()}…` : b));
            }
            text = render();
        }
    }

    return text.length <= maxChars ? text : text.slice(0, maxChars);
}

function formatStructuredReportSummary(d, fileName, isCorr, metaInput = null, opts = {}) {
    const meta = metaInput || extractMuseReportStats(d) || {};
    const effStatus = meta.effStatus || "completed";
    const statusBadge = getReportStatusBadge(effStatus, isCorr, meta.isV3, meta.isV32);
    const parsedSummary = parseTelegramSummaryBlocks(d?.telegram_summary || opts.fallbackSummary || "");
    const dedup = opts.dedupStats ? extractCrossRunDedupStats({ ...d, dedup_stats: opts.dedupStats }) : extractCrossRunDedupStats(d);
    const req = opts.crawlReq || null;
    const seen = new Set();

    // 1. 📊 Chất lượng crawl
    const qualityBullets = [];
    const srcLabel = fileName || d?.run_id || opts.sourceLabel || "report";
    const statusTags = [];
    if (meta.rawRunStatus) statusTags.push(meta.rawRunStatus);
    if (meta.rawTechStatus && meta.rawTechStatus !== meta.rawRunStatus) statusTags.push(meta.rawTechStatus);
    if (meta.rawDqStatus) statusTags.push(meta.rawDqStatus);
    pushUniqueBullet(
        qualityBullets,
        `📌 Nguồn: ${trimBullet(srcLabel, 80)}${isCorr ? " (Corrected V2)" : ""}${statusTags.length ? ` [${statusTags.join(" | ")}]` : ""}`,
        seen,
        180
    );
    if (opts.appStatusTag) {
        pushUniqueBullet(qualityBullets, opts.appStatusTag, seen, 130);
    }

    const targetN = pickFirstFiniteInt(req?.requested_count, meta.requestedCount);
    const effQualified = dedup.newQualifiedCount ?? meta.newQualifiedCount ?? meta.qualified ?? dedup.newCount ?? pickFirstFiniteInt(d?.record_count);
    const effNewTotal = dedup.newCount ?? meta.newCount;
    const stopReason = meta.stopReason || null;
    if (targetN !== null && effQualified !== null) {
        const reached = typeof meta.targetAchieved === "boolean" ? meta.targetAchieved : effQualified >= targetN;
        const reachedLabel = reached ? "Đạt mục tiêu" : "Chưa đạt mục tiêu";
        const stopPart = stopReason ? ` • stop: ${stopReason}` : "";
        pushUniqueBullet(qualityBullets, `🎯 Tiến độ đạt chuẩn: ${effQualified}/${targetN} (${reachedLabel}${stopPart})`, seen, 180);
    } else if (stopReason) {
        pushUniqueBullet(qualityBullets, `🎯 Trạng thái dừng: ${stopReason}`, seen, 140);
    }

    if (meta.isV3 || dedup.hasAny || meta.scanned !== null || meta.uniqueCandidates !== null) {
        const scanParts = [];
        if (meta.scanned !== null) scanParts.push(`Quét ${meta.scanned}`);
        if (meta.uniqueCandidates !== null) scanParts.push(`Duy nhất ${meta.uniqueCandidates}`);
        if (meta.rejected !== null) scanParts.push(`Loại ${meta.rejected}`);
        if (meta.crossVerified !== null) scanParts.push(`Web check ${meta.crossVerified}`);
        if (scanParts.length) {
            pushUniqueBullet(qualityBullets, `🔍 ${scanParts.join(" | ")}`, seen, 180);
        }

        const dedupParts = [];
        if (effNewTotal !== null && (targetN === null || effNewTotal !== effQualified)) {
            dedupParts.push(`Bài mới: ${effNewTotal}`);
        } else if (effQualified !== null && targetN === null) {
            dedupParts.push(`Mới đạt chuẩn: ${effQualified}`);
        }
        const effDup = dedup.duplicateCount ?? meta.duplicateHistoryCount;
        const effUpd = dedup.updatedCount ?? meta.updatedMetricsCount;
        if (effDup !== null) dedupParts.push(`Bài trùng lịch sử: ${effDup}`);
        if (effUpd !== null) dedupParts.push(`Cập nhật tương tác: ${effUpd}`);
        if (dedupParts.length) {
            pushUniqueBullet(qualityBullets, `🆕 ${dedupParts.join(" | ")}`, seen, 180);
        }

        const unvParts = [];
        const effMissingViews = dedup.missingViewsCount ?? meta.missingViewsCount;
        const effUnverif = dedup.unverifiedCount ?? meta.unverifiedExplicit;
        if (effUnverif !== null) unvParts.push(`Chưa xác minh: ${effUnverif}`);
        if (effMissingViews !== null) unvParts.push(`Bài thiếu views: ${effMissingViews}`);
        if (meta.similarContentCount !== null && meta.similarContentCount > 0) {
            unvParts.push(`Nội dung tương tự: ${meta.similarContentCount}`);
        }
        if (meta.countersReconciled === false) {
            unvParts.push("⚠️ Lệch đối soát số liệu");
        }
        if (unvParts.length) {
            pushUniqueBullet(qualityBullets, `⏳ ${unvParts.join(" | ")}`, seen, 180);
        }
    } else if (meta.totals || Object.keys(meta.platformResults || {}).length) {
        const rec = meta.totals?.records ?? "N/A";
        const usable = meta.totals?.business_usable_records ?? "N/A";
        const needsVerif = meta.totals?.needs_verification_records ?? "N/A";
        pushUniqueBullet(qualityBullets, `🔍 Tổng ${rec} | Usable ${usable} | Cần xác minh ${needsVerif}`, seen, 160);
    } else if (opts.hbCounts) {
        const dq = d?.data_quality || {};
        pushUniqueBullet(qualityBullets, `🔍 Kho local: ${opts.hbCounts.total_items ?? dq.total_items ?? 0} bản ghi | Usable ${dq.usable_items ?? "N/A"}`, seen, 160);
    } else if (d?.record_count !== undefined && d?.record_count !== null) {
        pushUniqueBullet(qualityBullets, `🔍 Tổng bản ghi: ${d.record_count}`, seen, 140);
    }

    if (effStatus === "source_error" || effStatus === "insufficient_data" || effStatus === "failed") {
        const errNote = trimBullet(d?.error_message || req?.error_message || meta.s1?.sources || "Nguồn trả lỗi hoặc thiếu dữ liệu", 160);
        pushUniqueBullet(qualityBullets, `❗ Giới hạn nguồn: ${errNote}`, seen, 180);
    }
    if (meta.s1?.rejected_reasons && typeof meta.s1.rejected_reasons === "object") {
        const rsnList = Object.entries(meta.s1.rejected_reasons).map(([k, v]) => `${k} (${v})`);
        if (rsnList.length) pushUniqueBullet(qualityBullets, `⚠️ Lý do loại: ${rsnList.join(", ")}`, seen, 180);
    }
    if (meta.verifDetail) {
        pushUniqueBullet(qualityBullets, `ℹ️ Xác minh: ${meta.verifDetail}`, seen, 180);
    }
    for (const seg of parsedSummary.quality) {
        pushUniqueBullet(qualityBullets, seg, seen, 200);
    }
    for (const lim of (meta.dataLimitations || []).slice(0, 2)) {
        pushUniqueBullet(qualityBullets, `⚠️ ${lim}`, seen, 180);
    }
    if (!qualityBullets.length) {
        qualityBullets.push("• Chưa có số liệu chất lượng crawl từ lượt này.");
    }

    // 2. 🔥 Trend thị trường
    const marketBullets = [];
    if (effStatus === "completed" || effStatus === "partial" || !meta.rawRunStatus) {
        if (meta.isV3 && (meta.tiers.very_hot || meta.tiers.hot || meta.tiers.emerging || meta.tiers.watchlist)) {
            pushUniqueBullet(
                marketBullets,
                `📊 Phân tầng: Very Hot ${meta.tiers.very_hot} • Hot ${meta.tiers.hot} • Emerging ${meta.tiers.emerging} • Watchlist ${meta.tiers.watchlist}`,
                seen,
                160
            );
        }
        for (const seg of parsedSummary.market) {
            pushUniqueBullet(marketBullets, seg, seen, 220);
        }
        if (meta.topViral.length) {
            const tv = meta.topViral[0];
            const viewsLbl = formatViewsLabel(tv);
            pushUniqueBullet(
                marketBullets,
                `🚀 Viral tiêu biểu: ${trimBullet(tv.post || tv.title, 80)} (${viewsLbl}, ${tv.likes ?? "—"} likes, ${tv.comments ?? "—"} cmt)`,
                seen,
                200
            );
        }
        for (const t of meta.trends || []) {
            if (marketBullets.length >= 5) break;
            const title = trimBullet(t?.theme || t?.title || t?.topic || t, 100);
            const why = trimBullet(t?.why_hot || "", 80);
            if (title) pushUniqueBullet(marketBullets, `${title}${why ? ` — ${why}` : ""}`, seen, 200);
        }
        if (!marketBullets.length && !parsedSummary.matched && parsedSummary.raw) {
            for (const seg of splitSummaryClauses(parsedSummary.raw, false).slice(0, 3)) {
                pushUniqueBullet(marketBullets, seg, seen, 180);
            }
        }
    }
    if (!marketBullets.length) {
        marketBullets.push("• Chưa ghi nhận xu hướng thị trường đủ chuẩn trong lượt này.");
    }

    // 3. 🆕 Trend mới & vòng đời
    const lifecycleBullets = [];
    if (effStatus === "completed" || effStatus === "partial" || !meta.rawRunStatus) {
        for (const seg of parsedSummary.lifecycle) {
            pushUniqueBullet(lifecycleBullets, seg, seen, 220);
        }
        for (const t of meta.trends || []) {
            const lcTag = formatTrendLifecycleTag(t?.lifecycle_state || t?.lifecycle || t?.state);
            const tName = trimBullet(t?.theme || t?.title || t?.topic || "", 110);
            if (lcTag && tName) {
                pushUniqueBullet(lifecycleBullets, `${lcTag} ${tName}`, seen, 190);
            }
        }
        for (const e of (meta.emerging || [])) {
            const emText = trimBullet(e?.topic || e?.title || e, 120);
            if (emText) pushUniqueBullet(lifecycleBullets, `[Mới nổi] ${emText}`, seen, 180);
        }
    }
    if (!lifecycleBullets.length) {
        lifecycleBullets.push("• Chưa đủ dữ liệu đa kỳ để phân tách vòng đời trend mới.");
    }

    // 4. 💰 Tín hiệu Affiliate
    const affBullets = [];
    if (effStatus === "completed" || effStatus === "partial" || !meta.rawRunStatus) {
        for (const seg of parsedSummary.affiliate) {
            pushUniqueBullet(affBullets, seg, seen, 220);
        }
        const cleanAff = filterCleanProductItems(meta.aff);
        for (const a of cleanAff) {
            const name = trimBullet(a?.product || a?.brand_or_topic || a?.title || a, 95);
            const prio = a?.priority ? ` [${trimBullet(a.priority, 30)}]` : "";
            const ev = trimBullet(a?.evidence || a?.evidence_summary || "", 85);
            if (name) pushUniqueBullet(affBullets, `${name}${prio}${ev ? ` — ${ev}` : ""}`, seen, 200);
        }
        for (const s of (meta.signals || [])) {
            const sg = trimBullet(s?.signal || s?.topic || s, 120);
            if (sg) pushUniqueBullet(affBullets, `💡 ${sg}`, seen, 180);
        }
        if (!affBullets.length && meta.needs.length) {
            for (const n of meta.needs.slice(0, 2)) {
                const nt = trimBullet(n?.title || n, 120);
                if (nt) pushUniqueBullet(affBullets, `Nhu cầu: ${nt}`, seen, 180);
            }
        }
    }
    if (!affBullets.length) {
        affBullets.push("• Chưa có tín hiệu Affiliate đủ bằng chứng ở lượt này.");
    }

    // 5. 📈 Biến động so với lượt trước
    const growthBullets = [];
    if (effStatus === "completed" || effStatus === "partial" || !meta.rawRunStatus) {
        for (const seg of parsedSummary.growth) {
            pushUniqueBullet(growthBullets, seg, seen, 220);
        }
        for (const g of (meta.growthTracking || [])) {
            if (typeof g === "string") {
                pushUniqueBullet(growthBullets, g, seen, 200);
                continue;
            }
            const label = trimBullet(g?.post || g?.author_handle || g?.title || g?.topic || g?.post_id || "", 70);
            const deltaText = trimBullet(g?.change_summary || g?.delta_summary || g?.note || "", 120);
            if (label || deltaText) {
                pushUniqueBullet(growthBullets, `${label}${deltaText ? `: ${deltaText}` : ""}`, seen, 200);
            }
        }
    }
    if (!growthBullets.length) {
        growthBullets.push("• Chưa ghi nhận bài cũ biến động đáng kể so với lượt trước.");
    }

    // 6. 🎯 Hành động đề xuất (tối đa 3 hành động)
    const actionBullets = [];
    if (effStatus === "source_error" || effStatus === "insufficient_data" || effStatus === "failed") {
        pushUniqueBullet(actionBullets, "Kiểm tra lại nguồn dữ liệu và chạy lại lượt crawl trước khi đánh giá trend.", seen, 180);
    } else if (effStatus === "no_qualifying_posts") {
        pushUniqueBullet(actionBullets, "Giữ lịch theo dõi lượt tiếp theo; không sản xuất nội dung theo tin chưa kiểm chứng.", seen, 180);
    } else {
        for (const seg of parsedSummary.actions) {
            if (actionBullets.length >= 3) break;
            pushUniqueBullet(actionBullets, seg, seen, 200);
        }
        for (const act of (meta.actions || [])) {
            if (actionBullets.length >= 3) break;
            const txt = trimBullet(act?.action || act?.title || act, 170);
            if (txt) pushUniqueBullet(actionBullets, txt, seen, 200);
        }
        if (actionBullets.length < 3 && Array.isArray(meta.contentSuggestions?.threads_facebook)) {
            for (const p of meta.contentSuggestions.threads_facebook) {
                if (actionBullets.length >= 3) break;
                pushUniqueBullet(actionBullets, `Bài viết: ${p}`, seen, 170);
            }
        }
        if (actionBullets.length < 3 && Array.isArray(meta.contentSuggestions?.text_posts)) {
            for (const p of meta.contentSuggestions.text_posts) {
                if (actionBullets.length >= 3) break;
                pushUniqueBullet(actionBullets, `Nội dung: ${p}`, seen, 170);
            }
        }
    }
    if (!actionBullets.length) {
        actionBullets.push("• Tiếp tục theo dõi App_Video (Auto Sync ngầm) và kiểm tra chi tiết ở báo cáo đầy đủ.");
    }

    return fitTelegramSummarySections(
        `${statusBadge}\n━━━━━━━━━━━━━━━━━━━━`,
        [
            { title: "📊 Chất lượng crawl", bullets: qualityBullets },
            { title: "🔥 Trend thị trường", bullets: marketBullets },
            { title: "🆕 Trend mới & vòng đời", bullets: lifecycleBullets },
            { title: "💰 Tín hiệu Affiliate", bullets: affBullets },
            { title: "📈 Biến động so với lượt trước", bullets: growthBullets },
            { title: "🎯 Hành động đề xuất", bullets: actionBullets.slice(0, 3) },
        ],
        2500
    );
}

function buildMuseTelegramNotificationText(d, fileName, isCorr) {
    return formatStructuredReportSummary(d, fileName, isCorr);
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

function sleepMs(ms) {
    const delay = Math.max(0, Number(ms) || 0);
    if (delay <= 0) return Promise.resolve();
    return new Promise(resolve => setTimeout(resolve, delay));
}

function normalizeNotifRunId(rawId) {
    return String(rawId || "report").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 96);
}

function resolveCanonicalNotificationKey({ requestId = null, runId = null, isCorrected = false, status = null, fileName = null }) {
    if (runId) {
        const base = normalizeNotifRunId(runId);
        if (isCorrected && !base.toLowerCase().endsWith("_corrected")) {
            return `${base}_corrected`.slice(0, 120);
        }
        return base;
    }
    if (requestId) {
        const reqNorm = normalizeNotifRunId(requestId);
        return status ? `crw_${reqNorm}_${status}`.slice(0, 120) : `crw_${reqNorm}`.slice(0, 120);
    }
    const fb = normalizeNotifRunId(fileName || "report");
    return (isCorrected && !fb.toLowerCase().endsWith("_corrected")) ? `${fb}_corrected`.slice(0, 120) : fb;
}

function extractReportCrawlMeta(parsed, candidate) {
    // Strictly order crawl runs by crawl execution timestamp from payload — NEVER MAX with Drive modifiedTime
    const payloadCandidates = [
        parsed?.crawled_at,
        parsed?.crawl_time,
        parsed?.ended_at,
        parsed?.source_run?.ended_at,
        parsed?.collected_at,
        parsed?.started_at,
        parsed?.source_run?.started_at,
        parsed?.generated_at,
    ];
    let crawlTs = 0;
    let hasPayloadCrawlTs = false;
    for (const rawTs of payloadCandidates) {
        if (!rawTs) continue;
        const parsedMs = Date.parse(String(rawTs));
        if (Number.isFinite(parsedMs) && parsedMs > 0) {
            crawlTs = parsedMs;
            hasPayloadCrawlTs = true;
            break;
        }
    }
    const revisionTs = Date.parse(candidate?.modifiedTime || candidate?.createdTime || "") || 0;
    if (!hasPayloadCrawlTs) {
        crawlTs = Date.parse(candidate?.createdTime || candidate?.modifiedTime || "") || 0;
    }
    return { crawlTs, hasPayloadCrawlTs, revisionTs };
}

function isInvalidReportPayload(fileName, parsed) {
    if (!parsed || typeof parsed !== "object") return true;
    return Boolean(
        isNonReportDocName(fileName) ||
        parsed.contract ||
        parsed.audit ||
        parsed.strategy ||
        parsed.engagement_verification_map ||
        (Array.isArray(parsed.sources) && !parsed.sections && !parsed.platform_results)
    );
}

function rankParsedReportCandidates(parsedCandidates) {
    if (!parsedCandidates.length) return [];
    const fileToGroup = new Map();
    for (const c of parsedCandidates) {
        if (c.file?.name) fileToGroup.set(String(c.file.name).toLowerCase(), c.groupKey);
    }
    for (const c of parsedCandidates) {
        const corrTarget = String(c.data?.corrects_report || "").toLowerCase();
        if (corrTarget && fileToGroup.has(corrTarget)) {
            c.groupKey = fileToGroup.get(corrTarget);
        }
    }

    // Inter-run order uses crawl execution time ONLY; file revision time (modifiedTime) breaks ties WITHIN same run.
    const groupCrawlTs = new Map();
    for (const c of parsedCandidates) {
        const cur = groupCrawlTs.get(c.groupKey);
        if (!cur) {
            groupCrawlTs.set(c.groupKey, { baseTs: c.crawlTs, fromOriginal: !c.isCorr, fromPayload: c.hasPayloadCrawlTs });
            continue;
        }
        if (c.hasPayloadCrawlTs && !cur.fromPayload) {
            groupCrawlTs.set(c.groupKey, { baseTs: c.crawlTs, fromOriginal: !c.isCorr, fromPayload: true });
        } else if (!c.isCorr && (!cur.fromOriginal || (c.crawlTs > 0 && c.crawlTs < cur.baseTs))) {
            groupCrawlTs.set(c.groupKey, { baseTs: c.crawlTs, fromOriginal: true, fromPayload: c.hasPayloadCrawlTs });
        } else if (cur.fromOriginal === !c.isCorr && c.crawlTs > 0 && (cur.baseTs === 0 || c.crawlTs < cur.baseTs)) {
            groupCrawlTs.set(c.groupKey, { baseTs: c.crawlTs, fromOriginal: cur.fromOriginal, fromPayload: cur.fromPayload || c.hasPayloadCrawlTs });
        }
    }

    parsedCandidates.sort((a, b) => {
        const aRunTs = groupCrawlTs.get(a.groupKey)?.baseTs || 0;
        const bRunTs = groupCrawlTs.get(b.groupKey)?.baseTs || 0;
        const gDiff = bRunTs - aRunTs;
        if (gDiff !== 0) return gDiff;
        if (b.isCorr !== a.isCorr) return (b.isCorr ? 1 : 0) - (a.isCorr ? 1 : 0);
        const rDiff = (b.revisionTs || 0) - (a.revisionTs || 0);
        if (rDiff !== 0) return rDiff;
        return (b.crawlTs || 0) - (a.crawlTs || 0);
    });
    return parsedCandidates.map(c => ({ ...c, groupCrawlTs: groupCrawlTs.get(c.groupKey)?.baseTs || c.crawlTs || 0 }));
}

async function listAllReportFileMetas(env) {
    const token = await getDriveToken(env);
    const reportsFolderId = await resolveReportsFolderId(token, env);
    const maxPages = Math.min(Math.max(Number(env?.MAX_DRIVE_LIST_PAGES) || DEFAULT_MAX_DRIVE_LIST_PAGES, 1), 100);
    const paged = await paginateDriveFolderChildren(token, reportsFolderId, maxPages, 100);
    const allReportFiles = [];
    for (const f of (paged.files || [])) {
        if (f.mimeType === "application/vnd.google-apps.folder" || isArchiveFolderName(f.name) || isArchiveRelPath(f.name)) continue;
        const low = String(f.name || "").toLowerCase();
        if (!low.endsWith(".json")) continue;
        if (isNonReportDocName(low)) continue;
        if (low.startsWith("trend_report") || low.startsWith("market_report") || low.includes("report")) {
            allReportFiles.push(f);
        }
    }
    return { token, reportsFolderId, allReportFiles, partialError: paged.partialError };
}

async function fetchAndParseDriveReports(token, reportsFolderId, files) {
    const parsedCandidates = [];
    let fetchErrorCount = 0;
    for (const candidate of files) {
        try {
            const rawRes = await fetch(
                `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(candidate.id)}?alt=media&supportsAllDrives=true`,
                { headers: { Authorization: `Bearer ${token}` } }
            );
            if (!rawRes.ok) {
                fetchErrorCount += 1;
                continue;
            }
            const parsed = await rawRes.json();
            if (isInvalidReportPayload(candidate.name, parsed)) continue;
            const isCorr = String(candidate.name || "").toLowerCase().includes("_corrected") || Boolean(parsed.corrects_report);
            const timing = extractReportCrawlMeta(parsed, candidate);
            parsedCandidates.push({
                reportsFolderId,
                file: candidate,
                data: parsed,
                isCorr,
                crawlTs: timing.crawlTs,
                hasPayloadCrawlTs: timing.hasPayloadCrawlTs,
                revisionTs: timing.revisionTs,
                effTs: timing.crawlTs,
                groupKey: String(parsed.request_id || parsed.run_id || getReportStem(candidate.name))
            });
        } catch {
            fetchErrorCount += 1;
        }
    }
    return { parsedCandidates, fetchErrorCount };
}

async function listValidMuseReportsOnDrive(env, maxFetch = 40) {
    const { token, reportsFolderId, allReportFiles, partialError } = await listAllReportFileMetas(env);
    if (!allReportFiles.length) return { reportsFolderId, ranked: [], partialError };

    // Sort newest candidates first for UI interactive lookup, keeping >=35 window so older modified reports still compete by crawlTs
    const sorted = allReportFiles.slice().sort(
        (a, b) => (Date.parse(b.modifiedTime || b.createdTime || 0) || 0) - (Date.parse(a.modifiedTime || a.createdTime || 0) || 0)
    );
    const subset = sorted.slice(0, Math.min(sorted.length, Math.max(20, Number(maxFetch) || 40)));
    const { parsedCandidates } = await fetchAndParseDriveReports(token, reportsFolderId, subset);
    return { reportsFolderId, ranked: rankParsedReportCandidates(parsedCandidates), partialError };
}

async function getLatestMuseReportFromDrive(env, targetRunOrRequestId = null) {
    const { reportsFolderId, ranked } = await listValidMuseReportsOnDrive(env);
    for (const item of ranked) {
        if (
            targetRunOrRequestId &&
            item.data?.request_id &&
            item.data.request_id !== targetRunOrRequestId &&
            item.data.run_id !== targetRunOrRequestId
        ) {
            continue;
        }
        return { reportsFolderId, file: item.file, data: item.data };
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
        pageSize: "5",
        supportsAllDrives: "true",
        includeItemsFromAllDrives: "true"
    });

    const res = await fetch(`https://www.googleapis.com/drive/v3/files?${params}`, { headers });
    if (!res.ok) throw new Error("Drive list failed");

    const list = await res.json();
    const file = (list.files || []).find(f => !isArchiveFolderName(f.name) && !isArchiveRelPath(f.name));
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

function isDefiniteUnsentTransientTelegram(httpStatus, data) {
    // Only Telegram explicit rate-limit (429) or runtime-guaranteed pre-send signal proves non-delivery.
    // Generic HTTP 408/503 MUST NOT be presumed unsent.
    const apiStatus = Number(httpStatus || 0);
    const errCode = Number(data?.error_code || 0);
    return apiStatus === 429 || errCode === 429 || data?.parameters?.definitely_not_sent === true;
}

function computeTelegramDeferredDelayMs(retryCount, data, env) {
    const baseMs = Math.max(0, Number(env?.TELEGRAM_RETRY_BASE_MS ?? 400));
    const maxDeferredMs = Math.max(baseMs, Number(env?.TELEGRAM_MAX_DEFERRED_BACKOFF_MS ?? 300000));
    const retryAfterSec = Number(data?.parameters?.retry_after || 0);
    if (Number.isFinite(retryAfterSec) && retryAfterSec > 0) {
        // Strictly honor Telegram retry_after when scheduling next_retry_at
        return Math.ceil(retryAfterSec * 1000);
    }
    return Math.min(Math.round(baseMs * Math.pow(2, Math.max(0, retryCount))), maxDeferredMs);
}

async function checkExistingNotificationKeys(env, keysToCheck) {
    const unique = Array.from(new Set(keysToCheck.filter(Boolean)));
    for (const k of unique) {
        let row = null;
        try {
            row = await env.DB.prepare(
                "SELECT run_id, status, created_at, retry_count, max_retries, next_retry_at, retryable FROM crawl_notifications WHERE run_id = ?"
            ).bind(k).first();
        } catch {
            row = await env.DB.prepare("SELECT run_id, status, created_at FROM crawl_notifications WHERE run_id = ?")
                .bind(k).first();
        }
        if (row) return { key: k, row };
    }
    return null;
}

async function finalizeNotificationState(env, notifKey, fields) {
    const {
        status,
        sentAt = null,
        retryCount = null,
        nextRetryAt = null,
        retryable = 0,
        lastError = null,
        retryPayloadJson = null,
    } = fields;
    try {
        await env.DB.prepare(`
            UPDATE crawl_notifications
            SET status = ?,
                sent_at = COALESCE(?, sent_at),
                retry_count = COALESCE(?, retry_count),
                next_retry_at = ?,
                retryable = ?,
                last_error = ?,
                retry_payload_json = COALESCE(?, retry_payload_json)
            WHERE run_id = ?
        `).bind(status, sentAt, retryCount, nextRetryAt, retryable ? 1 : 0, lastError, retryPayloadJson, notifKey).run();
    } catch {
        // Graceful compatibility if migration 0002 has not been applied yet
        if (status === "sent") {
            await env.DB.prepare("UPDATE crawl_notifications SET status = 'sent', sent_at = ? WHERE run_id = ?")
                .bind(sentAt || new Date().toISOString(), notifKey).run();
        } else {
            await env.DB.prepare("UPDATE crawl_notifications SET status = ? WHERE run_id = ?")
                .bind(status, notifKey).run();
        }
    }
}

async function deliverWithUncertaintyGuard(env, notifKey, recordCount, chatId, text, options = {}) {
    const aliasKeys = Array.isArray(options.aliasKeys) ? options.aliasKeys : [];
    const replyMarkup = options.replyMarkup || homeOnlyInlineKeyboard();
    const usePrimaryMessage = Boolean(options.usePrimaryMessage);
    const requestId = options.requestId ? String(options.requestId) : null;
    const bypassRequestClaim = Boolean(options.bypassRequestClaim);
    const bypassAliasPrecheck = Boolean(options.bypassAliasPrecheck);
    const maxRetries = Math.min(Math.max(Number(env?.TELEGRAM_MAX_RETRIES || 5), 1), 10);
    const immediateMaxWaitMs = Math.max(
        0,
        Number(env?.TELEGRAM_IMMEDIATE_RETRY_MAX_MS ?? env?.TELEGRAM_RETRY_MAX_WAIT_MS ?? 2000)
    );
    const claimNonce = genId("clm");
    const retryPayloadJson = JSON.stringify({
        chatId: String(chatId),
        text: String(text),
        usePrimaryMessage,
        recordCount,
        requestId,
        claim_nonce: claimNonce,
    });

    const nowIso = new Date().toISOString();

    // 1. Check aliases first (for distinct keys linking the same run/request) unless already reclaiming exact notifKey
    if (!bypassAliasPrecheck && aliasKeys.length > 0) {
        const aliasHit = await checkExistingNotificationKeys(env, aliasKeys);
        if (aliasHit && aliasHit.key !== notifKey) {
            const st = aliasHit.row.status;
            if (st === "sent" || st === "sending" || st === "delivery_uncertain" || st === "delivery_failed") {
                return { claimed: false, delivered: st === "sent", status: st, retries: 0 };
            }
        }
    }

    // 2. Check existing state of exact notifKey
    const existingExact = await checkExistingNotificationKeys(env, [notifKey]);
    let isReclaim = false;
    let currentRetryCount = 0;

    if (existingExact) {
        const row = existingExact.row;
        const st = row.status;
        if (st === "sent" || st === "sending" || st === "delivery_uncertain") {
            return { claimed: false, delivered: st === "sent", status: st, retries: 0 };
        }
        if (st === "delivery_failed") {
            const isRetryable = Number(row.retryable || 0) === 1;
            const rCount = Number(row.retry_count || 0);
            const rMax = Number(row.max_retries || maxRetries);
            if (!isRetryable || rCount >= rMax) {
                return { claimed: false, delivered: false, status: "delivery_failed", retryable: false, retries: 0 };
            }
            if (row.next_retry_at && String(row.next_retry_at) > nowIso) {
                return { claimed: false, delivered: false, status: "retry_scheduled", next_retry_at: row.next_retry_at, retries: 0 };
            }
        }
    }

    // 3. Atomic durable outbox + request-slot claim inside ONE D1 transaction:
    //    `notified_at` is NEVER written before or without a durable `crawl_notifications` record.
    if (!existingExact) {
        if (requestId && !bypassRequestClaim) {
            const reqIdStr = String(requestId);
            const runAtomicClaim = async (useCompat0001 = false) => {
                const insertStmt = useCompat0001
                    ? env.DB.prepare(`
                        INSERT OR IGNORE INTO crawl_notifications
                        (run_id, record_count, status, created_at)
                        SELECT ?, ?, 'sending', ?
                        WHERE EXISTS (SELECT 1 FROM crawl_requests WHERE id = ? AND notified_at IS NULL)
                    `).bind(notifKey, recordCount, nowIso, reqIdStr)
                    : env.DB.prepare(`
                        INSERT OR IGNORE INTO crawl_notifications
                        (run_id, record_count, status, created_at, retry_count, max_retries, next_retry_at, retryable, last_error, retry_payload_json)
                        SELECT ?, ?, 'sending', ?, 0, ?, NULL, 0, NULL, ?
                        WHERE EXISTS (SELECT 1 FROM crawl_requests WHERE id = ? AND notified_at IS NULL)
                    `).bind(notifKey, recordCount, nowIso, maxRetries, retryPayloadJson, reqIdStr);
                const updateReqStmt = useCompat0001
                    ? env.DB.prepare(`
                        UPDATE crawl_requests
                        SET notified_at = ?
                        WHERE id = ?
                          AND notified_at IS NULL
                          AND EXISTS (
                              SELECT 1 FROM crawl_notifications
                              WHERE run_id = ? AND status = 'sending' AND created_at = ?
                          )
                    `).bind(nowIso, reqIdStr, notifKey, nowIso)
                    : env.DB.prepare(`
                        UPDATE crawl_requests
                        SET notified_at = ?
                        WHERE id = ?
                          AND notified_at IS NULL
                          AND EXISTS (
                              SELECT 1 FROM crawl_notifications
                              WHERE run_id = ? AND status = 'sending' AND retry_payload_json = ?
                          )
                    `).bind(nowIso, reqIdStr, notifKey, retryPayloadJson);

                if (typeof env.DB.batch === "function") {
                    const batchRes = await env.DB.batch([insertStmt, updateReqStmt]);
                    return {
                        inserted: Number(batchRes?.[0]?.meta?.changes || 0),
                        slotUpdated: Number(batchRes?.[1]?.meta?.changes || 0),
                    };
                }
                // Non-batch fallback: insert durable notification FIRST, only claim slot after insert succeeds
                const insRes = await insertStmt.run();
                const insChanges = Number(insRes?.meta?.changes || 0);
                if (insChanges !== 1) return { inserted: 0, slotUpdated: 0 };
                try {
                    const updRes = await updateReqStmt.run();
                    const updChanges = Number(updRes?.meta?.changes || 0);
                    if (updChanges !== 1) {
                        await env.DB.prepare("DELETE FROM crawl_notifications WHERE run_id = ? AND status = 'sending'")
                            .bind(notifKey).run();
                    }
                    return { inserted: updChanges === 1 ? 1 : 0, slotUpdated: updChanges };
                } catch (slotErr) {
                    try {
                        await env.DB.prepare("DELETE FROM crawl_notifications WHERE run_id = ? AND status = 'sending'")
                            .bind(notifKey).run();
                    } catch {}
                    throw slotErr;
                }
            };

            let atomicRes;
            try {
                atomicRes = await runAtomicClaim(false);
            } catch (err) {
                if (/no such column/i.test(String(err?.message || ""))) {
                    atomicRes = await runAtomicClaim(true);
                } else {
                    throw err;
                }
            }
            if (atomicRes.inserted !== 1 || atomicRes.slotUpdated !== 1) {
                return { claimed: false, delivered: false, status: "duplicate", retries: 0 };
            }
        } else {
            let claim;
            try {
                claim = await env.DB.prepare(`
                    INSERT OR IGNORE INTO crawl_notifications
                    (run_id, record_count, status, created_at, retry_count, max_retries, next_retry_at, retryable, last_error, retry_payload_json)
                    VALUES (?, ?, 'sending', ?, 0, ?, NULL, 0, NULL, ?)
                `).bind(notifKey, recordCount, nowIso, maxRetries, retryPayloadJson).run();
            } catch (err) {
                if (!/no such column/i.test(String(err?.message || ""))) throw err;
                claim = await env.DB.prepare(`
                    INSERT OR IGNORE INTO crawl_notifications
                    (run_id, record_count, status, created_at)
                    VALUES (?, ?, 'sending', ?)
                `).bind(notifKey, recordCount, nowIso).run();
            }
            if (claim.meta.changes !== 1) {
                return { claimed: false, delivered: false, status: "duplicate", retries: 0 };
            }
        }
    } else {
        // Atomic reclaim: ONLY delivery_failed + retryable=1 + retry_count < max_retries + next_retry_at elapsed
        const reclaimNowIso = new Date().toISOString();
        let reclaim;
        try {
            reclaim = await env.DB.prepare(`
                UPDATE crawl_notifications
                SET status = 'sending',
                    created_at = ?,
                    retry_count = retry_count + 1,
                    next_retry_at = NULL,
                    retry_payload_json = COALESCE(?, retry_payload_json)
                WHERE run_id = ?
                  AND status = 'delivery_failed'
                  AND retryable = 1
                  AND retry_count < max_retries
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
            `).bind(reclaimNowIso, retryPayloadJson, notifKey, reclaimNowIso).run();
        } catch (err) {
            if (/no such column/i.test(String(err?.message || ""))) {
                return { claimed: false, delivered: false, status: "delivery_failed", retries: 0 };
            }
            throw err;
        }
        if (reclaim.meta.changes !== 1) {
            return { claimed: false, delivered: false, status: "reclaim_rejected", retries: 0 };
        }
        isReclaim = true;
        currentRetryCount = Number(existingExact.row.retry_count || 0) + 1;
    }

    const maxImmediateAttempts = Math.min(Math.max(Number(env?.TELEGRAM_MAX_ATTEMPTS || 3), 1), 5);
    let retriesInCall = 0;

    for (let attempt = 0; attempt < maxImmediateAttempts; attempt++) {
        try {
            const res = usePrimaryMessage
                ? await updatePrimaryProgressMessage(env, chatId, text, replyMarkup)
                : await sendTelegramMessage(env, chatId, text, replyMarkup);

            if (res.ok) {
                await finalizeNotificationState(env, notifKey, {
                    status: "sent",
                    sentAt: new Date().toISOString(),
                    retryCount: currentRetryCount,
                    nextRetryAt: null,
                    retryable: 0,
                    lastError: null,
                });
                return { claimed: true, delivered: true, status: "sent", retries: retriesInCall, reclaimed: isReclaim };
            }

            const apiStatus = Number(res.httpStatus || 0);
            if (isDefiniteUnsentTransientTelegram(apiStatus, res.data)) {
                const retryDelayMs = computeTelegramDeferredDelayMs(currentRetryCount, res.data, env);
                if (attempt < maxImmediateAttempts - 1 && retryDelayMs <= immediateMaxWaitMs && currentRetryCount < maxRetries) {
                    retriesInCall += 1;
                    currentRetryCount += 1;
                    await sleepMs(retryDelayMs);
                    continue;
                }
                const canScheduleReclaim = currentRetryCount < maxRetries;
                const nextRetryIso = canScheduleReclaim ? new Date(Date.now() + retryDelayMs).toISOString() : null;
                await finalizeNotificationState(env, notifKey, {
                    status: "delivery_failed",
                    retryCount: currentRetryCount,
                    nextRetryAt: nextRetryIso,
                    retryable: canScheduleReclaim ? 1 : 0,
                    lastError: `HTTP ${apiStatus}: definite unsent transient error`,
                    retryPayloadJson,
                });
                return {
                    claimed: true,
                    delivered: false,
                    status: "delivery_failed",
                    retryable: canScheduleReclaim,
                    next_retry_at: nextRetryIso,
                    retries: retriesInCall,
                    retryableExhausted: true,
                };
            }

            // Permanent 4xx (except 429/408 handled separately): permanent delivery_failed (not retryable).
            // 408, 500, 502, 503, 504: uncertain whether Telegram processed -> delivery_uncertain, NEVER retry.
            const isPermanent4xx = apiStatus >= 400 && apiStatus < 500 && apiStatus !== 408 && apiStatus !== 429;
            const nextState = isPermanent4xx ? "delivery_failed" : "delivery_uncertain";
            await finalizeNotificationState(env, notifKey, {
                status: nextState,
                retryCount: currentRetryCount,
                nextRetryAt: null,
                retryable: 0,
                lastError: `HTTP ${apiStatus}`,
            });
            return { claimed: true, delivered: false, status: nextState, retryable: false, retries: retriesInCall };
        } catch (err) {
            if (err && (err.notSent === true || err.code === "ECONNREFUSED")) {
                const retryDelayMs = computeTelegramDeferredDelayMs(currentRetryCount, null, env);
                if (attempt < maxImmediateAttempts - 1 && retryDelayMs <= immediateMaxWaitMs && currentRetryCount < maxRetries) {
                    retriesInCall += 1;
                    currentRetryCount += 1;
                    await sleepMs(retryDelayMs);
                    continue;
                }
                const canScheduleReclaim = currentRetryCount < maxRetries;
                const nextRetryIso = canScheduleReclaim ? new Date(Date.now() + retryDelayMs).toISOString() : null;
                await finalizeNotificationState(env, notifKey, {
                    status: "delivery_failed",
                    retryCount: currentRetryCount,
                    nextRetryAt: nextRetryIso,
                    retryable: canScheduleReclaim ? 1 : 0,
                    lastError: String(err.message || err.code || "Pre-send network failure").slice(0, 200),
                    retryPayloadJson,
                });
                return {
                    claimed: true,
                    delivered: false,
                    status: "delivery_failed",
                    retryable: canScheduleReclaim,
                    next_retry_at: nextRetryIso,
                    retries: retriesInCall,
                    retryableExhausted: true,
                };
            }
            // Ambiguous transport throw (timeout/reset) -> delivery_uncertain, NEVER retryable
            await finalizeNotificationState(env, notifKey, {
                status: "delivery_uncertain",
                retryCount: currentRetryCount,
                nextRetryAt: null,
                retryable: 0,
                lastError: String(err?.message || "Transport timeout").slice(0, 200),
            });
            return { claimed: true, delivered: false, status: "delivery_uncertain", retryable: false, retries: retriesInCall };
        }
    }
    return { claimed: true, delivered: false, status: "delivery_failed", retryable: false, retries: retriesInCall };
}

async function reclaimDueRetryNotifications(env, limit = 10) {
    const nowIso = new Date().toISOString();
    let dueRows = [];
    try {
        dueRows = (await env.DB.prepare(`
            SELECT run_id, record_count, retry_count, max_retries, next_retry_at, retry_payload_json
            FROM crawl_notifications
            WHERE status = 'delivery_failed'
              AND retryable = 1
              AND retry_count < max_retries
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY COALESCE(next_retry_at, created_at) ASC
            LIMIT ?
        `).bind(nowIso, limit).all()).results || [];
    } catch (err) {
        const msg = String(err?.message || "").toLowerCase();
        if (msg.includes("no such column") || msg.includes("no such table")) return;
        throw err;
    }
    for (const row of dueRows) {
        if (!row.retry_payload_json) continue;
        let p = null;
        try { p = JSON.parse(row.retry_payload_json); } catch {}
        if (!p || !p.chatId || !p.text) continue;
        const delivery = await deliverWithUncertaintyGuard(
            env,
            row.run_id,
            Number(row.record_count ?? p.recordCount ?? 0),
            p.chatId,
            p.text,
            {
                usePrimaryMessage: Boolean(p.usePrimaryMessage),
                requestId: p.requestId || null,
                bypassRequestClaim: true,
                bypassAliasPrecheck: true,
            }
        );
        if (delivery.retryableExhausted) break;
    }
}

function parseCompositeCursor(rawCursor) {
    if (!rawCursor || !String(rawCursor).includes("|")) return null;
    const sep = String(rawCursor).lastIndexOf("|");
    const rawTs = String(rawCursor).slice(0, sep);
    const id = String(rawCursor).slice(sep + 1);
    const tsMs = Date.parse(rawTs);
    if (!Number.isFinite(tsMs)) return null;
    return { rawTs, tsMs, id };
}

function compareCompositeItemToCursor(itemTsMs, itemId, parsedCursor) {
    if (!parsedCursor) return 1;
    if (itemTsMs !== parsedCursor.tsMs) return itemTsMs - parsedCursor.tsMs;
    const sId = String(itemId || "");
    if (sId === parsedCursor.id) return 0;
    return sId > parsedCursor.id ? 1 : -1;
}

function buildDirectD1CrawlNotificationText(req, summary) {
    const s = (summary && typeof summary === "object") ? summary : {};
    const platLabel = String(req?.platform || s.platform || "all").toUpperCase();
    const runStatus = s.run_status ? String(s.run_status).trim().toLowerCase() : (req?.status === "failed" ? "failed" : "completed");
    const techStatus = s.technical_status ? String(s.technical_status).trim().toLowerCase() : (req?.status === "failed" ? "failed" : "completed");
    const dqStatus = s.data_quality_status ? String(s.data_quality_status).trim() : null;
    const c = (s.counters && typeof s.counters === "object") ? s.counters : {};
    const screening = (s.sections?.["1_screening"] || s.screening || {});
    const scannedVal = pickFirstFiniteInt(c.scanned_count, s.scanned_count, s.scanned_posts, screening.scanned_count, screening.scanned_posts);
    const qualifiedVal = pickFirstFiniteInt(c.new_qualified_count, s.new_qualified_count, s.qualified_count, s.qualified_posts, screening.qualified_count, s.record_count);
    const rejectedVal = pickFirstFiniteInt(c.rejected_count, s.rejected_count, s.rejected_posts, screening.rejected_count);
    const syntheticReport = {
        ...s,
        run_id: s.run_id || req?.id || `crw_${platLabel.toLowerCase()}`,
        run_status: runStatus,
        technical_status: techStatus,
        data_quality_status: dqStatus,
        error_message: req?.error_message || s.error_message || null,
        sections: {
            ...(s.sections || {}),
            ...((scannedVal !== null || qualifiedVal !== null || rejectedVal !== null) ? {
                "1_screening": {
                    ...screening,
                    ...(scannedVal !== null ? { scanned_count: scannedVal } : {}),
                    ...(qualifiedVal !== null ? { qualified_count: qualifiedVal } : {}),
                    ...(rejectedVal !== null ? { rejected_count: rejectedVal } : {}),
                }
            } : {}),
        },
    };
    const isCorr = Boolean(s.is_corrected || s.corrects_report);
    return formatStructuredReportSummary(
        syntheticReport,
        `${platLabel} (${req?.id || s.run_id || "crawl"})`,
        isCorr,
        null,
        {
            crawlReq: req,
            dedupStats: s.dedup_stats || s,
            sourceLabel: req?.claimed_by ? `Muse (${req.claimed_by})` : "Muse D1",
        }
    );
}

async function notifyNewCrawl(env) {
    if (!env.TELEGRAM_OWNER_ID || !env.TELEGRAM_BOT_TOKEN_VIDEO) return;
    const cutoffMs = Date.parse("2026-11-26T12:25:00+07:00");
    const batchSize = Math.min(Math.max(Number(env?.NOTIFY_BATCH_SIZE) || DEFAULT_NOTIFY_BATCH_SIZE, 1), 25);
    const notifiedRequestIds = new Set();

    // 0. Atomically reclaim due retryable `delivery_failed` notifications whose next_retry_at has elapsed
    await reclaimDueRetryNotifications(env, batchSize);

    // 1. Batch + durable cursor backlog over Drive Reports (`01_Market_Data/Reports/`) — never misses >20 files
    try {
        const { token, reportsFolderId, allReportFiles, partialError } = await listAllReportFileMetas(env);
        if (allReportFiles.length > 0) {
            const rawCursor = await getRuntimeState(env, "notify_drive_cursor", null);
            const parsedCursor = parseCompositeCursor(rawCursor);

            const ascFiles = allReportFiles
                .map(f => ({
                    file: f,
                    mtimeStr: f.modifiedTime || f.createdTime || "1970-01-01T00:00:00Z",
                    mtimeMs: Date.parse(f.modifiedTime || f.createdTime || 0) || 0,
                }))
                .sort((a, b) => {
                    if (a.mtimeMs !== b.mtimeMs) return a.mtimeMs - b.mtimeMs;
                    return String(a.file.id) < String(b.file.id) ? -1 : (String(a.file.id) > String(b.file.id) ? 1 : 0);
                });

            const newEntries = ascFiles.filter(entry => compareCompositeItemToCursor(entry.mtimeMs, entry.file.id, parsedCursor) > 0);
            let usedRoundRobin = false;
            let batchEntries = newEntries.slice(0, batchSize);
            if (batchEntries.length === 0) {
                usedRoundRobin = true;
                const rawRrCursor = await getRuntimeState(env, "notify_drive_rr_cursor", null);
                const parsedRrCursor = parseCompositeCursor(rawRrCursor);
                let rrEntries = ascFiles.filter(entry => compareCompositeItemToCursor(entry.mtimeMs, entry.file.id, parsedRrCursor) > 0);
                if (rrEntries.length === 0) rrEntries = ascFiles;
                batchEntries = rrEntries.slice(0, batchSize);
            }
            if (batchEntries.length > 0) {
                const batchIds = new Set(batchEntries.map(e => e.file.id));
                const batchStems = new Set(batchEntries.map(e => getReportStem(e.file.name)));
                // Pull sibling original/_corrected sharing same stem so corrected beats original even across batch boundary
                const fetchFiles = batchEntries.map(e => e.file);
                for (const entry of ascFiles) {
                    if (!batchIds.has(entry.file.id) && batchStems.has(getReportStem(entry.file.name))) {
                        fetchFiles.push(entry.file);
                    }
                }

                const { parsedCandidates, fetchErrorCount } = await fetchAndParseDriveReports(token, reportsFolderId, fetchFiles);
                const ranked = rankParsedReportCandidates(parsedCandidates);
                const byGroup = new Map();
                for (const item of ranked) {
                    if (!byGroup.has(item.groupKey)) {
                        byGroup.set(item.groupKey, item);
                    }
                }
                // Process oldest crawl run first -> newest crawl run
                const winningReports = Array.from(byGroup.values()).sort((a, b) => a.groupCrawlTs - b.groupCrawlTs);

                let transientDeliveryHalted = false;
                for (const item of winningReports) {
                    const d = item.data || {};
                    if (item.groupCrawlTs && item.groupCrawlTs < cutoffMs) continue;
                    const effStatus = String(d.run_status || d.technical_status || "completed").toLowerCase();
                    if (!["completed", "partial", "failed", "source_error", "no_qualifying_posts", "insufficient_data"].includes(effStatus)) continue;

                    const rawRunId = d.run_id || item.file?.name || "report";
                    const notifKey = resolveCanonicalNotificationKey({ runId: rawRunId, isCorrected: item.isCorr });
                    const reqId = d.request_id ? String(d.request_id) : null;

                    // Cross-check if request_id was already notified (e.g. via /complete or direct D1) for non-corrected report
                    const aliasKeys = [];
                    if (reqId) {
                        aliasKeys.push(
                            resolveCanonicalNotificationKey({ requestId: reqId, status: "completed" }),
                            resolveCanonicalNotificationKey({ requestId: reqId, status: "failed" })
                        );
                        try {
                            const reqRow = await env.DB.prepare("SELECT status, notified_at FROM crawl_requests WHERE id = ?")
                                .bind(reqId).first();
                            if (reqRow && reqRow.notified_at && !item.isCorr) {
                                notifiedRequestIds.add(reqId);
                                continue;
                            }
                        } catch {}
                    }

                    const stats = extractMuseReportStats(d) || {};
                    const recCount = Number(stats.newQualifiedCount ?? stats.qualified ?? stats.scanned ?? d.totals?.records ?? 0);
                    const summaryText = buildMuseTelegramNotificationText(d, item.file?.name, item.isCorr);

                    const delivery = await deliverWithUncertaintyGuard(env, notifKey, recCount, env.TELEGRAM_OWNER_ID, summaryText, {
                        aliasKeys,
                        requestId: reqId,
                        bypassRequestClaim: item.isCorr,
                        bypassAliasPrecheck: item.isCorr,
                        replyMarkup: reportActionsInlineKeyboard(false),
                    });
                    if (delivery.retryableExhausted) {
                        transientDeliveryHalted = true;
                        break;
                    }
                    if (reqId && (delivery.status === "sent" || delivery.status === "delivery_uncertain")) {
                        notifiedRequestIds.add(reqId);
                        try {
                            await env.DB.prepare("UPDATE crawl_requests SET notified_at = ? WHERE id = ? AND notified_at IS NULL")
                                .bind(new Date().toISOString(), reqId).run();
                        } catch {}
                    }
                }

                // Never commit durable cursor if Drive pagination hit partialError or fetch/rate-limit failed
                if (!partialError && fetchErrorCount === 0 && !transientDeliveryHalted) {
                    const lastEntry = batchEntries[batchEntries.length - 1];
                    const lastKey = `${lastEntry.mtimeStr}|${lastEntry.file.id}`;
                    if (!usedRoundRobin) {
                        await setRuntimeState(env, "notify_drive_cursor", lastKey);
                    } else {
                        await setRuntimeState(env, "notify_drive_rr_cursor", lastKey);
                        if (!rawCursor) await setRuntimeState(env, "notify_drive_cursor", lastKey);
                    }
                }
            }
        }
    } catch {}

    // 2. Batch + durable cursor backlog over direct-D1 completed or failed crawl_requests not yet notified
    try {
        const rawD1Cursor = await getRuntimeState(env, "notify_d1_cursor", null);
        const parsedD1Cursor = parseCompositeCursor(rawD1Cursor);

        const rowsRes = await env.DB.prepare(`
            SELECT * FROM crawl_requests
            WHERE status IN ('completed', 'failed') AND notified_at IS NULL
            ORDER BY updated_at ASC, id ASC LIMIT 200
        `).all();
        const allPendingD1 = (rowsRes.results || []).slice().sort((a, b) => {
            const uDiff = String(a.updated_at || "").localeCompare(String(b.updated_at || ""));
            if (uDiff !== 0) return uDiff;
            return String(a.id || "").localeCompare(String(b.id || ""));
        });

        let candidatesD1 = allPendingD1.filter(
            r => compareCompositeItemToCursor(Date.parse(r.updated_at || 0) || 0, r.id, parsedD1Cursor) > 0
        );
        if (candidatesD1.length === 0 && allPendingD1.length > 0 && parsedD1Cursor) {
            // Wrap around if an out-of-order row had updated_at <= cursor
            candidatesD1 = allPendingD1;
        }
        const batchD1 = candidatesD1.slice(0, batchSize);
        let lastCleanD1 = null;

        for (const req of batchD1) {
            if (notifiedRequestIds.has(req.id)) {
                lastCleanD1 = req;
                continue;
            }
            let summary = {};
            try { summary = JSON.parse(req.result_summary_json || "{}"); } catch {}
            const notifKey = resolveCanonicalNotificationKey({
                requestId: req.id,
                runId: summary.run_id || null,
                status: req.status,
            });
            const aliasKeys = [
                resolveCanonicalNotificationKey({ requestId: req.id, status: req.status }),
                summary.run_id ? resolveCanonicalNotificationKey({ runId: summary.run_id, isCorrected: true }) : null,
            ].filter(Boolean);

            const recCount = Number(summary.qualified_count ?? summary.record_count ?? 0);
            const chatId = req.requested_by_chat_id || env.TELEGRAM_OWNER_ID;
            const text = buildDirectD1CrawlNotificationText(req, summary);

            const delivery = await deliverWithUncertaintyGuard(env, notifKey, recCount, chatId, text, {
                aliasKeys,
                requestId: req.id,
                replyMarkup: reportActionsInlineKeyboard(false),
            });
            if (delivery.retryableExhausted) {
                break;
            }
            if (delivery.status === "sent" || delivery.status === "delivery_uncertain") {
                try {
                    await env.DB.prepare("UPDATE crawl_requests SET notified_at = ? WHERE id = ? AND notified_at IS NULL")
                        .bind(new Date().toISOString(), req.id).run();
                } catch {}
            }
            lastCleanD1 = req;
        }
        if (lastCleanD1) {
            await setRuntimeState(env, "notify_d1_cursor", `${lastCleanD1.updated_at || new Date().toISOString()}|${lastCleanD1.id}`);
        }
    } catch {}

    // 3. Batch + durable cursor over V1 fallback on THREADS_FOLDER_ID (excluding Archive/ & unified with Drive/D1 keys)
    try {
        const token = await getDriveToken(env);
        const headers = { Authorization: `Bearer ${token}` };
        const folderId = env.THREADS_FOLDER_ID || LEGACY_THREADS_FOLDER_ID;
        const maxPages = Math.min(Math.max(Number(env?.MAX_DRIVE_LIST_PAGES) || DEFAULT_MAX_DRIVE_LIST_PAGES, 1), 100);

        const allV1Files = [];
        const seenTokens = new Set();
        let pageToken = null;
        let v1PartialError = null;
        for (let page = 0; page < maxPages; page++) {
            const params = new URLSearchParams({
                q: `'${folderId}' in parents and trashed = false and name contains 'threads_crawl'`,
                fields: "nextPageToken,files(id,name,mimeType,size,createdTime,modifiedTime)",
                orderBy: "createdTime asc",
                pageSize: "100",
                supportsAllDrives: "true",
                includeItemsFromAllDrives: "true"
            });
            if (pageToken) params.set("pageToken", pageToken);
            const res = await fetch(`https://www.googleapis.com/drive/v3/files?${params}`, { headers });
            if (!res.ok) {
                v1PartialError = `drive_http_${res.status}`;
                break;
            }
            const list = await res.json();
            allV1Files.push(...(list.files || []));
            const nextPt = list.nextPageToken ? String(list.nextPageToken) : null;
            if (list.has_more === true && !nextPt) {
                v1PartialError = "missing_page_token";
                break;
            }
            if (!nextPt) break;
            if (nextPt === pageToken || seenTokens.has(nextPt)) {
                v1PartialError = "repeated_page_token";
                break;
            }
            if (page >= maxPages - 1) {
                v1PartialError = "page_limit_exceeded";
                break;
            }
            seenTokens.add(nextPt);
            pageToken = nextPt;
        }

        const rawV1Cursor = await getRuntimeState(env, "notify_v1_fallback_cursor", null);
        const parsedV1Cursor = parseCompositeCursor(rawV1Cursor);

        const v1Asc = allV1Files
            .filter(f => f.mimeType !== "application/vnd.google-apps.folder" && !isArchiveFolderName(f.name) && !isArchiveRelPath(f.name))
            .map(f => ({
                file: f,
                tsStr: f.modifiedTime || f.createdTime || "1970-01-01T00:00:00Z",
                tsMs: Date.parse(f.modifiedTime || f.createdTime || 0) || 0,
            }))
            .sort((a, b) => {
                if (a.tsMs !== b.tsMs) return a.tsMs - b.tsMs;
                return String(a.file.id) < String(b.file.id) ? -1 : (String(a.file.id) > String(b.file.id) ? 1 : 0);
            });

        const v1After = v1Asc.filter(entry => compareCompositeItemToCursor(entry.tsMs, entry.file.id, parsedV1Cursor) > 0);
        let usedV1RoundRobin = false;
        let v1Batch = v1After.slice(0, batchSize);
        if (v1Batch.length === 0 && v1Asc.length > 0) {
            usedV1RoundRobin = true;
            const rawV1Rr = await getRuntimeState(env, "notify_v1_fallback_rr_cursor", null);
            const parsedV1Rr = parseCompositeCursor(rawV1Rr);
            let v1Rr = v1Asc.filter(entry => compareCompositeItemToCursor(entry.tsMs, entry.file.id, parsedV1Rr) > 0);
            if (v1Rr.length === 0) v1Rr = v1Asc;
            v1Batch = v1Rr.slice(0, batchSize);
        }
        let lastCleanV1 = null;
        let v1FetchErr = false;

        for (const entry of v1Batch) {
            const file = entry.file;
            if (!file.size || Number(file.size) > MAX_MARKET_JSON_BYTES) {
                lastCleanV1 = entry;
                continue;
            }
            const dataRes = await fetch(
                `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(file.id)}?alt=media&supportsAllDrives=true`,
                { headers }
            );
            if (!dataRes.ok) {
                v1FetchErr = true;
                break;
            }
            const data = await dataRes.json();
            if (!data || !["completed", "failed"].includes(data.status)) {
                lastCleanV1 = entry;
                continue;
            }
            const startedMs = Date.parse(data.started_at);
            if (!Number.isFinite(startedMs) || startedMs < cutoffMs) {
                lastCleanV1 = entry;
                continue;
            }
            if (typeof data.run_id !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(data.run_id)) {
                lastCleanV1 = entry;
                continue;
            }
            const postsLen = Array.isArray(data.posts) ? data.posts.length : 0;
            const endAt = data.ended_at;
            if (!Number.isFinite(Date.parse(endAt))) {
                lastCleanV1 = entry;
                continue;
            }

            const isCorr = String(file.name || "").toLowerCase().includes("_corrected") || Boolean(data.corrects_report);
            const notifKey = resolveCanonicalNotificationKey({ runId: data.run_id, isCorrected: isCorr });
            const corrAlias = resolveCanonicalNotificationKey({ runId: data.run_id, isCorrected: true });
            const reqAlias = data.request_id
                ? resolveCanonicalNotificationKey({ requestId: data.request_id, status: data.status })
                : null;
            const aliasKeys = [corrAlias, reqAlias].filter(Boolean);

            const time = formatVnTime(endAt);
            const text = data.status === "failed"
                ? `❌ MUSE CRAWL THẤT BẠI (Threads)\n━━━━━━━━━━━━━━━━━━━━\n` +
                  `🆔 Run: ${data.run_id}\n` +
                  `❗ Lỗi: ${String(data.error_message || "Unknown").slice(0, 300)}\n` +
                  `🕒 Thời gian: ${time}`
                : `🔔 MUSE CRAWL HOÀN THÀNH (Threads)\n━━━━━━━━━━━━━━━━━━━━\n` +
                  `🆔 Run: ${data.run_id}\n` +
                  `📊 Số bài: ${postsLen}\n` +
                  `🧹 Trùng đã loại: ${Number.isSafeInteger(data.duplicates_skipped) ? data.duplicates_skipped : "N/A"}\n` +
                  `🕒 Hoàn thành: ${time}\n\n` +
                  "🔄 Auto Sync đang chạy ngầm (hoặc vào ⚙️ Cài đặt → Đồng bộ thủ công).";

            const delivery = await deliverWithUncertaintyGuard(env, notifKey, postsLen, env.TELEGRAM_OWNER_ID, text, {
                aliasKeys,
                requestId: data.request_id || null,
                bypassRequestClaim: isCorr,
                bypassAliasPrecheck: isCorr,
            });
            if (delivery.retryableExhausted) break;
            lastCleanV1 = entry;
        }

        if (!v1PartialError && !v1FetchErr && lastCleanV1) {
            const lastV1Key = `${lastCleanV1.tsStr}|${lastCleanV1.file.id}`;
            if (!usedV1RoundRobin) {
                await setRuntimeState(env, "notify_v1_fallback_cursor", lastV1Key);
            } else {
                await setRuntimeState(env, "notify_v1_fallback_rr_cursor", lastV1Key);
                if (!rawV1Cursor) await setRuntimeState(env, "notify_v1_fallback_cursor", lastV1Key);
            }
        }
    } catch {}
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

function mainReplyKeyboard() {
    return {
        keyboard: [
            [{ text: "🕷️ Crawl dữ liệu" }, { text: "📊 Phân tích thị trường" }],
            [{ text: "⏰ Lịch tự động" }, { text: "📋 Lịch sử tác vụ" }],
            [{ text: "📡 Trạng thái hệ thống" }, { text: "⚙️ Cài đặt" }]
        ],
        resize_keyboard: true,
        is_persistent: true
    };
}

function homeOnlyInlineKeyboard() {
    return {
        inline_keyboard: [
            [{ text: "🏠 Trang chủ", callback_data: "menu:home" }]
        ]
    };
}

function reportActionsInlineKeyboard(isFullView = false) {
    return {
        inline_keyboard: [
            isFullView
                ? [
                    { text: "📌 Xem tóm tắt", callback_data: "rpt:summary" },
                    { text: "🔄 Làm mới báo cáo", callback_data: "rpt:full" }
                ]
                : [
                    { text: "📄 Xem báo cáo đầy đủ", callback_data: "rpt:full" },
                    { text: "🔄 Làm mới báo cáo", callback_data: "rpt:summary" }
                ],
            [{ text: "🏠 Trang chủ", callback_data: "menu:home" }]
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
    const data = await resp.json().catch(() => ({}));
    return { ok: Boolean(resp.ok && data.ok), httpStatus: resp.status, data };
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
    const data = await resp.json().catch(() => ({}));
    const notModified = !resp.ok && String(data.description || "").includes("message is not modified");
    return { ok: Boolean((resp.ok && data.ok) || notModified), httpStatus: resp.status, data };
}

function isUneditablePrimaryMessageError(httpStatus, data) {
    const apiStatus = Number(httpStatus || 0);
    if (apiStatus !== 400 && apiStatus !== 404) return false;
    const desc = String(data?.description || "").toLowerCase();
    return (
        desc.includes("message to edit not found") ||
        desc.includes("message can't be edited") ||
        desc.includes("message_id_invalid") ||
        desc.includes("message not found")
    );
}

async function sendOrEditTelegram(env, chatId, messageId, text, replyMarkup = null) {
    if (messageId) {
        const edited = await editTelegramMessage(env, chatId, messageId, text, replyMarkup);
        if (edited.ok) return edited;
        if (!isUneditablePrimaryMessageError(edited.httpStatus, edited.data)) {
            return edited;
        }
    }
    return sendTelegramMessage(env, chatId, text, replyMarkup);
}

async function updatePrimaryProgressMessage(env, chatId, text, replyMarkup = null) {
    const primaryMsgId = await getRuntimeState(env, "tg_primary_status_msg_id", null);
    if (primaryMsgId) {
        // Any 429, delivery_uncertain (408/5xx/transport throw), or non-missing permanent error MUST NOT
        // silently fall back to sendMessage — return/throw directly for deliverWithUncertaintyGuard retry taxonomy.
        const edited = await editTelegramMessage(env, chatId, primaryMsgId, text, replyMarkup);
        if (edited.ok) return edited;
        if (!isUneditablePrimaryMessageError(edited.httpStatus, edited.data)) {
            return edited;
        }
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
            `❌ LỆNH ${cmdType.toUpperCase()} THẤT BẠI\n━━━━━━━━━━━━━━━━━━━━\n` +
            `🆔 Mã lệnh: ${cmdRow.id}\n` +
            `❗ Lỗi: ${String(errorMessage || "Unknown error").slice(0, 400)}`
        );
    }

    if (cmdType === "sync_now") {
        const r = resultObj || {};
        return (
            "✅ APP_VIDEO ĐÃ ĐỒNG BỘ XONG\n━━━━━━━━━━━━━━━━━━━━\n" +
            `🆔 Mã lệnh: ${cmdRow.id}\n` +
            `📂 File kiểm tra: ${r.files_checked ?? 0}\n` +
            `📥 File tải mới: ${r.files_downloaded ?? 0}\n` +
            `➕ Bản ghi thêm mới: ${r.records_inserted ?? 0}\n` +
            `♻️ Cập nhật: ${r.records_updated ?? 0}\n` +
            `🧹 Trùng đã bỏ qua: ${r.records_deduped ?? 0}\n` +
            `📦 Tổng kho local: ${r.total_items ?? "N/A"}\n` +
            `🕒 Hoàn thành: ${formatVnTime(r.finished_at || new Date().toISOString())}`
        );
    }

    if (cmdType === "app_status") {
        const r = resultObj || {};
        return (
            "📡 APP_VIDEO STATUS (LIVE)\n━━━━━━━━━━━━━━━━━━━━\n" +
            `🟢 Trạng thái: ONLINE\n` +
            `🖥️ Client: ${r.client_id || "app-video"}\n` +
            `🔄 Đồng bộ nền: ${r.sync_status || "idle"} (mỗi ${r.sync_interval_seconds ?? 300}s)\n` +
            `🕒 Sync gần nhất: ${formatVnTime(r.last_sync_at)}\n` +
            `📦 Tổng bản ghi local: ${r.total_items ?? 0}`
        );
    }

    if (cmdType === "market_stats" || cmdType === "last_sync_result") {
        const r = resultObj || {};
        const byPlat = r.by_platform || {};
        return (
            "📊 THỐNG KÊ THỊ TRƯỜNG APP_VIDEO\n━━━━━━━━━━━━━━━━━━━━\n" +
            `🆔 Mã lệnh: ${cmdRow.id}\n` +
            `📦 Tổng bản ghi: ${r.total_items ?? 0}\n` +
            `🧵 Threads: ${byPlat.threads ?? 0}\n` +
            `🎵 TikTok: ${byPlat.tiktok ?? 0}\n` +
            `🛒 Shopee: ${byPlat.shopee ?? 0}\n` +
            `🕒 Sync gần nhất: ${formatVnTime(r.last_sync_at)}`
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
            const pageToken = url.searchParams.get("page_token") || null;
            const limit = Number(url.searchParams.get("limit") || "50");
            const maxPages = Math.min(Math.max(Number(url.searchParams.get("max_pages") || env.MAX_DRIVE_LIST_PAGES || DEFAULT_MAX_DRIVE_LIST_PAGES), 1), 100);
            const driveToken = await getDriveToken(env);
            const marketFolderId = await resolveMarketDataFolderId(driveToken, env);
            const listing = await listMarketJsonFilesRecursive(driveToken, marketFolderId, cursor, limit, pageToken, maxPages);
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

    // 3b. Cross-Run Deduplication & Post Registry endpoints (Fail-closed + Lease/Owner/Attempt Guard)
    if (request.method === "POST" && path === "/api/app/market/dedup/check") {
        let body = {};
        try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400); }
        try {
            const res = await checkCrossRunDedupInD1(env, body.items || []);
            return jsonResponse({ ok: true, ...res });
        } catch (err) {
            return jsonResponse(
                { ok: false, registry_unavailable: true, error: String(err?.message || "D1 registry unavailable") },
                503
            );
        }
    }

    if (request.method === "POST" && path === "/api/app/market/dedup/record") {
        let body = {};
        try { body = await request.json(); } catch { return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400); }
        const requestId = body.request_id ? String(body.request_id).trim() : null;
        if (requestId) {
            const reqRow = await env.DB.prepare("SELECT * FROM crawl_requests WHERE id = ?").bind(requestId).first();
            if (!reqRow) {
                return jsonResponse({ ok: false, error: "Crawl request not found", conflict: false }, 404);
            }
            const callerOwner = String(body.claimed_by || body.worker_id || clientId || "").trim();
            if (!callerOwner || (reqRow.claimed_by && callerOwner !== reqRow.claimed_by)) {
                return jsonResponse({ ok: false, error: "Crawl claim ownership mismatch", conflict: true }, 409);
            }
            const rawAttempt = body.attempt ?? body.attempts;
            if (rawAttempt !== undefined && rawAttempt !== null && Number(rawAttempt) !== Number(reqRow.attempts || 0)) {
                return jsonResponse({ ok: false, error: "Stale crawl claim attempt", conflict: true }, 409);
            }
            if (!["running", "completed"].includes(reqRow.status)) {
                return jsonResponse({ ok: false, error: "Crawl claim is not active or expired", conflict: true }, 409);
            }
            if (reqRow.status === "running" && reqRow.lease_expires_at && Date.parse(reqRow.lease_expires_at) < Date.now()) {
                await expireStaleLeases(env);
                return jsonResponse({ ok: false, error: "Crawl claim lease expired", conflict: true }, 409);
            }
        }
        try {
            const res = await recordCrossRunDedupInD1(env, body.items || [], body.run_id || null, {
                crawledAt: body.crawled_at || body.measured_at || null,
            });
            return jsonResponse({ ok: true, ...res });
        } catch (err) {
            return jsonResponse(
                { ok: false, registry_unavailable: true, error: String(err?.message || "D1 registry unavailable") },
                503
            );
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
                        claimed_by: clientId,
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

        const rawAttempt = body.attempt ?? body.attempts;
        const parsedAttempt = Number(rawAttempt);
        if (rawAttempt === undefined || rawAttempt === null || rawAttempt === "" || !Number.isInteger(parsedAttempt) || parsedAttempt < 1) {
            return jsonResponse({ ok: false, error: "Missing or invalid required attempt field", conflict: false }, 400);
        }
        const callerId = String(body.claimed_by || clientId || "").trim();
        if (!callerId) {
            return jsonResponse({ ok: false, error: "Missing claim owner identity", conflict: false }, 400);
        }

        const existing = await env.DB.prepare("SELECT * FROM app_commands WHERE id = ?").bind(commandId).first();
        if (!existing) return jsonResponse({ ok: false, error: "Command not found" }, 404);

        if (["completed", "failed", "cancelled"].includes(existing.status)) {
            if (
                (existing.claimed_by && callerId !== existing.claimed_by) ||
                parsedAttempt !== Number(existing.attempts || 0) ||
                existing.status !== nextStatus
            ) {
                return jsonResponse({ ok: false, error: "Command already finalized by a newer attempt", conflict: true }, 409);
            }
            return jsonResponse({ ok: true, already_finalized: true, status: existing.status });
        }

        if (existing.status !== "running") {
            return jsonResponse({ ok: false, error: "Claim expired or command is not running", conflict: true }, 409);
        }
        if (existing.claimed_by && callerId !== existing.claimed_by) {
            return jsonResponse({ ok: false, error: "Claim ownership mismatch", conflict: true }, 409);
        }
        if (parsedAttempt !== Number(existing.attempts || 0)) {
            return jsonResponse({ ok: false, error: "Stale claim attempt", conflict: true }, 409);
        }
        if (existing.lease_expires_at && Date.parse(existing.lease_expires_at) < Date.now()) {
            await expireStaleLeases(env);
            return jsonResponse({ ok: false, error: "Claim lease expired", conflict: true }, 409);
        }

        const upd = await env.DB.prepare(`
            UPDATE app_commands
            SET status = ?, result_json = ?, error_message = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND claimed_by = ? AND attempts = ?
              AND (lease_expires_at IS NULL OR lease_expires_at >= ?)
        `).bind(nextStatus, resultJson, errMsg, nowIso, commandId, callerId, parsedAttempt, nowIso).run();

        if (upd.meta.changes !== 1) {
            return jsonResponse({ ok: false, error: "Completion conflict: owner, attempt, status, or lease failed atomic guard", conflict: true }, 409);
        }

        if (existing.requested_by_chat_id && !existing.notified_at && env.TELEGRAM_BOT_TOKEN_VIDEO) {
            const text = formatCommandNotificationText(existing, nextStatus, body.result, errMsg);
            const sent = await updatePrimaryProgressMessage(env, existing.requested_by_chat_id, text, homeOnlyInlineKeyboard());
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
        const enriched = (rows.results || []).map(r => ({
            ...r,
            progress_metrics: computeCrawlRequestProgress(r),
        }));
        return jsonResponse({
            ok: true,
            schedule,
            items: enriched
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

        const rawAttempt = body.attempt ?? body.attempts;
        const parsedAttempt = Number(rawAttempt);
        if (rawAttempt === undefined || rawAttempt === null || rawAttempt === "" || !Number.isInteger(parsedAttempt) || parsedAttempt < 1) {
            return jsonResponse({ ok: false, error: "Missing or invalid required attempt field", conflict: false }, 400);
        }
        const callerOwner = String(body.claimed_by || body.worker_id || clientId || "").trim();
        if (!callerOwner) {
            return jsonResponse({ ok: false, error: "Missing crawl claim owner identity", conflict: false }, 400);
        }

        const existing = await env.DB.prepare("SELECT * FROM crawl_requests WHERE id = ?").bind(crawlId).first();
        if (!existing) return jsonResponse({ ok: false, error: "Crawl request not found" }, 404);
        if (["completed", "failed", "cancelled"].includes(existing.status)) {
            if (
                (existing.claimed_by && callerOwner !== existing.claimed_by) ||
                parsedAttempt !== Number(existing.attempts || 0) ||
                existing.status !== nextStatus
            ) {
                return jsonResponse({ ok: false, error: "Crawl request already finalized by another worker/attempt", conflict: true }, 409);
            }
            return jsonResponse({ ok: true, already_finalized: true, status: existing.status });
        }

        if (existing.status !== "running") {
            return jsonResponse({ ok: false, error: "Crawl lease expired or request is not running", conflict: true }, 409);
        }
        if (existing.claimed_by && callerOwner !== existing.claimed_by) {
            return jsonResponse({ ok: false, error: "Crawl claim ownership mismatch", conflict: true }, 409);
        }
        if (parsedAttempt !== Number(existing.attempts || 0)) {
            return jsonResponse({ ok: false, error: "Stale crawl claim attempt", conflict: true }, 409);
        }
        if (existing.lease_expires_at && Date.parse(existing.lease_expires_at) < Date.now()) {
            await expireStaleLeases(env);
            return jsonResponse({ ok: false, error: "Crawl claim lease expired", conflict: true }, 409);
        }

        const summaryObj = (body.result_summary && typeof body.result_summary === "object")
            ? { ...body.result_summary }
            : {};

        // Atomic completion guard FIRST so expired/conflicting requests NEVER mutate market_post_registry
        const upd = await env.DB.prepare(`
            UPDATE crawl_requests
            SET status = ?, result_summary_json = ?, report_file_id = ?, error_message = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND claimed_by = ? AND attempts = ?
              AND (lease_expires_at IS NULL OR lease_expires_at >= ?)
        `).bind(
            nextStatus,
            JSON.stringify(summaryObj),
            body.report_file_id || null,
            body.error_message || null,
            nowIso,
            crawlId,
            callerOwner,
            parsedAttempt,
            nowIso
        ).run();

        if (upd.meta.changes !== 1) {
            return jsonResponse({ ok: false, error: "Crawl completion conflict: owner, attempt, status, or lease failed atomic guard", conflict: true }, 409);
        }

        const dedupSourceItems = Array.isArray(summaryObj.items)
            ? summaryObj.items
            : (Array.isArray(body.items) ? body.items : null);
        if (dedupSourceItems && dedupSourceItems.length > 0) {
            try {
                const dRes = await recordCrossRunDedupInD1(env, dedupSourceItems, summaryObj.run_id || crawlId, {
                    crawledAt: summaryObj.crawled_at || summaryObj.generated_at || null,
                });
                if (summaryObj.new_count === undefined) summaryObj.new_count = dRes.new_count;
                if (summaryObj.new_qualified_count === undefined) summaryObj.new_qualified_count = dRes.new_qualified_count;
                if (summaryObj.duplicate_history_count === undefined && summaryObj.duplicate_count === undefined) {
                    summaryObj.duplicate_history_count = dRes.duplicate_history_count;
                    summaryObj.duplicate_count = dRes.duplicate_count;
                }
                if (summaryObj.updated_metrics_count === undefined && summaryObj.updated_count === undefined) {
                    summaryObj.updated_metrics_count = dRes.updated_metrics_count;
                    summaryObj.updated_count = dRes.updated_count;
                }
                if (summaryObj.missing_views_count === undefined) summaryObj.missing_views_count = dRes.missing_views_count;
                summaryObj.dedup_stats = summaryObj.dedup_stats || dRes;
                await env.DB.prepare(`
                    UPDATE crawl_requests SET result_summary_json = ? WHERE id = ?
                `).bind(JSON.stringify(summaryObj), crawlId).run();
            } catch (err) {
                summaryObj.registry_sync_status = "error";
                summaryObj.registry_sync_error = String(err?.message || "D1 registry unavailable");
                await env.DB.prepare(`
                    UPDATE crawl_requests SET result_summary_json = ? WHERE id = ?
                `).bind(JSON.stringify(summaryObj), crawlId).run();
                return jsonResponse(
                    {
                        ok: false,
                        registry_unavailable: true,
                        completed_status: nextStatus,
                        error: summaryObj.registry_sync_error,
                    },
                    503
                );
            }
        }

        if (existing.requested_by_chat_id && !existing.notified_at && env.TELEGRAM_BOT_TOKEN_VIDEO) {
            const summary = summaryObj;
            const notifKey = resolveCanonicalNotificationKey({
                requestId: crawlId,
                runId: summary.run_id || null,
                status: nextStatus,
            });
            const aliasKeys = [
                resolveCanonicalNotificationKey({ requestId: crawlId, status: nextStatus }),
                summary.run_id ? resolveCanonicalNotificationKey({ runId: summary.run_id, isCorrected: true }) : null,
            ].filter(Boolean);
            const pseudoReq = {
                ...existing,
                status: nextStatus,
                error_message: body.error_message || existing.error_message,
                updated_at: nowIso,
            };
            const text = buildDirectD1CrawlNotificationText(pseudoReq, summary);
            const recCount = Number(summary.new_qualified_count ?? summary.qualified_count ?? summary.record_count ?? 0);
            const delivery = await deliverWithUncertaintyGuard(
                env,
                notifKey,
                recCount,
                existing.requested_by_chat_id,
                text,
                { usePrimaryMessage: true, aliasKeys, requestId: crawlId, replyMarkup: reportActionsInlineKeyboard(false) }
            );
            if (delivery.status === "sent" || delivery.status === "delivery_uncertain") {
                await env.DB.prepare("UPDATE crawl_requests SET notified_at = ? WHERE id = ? AND notified_at IS NULL").bind(nowIso, crawlId).run();
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

// ─── Telegram V2 Views (Reports, Schedule, Settings, Status, Crawl, History) ─

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

async function buildHomeView(env) {
    const appState = await getAppOnlineSummary(env);
    const eta = await computeExpectedPickup(env);
    const text =
        "🤖 APP VIDEO MANAGER V2\n━━━━━━━━━━━━━━━━━━━━\n" +
        "• ☁️ Cloudflare Worker: ✅ Online\n" +
        `• 🖥️ App_Video Desktop: ${appState.online ? `🟢 ONLINE (${appState.age_sec}s)` : "🔴 OFFLINE (Auto Sync chạy khi mở app)"}\n` +
        `• ⏰ Lịch tự động Muse: ${eta.muse_schedule_confirmed ? "✅ Đã xác nhận" : "⚪ Chưa kích hoạt / Chưa xác nhận"}\n` +
        `• 🕒 Muse poll gần nhất: ${eta.muse_last_poll_at ? formatVnTime(eta.muse_last_poll_at) : "Chưa ghi nhận"}\n\n` +
        "👇 Chọn chức năng trên bàn phím bên dưới (tin nhắn thao tác sẽ hiển thị rõ trong cuộc trò chuyện):";
    return { text, reply_markup: mainReplyKeyboard() };
}

async function buildAnalysisReportBundle(env) {
    const appState = await getAppOnlineSummary(env);
    let hbReport = null;
    let hbCounts = {};
    if (appState.heartbeat) {
        try { hbCounts = JSON.parse(appState.heartbeat.market_counts_json || "{}"); } catch {}
        try { hbReport = JSON.parse(appState.heartbeat.latest_report_json || "null"); } catch {}
    }

    // Prioritize latest corrected Muse report directly from Drive (works even when App_Video is OFFLINE)
    let museDrive = { reportsFolderId: null, file: null, data: null };
    try {
        museDrive = await getLatestMuseReportFromDrive(env);
    } catch {}

    const museData = museDrive.data;
    let summaryText = "";
    let fullText = "";

    if (museData && typeof museData === "object") {
        const isCorr = String(museDrive.file?.name || "").toLowerCase().includes("_corrected") || Boolean(museData.corrects_report);
        const meta = extractMuseReportStats(museData) || {};
        const pr = meta.platformResults || {};
        const reportFileName = museDrive.file?.name || (meta.isV3 ? "trend_report_v3.json" : "market_report_muse.json");
        const appStatusTag = appState.online ? "App_Video: 🟢 ONLINE" : "App_Video: 🔴 OFFLINE (đọc trực tiếp Drive)";

        summaryText = formatStructuredReportSummary(museData, reportFileName, isCorr, meta, { appStatusTag });

        if (meta.isV3) {
            const detailParts = [
                `📄 BÁO CÁO CHI TIẾT ${meta.isV32 ? "V3.2" : "V3.1"}: ${museData.report || reportFileName}\n━━━━━━━━━━━━━━━━━━━━`,
                `📊 Chất lượng crawl\n• File: ${reportFileName}\n• run_status: ${meta.rawRunStatus || "—"} | technical_status: ${meta.rawTechStatus || "—"} | data_quality_status: ${meta.rawDqStatus || "—"}${meta.stopReason ? `\n• stop_reason: ${meta.stopReason} | target_achieved: ${meta.targetAchieved}` : ""}`,
                `📊 Sàng lọc & Phân tầng\n• Quét ${meta.scanned ?? "N/A"} | Duy nhất ${meta.uniqueCandidates ?? "N/A"} | Mới đạt chuẩn ${meta.newQualifiedCount ?? meta.qualified ?? "N/A"} | Loại ${meta.rejected ?? "N/A"}${meta.crossVerified !== null ? ` | Web: ${meta.crossVerified}` : ""}\n• Very Hot ${meta.tiers.very_hot} • Hot ${meta.tiers.hot} • Emerging ${meta.tiers.emerging} • Watchlist ${meta.tiers.watchlist} • Unverified ${meta.unverified}`
            ];
            if (meta.trends.length) {
                detailParts.push(
                    "🔥 XU HƯỚNG NỔI BẬT\n" +
                    meta.trends.slice(0, 4).map(t => `• ${t.theme || t.title || t}${t.lifecycle_state ? ` ${formatTrendLifecycleTag(t.lifecycle_state)}` : ""}\n  Bằng chứng: ${t.evidence || t.basis || "—"}`).join("\n")
                );
            }
            if (meta.topViral.length) {
                detailParts.push(
                    "🚀 TOP BÀI VIRAL\n" +
                    meta.topViral.slice(0, 4).map(v => `• ${v.post} (${formatViewsLabel(v)}, ${v.likes ?? "—"} likes, ${v.comments ?? "—"} cmt)${v.url ? `\n  ${v.url}` : ""}`).join("\n")
                );
            }
            if (meta.signals.length || meta.emerging.length) {
                const sigLines = meta.signals.slice(0, 3).map(s => `• [Tín hiệu] ${s.signal || s.topic}: ${s.evidence || ""}`);
                const emLines = meta.emerging.slice(0, 2).map(e => `• [Mới nổi] ${e.topic}: ${e.evidence || ""}`);
                detailParts.push("🆕 MỚI NỔI & TÍN HIỆU ĐƠN LẺ\n" + [...emLines, ...sigLines].join("\n"));
            }
            if (meta.growthTracking.length) {
                detailParts.push(
                    "📈 BIẾN ĐỘNG SO VỚI LƯỢT TRƯỚC\n" +
                    meta.growthTracking.slice(0, 4).map(g => `• ${typeof g === "string" ? g : `${g.post || g.author_handle || g.post_id || "Bài cũ"}: ${g.change_summary || g.delta_summary || g.note || ""}`}`).join("\n")
                );
            }
            if (meta.aff.length) {
                detailParts.push(
                    "💰 CƠ HỘI AFFILIATE ƯU TIÊN\n" +
                    meta.aff.slice(0, 4).map(a => `• ${a.product} [${a.priority || "—"}]: ${a.evidence || a.evidence_summary || ""}`).join("\n")
                );
            }
            if (meta.actions.length) {
                detailParts.push("🎯 HÀNH ĐỘNG ƯU TIÊN\n" + meta.actions.slice(0, 3).map(a => `• ${trimBullet(a?.action || a?.title || a, 180)}`).join("\n"));
            }
            if (meta.verifDetail) {
                detailParts.push(`⚠️ XÁC MINH & GIỚI HẠN\n• ${meta.verifDetail}`);
            }
            fullText = detailParts.join("\n\n").slice(0, 4000);
        } else {
            const detailSections = [`📄 BÁO CÁO ĐẦY ĐỦ: ${museData.report || reportFileName}\n━━━━━━━━━━━━━━━━━━━━`];
            for (const pKey of ["threads", "tiktok", "shopee"]) {
                const p = pr[pKey];
                if (!p) continue;
                const corrs = Array.isArray(p.corrections) ? p.corrections.slice(0, 3).map(c => `  + ${c}`).join("\n") : "";
                const issues = Array.isArray(p.remaining_issues) ? p.remaining_issues.slice(0, 3).map(i => `  - ${i}`).join("\n") : "";
                detailSections.push(
                    `[${pKey.toUpperCase()}] ${p.business_usable_records ?? 0}/${p.records ?? 0} usable (KT: ${p.technical_status || p.status}, CL: ${p.data_quality_status || "—"})` +
                    (corrs ? `\n• Đã chuẩn hóa:\n${corrs}` : "") +
                    (issues ? `\n• Hạn chế còn lại:\n${issues}` : "")
                );
            }
            const lims = Array.isArray(museData.data_quality_and_limitations)
                ? museData.data_quality_and_limitations.slice(0, 4).map(l => `• ${l}`).join("\n")
                : "";
            if (lims) detailSections.push(`⚠️ Giới hạn dữ liệu:\n${lims}`);
            fullText = detailSections.join("\n\n").slice(0, 4000);
        }
    } else if (hbReport && typeof hbReport === "object") {
        // Fallback strictly when NO Muse report JSON exists in 01_Market_Data/Reports/
        summaryText = formatStructuredReportSummary(
            hbReport,
            "App_Video Local Heartbeat",
            false,
            null,
            { appStatusTag: "App_Video: 🟢 ONLINE", hbCounts }
        );
        fullText = summaryText;
    } else {
        summaryText =
            "📊 BÁO CÁO PHÂN TÍCH THỊ TRƯỜNG\n━━━━━━━━━━━━━━━━━━━━\n\n" +
            "📊 Chất lượng crawl\n• Chưa tìm thấy báo cáo nào trong 01_Market_Data/Reports/.\n\n" +
            "🔥 Trend thị trường\n• Chưa có dữ liệu.\n\n" +
            "🆕 Trend mới & vòng đời\n• Chưa có dữ liệu.\n\n" +
            "💰 Tín hiệu Affiliate\n• Chưa có dữ liệu.\n\n" +
            "📈 Biến động so với lượt trước\n• Chưa có dữ liệu.\n\n" +
            "🎯 Hành động đề xuất\n• Vào 🕷️ Crawl dữ liệu để tạo lượt thu thập mới.";
        fullText = summaryText;
    }

    return {
        text: summaryText,
        fullText,
        reply_markup: reportActionsInlineKeyboard(false),
        full_reply_markup: reportActionsInlineKeyboard(true)
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
        "⏰ LỊCH TỰ ĐỘNG (TRẠNG THÁI THỰC TẾ)\n━━━━━━━━━━━━━━━━━━━━\n" +
        `• Lịch tự động Muse: ${museStatusText}\n` +
        `• Poll Muse thực tế gần nhất: ${lastPollText}\n` +
        `• Dự kiến nhận việc kế tiếp: ${nextEtaText}\n` +
        `• Chu kỳ đề xuất trên D1: ${eta.profile_label} (chỉ tham chiếu)\n` +
        `• Auto Sync App_Video: Chạy ngầm tự động mỗi ${syncIntervalSec}s khi online`;

    const kb = {
        inline_keyboard: [
            [
                { text: "🔄 Kiểm tra lại lịch", callback_data: "sched:view" },
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
        "⚙️ CÀI ĐẶT HỆ THỐNG & ĐỒNG BỘ\n━━━━━━━━━━━━━━━━━━━━\n" +
        `• Auto Sync App_Video: Chạy ngầm tự động (${syncIntervalSec}s/lần)\n` +
        `• Đồng bộ gần nhất: ${lastSyncText}\n` +
        `• Chu kỳ đề xuất trên D1: ${eta.profile_label}\n` +
        `• Xác nhận lịch phía Muse: ${eta.muse_schedule_confirmed ? "✅ Đã xác nhận" : "⚪ Chưa xác nhận"}`;

    const kb = {
        inline_keyboard: [
            [{ text: "🔄 Đồng bộ thủ công", callback_data: "cmd:sync_now" }],
            [{ text: "🔀 Đổi chu kỳ đề xuất (5p / 6h)", callback_data: "crawl:toggle_profile" }],
            [{ text: "🏠 Trang chủ", callback_data: "menu:home" }]
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
    let uncertainNotifs = 0;
    let stuckSendingNotifs = 0;
    const stuckCutoffIso = new Date(Date.now() - STUCK_SENDING_THRESHOLD_MS).toISOString();
    try {
        const issueRows = (await env.DB.prepare(
            "SELECT run_id, status, created_at FROM crawl_notifications WHERE status IN ('delivery_uncertain', 'sending') ORDER BY created_at DESC LIMIT 50"
        ).all()).results || [];
        for (const nr of issueRows) {
            if (nr.status === "delivery_uncertain") uncertainNotifs += 1;
            else if (nr.status === "sending" && nr.created_at && nr.created_at <= stuckCutoffIso) stuckSendingNotifs += 1;
        }
    } catch {
        try {
            const uRow = await env.DB.prepare("SELECT COUNT(*) AS cnt FROM crawl_notifications WHERE status = 'delivery_uncertain'").first();
            uncertainNotifs = Number(uRow?.cnt || 0);
        } catch {}
    }

    const notifWarnings = [];
    if (uncertainNotifs > 0) {
        notifWarnings.push(`⚠️ Cảnh báo gửi tin không chắc chắn (delivery_uncertain): ${uncertainNotifs} mục — Không tự gửi lại để tránh trùng; kiểm tra Telegram trước khi xử lý thủ công.`);
    }
    if (stuckSendingNotifs > 0) {
        notifWarnings.push(`⚠️ Cảnh báo trạng thái sending bị treo: ${stuckSendingNotifs} thông báo (>5 phút chưa hoàn tất). Phương án xử lý: kiểm tra lịch sử chat Telegram; chuyển sang không chắc chắn hoặc chỉ mở khóa gửi lại khi chắc chắn chưa từng nhận.`);
    }

    const text =
        "📡 TRẠNG THÁI HỆ THỐNG\n━━━━━━━━━━━━━━━━━━━━\n" +
        "• ☁️ Cloudflare Worker: ✅ Online\n" +
        "• 🤖 Telegram Bot: ✅ Connected\n" +
        `• 🗄️ D1 Database: ${dbStatus}\n` +
        `• 🔗 Drive Connector Backend: ${driveBackendStatus}\n` +
        `• 🖥️ App_Video Desktop: ${appState.online ? `🟢 ONLINE (${appState.age_sec}s)` : "🔴 OFFLINE"}\n` +
        `• 🔄 Đồng bộ ngầm gần nhất: ${hb?.last_sync_at ? formatVnTime(hb.last_sync_at) : "Chưa ghi nhận"} (${hb?.sync_status || "unknown"})\n` +
        `• 📦 Kho local App_Video: ${hbCounts.total_items ?? "N/A"} bản ghi\n` +
        `• ⏰ Lịch tự động Muse: ${eta.muse_schedule_confirmed ? "✅ Đã xác nhận" : "⚪ Chưa kích hoạt / Chưa xác nhận"}\n` +
        `• 🕒 Muse Poll gần nhất: ${eta.muse_last_poll_at ? formatVnTime(eta.muse_last_poll_at) : "Chưa ghi nhận"}\n` +
        (notifWarnings.length ? `${notifWarnings.join("\n")}\n` : "") +
        "• 🚫 Auto Publisher: Chưa triển khai";

    const inlineRows = [];
    if (uncertainNotifs > 0 || stuckSendingNotifs > 0) {
        inlineRows.push([{ text: "🔍 Xem chi tiết thông báo cần kiểm tra", callback_data: "notif:issues" }]);
    }
    if (stuckSendingNotifs > 0) {
        inlineRows.push([
            { text: "🛠️ Chuyển sending treo -> uncertain", callback_data: "notif:stuck:uncertain" },
            { text: "🧹 Mở khóa gửi lại", callback_data: "notif:stuck:reset" }
        ]);
    }
    inlineRows.push([
        { text: "🔄 Làm mới trạng thái", callback_data: "sys:status" },
        { text: "🏠 Trang chủ", callback_data: "menu:home" }
    ]);

    return { text, reply_markup: { inline_keyboard: inlineRows } };
}

async function buildNotificationIssuesView(env) {
    const stuckCutoffIso = new Date(Date.now() - STUCK_SENDING_THRESHOLD_MS).toISOString();
    let rows = [];
    try {
        rows = (await env.DB.prepare(
            "SELECT run_id, status, created_at FROM crawl_notifications WHERE status IN ('delivery_uncertain', 'sending') ORDER BY created_at DESC LIMIT 20"
        ).all()).results || [];
    } catch {}
    const stuck = rows.filter(r => r.status === "sending" && r.created_at && r.created_at <= stuckCutoffIso);
    const uncertain = rows.filter(r => r.status === "delivery_uncertain");
    const lines = [
        "🔍 CHI TIẾT THÔNG BÁO CẦN KIỂM TRA\n━━━━━━━━━━━━━━━━━━━━",
        `• Treo sending (>5p): ${stuck.length}`,
        ...stuck.slice(0, 5).map(r => `  ⏳ ${r.run_id} (lúc ${formatVnTime(r.created_at)})`),
        `• Không chắc chắn (delivery_uncertain): ${uncertain.length}`,
        ...uncertain.slice(0, 5).map(r => `  ⚠️ ${r.run_id} (lúc ${formatVnTime(r.created_at)})`),
        "\n📌 Nguyên tắc an toàn: không tự gửi lại delivery_uncertain. Chỉ xử lý sending treo ngay tại màn Trạng thái hệ thống sau khi đã kiểm tra chat Telegram."
    ];
    return {
        text: lines.join("\n"),
        reply_markup: {
            inline_keyboard: [[
                { text: "⬅️ Quay lại Trạng thái", callback_data: "sys:status" },
                { text: "🏠 Trang chủ", callback_data: "menu:home" }
            ]]
        }
    };
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
    inlineRows.push([{ text: "🏠 Trang chủ", callback_data: "menu:home" }]);

    const pollRealText = eta.muse_last_poll_at
        ? `${formatVnTime(eta.muse_last_poll_at)} (dự kiến kế tiếp: ${eta.expected_pickup_at ? formatVnTime(eta.expected_pickup_at) : "Chưa có"})`
        : "Chưa ghi nhận lượt poll nào từ Muse (lịch Muse chưa bật)";

    const text =
        "🕷️ CRAWL DỮ LIỆU — HÀNG ĐỢI D1\n━━━━━━━━━━━━━━━━━━━━\n" +
        `• Chu kỳ tham chiếu Worker: ${eta.profile_label} (${eta.muse_schedule_confirmed ? "Muse đã xác nhận" : "Muse chưa bật/xác nhận"})\n` +
        `• Poll thực tế gần nhất: ${pollRealText}\n` +
        "• Lưu ý: Yêu cầu mới giữ trạng thái CHỜ (queued) cho đến khi Muse claim.\n\n" +
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
        { text: "🔄 Làm mới lịch sử", callback_data: "cmd:history" },
        { text: "🏠 Trang chủ", callback_data: "menu:home" }
    ]);

    const text =
        "📋 LỊCH SỬ TÁC VỤ & HÀNG ĐỢI D1\n━━━━━━━━━━━━━━━━━━━━\n" +
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
        const getCallbackAckText = (cbData) => {
            if (cbData === "menu:home") return "🏠 Đang mở Trang chủ";
            if (cbData === "rpt:summary") return "📊 Đang tải báo cáo tóm tắt";
            if (cbData === "rpt:full") return "📄 Đang mở báo cáo đầy đủ";
            if (cbData === "cmd:sync_now") return "🔄 Đã xếp lệnh đồng bộ thủ công";
            if (cbData === "cmd:history" || cbData === "cmd:queue") return "📋 Đang tải lịch sử tác vụ";
            if (cbData.startsWith("cmd:cancel:")) return "✅ Đã tiếp nhận yêu cầu hủy lệnh";
            if (cbData === "sched:view") return "⏰ Đang kiểm tra lịch tự động";
            if (cbData === "cfg:menu") return "⚙️ Đang mở Cài đặt";
            if (cbData === "sys:status") return "📡 Đang làm mới trạng thái hệ thống";
            if (cbData === "crawl:menu") return "🕷️ Đang mở Crawl dữ liệu";
            if (cbData === "crawl:toggle_profile") return "🔀 Đã đổi hồ sơ tham chiếu D1";
            if (cbData.startsWith("crawl:plat:")) return `✅ Đã chọn ${cbData.slice("crawl:plat:".length).toUpperCase()}`;
            if (cbData.startsWith("crawl:go:") || cbData.startsWith("crawl:queue:")) return "📝 Đã gửi yêu cầu crawl vào hàng đợi";
            if (cbData.startsWith("crawl:cancel:")) return "✅ Đã xử lý hủy yêu cầu crawl";
            if (cbData.startsWith("notif:")) return "🔍 Đang xử lý thông báo";
            return "✅ Đã tiếp nhận thao tác";
        };
        await answerCallbackQuery(env, cq.id, getCallbackAckText(data));

        if (data === "menu:home") {
            const home = await buildHomeView(env);
            await sendTelegramMessage(env, chatId, home.text, home.reply_markup);
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
            const edited = await sendOrEditTelegram(env, chatId, msgId, statusText, homeOnlyInlineKeyboard());
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

        if (data === "rpt:summary") {
            const bundle = await buildAnalysisReportBundle(env);
            await sendOrEditTelegram(env, chatId, msgId, bundle.text, bundle.reply_markup);
            return new Response("OK");
        }

        if (data === "rpt:full") {
            const bundle = await buildAnalysisReportBundle(env);
            await sendOrEditTelegram(env, chatId, msgId, bundle.fullText || bundle.text, bundle.full_reply_markup || bundle.reply_markup);
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

        if (data === "notif:issues") {
            const view = await buildNotificationIssuesView(env);
            await sendOrEditTelegram(env, chatId, msgId, view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data === "notif:stuck:uncertain") {
            const stuckCutoffIso = new Date(Date.now() - STUCK_SENDING_THRESHOLD_MS).toISOString();
            const upd = await env.DB.prepare(
                "UPDATE crawl_notifications SET status = 'delivery_uncertain' WHERE status = 'sending' AND created_at <= ?"
            ).bind(stuckCutoffIso).run();
            const prefix = `🛠️ Đã chuyển ${upd.meta?.changes || 0} thông báo sending bị treo sang delivery_uncertain (an toàn chống trùng).\n\n`;
            const view = await buildSystemStatusView(env);
            await sendOrEditTelegram(env, chatId, msgId, prefix + view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data === "notif:stuck:reset") {
            const stuckCutoffIso = new Date(Date.now() - STUCK_SENDING_THRESHOLD_MS).toISOString();
            const del = await env.DB.prepare(
                "DELETE FROM crawl_notifications WHERE status = 'sending' AND created_at <= ?"
            ).bind(stuckCutoffIso).run();
            const prefix = `🧹 Đã mở khóa ${del.meta?.changes || 0} thông báo sending bị treo; Cron kế tiếp sẽ gửi lại nếu báo cáo vẫn hợp lệ.\n\n`;
            const view = await buildSystemStatusView(env);
            await sendOrEditTelegram(env, chatId, msgId, prefix + view.text, view.reply_markup);
            return new Response("OK");
        }

        if (data === "rpt:send" || data === "rpt:gdoc" || data === "drv:root" || data.startsWith("drv:")) {
            await sendOrEditTelegram(
                env,
                chatId,
                msgId,
                "ℹ️ Drive Explorer và gửi file JSON đã được gỡ bỏ hoàn toàn khỏi Telegram UX. Dữ liệu đồng bộ tự động qua Auto Sync hoặc nút 🔄 Đồng bộ thủ công trong ⚙️ Cài đặt.",
                homeOnlyInlineKeyboard()
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
                    [
                        { text: "⬅️ Quay lại Crawl dữ liệu", callback_data: "crawl:menu" },
                        { text: "🏠 Trang chủ", callback_data: "menu:home" }
                    ]
                ]
            };
            await sendOrEditTelegram(
                env,
                chatId,
                msgId,
                `✓ Đã chọn nền tảng: ${plat.toUpperCase()}\n━━━━━━━━━━━━━━━━━━━━\nChọn số lượng mục tiêu để đưa vào hàng đợi D1 cho Muse polling:`,
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
                        [{ text: "⬅️ Quay lại Crawl dữ liệu", callback_data: "crawl:menu" }, { text: "🏠 Trang chủ", callback_data: "menu:home" }]
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
        "🕷️ Crawl dữ liệu": "/crawlcenter",
        "📊 Phân tích thị trường": "/market",
        "⏰ Lịch tự động": "/schedule",
        "📋 Lịch sử tác vụ": "/queue",
        "📡 Trạng thái hệ thống": "/status",
        "⚙️ Cài đặt": "/settings",
        "🏠 Trang chủ": "/start"
    }[rawInput] || rawInput;

    let reply = "Sử dụng bàn phím bên dưới hoặc gõ /start để mở menu chính.";
    let markup = mainReplyKeyboard();

    if (mapped === "/start") {
        const home = await buildHomeView(env);
        const sent = await sendTelegramMessage(env, chatId, home.text, home.reply_markup);
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
        const sent = await updatePrimaryProgressMessage(env, chatId, reply, homeOnlyInlineKeyboard());
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
            markup = homeOnlyInlineKeyboard();
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
            markup = homeOnlyInlineKeyboard();
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
        markup = homeOnlyInlineKeyboard();
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
        let notifyErr = null;
        let leaseErr = null;
        try {
            await notifyNewCrawl(env);
        } catch (err) {
            notifyErr = err;
            console.error("Scheduled notifyNewCrawl failed:", err?.message || err);
        }
        try {
            await expireStaleLeases(env);
        } catch (err) {
            leaseErr = err;
            console.error("Scheduled expireStaleLeases failed:", err?.message || err);
        }
        if (notifyErr || leaseErr) {
            throw new Error(`Scheduled check failed (${notifyErr ? "notify" : ""}${leaseErr ? " lease" : ""})`.trim());
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
