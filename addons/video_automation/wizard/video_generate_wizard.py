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

    video_id = fields.Many2one("video.library", required=True, ondelete="cascade")
    audio_id = fields.Many2one(
        "audio.library",
        string="Nhạc / Audio",
        required=False,
        domain=[("active", "=", True), ("storage_path", "!=", False)],
        help="Để trống sẽ tự chọn audio ít dùng nhất.",
    )
    logo_file = fields.Binary(string="Logo (optional)")
    logo_filename = fields.Char()
    output_name = fields.Char(
        string="Tên output",
        help="Để trống sẽ dùng tên video gốc + Generated.",
    )

    def action_generate(self):
        """Tạo record video.library mới — không ghi đè bản gốc."""
        self.ensure_one()
        video = self.video_id
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
