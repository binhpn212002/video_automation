import logging
import os
import shutil
import tempfile
import uuid

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


class VideoGenerateJob(models.Model):
    _name = "video.generate.job"
    _description = "Tiến trình tạo Video (Generate Job)"
    _inherit = ["mail.thread"]
    _order = "priority desc, scheduled_date asc, create_date desc, id desc"

    name = fields.Char(
        string="Mã Job (Job ID)",
        required=True,
        index=True,
        default=lambda self: f"job_{uuid.uuid4().hex[:10]}",
        readonly=True,
    )
    job_type = fields.Selection(
        [
            ("music_video", "Video Ca Nhạc"),
            ("affiliate", "Video Affiliate Sản Phẩm"),
        ],
        string="Loại Job",
        default="music_video",
        required=True,
        index=True,
    )

    # --- Cấu hình cho Video Affiliate ---
    image_id = fields.Many2one(
        "product.image",
        string="Ảnh sản phẩm",
        required=False,
        ondelete="cascade",
        domain=[("image_type", "=", "product")],
    )
    hook_text = fields.Char(string="Hook Text")
    cta_text = fields.Char(string="CTA Text")
    flash_effect = fields.Boolean(string="White Flash", default=True)

    # --- Cấu hình cho Video Ca Nhạc ---
    bg_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Background",
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "background")],
        help="Ảnh nền cho video ca nhạc.",
    )
    character_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Nhân vật / Ca sĩ",
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "character")],
        help="Ảnh nhân vật hoặc ca sĩ (khuyên dùng ảnh PNG tách nền).",
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

    # --- Cấu hình chung ---
    audio_id = fields.Many2one(
        "audio.library",
        string="Audio / Nhạc nền",
        domain=[("active", "=", True), ("storage_path", "!=", False)],
        ondelete="set null",
    )
    storage_id = fields.Many2one(
        "video.storage",
        string="R2 Video Storage",
        default=lambda self: self.env["video.storage"].search([("active", "=", True)], limit=1).id,
        help="Cấu hình R2 Video Storage để lưu video thành phẩm.",
    )
    effect_preset = fields.Selection(
        [
            ("soft", "Soft (Nhẹ nhàng)"),
            ("normal", "Normal (Tiêu chuẩn)"),
            ("strong", "Strong (Mạnh mẽ)"),
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
    max_duration = fields.Float(
        string="Thời lượng tối đa (s)",
        default=0.0,
        help="0 để lấy trọn vẹn toàn bộ độ dài của bài nhạc.",
    )

    # --- Lập lịch và Thứ tự ưu tiên ---
    priority = fields.Selection(
        [
            ("0", "Thấp"),
            ("1", "Bình thường"),
            ("2", "Ưu tiên"),
            ("3", "Khẩn cấp"),
        ],
        default="1",
        string="Độ ưu tiên",
        index=True,
    )
    is_scheduled = fields.Boolean(
        string="Đã lập lịch tự động",
        default=False,
        index=True,
        help="Nếu bật, Cron định kỳ sẽ tự động thực thi khi đến thời điểm hẹn.",
    )
    scheduled_date = fields.Datetime(
        string="Lịch chạy dự kiến",
        index=True,
        help="Thời điểm dự kiến Cron sẽ thực thi job này.",
    )

    # --- Trạng thái và Kết quả ---
    state = fields.Selection(
        [
            ("draft", "Chờ xử lý"),
            ("processing", "Đang render"),
            ("completed", "Hoàn thành"),
            ("failed", "Thất bại"),
            ("cancelled", "Đã hủy"),
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
        """Thực thi job render video (Affiliate hoặc Music Video)."""
        for job in self:
            if job.state == "completed":
                _logger.info("Job %s đã hoàn thành trước đó, bỏ qua.", job.name)
                continue
            _logger.info(">>> [BẮT ĐẦU RENDER] Job: %s (Loại: %s) | ID: %s", job.name, job.job_type, job.id)
            job.write({"state": "processing", "error_message": False})
            job_type_label = "Video Ca Nhạc" if job.job_type == "music_video" else "Video Affiliate Sản Phẩm"
            job.message_post(
                body=f"🚀 <b>Bắt đầu Render Video</b> ({job_type_label}). Hệ thống đang chuẩn bị tài nguyên và xử lý..."
            )
            # Commit ngay lập tức để trạng thái 'Đang render' và Chatter hiển thị ngay trên UI
            self.env.cr.commit()
            try:
                if job.job_type == "music_video":
                    video = job._run_music_video()
                else:
                    video = job._run_affiliate_video()

                job.write(
                    {
                        "video_id": video.id,
                        "state": "completed",
                        "finish_date": fields.Datetime.now(),
                        "error_message": False,
                    }
                )
                job.message_post(
                    body=f"✅ <b>Job hoàn thành</b>: Đã tạo video <b>{video.name}</b> (Thời lượng: {video.duration:.1f}s | CDN: <a href='{video.cdn_url}' target='_blank'>{video.cdn_url}</a>)"
                )
                _logger.info("<<< [RENDER THÀNH CÔNG] Job: %s -> Video: %s (ID: %s)", job.name, video.name, video.id)
            except Exception as exc:
                job.write(
                    {
                        "state": "failed",
                        "error_message": str(exc),
                        "finish_date": fields.Datetime.now(),
                    }
                )
                job.message_post(body=f"❌ <b>Job thất bại</b>: {exc}")
                _logger.exception("Video generate job %s failed: %s", job.name, exc)
        return True

    def _run_affiliate_video(self):
        """Render video affiliate từ ảnh sản phẩm + audio."""
        self.ensure_one()
        if not self.image_id:
            raise UserError("Job Affiliate thiếu ảnh sản phẩm.")
        return self.image_id.generate_affiliate_video(
            audio=self.audio_id or None,
            video_storage=self.storage_id or None,
            effect_preset=self.effect_preset or "normal",
            motion_effect=self.motion_effect or "zoom_bounce",
            hook_text=self.hook_text,
            cta_text=self.cta_text,
            max_duration=self.max_duration or 25.0,
        )

    def _run_music_video(self):
        """Render video ca nhạc từ ảnh background + ảnh nhân vật + audio."""
        self.ensure_one()
        bg = self.bg_image_id
        char = self.character_image_id
        if not bg or not bg.storage_path or not bg.storage_id:
            raise UserError("Job thiếu Ảnh Background hợp lệ đã tải lên Cloudflare R2.")
        if not char or not char.storage_path or not char.storage_id:
            raise UserError("Job thiếu Ảnh Nhân vật hợp lệ đã tải lên Cloudflare R2.")

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

        video_name = f"MV - {char.name} ({self.music_preset.upper() if self.music_preset else 'LOFI'})"

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

        bg_ext = os.path.splitext(bg.storage_path or "")[1] or ".jpg"
        char_ext = os.path.splitext(char.storage_path or "")[1] or ".png"
        audio_ext = os.path.splitext(audio.storage_path or "")[1] or ".mp3"

        work_dir = _make_workdir("va_mvjob_")
        bg_local = os.path.join(work_dir, f"bg_image{bg_ext}")
        char_local = os.path.join(work_dir, f"char_image{char_ext}")
        audio_local = os.path.join(work_dir, f"audio{audio_ext}")
        output_local = os.path.join(work_dir, "output.mp4")

        try:
            _logger.info("[%s] [1/4] Đang tải tài nguyên từ Cloudflare R2 (Background: %s, Character: %s, Audio: %s)...", self.name, bg.name, char.name, audio.name)
            bg_client.download_file(bg.storage_path, bg_local)
            char_client.download_file(char.storage_path, char_local)
            audio_client.download_file(audio.storage_path, audio_local)
            _logger.info("[%s] Tải tài nguyên thành công. Workdir: %s", self.name, work_dir)

            effective_duration = (
                self.max_duration
                if (self.max_duration and self.max_duration > 0)
                else (audio.duration or 0.0)
            )

            _logger.info(
                "[%s] [2/4] Bắt đầu render FFmpeg (Layout: %s, Visualizer: %s - %s, Particle: %s, Preset: %s, Thời lượng: %.1fs)...",
                self.name,
                self.music_layout,
                self.visualizer_style,
                self.visualizer_color,
                self.particle_effect,
                self.music_preset,
                effective_duration,
            )

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

            file_size_mb = round(os.path.getsize(output_local) / (1024 * 1024), 2)
            _logger.info("[%s] [3/4] Render video xong (Dung lượng: %s MB). Đang tải lên Cloudflare R2...", self.name, file_size_mb)

            object_key = make_flat_object_key("m", ".mp4", record_id=video_rec.id)
            video_client.upload_file(output_local, object_key, content_type="video/mp4")
            meta = probe_media(output_local)
            _logger.info("[%s] [4/4] Upload R2 hoàn tất -> Key: %s. Cập nhật bản ghi video...", self.name, object_key)

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
            return video_rec
        except Exception:
            _logger.exception("[%s] Lỗi trong quá trình render music video", self.name)
            video_rec.write({"state": "draft"})
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def action_reset_draft(self):
        """Đặt lại trạng thái Chờ xử lý để chạy lại."""
        for job in self:
            if job.state in ("failed", "cancelled"):
                job.write({"state": "draft", "error_message": False})
        return True

    def action_cancel(self):
        """Hủy job khi chưa render xong."""
        for job in self:
            if job.state in ("draft", "failed"):
                job.write({"state": "cancelled"})
        return True

    @api.model
    def cron_process_jobs(self, batch_size=1):
        """
        Cron định kỳ thực thi các jobs đang chờ (draft) đã được lập lịch hoặc đến giờ chạy.
        Mỗi lần xử lý tuần tự batch_size (mặc định 1) để tránh làm nghẽn CPU server.
        """
        now = fields.Datetime.now()
        domain = [
            ("state", "=", "draft"),
            ("is_scheduled", "=", True),
            "|",
            ("scheduled_date", "=", False),
            ("scheduled_date", "<=", now),
        ]
        jobs = self.search(domain, order="priority desc, scheduled_date asc, id asc", limit=batch_size)
        if jobs:
            _logger.info(
                "Cron VA Video Generate Jobs: Tìm thấy %d job đến hạn chạy (Hiện tại UTC: %s): %s",
                len(jobs),
                now,
                ", ".join(f"{j.name} (Lịch: {j.scheduled_date})" for j in jobs),
            )
        else:
            _logger.info(
                "Cron VA Video Generate Jobs: Kiểm tra định kỳ (Hiện tại UTC: %s) - Chưa có job nào đến giờ cần chạy.",
                now,
            )
        for job in jobs:
            try:
                _logger.info("Cron bắt đầu xử lý job: %s (Loại: %s | Lịch hẹn: %s)", job.name, job.job_type, job.scheduled_date)
                job.action_run_job()
                self.env.cr.commit()
            except Exception as e:
                _logger.exception("Error processing video generate job %s in cron: %s", job.name, e)
