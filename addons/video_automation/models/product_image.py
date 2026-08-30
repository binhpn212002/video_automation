import base64
import logging
import mimetypes
import os
import shutil
import tempfile

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.ffmpeg_service import (
    generate_affiliate_video,
    probe_media,
)
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


class ProductImage(models.Model):
    _name = "product.image"
    _description = "Kho Ảnh Sản Phẩm (Affiliate Product Image)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date asc, id asc"

    name = fields.Char(string="Tên / SKU Sản Phẩm", required=True, tracking=True)
    image_type = fields.Selection(
        [
            ("product", "Ảnh Sản Phẩm (Product)"),
            ("character", "Ảnh Nhân Vật / Ca Sĩ (Character)"),
            ("background", "Ảnh Nền (Background)"),
        ],
        default="product",
        required=True,
        string="Loại Ảnh",
        tracking=True,
        index=True,
    )
    storage_id = fields.Many2one("image.storage", string="R2 Image Storage", required=True)
    storage_path = fields.Char(string="Object Path (R2)")
    cdn_url = fields.Char(
        string="CDN URL",
        compute="_compute_cdn_url",
        store=True,
        readonly=True,
        help="Public CDN URL từ Cloudflare R2",
    )
    upload_file = fields.Binary(string="File ảnh", attachment=False)
    upload_filename = fields.Char(string="Tên file gốc")
    width = fields.Integer(string="Width (px)")
    height = fields.Integer(string="Height (px)")
    file_size = fields.Integer(string="Dung lượng (bytes)")

    state = fields.Selection(
        [
            ("draft", "Nháp"),
            ("uploaded", "Đã tải lên R2"),
            ("generating", "Đang tạo Video"),
            ("generated", "Đã tạo Video"),
            ("failed", "Thất bại"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    generated = fields.Boolean(
        string="Đã Gen Video",
        default=False,
        index=True,
        tracking=True,
        help="Đánh dấu ảnh đã được tạo video hay chưa. Chỉ gen ảnh có generated=False.",
    )
    allow_multiple_gen = fields.Boolean(
        string="Cho phép gen nhiều lần",
        default=False,
        help="Nếu False: Chỉ tạo video đúng 1 lần (FIFO); Nếu True: cho phép tạo nhiều video từ 1 ảnh.",
    )
    generated_video_ids = fields.One2many(
        "video.library",
        "source_image_id",
        string="Video thành phẩm",
    )
    generated_video_count = fields.Integer(
        string="Số video đã tạo",
        compute="_compute_video_count",
        store=True,
    )
    last_generated_date = fields.Datetime(string="Ngày tạo video gần nhất")
    last_error = fields.Text(string="Chi tiết lỗi")

    default_hook = fields.Char(
        string="Hook Text Mặc định",
        default="Mẫu này đang cực hot 🔥",
        help="Xuất hiện ở Safe Area phía trên trong 2.5s đầu.",
    )
    default_cta = fields.Char(
        string="CTA Text Mặc định",
        default="Xem sản phẩm bên dưới ↓",
        help="Xuất hiện ở Safe Area phía dưới 4s cuối.",
    )
    active = fields.Boolean(default=True)

    preview_html = fields.Html(
        string="Xem trước ảnh",
        compute="_compute_preview_html",
        sanitize=False,
    )

    @api.depends("generated_video_ids")
    def _compute_video_count(self):
        for rec in self:
            rec.generated_video_count = len(rec.generated_video_ids)

    @api.depends("storage_id", "storage_id.cdn_domain", "storage_id.bucket_name", "storage_path")
    def _compute_cdn_url(self):
        for img in self:
            if img.storage_id and img.storage_path:
                img.cdn_url = R2Client(img.storage_id).cdn_url(img.storage_path)
            else:
                img.cdn_url = False

    @api.depends("cdn_url", "name")
    def _compute_preview_html(self):
        for img in self:
            if img.cdn_url:
                url = img.cdn_url.replace('"', "&quot;")
                title = (img.name or "Image").replace("<", "&lt;").replace(">", "&gt;")
                img.preview_html = (
                    f'<div class="o_va_image_preview" style="text-align:center;padding:10px;">'
                    f'<img src="{url}" alt="{title}" style="max-width:100%;max-height:400px;object-fit:contain;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.15);"/>'
                    f'</div>'
                )
            else:
                img.preview_html = (
                    '<p class="text-muted text-center" style="padding:20px;">Chưa có CDN URL — Hãy upload file lên R2.</p>'
                )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "storage_id" in fields_list and not res.get("storage_id"):
            storage = self.env["image.storage"].search([("active", "=", True)], limit=1)
            if storage:
                res["storage_id"] = storage.id
        return res

    def action_open_cdn_url(self):
        self.ensure_one()
        if not self.cdn_url:
            raise UserError("Chưa có CDN URL.")
        return {
            "type": "ir.actions.act_url",
            "url": self.cdn_url,
            "target": "new",
        }

    def action_rebuild_cdn_url(self):
        self._compute_cdn_url()
        return True

    def action_upload_to_r2(self):
        """Upload file ảnh từ thiết bị lên Cloudflare R2."""
        for img in self:
            img._upload_local_file_to_r2()
        return True

    def _upload_local_file_to_r2(self):
        self.ensure_one()
        if not self.upload_file:
            raise UserError("Vui lòng chọn file ảnh trước khi upload.")
        if not self.storage_id:
            raise UserError("Vui lòng chọn R2 Storage.")

        client = R2Client(self.storage_id)
        original_name = self.upload_filename or "image.jpg"
        if not self.name:
            self.name = os.path.splitext(os.path.basename(original_name))[0]

        ext = os.path.splitext(original_name)[1].lower() or ".jpg"
        if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
            ext = ".jpg"

        object_key = make_flat_object_key("i", ext, record_id=self.id)
        content_type = mimetypes.guess_type(object_key)[0] or "image/jpeg"
        work_dir = _make_workdir("va_iupload_")
        local_path = os.path.join(work_dir, os.path.basename(object_key))
        try:
            with open(local_path, "wb") as fh:
                fh.write(base64.b64decode(self.upload_file))
            client.upload_file(local_path, object_key, content_type=content_type)
            meta = probe_media(local_path)
            self.write(
                {
                    "storage_path": object_key,
                    "width": meta.get("width") or 0,
                    "height": meta.get("height") or 0,
                    "file_size": meta.get("file_size") or os.path.getsize(local_path),
                    "state": "uploaded",
                    "upload_file": False,
                    "upload_filename": False,
                    "last_error": False,
                }
            )
            self.message_post(body=f"Đã upload ảnh lên R2: <b>{object_key}</b>")
        except Exception as exc:
            self.write({"state": "failed", "last_error": str(exc)})
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @api.model
    def _pending_image_candidates(self, storage=None, limit=None):
        """
        Lấy danh sách các ảnh sản phẩm CHƯA TỪNG ĐƯỢC GEN (generated=False)
        theo thứ tự FIFO (ảnh upload trước được tạo video trước).
        """
        domain = [
            ("active", "=", True),
            ("storage_path", "!=", False),
            ("generated", "=", False),
            ("image_type", "=", "product"),
            ("state", "in", ("uploaded", "failed")),
        ]
        if storage and getattr(storage, "_name", None) == "image.storage":
            domain.append(("storage_id", "=", storage.id))
        return self.search(domain, order="create_date asc, id asc", limit=limit)

    def action_open_gen_wizard(self):
        self.ensure_one()
        if not self.storage_path:
            raise UserError("Ảnh chưa được tải lên R2 — không thể tạo video.")
        return {
            "type": "ir.actions.act_window",
            "name": "Tạo Video TikTok từ Ảnh",
            "res_model": "video.generate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_image_id": self.id,
                "default_hook_text": self.default_hook,
                "default_cta_text": self.default_cta,
                "default_output_name": f"{self.name} - TikTok Affiliate",
            },
        }

    def generate_affiliate_video(
        self,
        audio=None,
        video_storage=None,
        effect_preset="normal",
        motion_effect="zoom_bounce",
        hook_text=None,
        cta_text=None,
        output_name=None,
        max_duration=25.0,
    ):
        """
        Sinh video TikTok Affiliate 9:16 (1080x1920, 30 FPS) từ ảnh này và file MP3.
        Áp dụng Ken Burns (Slow Zoom), Beat Bounce, Beat Pulse + White Flash + Safe Area text.
        Thời lượng mặc định tối đa 25s (nếu bài nhạc dài hơn 25s sẽ tự động cắt ngắn lại).
        """
        self.ensure_one()
        if not self.storage_id or not self.storage_path:
            raise UserError("Ảnh sản phẩm chưa có trên R2 (thiếu storage_path).")

        if not self.allow_multiple_gen and self.generated:
            raise UserError(f"Ảnh '{self.name}' đã được tạo video rồi (chỉ tạo 1 lần).")

        VideoStorage = self.env["video.storage"]
        video_storage = video_storage or VideoStorage.search([("active", "=", True)], limit=1)
        if not video_storage:
            raise UserError("Chưa có cấu hình R2 Video Storage active để lưu video thành phẩm.")

        VideoLib = self.env["video.library"]
        audio = audio or VideoLib._pick_audio(video_storage)
        if not audio:
            audio = self.env["audio.library"].search(
                [("active", "=", True), ("storage_path", "!=", False)], limit=1
            )
        if not audio:
            raise UserError("Không có file âm thanh active trên R2 để ghép nhạc.")
        if not audio.storage_path or not audio.storage_id:
            raise UserError("File âm thanh chưa sẵn sàng trên R2.")

        self.write({"state": "generating"})

        # Tên video đầu ra
        name = output_name or f"{self.name} - TikTok Affiliate"
        hook = hook_text if hook_text is not None else (self.default_hook or "")
        cta = cta_text if cta_text is not None else (self.default_cta or "")

        # Tạo record video.library trên video_storage
        child = VideoLib.create(
            {
                "name": name,
                "storage_id": video_storage.id,
                "source_type": "affiliate_image",
                "source_image_id": self.id,
                "original_storage_path": self.storage_path,
                "hook_text": hook,
                "cta_text": cta,
                "effect_preset": effect_preset,
                "motion_effect": motion_effect or "zoom_bounce",
                "state": "processing",
                "generated": False,
                "allow_republish": True,
            }
        )

        image_client = R2Client(self.storage_id)
        audio_client = R2Client(audio.storage_id)
        video_client = R2Client(video_storage)
        work_dir = _make_workdir("va_affgen_")
        image_local = os.path.join(work_dir, "product_image.jpg")
        audio_local = os.path.join(work_dir, "audio.mp3")
        output_local = os.path.join(work_dir, "output.mp4")

        try:
            image_client.download_file(self.storage_path, image_local)
            audio_client.download_file(audio.storage_path, audio_local)

            # Lấy beats cache hoặc tính toán
            beats = audio.get_or_compute_beats(audio_local_path=audio_local)

            # Render 1-Pass FFmpeg (kèm Ken Burns + Beat Bounce, cắt tối đa max_duration)
            generate_affiliate_video(
                image_path=image_local,
                audio_path=audio_local,
                output_path=output_local,
                beat_data=beats,
                effect_preset=effect_preset,
                motion_effect=motion_effect,
                hook_text=hook,
                cta_text=cta,
                max_duration=max_duration or 25.0,
            )

            # Upload video kết quả lên R2 Video Storage
            object_key = make_flat_object_key("g", ".mp4", record_id=child.id)
            video_client.upload_file(output_local, object_key, content_type="video/mp4")
            meta = probe_media(output_local)

            child.write(
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
                    "audio_id": audio.id,
                }
            )

            # Cập nhật trạng thái của ảnh sản phẩm
            self.write(
                {
                    "state": "generated",
                    "generated": True,
                    "last_generated_date": fields.Datetime.now(),
                    "last_error": False,
                }
            )

            self.message_post(
                body=(
                    f"Đã tạo thành công Video TikTok: <b>{child.name}</b> (id={child.id}) "
                    f"kèm nhạc <b>{audio.name}</b> → {object_key}"
                )
            )
            child.message_post(
                body=f"Tạo từ ảnh sản phẩm <b>{self.name}</b> + nhạc <b>{audio.name}</b> (Motion: {motion_effect}, Pulse: {effect_preset})."
            )
            return child

        except Exception as exc:
            child.unlink()
            self.write({"state": "failed", "last_error": str(exc)})
            _logger.exception("Failed to generate affiliate video for product.image %s: %s", self.id, exc)
            raise UserError(f"Tạo video thất bại: {exc}") from exc
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def action_batch_generate_videos(self):
        """Action hàng loạt từ danh sách ảnh (Tree View). Chỉ gen ảnh generated=False."""
        created_videos = self.env["video.library"]
        skipped_count = 0
        success_count = 0
        error_messages = []

        for img in self:
            if img.generated and not img.allow_multiple_gen:
                skipped_count += 1
                continue
            if not img.storage_path:
                skipped_count += 1
                continue

            try:
                video = img.generate_affiliate_video()
                created_videos |= video
                success_count += 1
            except Exception as exc:
                error_messages.append(f"{img.name}: {exc}")

        msg = f"Đã tạo {success_count} video từ kho ảnh. Bỏ qua {skipped_count} ảnh đã tạo trước đó."
        if error_messages:
            msg += f"<br/>Lỗi ({len(error_messages)} ảnh):<br/>" + "<br/>".join(error_messages[:5])

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Tạo Video TikTok Hàng Loạt",
                "message": msg,
                "type": "success" if success_count > 0 else "warning",
                "sticky": bool(error_messages),
            },
        }

    def unlink(self):
        """Xóa file ảnh trên Cloudflare R2 khi xóa record."""
        for img in self:
            if img.storage_id and img.storage_path:
                try:
                    client = R2Client(img.storage_id)
                    client.delete_file(img.storage_path)
                except Exception as exc:
                    _logger.warning("Không thể xóa file R2 khi xóa ảnh %s (%s): %s", img.id, img.storage_path, exc)
        return super().unlink()

