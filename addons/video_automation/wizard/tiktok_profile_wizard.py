import base64
import json
import logging
from datetime import datetime

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TikTokProfileWizard(models.TransientModel):
    _name = "tiktok.profile.wizard"
    _description = "TikTok Account Profile & Token Inspector"

    tiktok_account_id = fields.Many2one(
        "tiktok.account", string="TikTok Account", required=True, readonly=True
    )
    is_valid = fields.Boolean(string="Token hợp lệ", readonly=True)
    token_status = fields.Selection(
        [
            ("valid", "🟢 Hợp lệ (Connected)"),
            ("expired", "🔴 Hết hạn (Expired)"),
            ("invalid", "⚠️ Không hợp lệ / Lỗi"),
            ("disconnected", "⚪ Chưa kết nối"),
        ],
        string="Trạng thái Token",
        readonly=True,
    )
    status_message = fields.Char(string="Thông điệp trạng thái", readonly=True)
    error_message = fields.Text(string="Chi tiết lỗi", readonly=True)
    token_expires_at = fields.Datetime(string="Hạn dùng Token", readonly=True)
    time_remaining = fields.Char(
        string="Thời gian còn lại", compute="_compute_time_remaining"
    )

    # Thông tin người dùng
    display_name = fields.Char(string="Tên hiển thị (Display Name)", readonly=True)
    creator_username = fields.Char(string="Username", readonly=True)
    open_id = fields.Char(string="Open ID", readonly=True)
    union_id = fields.Char(string="Union ID", readonly=True)
    avatar_url = fields.Char(string="Avatar URL", readonly=True)
    avatar_image = fields.Binary(
        string="Avatar", compute="_compute_avatar_image", store=False
    )
    profile_deep_link = fields.Char(string="Profile Deep Link", readonly=True)
    bio_description = fields.Text(string="Tiểu sử (Bio)", readonly=True)
    is_verified = fields.Boolean(string="Đã xác minh (Tích xanh)", readonly=True)

    # Thống kê kênh
    follower_count = fields.Integer(string="Followers", readonly=True)
    following_count = fields.Integer(string="Following", readonly=True)
    likes_count = fields.Integer(string="Likes", readonly=True)
    video_count = fields.Integer(string="Videos", readonly=True)

    # Cài đặt đăng video & Creator info
    max_video_post_duration_sec = fields.Integer(
        string="Thời lượng tối đa (giây)", readonly=True
    )
    privacy_level_options = fields.Char(
        string="Chế độ riêng tư được phép", readonly=True
    )
    duet_disabled = fields.Boolean(string="Tắt Duet", readonly=True)
    stitch_disabled = fields.Boolean(string="Tắt Stitch", readonly=True)
    comment_disabled = fields.Boolean(string="Tắt Comment", readonly=True)

    # Dữ liệu JSON thô trả về từ TikTok
    raw_user_info_json = fields.Text(string="User Info JSON", readonly=True)
    raw_creator_info_json = fields.Text(string="Creator Info JSON", readonly=True)

    @api.depends("token_expires_at", "is_valid")
    def _compute_time_remaining(self):
        now = fields.Datetime.now()
        for wiz in self:
            if not wiz.token_expires_at:
                wiz.time_remaining = "Không xác định"
            elif wiz.token_expires_at > now:
                diff = wiz.token_expires_at - now
                hours, remainder = divmod(int(diff.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                if hours > 24:
                    days = hours // 24
                    wiz.time_remaining = f"Còn {days} ngày {hours % 24} giờ"
                else:
                    wiz.time_remaining = f"Còn {hours} giờ {minutes} phút"
            else:
                diff = now - wiz.token_expires_at
                hours, _ = divmod(int(diff.total_seconds()), 3600)
                wiz.time_remaining = f"Đã hết hạn ({hours} giờ trước)"

    @api.depends("avatar_url")
    def _compute_avatar_image(self):
        for wiz in self:
            if wiz.avatar_url:
                try:
                    resp = requests.get(wiz.avatar_url, timeout=10)
                    if resp.status_code == 200:
                        wiz.avatar_image = base64.b64encode(resp.content)
                        continue
                except Exception as e:
                    _logger.debug("Fetch avatar image failed: %s", e)
            wiz.avatar_image = False

    def action_refresh_token(self):
        """Refresh token từ TikTok API và tải lại profile."""
        self.ensure_one()
        account = self.tiktok_account_id
        if not account.refresh_token:
            raise UserError("Account này chưa có Refresh Token. Vui lòng bấm Login TikTok lại.")
        account.refresh_tokens()
        return account.action_show_profile()

    def action_login_tiktok(self):
        """Mở trình duyệt để OAuth Login lại."""
        self.ensure_one()
        return self.tiktok_account_id.action_login_tiktok()
