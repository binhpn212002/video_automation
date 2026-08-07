import logging
import os
import shutil

from odoo import fields, models
from odoo.exceptions import UserError

from ..services.ffmpeg_service import extract_audio, probe_media
from ..services.r2_client import R2Client, make_flat_object_key
from ..models.video_library import _make_workdir

_logger = logging.getLogger(__name__)


class VideoExtractAudioWizard(models.TransientModel):
    _name = "video.extract.audio.wizard"
    _description = "Extract Audio from Video"

    video_id = fields.Many2one("video.library", required=True, ondelete="cascade")
    name = fields.Char(
        string="Audio Name",
        required=True,
        help="Tên bản ghi audio sẽ tạo trong Audio Library.",
    )
    storage_id = fields.Many2one(
        "video.storage",
        related="video_id.storage_id",
        readonly=True,
    )

    def action_extract(self):
        self.ensure_one()
        video = self.video_id
        if not self.name or not self.name.strip():
            raise UserError("Vui lòng nhập tên audio.")
        if not video.storage_path or not video.storage_id:
            raise UserError("Video chưa upload lên R2.")

        audio_name = self.name.strip()
        client = R2Client(video.storage_id)
        work_dir = _make_workdir("va_extract_")
        video_local = os.path.join(work_dir, "source.mp4")
        audio_local = os.path.join(work_dir, "extracted.mp3")
        try:
            client.download_file(video.storage_path, video_local)
            extract_audio(video_local, audio_local)
            meta = probe_media(audio_local)
            object_key = make_flat_object_key("a", ".mp3", record_id=video.id)
            client.upload_file(audio_local, object_key, content_type="audio/mpeg")
            audio = self.env["audio.library"].create(
                {
                    "name": audio_name,
                    "filename": object_key,
                    "storage_id": video.storage_id.id,
                    "storage_path": object_key,
                    "duration": meta["duration"],
                    "file_size": meta["file_size"] or os.path.getsize(audio_local),
                    "source_video_id": video.id,
                    "active": True,
                }
            )
            video.message_post(
                body=f"Extracted audio <b>{audio_name}</b> → Audio Library #{audio.id}"
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        return {
            "type": "ir.actions.act_window",
            "name": "Audio Library",
            "res_model": "audio.library",
            "res_id": audio.id,
            "view_mode": "form",
            "target": "current",
        }
