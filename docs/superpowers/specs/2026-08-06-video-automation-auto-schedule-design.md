# Video Automation Auto Schedule — Design Spec

**Version:** 2.2 (design)  
**Date:** 2026-08-06  
**Status:** Approved for implementation planning  
**Stack:** Odoo module + Cloudflare R2 + TikTok Content Posting API (Sandbox)

## 1. Goal

Cloud-first video pipeline: Odoo owns metadata, workflow, scheduling, and automation. Media lives on Cloudflare R2. Each day the system auto-selects eligible videos (FIFO), builds a publish queue from schedule rules, and posts to TikTok via official API (`PULL_FROM_URL` from CDN).

Users only: upload/generate videos, connect TikTok accounts (OAuth), configure videos-per-day slots and caption template. The system selects, schedules, publishes, tracks, and retries.

## 2. Architecture

**Approach:** Odoo-all-in MVP — one module `video_automation`. Cron jobs handle schedule creation, publish, token refresh, and retry. FFmpeg runs on the Odoo server for generate/encode. Split to external workers later if encode load grows.

```
[User Odoo UI]
     │
     ▼
┌─────────────────────────────────────────┐
│  Odoo Module video_automation           │
│  Library / Job / Schedule / Queue       │
│  Cron: schedule · publish · token · retry│
│  FFmpeg (generate/encode)               │
└────────────┬───────────────┬────────────┘
             │               │
             ▼               ▼
      Cloudflare R2     TikTok API
      (video/audio)     (Sandbox OAuth
       + CDN URL         + PULL_FROM_URL)
```

**Principles:**
- Odoo never stores video/audio binaries; only path, URL, metadata, state.
- TikTok pulls media from verified CDN URLs; Odoo does not stream large binaries to TikTok.
- Each TikTok account is an OAuth-connected target user (Sandbox).
- Auto Schedule creates queue records; a separate cron publishes when due.

## 3. Module structure

```
video_automation
├── Configuration / TikTok App
├── Cloud Storage (R2)
├── Video Library
├── Audio Library
├── Generate Video (video.job)
├── TikTok Account (OAuth)
├── TikTok Schedule Rule
├── TikTok Publish Queue
├── Upload History
└── Scheduler (crons)
```

## 4. Storage (Cloudflare R2)

### Purpose
Store originals, generated videos, audio, thumbnails, previews. Odoo stores path + CDN URL + metadata only.

### Buckets (example)
- Video: `study5-video` → `videos/YYYY/MM/original/…`, `videos/YYYY/MM/generated/…`
- Audio: `study5-audio` → `audio/…`

### Model: `video.storage`
| Field | Description |
|-------|-------------|
| name | Storage name |
| account_id | Cloudflare account |
| bucket_name | R2 bucket |
| access_key_id | Access key |
| secret_key | Secret (admin-only, never logged) |
| endpoint | R2 endpoint |
| cdn_domain | CDN domain (must be HTTPS; verify on TikTok URL properties) |
| active | Active |

## 5. Models

### 5.1 `tiktok.app`
App credentials for Login Kit / Content Posting API.

| Field | Description |
|-------|-------------|
| name | App name |
| client_key | TikTok client key |
| client_secret | Client secret (admin-only) |
| redirect_uri | OAuth callback URL |
| environment | `sandbox` \| `production` |
| active | Active |

**Sandbox notes:** Up to ~5 sandboxes per app; ~10 target users; posts from unaudited apps are typically forced private. MVP expects private test posts, not public production reach.

### 5.2 `video.library`
Central video record.

| Field | Description |
|-------|-------------|
| name | Video name |
| filename | File name |
| storage_id | R2 storage |
| storage_path | Object path |
| cdn_url | CDN URL (used for TikTok PULL_FROM_URL) |
| thumbnail_url | Thumbnail |
| duration, width, height, fps, bitrate, file_size | Media metadata |
| state | See state machine |
| scheduled_count | Times scheduled |
| published_count | Times published successfully |
| allow_republish | Per-video gate: if False, never select again for any account after a success |

**State machine:**
```
draft → uploaded → processing → available
```
- Generate job success → set `available` automatically (no manual approve).
- `scheduled_count` / `published_count` are counters only; publishing does **not** remove the video from the pool by changing state to a terminal “published” state.
- Optional later: `archived` for soft-removal from selection.

