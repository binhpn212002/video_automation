import logging
from datetime import datetime, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.tiktok_client import TikTokClient, generate_code_verifier

_logger = logging.getLogger(__name__)


class TikTokAccount(models.Model):
    _name = "tiktok.account"
    _description = "TikTok Account"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True)
    tiktok_app_id = fields.Many2one("tiktok.app", required=True, ondelete="restrict")
    bucket_id = fields.Many2one(
        "video.storage",
        string="Bucket / R2 Storage",
        ondelete="restrict",
        help="Bucket R2 dùng cho account này. Auto schedule chỉ lấy video thuộc bucket này.",
    )
    username = fields.Char(string="Display Username")
    profile_url = fields.Char()
    open_id = fields.Char(
        help="TikTok open_id — có thể nhập thủ công nếu không dùng OAuth.",
    )
    access_token = fields.Char(
        groups="video_automation.group_video_automation_manager",
        help="Access token — có thể cập nhật thủ công.",
    )
    refresh_token = fields.Char(
        groups="video_automation.group_video_automation_manager",
        help="Refresh token — có thể cập nhật thủ công.",
    )
    token_expires_at = fields.Datetime(
        help="Thời điểm access token hết hạn.",
    )
    scopes = fields.Char()
    auth_state = fields.Selection(
        [
            ("disconnected", "Disconnected"),
            ("connected", "Connected"),
            ("expired", "Expired"),
        ],
        default="disconnected",
        required=True,
        tracking=True,
    )
    timezone = fields.Char(default="Asia/Ho_Chi_Minh", required=True)
    active = fields.Boolean(default=True)
    oauth_state = fields.Char(readonly=True)
    oauth_code_verifier = fields.Char(
        readonly=True,
        groups="video_automation.group_video_automation_manager",
        help="Temporary PKCE verifier for OAuth (cleared after connect).",
    )

    def action_apply_manual_tokens(self):
        """Mark account connected after manual token entry."""
        for account in self:
            if not account.access_token:
                raise UserError("Nhập Access Token trước.")
            vals = {"auth_state": "connected"}
            if not account.token_expires_at:
                vals["token_expires_at"] = datetime.utcnow() + timedelta(hours=24)
            account.write(vals)
            account.message_post(body="Tokens cập nhật thủ công — auth_state = connected.")
        return True

    def action_connect_oauth(self):
        self.ensure_one()
        if not self.tiktok_app_id:
            raise UserError("Select a TikTok App first.")
        state = f"account-{self.id}-{fields.Datetime.now().timestamp()}"
        code_verifier = generate_code_verifier()
        self.write({"oauth_state": state, "oauth_code_verifier": code_verifier})
        client = TikTokClient(self.tiktok_app_id, self)
        url = client.build_authorize_url(state, code_verifier)
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    def apply_token_response(self, data):
        self.ensure_one()
        # TikTok may wrap in data key depending on endpoint version
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        expires_in = int(payload.get("expires_in") or 0)
        vals = {
            "open_id": payload.get("open_id") or self.open_id,
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token") or self.refresh_token,
            "scopes": payload.get("scope") or self.scopes,
            "auth_state": "connected",
            "token_expires_at": datetime.utcnow() + timedelta(seconds=expires_in or 86400),
        }
        self.write(vals)

    def ensure_valid_token(self):
        self.ensure_one()
        if self.auth_state == "disconnected" or not self.access_token:
            raise UserError(f"Account {self.display_name} is not connected.")
        if self.token_expires_at and self.token_expires_at <= fields.Datetime.now() + timedelta(minutes=30):
            self.refresh_tokens()
        return True

    def refresh_tokens(self):
        for account in self:
            if not account.refresh_token:
                account.auth_state = "expired"
                continue
            try:
                client = TikTokClient(account.tiktok_app_id, account)
                data = client.refresh_access_token(account.refresh_token)
                account.apply_token_response(data)
            except Exception as exc:
                _logger.exception("Token refresh failed for account %s", account.id)
                account.write({"auth_state": "expired"})
                account.message_post(body=f"Token refresh failed: {exc}")

    @api.model
    def cron_refresh_tokens(self):
        soon = fields.Datetime.now() + timedelta(hours=2)
        accounts = self.search(
            [
                ("active", "=", True),
                ("auth_state", "=", "connected"),
                "|",
                ("token_expires_at", "=", False),
                ("token_expires_at", "<=", soon),
            ]
        )
        accounts.refresh_tokens()
