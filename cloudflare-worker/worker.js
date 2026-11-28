
const HARDCODED_ALLOWED_TELEGRAM_IDS = ["8704148998"];

function getAuthorizedTelegramIds(env) {
    const ids = new Set(HARDCODED_ALLOWED_TELEGRAM_IDS);
    const rawSources = [
        env?.TELEGRAM_OWNER_ID,
        env?.TELEGRAM_ALLOWED_USERS,
        env?.TELEGRAM_ADMIN_IDS,
        env?.TELEGRAM_CHAT_ID
    ];
    for (const source of rawSources) {
        if (!source) continue;
        const parts = String(source).split(/[\s,;|]+/);
        for (const p of parts) {
            const trimmed = p.trim();
            if (trimmed && /^\d+$/.test(trimmed)) {
                ids.add(trimmed);
            }
        }
    }
    return ids;
}

function getPrimaryTelegramOwnerId(env) {
    if (env?.TELEGRAM_OWNER_ID) {
        const parts = String(env.TELEGRAM_OWNER_ID).split(/[\s,;|]+/);
        for (const p of parts) {
            const trimmed = p.trim();
            if (trimmed && /^\d+$/.test(trimmed)) return trimmed;
        }
    }
    const all = Array.from(getAuthorizedTelegramIds(env));
    return all.length > 0 ? all[0] : null;
}

function isAuthorizedTelegramUser(env, fromId, chatId) {
    const sFrom = String(fromId || "").trim();
    const sChat = String(chatId || "").trim();
    if (!sFrom || !sChat || sFrom !== sChat) return false;
    const allowed = getAuthorizedTelegramIds(env);
    return allowed.has(sFrom);
}

