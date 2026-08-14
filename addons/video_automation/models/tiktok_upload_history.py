from odoo import api, fields, models
from odoo.exceptions import UserError


class TikTokUploadHistory(models.Model):
    _name = "tiktok.upload.history"
    _description = "TikTok Upload History"
    _order = "upload_time desc, id desc"

    video_id = fields.Many2one("video.library", required=True, ondelete="cascade", index=True)
    tiktok_account_id = fields.Many2one(
        "tiktok.account", required=True, ondelete="cascade", index=True
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
        index=True,
    )
    response = fields.Text()

    def init(self):
        # 1 video chỉ được đăng thành công 1 lần trên 1 tài khoản.
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS tiktok_upload_history_uniq_video_account_success
            ON tiktok_upload_history (video_id, tiktok_account_id)
            WHERE status = 'success'
            """
        )

    @api.model
    def already_posted(self, account, video):
        if not account or not video:
            return False
        return bool(
            self.search_count(
                [
                    ("tiktok_account_id", "=", account.id),
                    ("video_id", "=", video.id),
                    ("status", "=", "success"),
                ]
            )
        )

    @api.model
    def posted_video_ids(self, account):
        if not account:
            return []
        return self.search(
            [
                ("tiktok_account_id", "=", account.id),
                ("status", "=", "success"),
            ]
        ).mapped("video_id").ids

    @api.model
    def assert_can_post(self, account, video):
        if self.already_posted(account, video):
            raise UserError(
                f"Video «{video.display_name}» đã đăng trên tài khoản "
                f"«{account.display_name}». Không được đăng lần 2."
            )
