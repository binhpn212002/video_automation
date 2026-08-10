import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.tiktok_client import TikTokClient

_logger = logging.getLogger(__name__)


class TikTokPublishQueue(models.Model):
    _name = "tiktok.publish.queue"
    _description = "TikTok Publish Queue"
    _order = "scheduled_time asc, id asc"
    _inherit = ["mail.thread"]

    video_id = fields.Many2one("video.library", required=True, ondelete="restrict")
    tiktok_account_id = fields.Many2one(
        "tiktok.account", required=True, ondelete="cascade"
    )
    schedule_rule_id = fields.Many2one("tiktok.schedule.rule", ondelete="set null")
    scheduled_time = fields.Datetime(required=True, index=True)
    schedule_date = fields.Date(required=True, index=True)
    slot_time = fields.Char(required=True, help="HH:MM slot key")
    caption = fields.Text(
        help="Caption lưu trên queue (user edit khi post từ Inbox TikTok)."
    )
    privacy_level = fields.Selection(
        [
            ("SELF_ONLY", "Private (Self Only)"),
            ("MUTUAL_FOLLOW_FRIENDS", "Friends"),
            ("FOLLOWER_OF_CREATOR", "Followers"),
            ("PUBLIC_TO_EVERYONE", "Public"),
        ],
        default="SELF_ONLY",
        required=True,
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("uploading", "Uploading"),
            ("success", "In TikTok Inbox"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
        tracking=True,
        index=True,
        help="success = video đã vào TikTok Inbox (draft); user mở app → Edit → Post.",
    )
    retry_count = fields.Integer(default=0)
    error_message = fields.Text()
    tiktok_publish_id = fields.Char(string="TikTok publish_id")
    share_url = fields.Char()

    _sql_constraints = [
        (
            "uniq_account_date_slot",
            "unique(tiktok_account_id, schedule_date, slot_time)",
            "A publish slot already exists for this account/date/time.",
        )
    ]

    @api.model
    def cron_auto_create_queue(self):
        rules = self.env["tiktok.schedule.rule"].search([("active", "=", True)])
        for rule in rules:
            try:
                self._create_queue_for_rule(rule)
            except Exception:
                _logger.exception("Auto schedule failed for rule %s", rule.id)

    @api.model
    def _create_queue_for_rule(self, rule, target_date=None):
        account = rule.tiktok_account_id
        if not account.active:
            return
        tz_name = rule._resolved_timezone()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
            tz_name = "UTC"

        now_local = datetime.now(tz)
        schedule_date = target_date or now_local.date()
        slots = sorted(rule.upload_time_ids, key=lambda r: (r.hour, r.minute))
        if not slots:
            return

        existing = self.search(
            [
                ("tiktok_account_id", "=", account.id),
                ("schedule_date", "=", schedule_date),
            ]
        )
        existing_slots = set(existing.mapped("slot_time"))
        missing = [s for s in slots if f"{s.hour:02d}:{s.minute:02d}" not in existing_slots]
        if not missing:
            return

        videos = self.env["video.library"]._fifo_candidates(
            account, rule.allow_republish, limit=len(missing)
        )
        if len(videos) < len(missing):
            rule.message_post(
                body=(
                    f"Partial schedule for {schedule_date}: "
                    f"needed {len(missing)} videos, found {len(videos)}."
                )
            )

        created = 0
        for slot, video in zip(missing, videos):
            slot_key = f"{slot.hour:02d}:{slot.minute:02d}"
            local_dt = datetime.combine(
                schedule_date, time(hour=slot.hour, minute=slot.minute), tzinfo=tz
            )
            utc_dt = local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            caption = rule.render_caption(video, schedule_date)
            self.create(
                {
                    "video_id": video.id,
                    "tiktok_account_id": account.id,
                    "schedule_rule_id": rule.id,
                    "scheduled_time": utc_dt,
                    "schedule_date": schedule_date,
                    "slot_time": slot_key,
                    "caption": caption,
                    "privacy_level": "SELF_ONLY",
                    "state": "pending",
                }
            )
            video.scheduled_count += 1
            created += 1
        _logger.info(
            "Rule %s created %s queue rows for %s (%s)",
            rule.id,
            created,
            schedule_date,
            tz_name,
        )

    def action_publish_now(self):
        for row in self.filtered(lambda r: r.state in ("pending", "failed")):
            row._publish()

    def _publish(self):
        """
        Draft/Inbox flow (PULL_FROM_URL):
          POST /v2/post/publish/inbox/video/init/
              source=PULL_FROM_URL, video_url=<CDN>
          → TikTok tự pull video
          → User mở TikTok Inbox → Edit → Post
        """
        self.ensure_one()
        account = self.tiktok_account_id
        video = self.video_id
        self.write({"state": "uploading", "error_message": False})
        try:
            account.ensure_valid_token()
            video_url = video.cdn_url
            if not video_url:
                raise UserError(
                    "Video chưa có CDN URL. Kiểm tra storage/cdn_domain và Rebuild CDN URL."
                )

            client = TikTokClient(account.tiktok_app_id, account)
            result = client.publish_inbox_draft_from_url(video_url)
            publish_id = result.get("publish_id")
            self.write(
                {
                    "state": "success",
                    "tiktok_publish_id": publish_id,
                }
            )
            video.published_count += 1
            self.message_post(
                body=(
                    f"Đã gửi draft vào TikTok Inbox (PULL_FROM_URL). "
                    f"publish_id=<code>{publish_id}</code>. "
                    f"URL=<code>{video_url}</code>. "
                    f"Mở app TikTok → Inbox → Edit → Post."
                )
            )
            self.env["tiktok.upload.history"].create(
                {
                    "video_id": video.id,
                    "tiktok_account_id": account.id,
                    "publish_queue_id": self.id,
                    "upload_time": fields.Datetime.now(),
                    "status": "success",
                    "response": str(
                        self._sanitize_response(
                            {
                                "publish_id": publish_id,
                                "mode": "inbox_pull_from_url",
                                "video_url": video_url,
                            }
                        )
                    ),
                }
            )
        except Exception as exc:
            _logger.exception("Inbox publish failed for queue %s", self.id)
            self.write({"state": "failed", "error_message": str(exc)})
            self.env["tiktok.upload.history"].create(
                {
                    "video_id": video.id,
                    "tiktok_account_id": account.id,
                    "publish_queue_id": self.id,
                    "upload_time": fields.Datetime.now(),
                    "status": "failed",
                    "response": str(exc),
                }
            )

    @staticmethod
    def _sanitize_response(payload):
        return str(payload)[:5000]

    @api.model
    def cron_publish_due(self):
        now = fields.Datetime.now()
        rows = self.search(
            [("state", "=", "pending"), ("scheduled_time", "<=", now)],
            order="scheduled_time asc",
            limit=20,
        )
        for row in rows:
            row._publish()
            self.env.cr.commit()

    @api.model
    def cron_retry_failed(self):
        rows = self.search(
            [("state", "=", "failed"), ("retry_count", "<", 3)],
            limit=20,
        )
        for row in rows:
            row.write(
                {
                    "state": "pending",
                    "retry_count": row.retry_count + 1,
                    "scheduled_time": fields.Datetime.now() + timedelta(minutes=5),
                }
            )