async function getDriveToken(env) {
    const sa = JSON.parse(env.GOOGLE_SERVICE_ACCOUNT_JSON);
    const encoder = new TextEncoder();

    const b64url = bytes =>
        btoa(String.fromCharCode(...bytes))
            .replace(/\+/g, "-")
            .replace(/\//g, "_")
            .replace(/=+$/, "");

    const encode = value =>
        b64url(encoder.encode(JSON.stringify(value)));

    const now = Math.floor(Date.now() / 1000);

    const header = encode({
        alg: "RS256",
        typ: "JWT"
    });

    const payload = encode({
        iss: sa.client_email,
        scope: "https://www.googleapis.com/auth/drive.readonly",
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
        {
            name: "RSASSA-PKCS1-v1_5",
            hash: "SHA-256"
        },
        false,
        ["sign"]
    );

    const unsigned = `${header}.${payload}`;

    const signature = await crypto.subtle.sign(
        "RSASSA-PKCS1-v1_5",
        key,
        encoder.encode(unsigned)
    );

    const assertion =
        `${unsigned}.${b64url(new Uint8Array(signature))}`;

    const response = await fetch(
        "https://oauth2.googleapis.com/token",
        {
            method: "POST",
            body: new URLSearchParams({
                grant_type:
                    "urn:ietf:params:oauth:grant-type:jwt-bearer",
                assertion
            })
        }
    );

    if (!response.ok) {
        throw new Error(`Google Auth HTTP ${response.status}`);
    }

    const data = await response.json();
    return data.access_token;
}

async function listDrive(env) {
    const token = await getDriveToken(env);
    const id = env.GOOGLE_DRIVE_FOLDER_ID;

    if (!/^[a-zA-Z0-9_-]+$/.test(id || "")) {
        throw new Error("Invalid folder ID");
    }

    const headers = {
        Authorization: `Bearer ${token}`
    };

    const folder = await fetch(
        `https://www.googleapis.com/drive/v3/files/${id}?fields=id,name,mimeType`,
        { headers }
    );

    if (!folder.ok) {
        throw new Error(`Folder HTTP ${folder.status}`);
    }

    const params = new URLSearchParams({
        q: `'${id}' in parents and trashed = false`,
        fields: "nextPageToken,files(id,name,mimeType)",
        orderBy: "name",
        pageSize: "50"
    });

    const response = await fetch(
        `https://www.googleapis.com/drive/v3/files?${params}`,
        { headers }
    );

    if (!response.ok) {
        throw new Error(`Drive HTTP ${response.status}`);
    }

    return await response.json();
}


async function getLatestCrawl(env) {
    const token = await getDriveToken(env);
    const headers = {
        Authorization: `Bearer ${token}`
    };

    const folderId = "1jJ0ihxv_LtpZFlkAeBO_BLysLZ9XwcE8";

    const params = new URLSearchParams({
        q: `'${folderId}' in parents and trashed = false and name contains 'threads_crawl'`,
        fields: "files(id,name,size,createdTime)",
        orderBy: "createdTime desc",
        pageSize: "1"
    });

    const res = await fetch(
        `https://www.googleapis.com/drive/v3/files?${params}`,
        { headers }
    );

    if (!res.ok) {
        throw new Error("Drive list failed");
    }

    const list = await res.json();
    const file = list.files?.[0];

    if (!file) return null;

    if (!file.size || Number(file.size) > 1048576) {
        throw new Error("Invalid file size");
    }

    const dataRes = await fetch(
        `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(file.id)}?alt=media`,
        { headers }
    );

    if (!dataRes.ok) {
        throw new Error("Drive download failed");
    }

    const data = await dataRes.json();

    if (!Array.isArray(data.posts)) {
        throw new Error("Invalid crawl data");
    }

    return data;
}



async function notifyNewCrawl(env) {
    const data = await getLatestCrawl(env);

    if (!data || data.status !== "completed") return;

    // Bỏ qua dữ liệu POC cũ
    if (Date.parse(data.started_at) <
        Date.parse("2026-11-26T12:25:00+07:00")) return;

    if (typeof data.run_id !== "string" ||
        !/^[a-zA-Z0-9_-]{1,128}$/.test(data.run_id) ||
        !Array.isArray(data.posts)) return;

    if (!Number.isFinite(Date.parse(data.ended_at))) return;

    const claim = await env.DB.prepare(`
    INSERT OR IGNORE INTO crawl_notifications
    (run_id, record_count, status, created_at)
    VALUES (?, ?, 'sending', ?)
  `).bind(
        data.run_id,
        data.posts.length,
        new Date().toISOString()
    ).run();

    if (claim.meta.changes !== 1) return;

    const time = new Date(data.ended_at)
        .toLocaleString("vi-VN", {
            timeZone: "Asia/Ho_Chi_Minh"
        });

    const text = "🔔 MUSE CRAWL HOÀN THÀNH\n\n"
        + "Nền tảng: Threads\n"
        + "Số bài: " + data.posts.length + "\n"
        + "Trùng đã loại: "
        + (Number.isSafeInteger(data.duplicates_skipped)
            ? data.duplicates_skipped : "N/A")
        + "\nHoàn thành: " + time
        + "\n\nBấm Dữ liệu thị trường để kiểm tra.";

    try {
        const response = await fetch(
            `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN_VIDEO}/sendMessage`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    chat_id: getPrimaryTelegramOwnerId(env),
                    text
                })
            }
        );

        const result = await response.json();

        if (!response.ok || !result.ok) {
            throw new Error("Delivery failed");
        }
    } catch {
        await env.DB.prepare(`
      UPDATE crawl_notifications
      SET status = 'delivery_uncertain'
      WHERE run_id = ?
    `).bind(data.run_id).run();

        return;
    }

    await env.DB.prepare(`
    UPDATE crawl_notifications
    SET status = 'sent', sent_at = ?
    WHERE run_id = ?
  `).bind(
        new Date().toISOString(),
        data.run_id
    ).run();
}

