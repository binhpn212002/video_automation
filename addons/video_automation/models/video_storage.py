from odoo import api, fields, models


class VideoStorage(models.Model):
    _name = "video.storage"
    _description = "Cloudflare R2 Storage"
    _order = "name"

    name = fields.Char(required=True)
    account_id = fields.Char(string="Cloudflare Account ID")
    bucket_name = fields.Char(required=True)
    access_key_id = fields.Char(required=True)
    secret_key = fields.Char(required=True)
    endpoint = fields.Char(
        required=True,
        help="S3 API endpoint: https://<accountid>.r2.cloudflarestorage.com",
    )
    cdn_domain = fields.Char(
        required=True,
        help=(
            "Public CDN base for this bucket (no trailing slash). "
            "Example r2.dev: https://pub-xxxx.r2.dev — CDN URL = cdn_domain + / + object_path"
        ),
    )
    active = fields.Boolean(default=True)

    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ("cdn_domain", "bucket_name")):
            videos = self.env["video.library"].search([("storage_id", "in", self.ids)])
            videos._compute_cdn_url()
            audios = self.env["audio.library"].search([("storage_id", "in", self.ids)])
            audios._compute_cdn_url()
        return res
