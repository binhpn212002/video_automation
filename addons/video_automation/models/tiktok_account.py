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
        help="TikTok open_id — ID user trong app (sau Login OAuth).",
    )
    access_token = fields.Char(
        groups="video_automation.group_video_automation_manager",
        help="Access token — lấy qua Login TikTok hoặc nhập thủ công.",
    )
    refresh_token = fields.Char(
        groups="video_automation.group_video_automation_manager",
        help="Refresh token — lấy qua Login TikTok hoặc nhập thủ công.",
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

    def action_login_tiktok(self):
        """Alias: Login TikTok (OAuth) để lấy access/refresh token."""
        return self.action_connect_oauth()

    def action_connect_oauth(self):
        """Mở TikTok Login Kit → callback lưu token vào account."""
        self.ensure_one()
        if not isinstance(self.id, int):
            raise UserError("Lưu account trước khi Login TikTok.")
        app = self.tiktok_app_id
        if not app:
            raise UserError("Chọn TikTok App (client_key / secret / redirect_uri) trước.")
        if not app.client_key or not app.client_secret:
            raise UserError("TikTok App thiếu client_key hoặc client_secret.")
        if not app.redirect_uri:
            raise UserError("TikTok App thiếu redirect_uri.")

        state = f"account-{self.id}-{fields.Datetime.now().timestamp()}"
        code_verifier = generate_code_verifier()
        self.write({"oauth_state": state, "oauth_code_verifier": code_verifier})
        client = TikTokClient(app, self)
        url = client.build_authorize_url(state, code_verifier)
        self.message_post(body=f"Bắt đầu Login TikTok OAuth. Redirect URI: {app.redirect_uri}")
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    def apply_token_response(self, data, fetch_profile=True):
        self.ensure_one()
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        if not payload.get("access_token"):
            raise UserError(f"TikTok không trả access_token: {data}")

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

        if fetch_profile and vals.get("access_token"):
            try:
                client = TikTokClient(self.tiktok_app_id, self)
                user = client.get_user_info(vals["access_token"])
                profile_vals = {}
                if user.get("open_id"):
                    profile_vals["open_id"] = user["open_id"]
                if user.get("display_name"):
                    profile_vals["username"] = user["display_name"]
                    if not self.name or self.name.startswith("TikTok"):
                        profile_vals["name"] = user["display_name"]
                if profile_vals:
                    self.write(profile_vals)
            except Exception:
                _logger.exception("Fetch TikTok user info failed for account %s", self.id)

        self.message_post(
            body=(
                f"Login TikTok thành công. open_id={self.open_id or '-'}, "
                f"expires_at={self.token_expires_at or '-'}"
            )
        )

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
                account.apply_token_response(data, fetch_profile=False)
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