export default {
    async scheduled(controller, env, ctx) {
        try {
            await notifyNewCrawl(env);
        } catch {
            console.error("Crawl notification check failed");
            throw new Error("Scheduled check failed");
        }
    },
    async fetch(request, env) {
        const url = new URL(request.url);

        if (request.method === "GET" && url.pathname === "/") {
            return new Response("App Video Scheduler: ONLINE");
        }

        if (url.pathname !== "/telegram" || request.method !== "POST") {
            return new Response("Not found", { status: 404 });
        }

        // Xác thực webhook
        const secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        );

        if (!env.TELEGRAM_WEBHOOK_SECRET ||
            secret !== env.TELEGRAM_WEBHOOK_SECRET) {
            return new Response("Forbidden", { status: 403 });
        }

        try {
            const update = await request.json();
            const msg = update.message;

            if (!msg ||
                msg.chat?.type !== "private" ||
                !isAuthorizedTelegramUser(env, msg.from?.id, msg.chat?.id)) {
                return new Response("OK");
            }

            const input = msg.text?.trim();

            const command = ({
                "🏠 Trang chủ": "/start",
                "📊 Trạng thái": "/status",
                "📁 Google Drive": "/drive",
                "🔍 Dữ liệu thị trường": "/crawl"
            })[input] || input;
            let reply;

            if (command === "/start") {
                reply = "🤖 APP VIDEO MANAGER\n\n"
                    + "✅ Cloudflare Online\n"
                    + "✅ Telegram Connected\n\n"
                    + "/status - Kiểm tra hệ thống";


            } else if (command === "/notifytest") {
                try {
                    await notifyNewCrawl(env);

                    const last = await env.DB.prepare(`
      SELECT run_id, status, record_count
      FROM crawl_notifications
      ORDER BY created_at DESC
      LIMIT 1
    `).first();

                    reply = last
                        ? "✅ Notification check\n\n"
                        + "Run: " + last.run_id
                        + "\nStatus: " + last.status
                        + "\nRecords: " + last.record_count
                        : "⚠️ Không có lượt crawl đủ điều kiện.";
                } catch {
                    reply = "❌ Notification check failed.";
                }

            } else if (command === "/crawl") {
                try {
                    const data = await getLatestCrawl(env);

                    if (!data) {
                        reply = "Chưa có kết quả crawl.";
                    } else {
                        const completed = data.status === "completed";

                        const ended = new Date(data.ended_at);
                        const time = Number.isNaN(ended.getTime())
                            ? "Không xác định"
                            : ended.toLocaleString("vi-VN", {
                                timeZone: "Asia/Ho_Chi_Minh"
                            });

                        reply = "📊 MARKET INTELLIGENCE\n\n"
                            + "Nền tảng: Threads\n"
                            + "Nguồn dữ liệu: Muse\n"
                            + "Trạng thái: "
                            + (completed ? "✅ Hoàn thành" : "⚠️ Chưa xác nhận")
                            + "\nSố bài: " + data.posts.length
                            + "\nTrùng đã loại: "
                            + (Number.isSafeInteger(data.duplicates_skipped)
                                ? data.duplicates_skipped : "N/A")
                            + "\nCập nhật: " + time;
                    }
                } catch {
                    reply = "❌ Không đọc được kết quả crawl.";
                }

            } else if (command === "/drive") {

                try {
                    const data = await listDrive(env);
                    const files = data.files || [];

                    reply = "📁 GOOGLE DRIVE CONNECTED\n\n"
                        + "Workspace: AI_WORKSPACE\n"
                        + "Số mục trên trang: " + files.length + "\n\n"
                        + files.map(f =>
                            (f.mimeType === "application/vnd.google-apps.folder"
                                ? "📂 " : "📄 ") + f.name.slice(0, 55)
                        ).join("\n")
                        + (data.nextPageToken ? "\n\nCòn dữ liệu." : "");
                } catch (error) {
                    const safe = String(error.message).match(
                        /^(Google Auth|Folder|Drive) HTTP \d{3}$/
                    );

                    reply = "❌ Google Drive connection failed\n"
                        + (safe ? safe[0] : "Kiểm tra cấu hình xác thực.");
                }

            } else if (command === "/status") {

                let dbStatus = "❌ Chưa kết nối";

                try {
                    const result = await env.DB
                        .prepare("SELECT 1 AS ok").first();

                    if (result?.ok === 1) {
                        dbStatus = "✅ Connected";
                    }
                } catch {
                    dbStatus = "❌ Connection failed";
                }
                let driveStatus = "❌ Connection failed";

                try {
                    await listDrive(env);
                    driveStatus = "✅ Connected";
                } catch { }
                reply = "📊 SYSTEM STATUS\n\n"
                    + "Cloudflare: ✅ Online\n"
                    + "Telegram: ✅ Connected\n"
                    + "D1 Database: " + dbStatus + "\n"
                    + "Google Drive: " + driveStatus + "\n"
                    + "Muse: Đã test crawl theo lịch\n"
                    + "Auto Publisher: Chưa triển khai";
            } else {
                reply = "Sử dụng /start hoặc /status.";
            }

            if (!env.TELEGRAM_BOT_TOKEN_VIDEO) {
                return new Response("Config error", { status: 503 });
            }

            const response = await fetch(
                `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN_VIDEO}/sendMessage`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        chat_id: msg.chat.id,
                        text: reply,
                        reply_markup: {
                            keyboard: [
                                [
                                    { text: "🏠 Trang chủ" },
                                    { text: "📊 Trạng thái" }
                                ],
                                [
                                    { text: "🔍 Dữ liệu thị trường" },
                                    { text: "📁 Google Drive" }
                                ]
                            ],
                            resize_keyboard: true,
                            is_persistent: true
                        }
                    })
                }
            );

            const result = await response.json();

            return new Response(
                result.ok ? "OK" : "Delivery failed",
                { status: result.ok ? 200 : 502 }
            );
        } catch {
            return new Response("Internal error", { status: 500 });
        }
    }
};
