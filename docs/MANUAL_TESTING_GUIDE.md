# End-to-End Manual Testing Guide (Real Developer Credentials)

This checklist walks you through validating the complete production flow of the **Social Media Auto Publishing Module** using real developer accounts on Meta and Google.

---

## Prerequisites
1. Start infrastructure via Docker Compose:
   ```bash
   docker-compose up -d
   ```
2. Verify all 3 core services are healthy:
   - PostgreSQL: `localhost:5432`
   - Redis: `localhost:6379`
   - MinIO Console: `http://localhost:9001` (user: `minioadmin`, pass: `minioadmin_secret`)
3. Run Prisma database migrations:
   ```bash
   cd server
   npx prisma migrate dev --name init
   ```
4. Start Backend API and Frontend App:
   ```bash
   # Terminal 1: Backend
   cd server
   npm run start:dev

   # Terminal 2: Frontend
   cd client
   npm run dev
   ```
5. Open browser at `http://localhost:5173`.

---

## Test Scenarios

### Scenario 1: Connect a Real Facebook Page
- [ ] Navigate to **Connected Accounts** (`/accounts`).
- [ ] Click **Connect Facebook Page**.
- [ ] The browser should redirect to `https://www.facebook.com/v21.0/dialog/oauth` with your `META_APP_ID`.
- [ ] Authenticate with your Facebook account and select at least one Page you administer.
- [ ] Grant requested permissions (`pages_show_list`, `pages_manage_posts`, `pages_read_engagement`).
- [ ] You are redirected back to `http://localhost:5173/accounts?status=success&platform=facebook`.
- [ ] Verify that your Facebook Page name, avatar, and Page ID appear in the Connected Accounts list with status `CONNECTED`.
- [ ] In PostgreSQL `social_accounts` table, verify `accessTokenEncrypted` is populated with an `iv:authTag:ciphertext` string (never plain text).

---

### Scenario 2: Connect a Real YouTube Channel
- [ ] From **Connected Accounts**, click **Connect YouTube Channel**.
- [ ] The browser should redirect to Google OAuth consent screen (`accounts.google.com`).
- [ ] Choose the Google account owning the target YouTube Channel.
- [ ] Review scopes (`YouTube video upload`, `view account`). Click **Continue**.
- [ ] You are redirected back to `http://localhost:5173/accounts?status=success&platform=youtube`.
- [ ] Verify that the YouTube Channel title, avatar, subscriber count, and Channel ID appear in the Connected Accounts list.
- [ ] In PostgreSQL `social_accounts` table, verify both `accessTokenEncrypted` and `refreshTokenEncrypted` are securely encrypted at rest.

---

### Scenario 3: Upload Media Assets (MinIO / S3)
- [ ] Go to **Media Library** (`/media`) or open the file uploader on **Create Post** (`/create`).
- [ ] Upload an image (`.jpg` or `.png`). Verify it previews immediately.
- [ ] Upload an MP4 video (`.mp4`).
- [ ] Inspect MinIO web console at `http://localhost:9001`:
  - Verify file exists under bucket `social-media-assets/media/{userId}/...`.
  - Verify size, content-type, and ETag match the uploaded file.

---

### Scenario 4: Publish a Real Text & Image Post to Facebook
- [ ] Go to **Create Post** (`/create`).
- [ ] Select **Facebook Pages** only.
- [ ] Enter a caption: `Testing AutoPub automated publishing system! 🚀 #automation #tech`.
- [ ] Attach an uploaded image.
- [ ] Look at the live **Facebook Preview** on the right side. Verify text and image render cleanly.
- [ ] Click **Publish Now**.
- [ ] Navigate to **Publishing History** (`/history`).
- [ ] Verify the task transitions to `PUBLISHED` with an `externalPostUrl`.
- [ ] Click **Open Post**: the real Facebook post should open in a new tab on Facebook!

---

### Scenario 5: Upload a Real YouTube Video (and Shorts Detection)
- [ ] Go to **Create Post** (`/create`).
- [ ] Select **YouTube Channels** only.
- [ ] Enter a Title: `My Amazing Vertical Video #Shorts`.
- [ ] Enter a Description: `Testing official YouTube Data API v3 integration.`
- [ ] Attach an MP4 video file.
- [ ] If vertical (9:16) and <= 60 seconds, notice `#Shorts` is automatically detected.
- [ ] Click **Publish Now**.
- [ ] Go to **Publishing History** (`/history`).
- [ ] Verify the task is processed by the background worker and marked `PUBLISHED`.
- [ ] Click **Open Post**: verify the video is live or processing in YouTube Studio / Watch page.

---

### Scenario 6: Multi-Platform Campaign & Independent Retries
- [ ] Create a post targeting **BOTH Facebook AND YouTube**.
- [ ] To simulate an isolated failure, temporarily input an invalid YouTube title or revoke YouTube permissions.
- [ ] Trigger **Publish Now**.
- [ ] Observe:
  - Facebook publishing succeeds and task status is `PUBLISHED`.
  - YouTube publishing fails and task status is `FAILED`.
  - The parent Content status becomes `PARTIALLY_PUBLISHED`.
- [ ] Fix the YouTube error (or re-authenticate).
- [ ] In **Publishing History**, click **Retry** on the failed YouTube task.
- [ ] Verify YouTube runs independently and publishes successfully.
- [ ] Verify **Facebook was NEVER re-published** (preventing duplicate posts!).

---

### Scenario 7: Scheduled Post Survival Across Server Restarts
- [ ] Schedule a post 10 minutes into the future.
- [ ] Verify the task appears under **Publishing Queue** with status `PENDING`.
- [ ] Stop the backend process: `Ctrl+C`.
- [ ] Restart the backend: `npm run start:dev`.
- [ ] Observe BullMQ and Redis reconnecting.
- [ ] Wait until the scheduled timestamp arrives:
- [ ] The background worker picks up the job from Redis and executes publication automatically.
