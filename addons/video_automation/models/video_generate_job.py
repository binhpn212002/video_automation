import logging
import uuid

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class VideoGenerateJob(models.Model):
    _name = "video.generate.job"
    _description = "Tiến trình tạo Video (Generate Job)"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Mã Job (Job ID)",
        required=True,
        index=True,
        default=lambda self: f"aff_{uuid.uuid4().hex[:10]}",
        readonly=True,
    )
    image_id = fields.Many2one(
        "product.image",
        string="Ảnh sản phẩm",
        required=True,
        ondelete="cascade",
    )
    audio_id = fields.Many2one(
        "audio.library",
        string="Audio / Nhạc nền",
        ondelete="set null",
    )
    storage_id = fields.Many2one(
        "video.storage",
        string="R2 Storage",
        compute="_compute_storage_id",
        store=True,
        readonly=False,
    )
    effect_preset = fields.Selection(
        [
            ("soft", "Soft"),
            ("normal", "Normal"),
            ("strong", "Strong"),
        ],
        default="normal",
        string="Hiệu ứng Beat Pulse",
    )
    motion_effect = fields.Selection(
        [
            ("zoom_bounce", "Ken Burns + Beat Bounce"),
            ("zoom_in", "Slow Zoom In"),
            ("zoom_out", "Slow Zoom Out"),
            ("bounce_only", "Beat Bounce Only"),
            ("none", "None (Static)"),
        ],
        default="zoom_bounce",
        string="Hiệu ứng Motion",
    )
    hook_text = fields.Char(string="Hook Text")
    cta_text = fields.Char(string="CTA Text")
    max_duration = fields.Float(string="Thời lượng tối đa (s)", default=25.0)
    flash_effect = fields.Boolean(string="White Flash", default=True)

    state = fields.Selection(
        [
            ("draft", "Chờ xử lý"),
            ("processing", "Đang render"),
            ("completed", "Hoàn thành"),
            ("failed", "Thất bại"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )

    video_id = fields.Many2one(
        "video.library",
        string="Video thành phẩm",
        readonly=True,
    )
    video_url = fields.Char(
        string="CDN URL",
        related="video_id.cdn_url",
        readonly=True,
    )
    duration = fields.Float(
        string="Thời lượng (s)",
        related="video_id.duration",
        readonly=True,
    )
    error_message = fields.Text(string="Chi tiết lỗi", readonly=True)
    finish_date = fields.Datetime(string="Thời gian hoàn thành", readonly=True)

    @api.depends("image_id", "image_id.storage_id")
    def _compute_storage_id(self):
        for job in self:
            if job.image_id and job.image_id.storage_id:
                job.storage_id = job.image_id.storage_id.id
            elif not job.storage_id:
                job.storage_id = self.env["video.storage"].search([("active", "=", True)], limit=1).id

    def action_run_job(self):
        """Thực thi job render video từ ảnh + audio."""
        for job in self:
            if job.state == "completed":
                continue
            job.write({"state": "processing", "error_message": False})
            try:
                video = job.image_id.generate_affiliate_video(
                    audio=job.audio_id or None,
                    effect_preset=job.effect_preset or "normal",
                    motion_effect=job.motion_effect or "zoom_bounce",
                    hook_text=job.hook_text,
                    cta_text=job.cta_text,
                    max_duration=job.max_duration or 25.0,
                )
                job.write(
                    {
                        "video_id": video.id,
                        "state": "completed",
                        "finish_date": fields.Datetime.now(),
                        "error_message": False,
                    }
                )
                job.message_post(
                    body=f"Job hoàn thành: Đã tạo video <b>{video.name}</b> (CDN: <a href='{video.cdn_url}'>{video.cdn_url}</a>)"
                )
            except Exception as exc:
                job.write(
                    {
                        "state": "failed",
                        "error_message": str(exc),
                        "finish_date": fields.Datetime.now(),
                    }
                )
                job.message_post(body=f"Job thất bại: {exc}")
                _logger.exception("Video generate job %s failed: %s", job.name, exc)
        return True
