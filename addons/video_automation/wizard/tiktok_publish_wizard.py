from datetime import datetime
from zoneinfo import ZoneInfo

from odoo import api, fields, models
from odoo.exceptions import UserError


class TikTokPublishWizard(models.TransientModel):
    _name = "tiktok.publish.wizard"
    _description = "Đăng video lên TikTok Inbox"

    tiktok_account_id = fields.Many2one(
        "tiktok.account", required=True, ondelete="cascade"
    )
    video_id = fields.Many2one(
        "video.library",
        string="Video",
        required=True,
        domain="[('storage_path', '!=', False)]",
    )
    caption = fields.Text(
        string="Caption (gợi ý)",
        help="Lưu trên queue; khi post từ Inbox TikTok user vẫn edit caption trong app.",
    )

    @api.onchange("tiktok_account_id")
    def _onchange_tiktok_account_id(self):
        self.video_id = False
        domain = [("storage_path", "!=", False)]
        if self.tiktok_account_id.bucket_id:
            domain.append(("storage_id", "=", self.tiktok_account_id.bucket_id.id))
        return {"domain": {"video_id": domain}}

    def action_apply(self):
        """Chọn video → kéo về temp → FILE_UPLOAD vào TikTok Inbox."""
        self.ensure_one()
        account = self.tiktok_account_id
        video = self.video_id
        if not account:
            raise UserError("Thiếu TikTok Account.")
        if not video:
            raise UserError("Chọn video trước khi Apply.")
        if account.bucket_id and video.storage_id != account.bucket_id:
            raise UserError(
                f"Video không thuộc bucket của account ({account.bucket_id.display_name})."
            )
        if not video.storage_path or not video.storage_id:
            raise UserError("Video chưa upload lên R2.")
        account.ensure_valid_token()

        tz_name = account.timezone or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        now_local = datetime.now(tz)
        schedule_date = now_local.date()
        slot_time = f"m{now_local.strftime('%H%M%S')}"
        utc_now = fields.Datetime.now()

        Queue = self.env["tiktok.publish.queue"].sudo()
        queue = Queue.create(
            {
                "video_id": video.id,
                "tiktok_account_id": account.id,
                "scheduled_time": utc_now,
                "schedule_date": schedule_date,
                "slot_time": slot_time,
                "caption": self.caption or video.name or "",
                "privacy_level": "SELF_ONLY",
                "state": "pending",
            }
        )
        video.scheduled_count += 1
        queue._publish()

        if queue.state == "failed":
            raise UserError(
                queue.error_message or "Gửi TikTok Inbox thất bại. Xem Publish Queue."
            )

        return {
            "type": "ir.actions.act_window",
            "name": "Publish Queue",
            "res_model": "tiktok.publish.queue",
            "res_id": queue.id,
            "view_mode": "form",
            "target": "current",
        }
