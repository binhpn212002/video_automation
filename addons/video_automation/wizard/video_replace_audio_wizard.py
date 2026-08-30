import base64
import logging
import mimetypes
import os
import shutil

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..models.video_library import _make_workdir
from ..services.ffmpeg_service import detect_beats, probe_media, replace_video_audio
from ..services.r2_client import R2Client, make_flat_object_key

_logger = logging.getLogger(__name__)


class VideoReplaceAudioWizard(models.TransientModel):
    _name = "video.replace.audio.wizard"
    _description = "Thay Đổi Âm Thanh Cho Video"

    video_id = fields.Many2one(
        "video.library",
        string="Video cần thay âm thanh",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    storage_id = fields.Many2one(
        "video.storage",
        related="video_id.storage_id",
        string="R2 Video Storage",
        readonly=True,
    )
    audio_source = fields.Selection(
        [
            ("library", "Chọn từ Thư Viện Âm Thanh"),
            ("upload", "Tải lên File Âm Thanh Mới"),
        ],
        default="library",
        required=True,
        string="Nguồn Âm Thanh",
    )
    audio_id = fields.Many2one(
        "audio.library",
        string="Bài Nhạc / Âm Thanh",
        domain=[("active", "=", True), ("storage_path", "!=", False)],
        help="Chọn bài nhạc có sẵn trong Thư Viện Âm Thanh.",
    )
    upload_audio_file = fields.Binary(
        string="Tệp Âm Thanh (.mp3, .wav, .aac, .m4a)",
        attachment=False,
    )
    upload_audio_filename = fields.Char(string="Tên tệp gốc")
    upload_audio_name = fields.Char(
        string="Tên Bài Nhạc Mới",
        help="Tên bài nhạc sẽ lưu vào Thư Viện Âm Thanh để tái sử dụng.",
    )
    keep_original_audio = fields.Boolean(
        string="Trộn cùng âm thanh gốc (Nhạc nền)",
        default=False,
        help="Nếu Bật: Giữ lại tiếng nói của video gốc và chèn thêm nhạc nền.\n"
        "Nếu Tắt: Thay thế 100% âm thanh cũ bằng bài nhạc mới.",
    )
    original_volume = fields.Float(
        string="Âm lượng video gốc (%)",
        default=100.0,
        help="Mức âm lượng của âm thanh gốc khi trộn.",
    )
    bg_music_volume = fields.Float(
        string="Âm lượng nhạc nền (%)",
        default=30.0,
        help="Mức âm lượng của bài nhạc nền mới khi trộn.",
    )
    audio_fade_out = fields.Float(
        string="Fade-out cuối video (giây)",
        default=0.8,
        help="Thời gian giảm âm lượng dần ở cuối video để kết thúc êm ái.",
    )
    replace_mode = fields.Selection(
        [
            ("overwrite", "Ghi đè video hiện tại (Cập nhật trực tiếp)"),
            ("new_record", "Tạo bản ghi Video mới (Giữ nguyên video gốc)"),
        ],
        default="overwrite",
        required=True,
        string="Chế độ lưu",
        help="Ghi đè trực tiếp video hiện tại hoặc tạo ra một bản sao video mới.",
    )
    output_video_name = fields.Char(
        string="Tên Video Mới",
        help="Để trống sẽ tự động lấy tên video gốc + (Audio mới).",
    )

    @api.onchange("upload_audio_filename")
    def _onchange_upload_audio_filename(self):
        if self.upload_audio_filename and not self.upload_audio_name:
            self.upload_audio_name = os.path.splitext(self.upload_audio_filename)[0]

    def action_replace(self):
        """Xử lý thay thế / trộn âm thanh cho video và cập nhật lên R2."""
        self.ensure_one()
        video = self.video_id
        if not video:
            raise UserError("Không tìm thấy video cần xử lý.")
        if not video.storage_path or not video.storage_id:
            raise UserError("Video chưa được tải lên Cloudflare R2.")

        # 1. Xác định hoặc tạo audio
        audio = False
        if self.audio_source == "library":
            audio = self.audio_id
            if not audio:
                raise UserError("Vui lòng chọn một bài nhạc từ Thư Viện Âm Thanh.")
            if not audio.storage_path or not audio.storage_id:
                raise UserError(f"Bài nhạc '{audio.name}' chưa sẵn sàng trên R2.")
        else:
            if not self.upload_audio_file:
                raise UserError("Vui lòng chọn file âm thanh từ thiết bị.")
            audio_name = (
                self.upload_audio_name
                or (os.path.splitext(self.upload_audio_filename)[0] if self.upload_audio_filename else False)
                or "Audio Tải Lên"
            ).strip()

            # Upload new audio to audio.library & R2
            client = R2Client(video.storage_id)
            orig_filename = self.upload_audio_filename or "audio.mp3"
            ext = os.path.splitext(orig_filename)[1].lower() or ".mp3"
            if ext not in [".mp3", ".wav", ".aac", ".m4a", ".ogg"]:
                ext = ".mp3"

            work_dir_audio = _make_workdir("va_newaudio_")
            audio_upload_local = os.path.join(work_dir_audio, f"new_audio{ext}")
            try:
                with open(audio_upload_local, "wb") as fh:
                    fh.write(base64.b64decode(self.upload_audio_file))

                audio_meta = probe_media(audio_upload_local)
                beats, bpm, status = detect_beats(audio_upload_local)
                object_key = make_flat_object_key("a", ext, record_id=video.id)
                content_type = mimetypes.guess_type(orig_filename)[0] or "audio/mpeg"
                client.upload_file(audio_upload_local, object_key, content_type=content_type)

                audio = self.env["audio.library"].create(
                    {
                        "name": audio_name,
                        "filename": object_key,
                        "storage_id": video.storage_id.id,
                        "storage_path": object_key,
                        "duration": audio_meta.get("duration") or 0.0,
                        "file_size": audio_meta.get("file_size") or os.path.getsize(audio_upload_local),
                        "beat_data": str(beats),
                        "bpm": bpm,
                        "beat_status": status,
                        "active": True,
                    }
                )
            finally:
                shutil.rmtree(work_dir_audio, ignore_errors=True)

        # 2. Xử lý tải video & audio về máy để FFmpeg thay thế âm thanh
        video_client = R2Client(video.storage_id)
        audio_client = R2Client(audio.storage_id)
        work_dir = _make_workdir("va_repaudio_")
        video_local = os.path.join(work_dir, "input_video.mp4")
        audio_local = os.path.join(work_dir, "input_audio.mp3")
        output_local = os.path.join(work_dir, "output_video.mp4")

        try:
            video_client.download_file(video.storage_path, video_local)
            audio_client.download_file(audio.storage_path, audio_local)

            replace_video_audio(
                video_path=video_local,
                audio_path=audio_local,
                output_path=output_local,
                keep_original_audio=self.keep_original_audio,
                original_vol_ratio=(self.original_volume or 100.0) / 100.0,
                bg_music_vol_ratio=(self.bg_music_volume or 30.0) / 100.0,
                audio_fade_out=self.audio_fade_out or 0.8,
            )

            meta = probe_media(output_local)
            target_storage = video.storage_id

            if self.replace_mode == "overwrite":
                # Flat key mới để tránh cache cũ của CDN / Browser
                object_key = make_flat_object_key("v", ".mp4", record_id=video.id)
                video_client.upload_file(output_local, object_key, content_type="video/mp4")

                video.write(
                    {
                        "filename": object_key,
                        "storage_path": object_key,
                        "duration": meta.get("duration") or video.duration,
                        "width": meta.get("width") or video.width,
                        "height": meta.get("height") or video.height,
                        "fps": meta.get("fps") or video.fps,
                        "bitrate": meta.get("bitrate") or video.bitrate,
                        "file_size": meta.get("file_size") or os.path.getsize(output_local),
                        "audio_id": audio.id,
                        "state": "available",
                    }
                )
                video._compute_cdn_url()

                mode_text = (
                    "Trộn nhạc nền (Background Music)"
                    if self.keep_original_audio
                    else "Thay thế 100% âm thanh cũ"
                )
                video.message_post(
                    body=(
                        f"Đã thay đổi âm thanh video bằng bài nhạc <b>{audio.name}</b> "
                        f"(Chế độ: <i>{mode_text}</i>, Ghi đè file R2: <code>{object_key}</code>)."
                    )
                )
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Thay Đổi Âm Thanh Thành Công",
                        "message": f"Đã cập nhật âm thanh cho video '{video.name}'!",
                        "type": "success",
                        "sticky": False,
                        "next": {
                            "type": "ir.actions.act_window",
                            "res_model": "video.library",
                            "res_id": video.id,
                            "view_mode": "form",
                            "target": "current",
                        },
                    },
                }

            else:
                # Tạo bản ghi video.library mới
                new_name = (
                    self.output_video_name
                    or f"{video.name} (Audio mới)"
                ).strip()
                child = self.env["video.library"].create(
                    {
                        "name": new_name,
                        "storage_id": target_storage.id,
                        "source_type": video.source_type,
                        "source_image_id": video.source_image_id.id if video.source_image_id else False,
                        "source_video_id": video.id,
                        "original_storage_path": video.storage_path,
                        "state": "processing",
                        "generated": True,
                        "allow_republish": True,
                        "audio_id": audio.id,
                    }
                )

                object_key = make_flat_object_key("g", ".mp4", record_id=child.id)
                video_client.upload_file(output_local, object_key, content_type="video/mp4")

                child.write(
                    {
                        "filename": object_key,
                        "storage_path": object_key,
                        "duration": meta.get("duration") or video.duration,
                        "width": meta.get("width") or video.width,
                        "height": meta.get("height") or video.height,
                        "fps": meta.get("fps") or video.fps,
                        "bitrate": meta.get("bitrate") or video.bitrate,
                        "file_size": meta.get("file_size") or os.path.getsize(output_local),
                        "state": "available",
                    }
                )
                child._compute_cdn_url()

                mode_text = (
                    "Trộn nhạc nền" if self.keep_original_audio else "Thay thế 100% âm thanh"
                )
                video.message_post(
                    body=(
                        f"Đã tạo bản sao video mới <b>{child.name}</b> (id={child.id}) "
                        f"với âm thanh <b>{audio.name}</b> ({mode_text})."
                    )
                )
                child.message_post(
                    body=f"Tạo từ video gốc <b>{video.name}</b> kèm âm thanh <b>{audio.name}</b>."
                )

                return {
                    "type": "ir.actions.act_window",
                    "name": "Video Thành Phẩm Mới",
                    "res_model": "video.library",
                    "res_id": child.id,
                    "view_mode": "form",
                    "target": "current",
                }

        except Exception as exc:
            _logger.exception("Failed to replace video audio for video %s: %s", video.id, exc)
            raise UserError(f"Thay đổi âm thanh thất bại: {exc}") from exc
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
