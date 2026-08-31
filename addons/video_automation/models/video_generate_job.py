import logging
import os
import shutil
import tempfile
import threading
import uuid

import odoo
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


def _run_jobs_in_background(db_name, uid, job_ids):
    """Background worker thread to sequentially execute video rendering jobs without HTTP timeout."""
    _logger.info("Background video worker thread started for %s jobs...", len(job_ids))
    try:
        with odoo.registry(db_name).cursor() as cr:
            env = api.Environment(cr, uid, {})
            for jid in job_ids:
                job = env["video.generate.job"].browse(jid)
                if not job.exists() or job.state == "completed":
                    continue
                try:
                    job._execute_single_job()
                    cr.commit()
                except Exception as exc:
                    cr.rollback()
                    _logger.exception("Background render error on job %s: %s", jid, exc)
                    try:
                        job.write({"state": "failed", "error_message": str(exc), "finish_date": fields.Datetime.now()})
                        cr.commit()
                    except Exception:
                        pass
        _logger.info("Background video worker thread finished processing %s jobs.", len(job_ids))
    except Exception as exc:
        _logger.exception("Fatal error in background video worker thread: %s", exc)


class VideoGenerateJob(models.Model):
    _name = "video.generate.job"
    _description = "Tiến trình tạo Video (Generate Job)"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Mã Job (Job ID)",
        required=True,
        index=True,
        default=lambda self: f"job_{uuid.uuid4().hex[:10]}",
        readonly=True,
    )
    job_type = fields.Selection(
        [
            ("affiliate", "TikTok Affiliate (Ảnh sản phẩm)"),
            ("music_video", "Video Ca Nhạc (Background + Nhân vật + Nhạc)"),
        ],
        default="affiliate",
        string="Loại Video",
        required=True,
        index=True,
    )
    image_id = fields.Many2one(
        "product.image",
        string="Ảnh sản phẩm",
        required=False,
        ondelete="cascade",
    )
    bg_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Background",
        domain=[("image_type", "=", "background")],
        ondelete="set null",
    )
    character_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Nhân vật / Ca sĩ",
        domain=[("image_type", "=", "character")],
        ondelete="set null",
    )
    audio_id = fields.Many2one(
        "audio.library",
        string="Audio / Nhạc nền",
        ondelete="set null",
    )
    storage_id = fields.Many2one(
        "video.storage",
        string="R2 Video Storage",
        default=lambda self: self.env["video.storage"].search([("active", "=", True)], limit=1).id,
        help="Cấu hình R2 Video Storage để lưu video thành phẩm.",
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
    )
    particle_effect = fields.Selection(
        [
            ("none", "Không có"),
            ("snow_fall", "Tuyết rơi lãng mạn (Snow Fall)"),
            ("rain_drops", "Giọt mưa rơi (Rain Drops)"),
            ("dust_bokeh", "Hạt bụi sáng (Dust & Bokeh)"),
            ("stage_lights", "Tia đèn sân khấu (Stage Lights)"),
        ],
        default="snow_fall",
        string="Hiệu Ứng Không Khí",
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
    )
    effect_preset = fields.Selection(
        [
            ("soft", "Soft"),
            ("normal", "Normal"),
            ("strong", "Strong"),
        ],
        default="normal",
        string="Hiệu ứng Beat Pulse",
    )
    motion_effect = fields.Selection(
        [
            ("zoom_bounce", "Ken Burns + Beat Bounce"),
            ("zoom_in", "Slow Zoom In"),
            ("zoom_out", "Slow Zoom Out"),
            ("bounce_only", "Beat Bounce Only"),
            ("none", "None (Static)"),
        ],
        default="zoom_bounce",
        string="Hiệu ứng Motion",
    )
    hook_text = fields.Char(string="Hook Text")
    cta_text = fields.Char(string="CTA Text")
    output_video_name = fields.Char(string="Tên Video Thành Phẩm")
    max_duration = fields.Float(string="Thời lượng tối đa (s)", default=0.0)
    flash_effect = fields.Boolean(string="White Flash", default=True)

    state = fields.Selection(
        [
            ("draft", "Chờ xử lý"),
            ("processing", "Đang render"),
            ("completed", "Hoàn thành"),
            ("failed", "Thất bại"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )

    video_id = fields.Many2one(
        "video.library",
        string="Video thành phẩm",
        readonly=True,
    )
    video_url = fields.Char(
        string="CDN URL",
        related="video_id.cdn_url",
        readonly=True,
    )
    duration = fields.Float(
        string="Thời lượng (s)",
        related="video_id.duration",
        readonly=True,
    )
    error_message = fields.Text(string="Chi tiết lỗi", readonly=True)
    finish_date = fields.Datetime(string="Thời gian hoàn thành", readonly=True)

    def action_run_job(self):
        """Kích hoạt chạy job qua background worker thread để tránh đơ web và tránh HTTP timeout."""
        pending_jobs = self.filtered(lambda j: j.state != "completed")
        if not pending_jobs:
            return True

        if self.env.context.get("sync_execution"):
            for job in pending_jobs:
                job._execute_single_job()
            return True

        pending_jobs.write({"state": "processing", "error_message": False})
        self.env.cr.commit()

        db_name = self.env.cr.dbname
        uid = self.env.uid
        job_ids = pending_jobs.ids

        threading.Thread(target=_run_jobs_in_background, args=(db_name, uid, job_ids), daemon=True).start()

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Tiến trình render đang chạy ngầm ⏳",
                "message": f"Hệ thống đang render {len(pending_jobs)} job(s) trong nền. Trạng thái sẽ tự động cập nhật khi hoàn thành!",
                "type": "info",
                "sticky": False,
            },
        }

    def _execute_single_job(self):
        """Thực thi trực tiếp 1 job render video."""
        self.ensure_one()
        if self.state == "completed":
            return True

        VideoLib = self.env["video.library"]
        self.write({"state": "processing", "error_message": False})

        try:
            if self.job_type == "music_video":
                bg = self.bg_image_id
                char = self.character_image_id
                audio = self.audio_id
                video_storage = self.storage_id or self.env["video.storage"].search([("active", "=", True)], limit=1)

                if not bg or not bg.storage_path:
                    raise UserError("Ảnh Background chưa sẵn sàng trên R2.")
                if not char or not char.storage_path:
                    raise UserError("Ảnh Nhân vật chưa sẵn sàng trên R2.")
                if not audio or not audio.storage_path:
                    raise UserError("File nhạc chưa sẵn sàng trên R2.")
                if not video_storage:
                    raise UserError("Chưa cấu hình R2 Video Storage.")

                video_name = self.output_video_name or f"MV - {audio.name} ({char.name})"

                video_rec = VideoLib.create(
                    {
                        "name": video_name,
                        "storage_id": video_storage.id,
                        "source_type": "music_video",
                        "bg_image_id": bg.id,
                        "character_image_id": char.id,
                        "audio_id": audio.id,
                        "music_layout": self.music_layout or "spotify_card",
                        "visualizer_style": self.visualizer_style or "spectrum_bars",
                        "visualizer_color": self.visualizer_color or "cyan_neon",
                        "particle_effect": self.particle_effect or "snow_fall",
                        "music_preset": self.music_preset or "lofi_chill",
                        "effect_preset": self.effect_preset or "normal",
                        "state": "processing",
                        "generated": False,
                        "allow_republish": True,
                    }
                )

                bg_ext = os.path.splitext(bg.storage_path)[1] or ".jpg"
                char_ext = os.path.splitext(char.storage_path)[1] or ".png"
                audio_ext = os.path.splitext(audio.storage_path)[1] or ".mp3"

                work_dir = _make_workdir("va_job_mv_")
                bg_local = os.path.join(work_dir, f"bg_image{bg_ext}")
                char_local = os.path.join(work_dir, f"char_image{char_ext}")
                audio_local = os.path.join(work_dir, f"audio{audio_ext}")
                output_local = os.path.join(work_dir, "output.mp4")

                try:
                    R2Client(bg.storage_id).download_file(bg.storage_path, bg_local)
                    R2Client(char.storage_id).download_file(char.storage_path, char_local)
                    R2Client(audio.storage_id).download_file(audio.storage_path, audio_local)

                    effective_duration = self.max_duration if (self.max_duration and self.max_duration > 0) else (audio.duration or 0.0)

                    generate_music_video(
                        bg_image_path=bg_local,
                        character_image_path=char_local,
                        audio_path=audio_local,
                        output_path=output_local,
                        layout=self.music_layout or "spotify_card",
                        visualizer_style=self.visualizer_style or "spectrum_bars",
                        visualizer_color=self.visualizer_color or "cyan_neon",
                        particle_effect=self.particle_effect or "snow_fall",
                        music_preset=self.music_preset or "lofi_chill",
                        effect_preset=self.effect_preset or "normal",
                        max_duration=effective_duration,
                    )

                    object_key = make_flat_object_key("m", ".mp4", record_id=video_rec.id)
                    R2Client(video_storage).upload_file(output_local, object_key, content_type="video/mp4")
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
                            "file_size": meta.get("file_size") or (os.path.getsize(output_local) if os.path.exists(output_local) else 0),
                            "state": "available",
                            "generated": True,
                        }
                    )
                    video_rec.message_post(
                        body=f"Job {self.name}: Tạo Video Ca Nhạc thành công: <b>{object_key}</b> ({meta.get('duration', 0):.1f}s)"
                    )

                    self.write(
                        {
                            "video_id": video_rec.id,
                            "state": "completed",
                            "finish_date": fields.Datetime.now(),
                            "error_message": False,
                        }
                    )
                    self.message_post(
                        body=f"Job hoàn thành: Đã tạo video <b>{video_rec.name}</b> (CDN: <a href='{video_rec.cdn_url}'>{video_rec.cdn_url}</a>)"
                    )
                finally:
                    shutil.rmtree(work_dir, ignore_errors=True)

            else:
                # Affiliate video
                if not self.image_id:
                    raise UserError("Vui lòng chọn Ảnh sản phẩm cho Affiliate Job.")
                video = self.image_id.generate_affiliate_video(
                    audio=self.audio_id or None,
                    video_storage=self.storage_id or None,
                    effect_preset=self.effect_preset or "normal",
                    motion_effect=self.motion_effect or "zoom_bounce",
                    hook_text=self.hook_text,
                    cta_text=self.cta_text,
                    max_duration=self.max_duration or 25.0,
                )
                self.write(
                    {
                        "video_id": video.id,
                        "state": "completed",
                        "finish_date": fields.Datetime.now(),
                        "error_message": False,
                    }
                )
                self.message_post(
                    body=f"Job hoàn thành: Đã tạo video <b>{video.name}</b> (CDN: <a href='{video.cdn_url}'>{video.cdn_url}</a>)"
                )
        except Exception as exc:
            self.write(
                {
                    "state": "failed",
                    "error_message": str(exc),
                    "finish_date": fields.Datetime.now(),
                }
            )
            self.message_post(body=f"Job thất bại: {exc}")
            _logger.exception("Video generate job %s failed: %s", self.name, exc)
        return True