### 5.3 `audio.library`
| Field | Description |
|-------|-------------|
| name | Name |
| storage_id | R2 storage |
| storage_path | Path |
| cdn_url | CDN URL |
| duration, file_size | Metadata |
| active | Active |

### 5.4 `video.job`
| Field | Description |
|-------|-------------|
| name | Job name |
| job_type | `upload_video` \| `extract_audio` \| `generate_video` \| `encode_video` |
| video_id | Related video |
| state | `pending` \| `running` \| `success` \| `failed` |
| progress | 0–100 |
| retry_count | Retry |
| error_message | Error text |

### 5.5 `tiktok.account`
OAuth-connected account (no manual-username-only upload).

| Field | Description |
|-------|-------------|
| name | Display name |
| tiktok_app_id | App used for OAuth |
| username | Display only (not used for API upload) |
| profile_url | Optional |
| open_id | TikTok open_id after OAuth |
| access_token | Access token |
| refresh_token | Refresh token |
| token_expires_at | Access token expiry |
| scopes | Granted scopes |
| auth_state | `disconnected` \| `connected` \| `expired` |
| timezone | e.g. `Asia/Ho_Chi_Minh` |
| active | Active |

### 5.6 `tiktok.schedule.rule`
| Field | Description |
|-------|-------------|
| name | Rule name |
| tiktok_account_id | Target account |
| upload_times | List of daily post times (defines slot count) |
| caption_template | Caption/hashtag template (MVP source of caption) |
| allow_republish | Whether already-published videos on this account may be selected again |
| timezone | Scheduling timezone (fallback: account timezone) |
| active | Active |

**Slot count:** Number of posts per day = `len(upload_times)`. Do not keep a separate conflicting `videos_per_day` unless it only validates equality with `upload_times`.

**Caption:** Snapshot from `caption_template` when creating queue rows. MVP placeholders: `{video_name}`, `{date}` (optional); fixed text + hashtags is enough.

**Out of MVP:** complex `video_filter`, priority, random selection.

### 5.7 `tiktok.publish.queue`
Auto-created only (not manual in MVP).

| Field | Description |
|-------|-------------|
| video_id | Selected video |
| tiktok_account_id | Account |
| schedule_rule_id | Source rule |
| scheduled_time | Datetime to publish |
| schedule_date | Date part for uniqueness |
| slot_time | Time-of-day slot |
| caption | Snapshot from template |
| privacy_level | Sandbox: private / API-required value |
| state | `pending` \| `uploading` \| `success` \| `failed` |
| retry_count | Retry |
| error_message | Error |
| tiktok_publish_id | External id when success |
| share_url | Optional |

**Uniqueness:** `(tiktok_account_id, schedule_date, slot_time)` — cron re-runs must not duplicate slots.

**Queue state:**
```
pending → uploading → success
                    ↘ failed  (retry_count < 3 → back to pending)
```

### 5.8 `tiktok.upload.history`
| Field | Description |
|-------|-------------|
| video_id | Video |
| tiktok_account_id | Account |
| publish_queue_id | Source queue row |
| upload_time | Time |
| status | Status |
| response | Sanitized API log (no tokens) |

## 6. Flows

### 6.1 Upload
1. User uploads file in Odoo.
2. Stream to R2 under `videos/YYYY/MM/original/…`.
3. Extract metadata (duration, resolution, fps, bitrate, size).
4. Create/update `video.library` → `uploaded`.

### 6.2 Generate
1. Create `video.job` type `generate_video`.
2. Download video + audio via CDN (or R2 API).
3. FFmpeg: merge audio, insert logo, encode.
4. Upload result to R2 `generated/`.
5. Update `cdn_url`, metadata; set state **`available`** automatically.

### 6.3 TikTok OAuth (Sandbox)
1. Admin clicks Connect on `tiktok.account`.
2. Redirect to TikTok authorization (Login Kit).
3. Callback exchanges code → store `open_id`, tokens, expiry, scopes.
4. Set `auth_state = connected`.
5. Hourly cron refreshes access token before expiry (~24h).

