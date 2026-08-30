import logging
import os
import shutil
import tempfile

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.ffmpeg_service import generate_music_video, probe_media
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


class MusicVideoGenerateWizard(models.TransientModel):
    _name = "music.video.generate.wizard"
    _description = "Tạo Video Ca Nhạc (Music Video Generator)"

    name = fields.Char(
        string="Tên Video",
        help="Tên đặt cho video ca nhạc thành phẩm.",
    )
    storage_id = fields.Many2one(
        "video.storage",
        string="R2 Video Storage",
        required=True,
        default=lambda self: self.env["video.storage"].search([("active", "=", True)], limit=1),
        help="Nơi lưu trữ file video sau khi render.",
    )
    bg_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Background (Hình nền)",
        required=True,
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "background")],
        help="Ảnh nền cho video ca nhạc (phong cảnh, phòng lofi, sân khấu, vũ trụ...).",
    )
    character_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Nhân vật / Ca sĩ",
        required=True,
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "character")],
        help="Ảnh nhân vật hoặc ca sĩ (khuyên dùng ảnh PNG đã tách nền).",
    )
    audio_id = fields.Many2one(
        "audio.library",
        string="Nhạc / Audio MP3",
        required=False,
        domain=[("active", "=", True), ("storage_path", "!=", False)],
        help="Để trống sẽ tự động chọn bài nhạc ít dùng nhất từ thư viện nhạc.",
    )
    music_layout = fields.Selection(
        [
            ("spotify_card", "Card Âm Nhạc Sang Trọng (Spotify / Lofi Card)"),
            ("vinyl_retro", "Đĩa Than Cổ Điển Xoay 360° (Vinyl Retro)"),
            ("circular_avatar", "Avatar Tròn Tinh Tế (Circular Avatar)"),
            ("floating_portrait", "Chân Dung Nghệ Thuật (Floating Portrait)"),
            ("center_cutout", "Nhân vật Tách nền (Center Cutout)"),
            ("spinning_vinyl", "Đĩa than xoay (Spinning Vinyl)"),
            ("glass_card", "Khung kính mờ (Glassmorphism Card)"),
        ],
        default="spotify_card",
        string="Kiểu Bố Cục Nhân Vật",
        required=True,
    )
    visualizer_style = fields.Selection(
        [
            ("none", "Không hiển thị"),
            ("spectrum_bars", "Cột sóng Equalizer (Spectrum Bars)"),
            ("sine_wave", "Đường sóng lượn (Smooth Wave)"),
            ("radial_circle", "Sóng tròn bao quanh (Radial Wave)"),
        ],
        default="spectrum_bars",
        string="Kiểu Sóng Nhạc (Visualizer)",
        required=True,
    )
    visualizer_color = fields.Selection(
        [
            ("cyan_neon", "Xanh Neon (Cyan Glow)"),
            ("pink_purple", "Hồng Tím (Synthwave Pink)"),
            ("golden_warm", "Vàng Ánh Kim (Golden Glow)"),
            ("white_minimal", "Trắng Tối Giản (Pure White)"),
        ],
        default="cyan_neon",
        string="Màu Sóng Nhạc",
        required=True,
    )
    particle_effect = fields.Selection(
        [
            ("none", "Không có"),
            ("snow_fall", "Tuyết rơi lãng mạn (Snow Fall)"),
            ("rain_drops", "Giọt mưa rơi mộng ảo (Rain Drops)"),
            ("dust_bokeh", "Hạt bụi sáng (Dust & Bokeh)"),
            ("stage_lights", "Tia đèn sân khấu (Stage Lights)"),
        ],
        default="snow_fall",
        string="Hiệu Ứng Không Khí",
        required=True,
    )
    music_preset = fields.Selection(
        [
            ("lofi_chill", "Lofi / Chill (Đĩa than xoay, Nhẹ nhàng)"),
            ("edm_remix", "EDM / Remix / Vinahouse (Flash mạnh, Bass Bounce)"),
            ("ballad_acoustic", "Ballad / Acoustic (Mộng ảo, Mưa rơi)"),
            ("hiphop_cyber", "Rap / HipHop / Cyberpunk (Neon Glow)"),
        ],
        default="lofi_chill",
        string="Preset Thể Loại Nhạc",
        required=True,
    )
    effect_preset = fields.Selection(
        [
            ("soft", "Soft (Nhẹ nhàng)"),
            ("normal", "Normal (Tiêu chuẩn)"),
            ("strong", "Strong (Mạnh mẽ)"),
        ],
        default="normal",
        string="Cường độ Beat Pulse & Bounce",
        required=True,
    )
    max_duration = fields.Float(
        string="Thời lượng video (giây)",
        default=0.0,
        help="Tự động lấy theo thời lượng của file âm thanh. Bạn có thể chỉnh lại hoặc để 0 để tạo video trọn vẹn toàn bộ bài nhạc.",
    )

    @api.onchange("audio_id")
    def _onchange_audio_id(self):
        if self.audio_id and self.audio_id.duration:
            self.max_duration = self.audio_id.duration

    def action_generate(self):
        """Tạo Video Ca Nhạc từ Ảnh Background + Ảnh Nhân Vật + File Nhạc MP3."""
        self.ensure_one()
        bg = self.bg_image_id
        char = self.character_image_id

        if not bg or not bg.storage_path or not bg.storage_id:
            raise UserError("Vui lòng chọn Ảnh Background hợp lệ đã tải lên Cloudflare R2.")
        if not char or not char.storage_path or not char.storage_id:
            raise UserError("Vui lòng chọn Ảnh Nhân vật hợp lệ đã tải lên Cloudflare R2.")

        video_storage = self.storage_id or self.env["video.storage"].search([("active", "=", True)], limit=1)
        if not video_storage:
            raise UserError("Chưa có cấu hình R2 Video Storage active để lưu video thành phẩm.")

        VideoLib = self.env["video.library"]
        audio = self.audio_id or VideoLib._pick_audio(video_storage)
        if not audio:
            audio = self.env["audio.library"].search(
                [("active", "=", True), ("storage_path", "!=", False)], limit=1
            )
        if not audio:
            raise UserError("Không tìm thấy file nhạc MP3 active trên R2 để lồng vào video.")
        if not audio.storage_path or not audio.storage_id:
            raise UserError("File nhạc chưa sẵn sàng trên R2.")

        video_name = self.name or f"MV - {char.name} ({self.music_preset.upper()})"

        # Tạo record video.library
        video_rec = VideoLib.create(
            {
                "name": video_name,
                "storage_id": video_storage.id,
                "source_type": "music_video",
                "bg_image_id": bg.id,
                "character_image_id": char.id,
                "audio_id": audio.id,
                "music_layout": self.music_layout,
                "visualizer_style": self.visualizer_style,
                "visualizer_color": self.visualizer_color,
                "particle_effect": self.particle_effect,
                "music_preset": self.music_preset,
                "effect_preset": self.effect_preset,
                "state": "processing",
                "generated": False,
                "allow_republish": True,
            }
        )

        bg_client = R2Client(bg.storage_id)
        char_client = R2Client(char.storage_id)
        audio_client = R2Client(audio.storage_id)
        video_client = R2Client(video_storage)

        work_dir = _make_workdir("va_mvgen_")
        bg_local = os.path.join(work_dir, "bg_image.jpg")
        char_local = os.path.join(work_dir, "char_image.png")
        audio_local = os.path.join(work_dir, "audio.mp3")
        output_local = os.path.join(work_dir, "output.mp4")

        try:
            bg_client.download_file(bg.storage_path, bg_local)
            char_client.download_file(char.storage_path, char_local)
            audio_client.download_file(audio.storage_path, audio_local)

            # Lấy thời lượng: ưu tiên thời lượng bài nhạc nếu max_duration không chỉ định hoặc <= 0
            effective_duration = self.max_duration if (self.max_duration and self.max_duration > 0) else (audio.duration or 0.0)

            generate_music_video(
                bg_image_path=bg_local,
                character_image_path=char_local,
                audio_path=audio_local,
                output_path=output_local,
                layout=self.music_layout,
                visualizer_style=self.visualizer_style,
                visualizer_color=self.visualizer_color,
                particle_effect=self.particle_effect,
                music_preset=self.music_preset,
                effect_preset=self.effect_preset,
                max_duration=effective_duration,
            )

            object_key = make_flat_object_key("m", ".mp4", record_id=video_rec.id)
            video_client.upload_file(output_local, object_key, content_type="video/mp4")
            meta = probe_media(output_local)

            video_rec.write(
                {
                    "filename": object_key,
                    "storage_path": object_key,
                    "duration": meta.get("duration") or 0.0,
                    "width": meta.get("width") or 1080,
                    "height": meta.get("height") or 1920,
                    "fps": meta.get("fps") or 30.0,
                    "bitrate": meta.get("bitrate") or 0,
                    "file_size": meta.get("file_size") or os.path.getsize(output_local),
                    "state": "available",
                    "generated": True,
                }
            )

            video_rec.message_post(
                body=f"Tạo Video Ca Nhạc thành công: <b>{object_key}</b> ({meta.get('duration', 0):.1f}s)"
            )

        except Exception as exc:
            video_rec.write({"state": "draft"})
            _logger.exception("Failed to generate music video: %s", exc)
            raise UserError(f"Quá trình tạo Video Ca Nhạc thất bại: {exc}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        return {
            "type": "ir.actions.act_window",
            "res_model": "video.library",
            "res_id": video_rec.id,
            "view_mode": "form",
            "target": "current",
        }
