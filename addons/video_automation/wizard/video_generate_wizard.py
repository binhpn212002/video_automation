import base64
import logging
import os
import shutil

from odoo import fields, models
from odoo.exceptions import UserError

from ..models.video_library import _make_workdir

_logger = logging.getLogger(__name__)


class VideoGenerateWizard(models.TransientModel):
    _name = "video.generate.wizard"
    _description = "Generate Video Output"

    source_type = fields.Selection(
        [
            ("raw_video", "Raw Video"),
            ("affiliate_image", "Ảnh Sản Phẩm (Affiliate)"),
        ],
        string="Loại nguồn",
        default="raw_video",
    )
    video_id = fields.Many2one("video.library", string="Video Gốc", ondelete="cascade")
    image_id = fields.Many2one("product.image", string="Ảnh Sản Phẩm", ondelete="cascade")
    audio_id = fields.Many2one(
        "audio.library",
        string="Nhạc / Audio",
        required=False,
        domain=[("active", "=", True), ("storage_path", "!=", False)],
        help="Để trống sẽ tự chọn audio ít dùng nhất.",
    )
    effect_preset = fields.Selection(
        [
            ("soft", "Soft (Nhẹ nhàng)"),
            ("normal", "Normal (Tiêu chuẩn)"),
            ("strong", "Strong (Mạnh mẽ)"),
        ],
        default="normal",
        string="Hiệu ứng Beat Pulse",
    )
    hook_text = fields.Char(
        string="Hook Text (Safe Area Top)",
        help="Xuất hiện trong 2.5s đầu video.",
    )
    cta_text = fields.Char(
        string="CTA Text (Safe Area Bottom)",
        help="Xuất hiện trong 4s cuối video.",
    )
    logo_file = fields.Binary(string="Logo (optional)")
    logo_filename = fields.Char()
    output_name = fields.Char(
        string="Tên video đầu ra",
        help="Để trống sẽ tự động đặt tên theo nguồn.",
    )

    def action_generate(self):
        """Tạo record video.library mới — không ghi đè bản gốc."""
        self.ensure_one()

        if self.image_id or (not self.video_id and self._context.get("default_image_id")):
            image = self.image_id or self.env["product.image"].browse(self._context.get("default_image_id"))
            if not image.storage_path or not image.storage_id:
                raise UserError("Ảnh sản phẩm chưa tải lên R2.")

            child = image.generate_affiliate_video(
                audio=self.audio_id or None,
                effect_preset=self.effect_preset or "normal",
                hook_text=self.hook_text,
                cta_text=self.cta_text,
                output_name=self.output_name or False,
            )
            return {
                "type": "ir.actions.act_window",
                "res_model": "video.library",
                "res_id": child.id,
                "view_mode": "form",
                "target": "current",
            }

        video = self.video_id
        if not video:
            raise UserError("Vui lòng chọn Video gốc hoặc Ảnh sản phẩm.")
        if not video.storage_path or not video.storage_id:
            raise UserError("Video chưa upload lên R2.")

        audio = self.audio_id
        work_dir = None
        logo_local = None
        try:
            if self.logo_file:
                work_dir = _make_workdir("va_logo_")
                logo_local = os.path.join(work_dir, self.logo_filename or "logo.png")
                with open(logo_local, "wb") as fh:
                    fh.write(base64.b64decode(self.logo_file))

            child = video.generate_output(
                audio=audio or None,
                logo_path=logo_local,
                output_name=self.output_name or False,
            )
        finally:
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)

        return {
            "type": "ir.actions.act_window",
            "res_model": "video.library",
            "res_id": child.id,
            "view_mode": "form",
            "target": "current",
        }

