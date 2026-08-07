import base64
import logging
import mimetypes
import os
import shutil
import tempfile

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.ffmpeg_service import probe_media
from ..services.r2_client import R2Client, make_flat_object_key

_logger = logging.getLogger(__name__)


def _make_workdir(prefix):
    preferred = "/tmp/video_work"
    try:
        if os.path.isdir(preferred):
            return tempfile.mkdtemp(prefix=prefix, dir=preferred)
    except OSError:
        _logger.warning("Cannot use %s, falling back to system temp", preferred)
    return tempfile.mkdtemp(prefix=prefix)


class AudioLibrary(models.Model):
    _name = "audio.library"
    _description = "Audio Library"
    _order = "name"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True)
    filename = fields.Char()
    storage_id = fields.Many2one("video.storage", required=True)
    storage_path = fields.Char()
    cdn_url = fields.Char(
        compute="_compute_cdn_url",
        store=True,
        readonly=True,
        help="Computed from Storage CDN domain + object path.",
    )
    duration = fields.Float()
    file_size = fields.Integer()
    active = fields.Boolean(default=True)
    upload_file = fields.Binary(string="Audio file", attachment=False)
    upload_filename = fields.Char()
    source_video_id = fields.Many2one(
        "video.library",
        string="Source Video",
        ondelete="set null",
        help="Video mà audio này được extract từ đó.",
    )

    @api.depends("storage_id", "storage_id.cdn_domain", "storage_id.bucket_name", "storage_path")
    def _compute_cdn_url(self):
        for audio in self:
            if audio.storage_id and audio.storage_path:
                audio.cdn_url = R2Client(audio.storage_id).cdn_url(audio.storage_path)
            else:
                audio.cdn_url = False

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "storage_id" in fields_list and not res.get("storage_id"):
            storage = self.env["video.storage"].search([("active", "=", True)], limit=1)
            if storage:
                res["storage_id"] = storage.id
        return res

    def action_upload_to_r2(self):
        for audio in self:
            audio._upload_local_file_to_r2()
        return True

    def _upload_local_file_to_r2(self):
        self.ensure_one()
        if not self.upload_file:
            raise UserError("Chọn file audio từ thiết bị trước khi upload.")
        if not self.storage_id:
            raise UserError("Chọn R2 Storage trước.")

        filename = self.upload_filename or self.filename or "audio.mp3"
        if not self.name:
            self.name = os.path.splitext(os.path.basename(filename))[0]

        client = R2Client(self.storage_id)
        ext = os.path.splitext(filename)[1] or ".mp3"
        object_key = make_flat_object_key("a", ext, record_id=self.id)
        content_type = mimetypes.guess_type(object_key)[0] or "audio/mpeg"
        work_dir = _make_workdir("va_aupload_")
        local_path = os.path.join(work_dir, object_key)
        try:
            with open(local_path, "wb") as fh:
                fh.write(base64.b64decode(self.upload_file))
            client.upload_file(local_path, object_key, content_type=content_type)
            meta = probe_media(local_path)
            self.write(
                {
                    "filename": object_key,
                    "storage_path": object_key,
                    "duration": meta["duration"],
                    "file_size": meta["file_size"] or os.path.getsize(local_path),
                    "upload_file": False,
                    "upload_filename": False,
                }
            )
            self.message_post(body=f"Uploaded to R2: {object_key}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