### 6.4 Auto Schedule (daily ~00:05)
For each active rule:
1. Resolve timezone; build today’s slots from `upload_times`.
2. Skip slots that already exist (idempotent).
3. Select videos FIFO (see §7).
4. Create `tiktok.publish.queue` rows with caption snapshot.
5. If fewer videos than slots: create partial queue + warning (activity/log). Do not abort the whole day.

### 6.5 Publish (every ~5 minutes)
1. Find queue: `state = pending` AND `scheduled_time <= now`.
2. Ensure account token valid (refresh if needed).
3. Set `uploading`; call Content Posting API with `PULL_FROM_URL` using `video.cdn_url`.
4. On success: `success`, write history, increment `published_count` (and related counters).
5. On failure: `failed`, store error, rely on retry cron.

### 6.6 Retry (hourly)
- `failed` with `retry_count < 3` → increment retry, set `pending` (optionally delay a few minutes).
- At max retry: leave `failed`, notify via activity/message.

## 7. Video selection (FIFO)

Eligible if:
1. `state = available`
2. No queue row for this **account + video** in `pending` or `uploading`
3. If `video.allow_republish = False` and the video has any `success` (any account): exclude
4. If rule `allow_republish = False` and the video has `success` **for this account**: exclude
5. Otherwise (rule allows republish and video allows republish): may select again after prior success

Both gates apply (AND). Rule controls per-account reuse; video flag is a hard global stop when False.

Order: `create_date ASC` (oldest available first).

Insufficient pool: create as many queue rows as videos available; log warning.

## 8. Cron jobs

| Job | Schedule | Action |
|-----|----------|--------|
| Auto create publish queue | Daily 00:05 | §6.4 |
| Publish TikTok | Every 5 minutes | §6.5 |
| Refresh OAuth token | Every hour | Refresh before expiry |
| Retry failed | Every hour | §6.6 |

## 9. Security

- Restrict `secret_key`, `client_secret`, tokens to admin groups; never show in normal forms/logs.
- Sanitize TikTok API responses stored in history (strip tokens).
- CDN domain must be verified in TikTok Developer Portal URL properties for `PULL_FROM_URL`.

## 10. Error handling

| Case | Behavior |
|------|----------|
| Token expired | Refresh before publish; if refresh fails → `auth_state = expired`, queue stays pending/failed with clear message |
| CDN / URL not verified | Fail with explicit error pointing to URL properties |
| FFmpeg / generate failure | Job `failed` + `error_message` |
| TikTok API transient error | Queue `failed` → retry up to 3 |
| Partial daily fill | Warning only; do not fail other slots |

## 11. MVP scope

**In:**
- R2 + video/audio library
- Upload + generate jobs (FFmpeg on Odoo host)
- `tiktok.app` + OAuth accounts (Sandbox)
- Schedule rule: `upload_times`, caption template, `allow_republish`, timezone
- FIFO auto queue + partial fill + warning
- Publish via PULL_FROM_URL + history + max 3 retries
- Token refresh cron

**Out:**
- Complex `video_filter`, priority, random strategies
- Production public posting (after TikTok app audit)
- Separate worker microservice
- AI caption / multi-template engines

## 12. Minimum test plan

1. Upload → correct R2 path + metadata on `video.library`.
2. Generate → `available` + playable CDN URL.
3. OAuth Sandbox connect + token refresh cron.
4. Daily cron creates N slots; second run same day creates zero duplicates.
5. Publish private post via PULL_FROM_URL + history row.
6. Pool smaller than slots → partial queue + warning.
7. Forced publish failure → retries until max 3, then stays failed.
8. `allow_republish = False` excludes already-successful videos for that account.

## 13. Decisions log

| Topic | Decision |
|-------|----------|
| TikTok auth | Official OAuth + Sandbox (not username-only / unofficial automation) |
| Caption | Template on `tiktok.schedule.rule` |
| Selection | FIFO by `create_date` |
| Short pool | Partial fill + warning |
| After generate | Auto `available` |
| Runtime | Odoo-all-in MVP |
| Transfer | `PULL_FROM_URL` from R2 CDN |

## 14. Open for implementation plan (non-blocking)

- Exact Odoo version target (e.g. 16/17/18) — confirm at planning start.
- Logo asset source for generate (static config vs per-video).
- Whether `queue_job` (OCA) is available or use native cron + transient jobs only.
