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
        domain=[("image_type", "=", "product")],
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
        string="Cường độ Beat Pulse",
    )
    motion_effect = fields.Selection(
        [
            ("zoom_bounce", "Ken Burns (Zoom In) + Beat Bounce 🔥"),
            ("zoom_in", "Slow Zoom In (Phóng to nhẹ)"),
            ("zoom_out", "Slow Zoom Out (Thu nhỏ nhẹ)"),
            ("bounce_only", "Chỉ Beat Bounce (Nảy theo nhịp)"),
            ("none", "Tĩnh (Không chuyển động)"),
        ],
        default="zoom_bounce",
        string="Hiệu ứng Chuyển động (Motion FX)",
        help="Ken Burns phóng to nhẹ kết hợp nảy hình ảnh theo nhịp điệu của âm thanh.",
    )
    max_duration = fields.Float(
        string="Thời lượng tối đa (giây)",
        default=25.0,
        help="Mặc định 25 giây. Nếu file nhạc dài hơn 25s thì hệ thống sẽ tự động cắt ngắn lại và fade-out âm thanh êm ái.",
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
            motion_effect=self.motion_effect or "zoom_bounce",
            hook_text=self.hook_text or image.default_hook,
            cta_text=self.cta_text or image.default_cta,
            output_name=self.output_name or False,
            max_duration=self.max_duration or 25.0,
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "video.library",
            "res_id": child.id,
            "view_mode": "form",
            "target": "current",
        }
