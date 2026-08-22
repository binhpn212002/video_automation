import base64
import json
import logging
import mimetypes
import os
import shutil
import tempfile

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.ffmpeg_service import detect_beats, probe_media
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

    beat_data = fields.Text(
        string="Beat Timestamps (JSON)",
        help="Danh sách timestamps mốc nhịp âm thanh để đồng bộ hiệu ứng (VD: [0.5, 1.1, ...])",
    )
    bpm = fields.Float(
        string="BPM",
        help="Tốc độ nhịp ước tính (Beats Per Minute)",
    )
    beat_status = fields.Selection(
        [
            ("none", "Chưa phân tích"),
            ("detected", "Đã nhận diện Beat"),
            ("fallback", "Fallback (Nhịp đều)"),
        ],
        default="none",
        string="Trạng thái Beat",
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
        local_path = os.path.join(work_dir, os.path.basename(object_key))
        try:
            with open(local_path, "wb") as fh:
                fh.write(base64.b64decode(self.upload_file))
            client.upload_file(local_path, object_key, content_type=content_type)
            meta = probe_media(local_path)
            
            # Analyze beats immediately on upload
            beats, bpm, status = detect_beats(local_path)
            
            self.write(
                {
                    "filename": object_key,
                    "storage_path": object_key,
                    "duration": meta["duration"],
                    "file_size": meta["file_size"] or os.path.getsize(local_path),
                    "beat_data": json.dumps(beats),
                    "bpm": bpm,
                    "beat_status": status,
                    "upload_file": False,
                    "upload_filename": False,
                }
            )
            self.message_post(body=f"Uploaded to R2: {object_key} (BPM: {bpm}, Beats: {len(beats)})")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def action_analyze_beats(self):
        """Phân tích nhịp âm thanh và lưu cache timestamps."""
        for audio in self:
            audio._analyze_beats()
        return True

    def _analyze_beats(self):
        self.ensure_one()
        if not self.storage_id or not self.storage_path:
            raise UserError("File âm thanh chưa có trên R2.")

        client = R2Client(self.storage_id)
        work_dir = _make_workdir("va_beat_")
        local_path = os.path.join(work_dir, "audio.mp3")
        try:
            client.download_file(self.storage_path, local_path)
            beats, bpm, status = detect_beats(local_path)
            self.write(
                {
                    "beat_data": json.dumps(beats),
                    "bpm": bpm,
                    "beat_status": status,
                }
            )
            self.message_post(
                body=f"Đã phân tích beat: <b>{len(beats)} beats</b>, BPM: <b>{bpm}</b>, Trạng thái: <b>{status}</b>"
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def get_or_compute_beats(self, storage=None, audio_local_path=None):
        """
        Lấy danh sách timestamps nhịp từ cache hoặc tính toán trực tiếp.
        Returns: list[float]
        """
        self.ensure_one()
        if self.beat_data:
            try:
                data = json.loads(self.beat_data)
                if isinstance(data, list) and len(data) > 0:
                    return data
            except Exception:
                pass

        if audio_local_path and os.path.exists(audio_local_path):
            beats, bpm, status = detect_beats(audio_local_path)
            self.write(
                {
                    "beat_data": json.dumps(beats),
                    "bpm": bpm,
                    "beat_status": status,
                }
            )
            return beats

        # Download from R2 if needed
        storage = storage or self.storage_id
        if not storage or not self.storage_path:
            return []

        client = R2Client(storage)
        work_dir = _make_workdir("va_beat_get_")
        local_path = os.path.join(work_dir, "audio.mp3")
        try:
            client.download_file(self.storage_path, local_path)
            beats, bpm, status = detect_beats(local_path)
            self.write(
                {
                    "beat_data": json.dumps(beats),
                    "bpm": bpm,
                    "beat_status": status,
                }
            )
            return beats
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

