from datetime import timedelta
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class VideoGenerateJobScheduleWizard(models.TransientModel):
    _name = "video.generate.job.schedule.wizard"
    _description = "Lập Lịch Chạy Hàng Loạt Cho Video Jobs"

    start_date = fields.Datetime(
        string="Thời gian bắt đầu",
        default=fields.Datetime.now,
        required=True,
        help="Thời điểm bắt đầu chạy job đầu tiên.",
    )
    interval_minutes = fields.Integer(
        string="Khoảng cách giữa các job (phút)",
        default=5,
        required=True,
        help="Giãn cách thời gian giữa mỗi job (ví dụ: 5 phút để tránh nghẽn server).",
    )
    set_priority = fields.Boolean(
        string="Cập nhật lại Độ ưu tiên",
        default=False,
    )
    priority = fields.Selection(
        [
            ("0", "Thấp"),
            ("1", "Bình thường"),
            ("2", "Ưu tiên"),
            ("3", "Khẩn cấp"),
        ],
        default="1",
        string="Độ ưu tiên mới",
    )

    def action_apply_schedule(self):
        """Áp dụng lịch chạy giãn cách cho các Job đã chọn."""
        self.ensure_one()
        active_ids = self._context.get("active_ids", [])
        if not active_ids:
            raise UserError("Vui lòng chọn ít nhất một Job để lập lịch.")

        jobs = self.env["video.generate.job"].browse(active_ids)
        # Chỉ lập lịch cho các job chưa hoàn thành (draft, failed, cancelled)
        valid_jobs = jobs.filtered(lambda j: j.state in ("draft", "failed", "cancelled"))
        if not valid_jobs:
            raise UserError("Không có job nào ở trạng thái hợp lệ (Chờ xử lý / Thất bại) để lập lịch.")

        start_time = self.start_date or fields.Datetime.now()
        interval = max(1, self.interval_minutes or 5)

        for idx, job in enumerate(valid_jobs):
            sched_time = start_time + timedelta(minutes=idx * interval)
            vals = {
                "is_scheduled": True,
                "scheduled_date": sched_time,
            }
            if job.state in ("failed", "cancelled"):
                vals["state"] = "draft"
                vals["error_message"] = False
            if self.set_priority and self.priority:
                vals["priority"] = self.priority

            job.write(vals)
            job.message_post(body=f"Đã lập lịch chạy tự động vào: <b>{sched_time}</b>")

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Lập lịch thành công",
                "message": f"Đã lập lịch cho {len(valid_jobs)} jobs.",
                "type": "success",
                "sticky": False,
            },
        }
