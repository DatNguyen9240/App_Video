# Platform Approval, API Quotas & Restrictions Guide

This document details the official API limits, permission reviews, and platform constraints required for deploying the **Social Media Auto Publishing Module** to production.

---

## 1. Meta Graph API (Facebook Pages & Instagram)

### API Versions & Lifecycles
- Current Version: `v21.0` (Active).
- Endpoints used:
  - `POST /{page_id}/feed`: Text-only posts to page timeline.
  - `POST /{page_id}/photos`: Image posts with caption.
  - `POST /{page_id}/videos`: Long-form and standard landscape video posts.
  - `POST /{page_id}/video_reels`: Short-form vertical Reels API (Init session, binary upload, finish).

### Token Lifecycle & Expiration
- **Short-Lived User Access Token**: Generated during authorization code flow (`~1-2 hours validity`).
- **Long-Lived User Access Token**: Exchanged using `grant_type=fb_exchange_token` (`60 days validity`).
- **Page Access Token**: When queried via `GET /me/accounts` with a Long-Lived User Access Token, the resulting **Page Access Token has no expiration date (Never Expire)** unless the user changes their password, removes the app, or revokes admin permissions on the Page.

### Meta App Review Requirements
For apps in **Development Mode**, any administrator, developer, or tester registered in the Meta App can test all endpoints immediately without review.

To publish posts for general third-party users in **Live Mode**, the app must pass Meta App Review for:
1. `pages_show_list`: Allows the app to retrieve Page IDs and names.
2. `pages_manage_posts`: Allows creating, publishing, and deleting posts on behalf of the Page.
3. `pages_read_engagement`: Allows reading post engagement data and metrics.

### Meta Rate Limits
- Page-level rate limiting: 200 calls per hour per user of your app.
- Graph API utilizes `X-Business-Use-Case-Usage` headers to monitor real-time rate usage.

---

## 2. Google YouTube Data API v3

### YouTube Quota Cost Structure
Google Cloud allocates a default quota of **10,000 units per day** per project (resets at midnight Pacific Time).

| Method | Quota Cost per Call | Daily Limit with 10k Default Quota |
|---|---|---|
| `videos.insert` (Upload video) | **1,600 units** | ~6 video uploads per day |
| `channels.list` (Read channel info) | **1 unit** | 10,000 queries per day |
| `videos.list` (Read video details) | **1 unit** | 10,000 queries per day |

### Requesting Quota Extensions
If your application publishes more than 6 videos per day across all users:
1. Go to **Google Cloud Console** > **APIs & Services** > **Enabled APIs & services** > **YouTube Data API v3**.
2. Click **Quotas & System Limits**.
3. Click the edit pencil on **Queries per day** and request an increase (typical tier 1 extension is 100,000 or 1,000,000 units/day).

### Google OAuth Verification Requirements
- While in **Testing** status: Up to 100 test users can authorize the app. A warning screen ("Google hasn't verified this app") will show unless test users are whitelisted.
- For public production: Submit your app for **Google Verification** via the OAuth consent screen.
  - Requires: Privacy Policy URL, Terms of Service URL, domain verification via Google Search Console, and a YouTube video demonstration showing how the app requests tokens and uploads a video.

### YouTube Shorts Eligibility Rules
A video uploaded to YouTube is automatically categorized as a Short if:
1. Duration is **60 seconds or less**.
2. Aspect ratio is **vertical (9:16)** or square (1:1).
3. The video title or description contains `#Shorts`.

### Scheduled Publishing Requirement
Under YouTube Data API v3:
- Setting `status.publishAt` requires `status.privacyStatus` to be set to `'private'`. Setting `privacyStatus` to `'public'` with `publishAt` will result in an API validation error. The YouTube adapter handles this requirement automatically.
