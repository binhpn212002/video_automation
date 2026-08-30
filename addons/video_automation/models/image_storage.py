import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ImageStorage(models.Model):
    _name = "image.storage"
    _description = "Cloudflare R2 Storage - Kho Ảnh Sản Phẩm"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(string="Tên Cấu Hình", required=True)
    account_id = fields.Char(string="Cloudflare Account ID")
    bucket_name = fields.Char(string="Bucket Name", required=True)
    access_key_id = fields.Char(string="Access Key ID", required=True)
    secret_key = fields.Char(string="Secret Access Key", required=True)
    endpoint = fields.Char(
        string="S3 Endpoint",
        required=True,
        help="S3 API endpoint: https://<accountid>.r2.cloudflarestorage.com",
    )
    cdn_domain = fields.Char(
        string="CDN Domain / Public URL",
        required=True,
        help=(
            "Public CDN base cho bucket này (không có gạch chéo cuối). "
            "Ví dụ: https://pub-xxxx.r2.dev — CDN URL = cdn_domain + / + object_path"
        ),
    )
    active = fields.Boolean(default=True)

    image_ids = fields.One2many(
        "product.image",
        "storage_id",
        string="Danh sách Ảnh",
    )
    total_image_count = fields.Integer(
        string="Tổng số ảnh",
        compute="_compute_image_stats",
    )
    pending_image_count = fields.Integer(
        string="Ảnh chưa gen video",
        compute="_compute_image_stats",
    )
    generated_image_count = fields.Integer(
        string="Ảnh đã gen video",
        compute="_compute_image_stats",
    )

    def _compute_image_stats(self):
        Image = self.env["product.image"]
        for storage in self:
            total = Image.search_count([("storage_id", "=", storage.id)])
            pending = Image.search_count(
                [
                    ("storage_id", "=", storage.id),
                    ("active", "=", True),
                    ("storage_path", "!=", False),
                    ("generated", "=", False),
                    ("image_type", "=", "product"),
                    ("state", "in", ("uploaded", "failed")),
                ]
            )
            generated = Image.search_count(
                [
                    ("storage_id", "=", storage.id),
                    ("generated", "=", True),
                ]
            )
            storage.total_image_count = total
            storage.pending_image_count = pending
            storage.generated_image_count = generated

    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ("cdn_domain", "bucket_name")):
            images = self.env["product.image"].search([("storage_id", "in", self.ids)])
            images._compute_cdn_url()
        return res

    def action_rebuild_cdn_urls(self):
        self.ensure_one()
        images = self.env["product.image"].search([("storage_id", "=", self.id)])
        images._compute_cdn_url()
        return True
