# Video Automation

Odoo 17 module: Cloudflare R2 storage, FFmpeg generate, TikTok Sandbox OAuth + auto schedule + PULL_FROM_URL publish.

## Quick start

```bash
copy .env.example .env
docker compose up -d --build
```

Open http://localhost:8069

1. Create database (master password: `admin` from `config/odoo.conf`)
2. Apps → Update Apps List → install **Video Automation**
3. Add admin user to group **Video Automation / Manager** if needed

### Install module via CLI (optional)

```bash
docker compose exec odoo odoo -d YOUR_DB -i video_automation --stop-after-init
docker compose restart odoo
```

## Configure

1. **Configuration → R2 Storage** — endpoint, keys, bucket, CDN domain  
2. **Configuration → TikTok Apps** — Sandbox `client_key` / `client_secret`, redirect  
   `http://localhost:8069/tiktok/oauth/callback`  
3. **TikTok → Accounts** → Connect TikTok (OAuth)  
4. **TikTok → Schedule Rules** — upload times + caption template  
5. Create **Video Library** + **Video Job** (upload / generate)

## Crons

| Job | Interval |
|-----|----------|
| Auto Create Publish Queue | daily |
| Publish TikTok Due | 5 minutes |
| Retry Failed Publish | hourly |
| Refresh TikTok Tokens | hourly |

## Notes

- Sandbox posts are typically private (`SELF_ONLY`).
- CDN domain must be verified in TikTok Developer Portal for `PULL_FROM_URL`.
- Design spec: `docs/superpowers/specs/2026-08-06-video-automation-auto-schedule-design.md`
