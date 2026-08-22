import logging

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class VideoGenerateWizard(models.TransientModel):
    _name = "video.generate.wizard"
    _description = "Tạo Video TikTok Affiliate từ Ảnh Sản Phẩm"

    image_id = fields.Many2one(
        "product.image",
        string="Ảnh Sản Phẩm",
        required=True,
        ondelete="cascade",
    )
    audio_id = fields.Many2one(
        "audio.library",
        string="Nhạc / Audio",
        required=False,
        domain=[("active", "=", True), ("storage_path", "!=", False)],
        help="Để trống sẽ tự động chọn bài nhạc ít dùng nhất.",
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
    output_name = fields.Char(
        string="Tên video đầu ra",
        help="Để trống sẽ tự động đặt tên theo tên ảnh sản phẩm.",
    )

    def action_generate(self):
        """Tạo video TikTok Affiliate từ ảnh sản phẩm."""
        self.ensure_one()
        image = self.image_id
        if not image:
            raise UserError("Vui lòng chọn ảnh sản phẩm.")
        if not image.storage_path or not image.storage_id:
            raise UserError("Ảnh sản phẩm chưa được tải lên Cloudflare R2.")

        child = image.generate_affiliate_video(
            audio=self.audio_id or None,
            effect_preset=self.effect_preset or "normal",
            hook_text=self.hook_text or image.default_hook,
            cta_text=self.cta_text or image.default_cta,
            output_name=self.output_name or False,
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "video.library",
            "res_id": child.id,
            "view_mode": "form",
            "target": "current",
        }
