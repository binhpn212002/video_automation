import base64
import logging
import os
import random
import shutil

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..models.video_library import _make_workdir
from ..services.ffmpeg_service import generate_video, probe_media
from ..services.r2_client import R2Client, make_flat_object_key

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
        help="Để trống sẽ tự chọn ngẫu nhiên 1 audio active có trên R2.",
    )
    logo_file = fields.Binary(string="Logo (optional)")
    logo_filename = fields.Char()
    output_name = fields.Char(
        string="Tên output",
        help="Để trống sẽ dùng tên video + Generated.",
    )

    @api.model
    def _auto_pick_audio(self):
        """Pick a random active audio that already exists on R2."""
        audios = self.env["audio.library"].search(
            [("active", "=", True), ("storage_path", "!=", False)]
        )
        if not audios:
            return self.env["audio.library"]
        return random.choice(audios)

    def action_generate(self):
        self.ensure_one()
        video = self.video_id
        audio = self.audio_id
        if not audio:
            audio = self._auto_pick_audio()
            if not audio:
                raise UserError(
                    "Chưa chọn nhạc và không có audio nào trong Audio Library để auto chọn."
                )
            self.audio_id = audio.id

        if not video.storage_path or not video.storage_id:
            raise UserError("Video chưa upload lên R2.")
        if not audio.storage_path or not audio.storage_id:
            raise UserError("Audio chưa có trên R2.")

        video_client = R2Client(video.storage_id)
        audio_client = R2Client(audio.storage_id)
        work_dir = _make_workdir("va_genwiz_")
        video_local = os.path.join(work_dir, "input.mp4")
        audio_local = os.path.join(work_dir, "audio.mp3")
        output_local = os.path.join(work_dir, "output.mp4")
        logo_local = None

        try:
            video.write({"state": "processing"})
            if not video.original_storage_path:
                video.original_storage_path = video.storage_path

            video_client.download_file(
                video.original_storage_path or video.storage_path, video_local
            )
            audio_client.download_file(audio.storage_path, audio_local)

            if self.logo_file:
                logo_local = os.path.join(work_dir, self.logo_filename or "logo.png")
                with open(logo_local, "wb") as fh:
                    fh.write(base64.b64decode(self.logo_file))

            generate_video(video_local, audio_local, output_local, logo_path=logo_local)

            object_key = make_flat_object_key("g", ".mp4", record_id=video.id)
            video_client.upload_file(output_local, object_key, content_type="video/mp4")
            meta = probe_media(output_local)

            video.write(
                {
                    "filename": object_key,
                    "storage_path": object_key,
                    "duration": meta["duration"],
                    "width": meta["width"],
                    "height": meta["height"],
                    "fps": meta["fps"],
                    "bitrate": meta["bitrate"],
                    "file_size": meta["file_size"],
                    "state": "available",
                    "generated": True,
                    "audio_id": audio.id,
                }
            )
            video.message_post(
                body=f"Generated với audio <b>{audio.name}</b> → {object_key}"
            )
        except Exception:
            if video.original_storage_path:
                video.write({"state": "uploaded"})
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        return {
            "type": "ir.actions.act_window",
            "res_model": "video.library",
            "res_id": video.id,
            "view_mode": "form",
            "target": "current",
        }
