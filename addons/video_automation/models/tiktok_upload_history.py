from odoo import fields, models


class TikTokUploadHistory(models.Model):
    _name = "tiktok.upload.history"
    _description = "TikTok Upload History"
    _order = "upload_time desc, id desc"

    video_id = fields.Many2one("video.library", required=True, ondelete="cascade")
    tiktok_account_id = fields.Many2one(
        "tiktok.account", required=True, ondelete="cascade"
    )
    publish_queue_id = fields.Many2one(
        "tiktok.publish.queue", ondelete="set null"
    )
    upload_time = fields.Datetime(required=True, default=fields.Datetime.now)
    status = fields.Selection(
        [
            ("success", "Success"),
            ("failed", "Failed"),
        ],
        required=True,
    )
    response = fields.Text()
