from odoo import api, fields, models
from odoo.exceptions import ValidationError


class TikTokScheduleRule(models.Model):
    _name = "tiktok.schedule.rule"
    _description = "TikTok Auto Schedule Rule"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True)
    tiktok_account_id = fields.Many2one(
        "tiktok.account", required=True, ondelete="cascade"
    )
    upload_time_ids = fields.One2many(
        "tiktok.schedule.rule.time", "rule_id", string="Upload Times", copy=True
    )
    caption_template = fields.Text(
        required=True,
        default="{video_name}\n#viral #fyp",
        help="Placeholders: {video_name}, {date}",
    )
    allow_republish = fields.Boolean(
        default=False,
        help="Allow selecting videos already published successfully on this account.",
    )
    timezone = fields.Char(
        help="Optional override; falls back to account timezone.",
    )
    active = fields.Boolean(default=True)
    slots_per_day = fields.Integer(
        string="Slot / ngày", compute="_compute_pool_stats"
    )
    pool_available_count = fields.Integer(
        string="Video chưa đăng (account này)",
        compute="_compute_pool_stats",
        help="Số video generated available mà account này chưa đăng thành công.",
    )
    pool_needed = fields.Integer(
        string="Cần (buffer)",
        compute="_compute_pool_stats",
        help="slots/ngày × buffer_days của bucket.",
    )
    pool_status = fields.Selection(
        [
            ("ok", "Đủ hàng"),
            ("warning", "Sắp thiếu"),
            ("critical", "Thiếu — lịch có thể trống"),
        ],
        compute="_compute_pool_stats",
    )
    upload_times_display = fields.Char(
        string="Khung giờ đăng",
        compute="_compute_upload_times_display",
    )
    queue_count = fields.Integer(
        string="Hàng chờ",
        compute="_compute_queue_count",
    )

    @api.depends("upload_time_ids", "upload_time_ids.hour", "upload_time_ids.minute")
    def _compute_upload_times_display(self):
        for rule in self:
            times = [t.name for t in sorted(rule.upload_time_ids, key=lambda r: (r.hour, r.minute)) if t.name]
            rule.upload_times_display = ", ".join(times) or "Chưa cấu hình"

    def _compute_queue_count(self):
        Queue = self.env["tiktok.publish.queue"]
        for rule in self:
            rule.queue_count = Queue.search_count([("schedule_rule_id", "=", rule.id)])

    @api.depends(
        "upload_time_ids",
        "tiktok_account_id",
        "tiktok_account_id.bucket_id",
        "tiktok_account_id.bucket_id.buffer_days",
        "active",
    )
    def _compute_pool_stats(self):
        Video = self.env["video.library"]
        History = self.env["tiktok.upload.history"]
        for rule in self:
            slots = len(rule.upload_time_ids)
            rule.slots_per_day = slots
            account = rule.tiktok_account_id
            storage = account.bucket_id
            buffer = (storage.buffer_days if storage else 3) or 3
            rule.pool_needed = slots * buffer
            if not account:
                rule.pool_available_count = 0
                rule.pool_status = "ok"
                continue
            domain = [
                ("state", "=", "available"),
                ("generated", "=", True),
            ]
            if storage:
                domain.append(("storage_id", "=", storage.id))
            posted_ids = History.posted_video_ids(account)
            if posted_ids:
                domain.append(("id", "not in", posted_ids))
            available = Video.search_count(domain)
            rule.pool_available_count = available
            if not rule.active or slots <= 0:
                rule.pool_status = "ok"
            elif available < slots:
                rule.pool_status = "critical"
            elif available < rule.pool_needed:
                rule.pool_status = "warning"
            else:
                rule.pool_status = "ok"

    @api.constrains("upload_time_ids")
    def _check_upload_times(self):
        for rule in self:
            if rule.active and not rule.upload_time_ids:
                raise ValidationError("Active schedule rules need at least one upload time.")

    def _resolved_timezone(self):
        self.ensure_one()
        return self.timezone or self.tiktok_account_id.timezone or "UTC"

    def render_caption(self, video, schedule_date):
        self.ensure_one()
        return (self.caption_template or "").format(
            video_name=video.name or "",
            date=schedule_date.isoformat() if schedule_date else "",
        )

    def action_top_up_pool(self):
        for rule in self:
            storage = rule.tiktok_account_id.bucket_id
            if not storage:
                storage = self.env["video.storage"].search(
                    [("active", "=", True)], limit=1
                )
            if storage:
                storage.action_top_up_pool()
        return True

    def action_generate_queue_today(self):
        Queue = self.env["tiktok.publish.queue"]
        for rule in self:
            Queue._create_queue_for_rule(rule)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Lên lịch hôm nay",
                "message": "Đã kiểm tra và cập nhật hàng chờ cho rule.",
                "type": "success",
                "sticky": False,
            },
        }

    def action_view_queue(self):
        self.ensure_one()
        return {
            "name": f"Hàng chờ - {self.name}",
            "type": "ir.actions.act_window",
            "res_model": "tiktok.publish.queue",
            "view_mode": "tree,form",
            "domain": [("schedule_rule_id", "=", self.id)],
            "context": {
                "default_schedule_rule_id": self.id,
                "default_tiktok_account_id": self.tiktok_account_id.id,
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rule in records.filtered(lambda r: r.active):
            try:
                self.env["tiktok.publish.queue"]._create_queue_for_rule(rule)
            except Exception:
                pass
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get("active") or "upload_time_ids" in vals:
            for rule in self.filtered(lambda r: r.active):
                try:
                    self.env["tiktok.publish.queue"]._create_queue_for_rule(rule)
                except Exception:
                    pass
        return res


class TikTokScheduleRuleTime(models.Model):
    _name = "tiktok.schedule.rule.time"
    _description = "Schedule Rule Upload Time"
    _order = "hour, minute"

    rule_id = fields.Many2one(
        "tiktok.schedule.rule", required=True, ondelete="cascade"
    )
    hour = fields.Integer(required=True, default=8)
    minute = fields.Integer(required=True, default=0)
    name = fields.Char(compute="_compute_name", store=True)

    @api.depends("hour", "minute")
    def _compute_name(self):
        for row in self:
            row.name = f"{row.hour:02d}:{row.minute:02d}"

    @api.constrains("hour", "minute")
    def _check_time(self):
        for row in self:
            if not (0 <= row.hour <= 23):
                raise ValidationError("Hour must be 0–23.")
            if not (0 <= row.minute <= 59):
                raise ValidationError("Minute must be 0–59.")
