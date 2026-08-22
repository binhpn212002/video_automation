import base64
import logging
import os
import shutil
import tempfile

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.ffmpeg_service import generate_video, probe_media
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


class VideoLibrary(models.Model):
    _name = "video.library"
    _description = "Video Library"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date asc, id asc"

    name = fields.Char(required=True, tracking=True)
    filename = fields.Char()
    storage_id = fields.Many2one("video.storage", required=True)
    storage_path = fields.Char(string="Object Path")
    cdn_url = fields.Char(
        compute="_compute_cdn_url",
        store=True,
        readonly=True,
        help="Computed from Storage CDN domain + object path.",
    )
    thumbnail_url = fields.Char()
    duration = fields.Float()
    width = fields.Integer()
    height = fields.Integer()
    fps = fields.Float()
    bitrate = fields.Integer()
    file_size = fields.Integer()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("uploaded", "Uploaded"),
            ("processing", "Processing"),
            ("available", "Available"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    scheduled_count = fields.Integer(default=0)
    published_count = fields.Integer(default=0)
    allow_republish = fields.Boolean(
        default=True,
        help="If False, never select again after any successful publish.",
    )
    generated = fields.Boolean(
        string="Generated",
        default=False,
        tracking=True,
        help="True sau khi Gen Video (merge nhạc) thành công.",
    )
    source_type = fields.Selection(
        [
            ("raw_video", "Raw Video"),
            ("affiliate_image", "Affiliate Image"),
        ],
        default="raw_video",
        required=True,
        string="Nguồn tạo",
        tracking=True,
    )
    source_image_id = fields.Many2one(
        "product.image",
        string="Ảnh sản phẩm gốc",
        ondelete="set null",
        index=True,
        help="Ảnh sản phẩm Affiliate dùng để tạo video này.",
    )
    hook_text = fields.Char(string="Hook Text", help="Câu Hook ở đầu video.")
    cta_text = fields.Char(string="CTA Text", help="Câu Call-To-Action ở cuối video.")
    effect_preset = fields.Selection(
        [
            ("soft", "Soft (Nhẹ nhàng)"),
            ("normal", "Normal (Tiêu chuẩn)"),
            ("strong", "Strong (Mạnh mẽ)"),
        ],
        default="normal",
        string="Cường độ Beat Pulse",
    )
    original_storage_path = fields.Char(
        string="Original Path",
        help="Đường dẫn video gốc trên R2 (trước khi generate).",
        readonly=True,
    )
    audio_id = fields.Many2one(
        "audio.library",
        string="Audio used",
        help="Audio đã dùng khi generate.",
        readonly=True,
    )
    source_video_id = fields.Many2one(
        "video.library",
        string="Video gốc",
        ondelete="set null",
        index=True,
        help="Raw video dùng để generate output này. Output là record mới, không ghi đè gốc.",
    )
    generated_child_ids = fields.One2many(
        "video.library",
        "source_video_id",
        string="Video đã generate",
    )
    publish_queue_ids = fields.One2many(
        "tiktok.publish.queue", "video_id", string="Publish Queue"
    )
    upload_history_ids = fields.One2many(
        "tiktok.upload.history", "video_id", string="Lịch sử đăng TikTok"
    )
    is_posted = fields.Boolean(
        string="Đã đăng",
        compute="_compute_is_posted",
        store=True,
        help="True nếu video đã đăng thành công ít nhất 1 tài khoản TikTok.",
    )
    posted_account_ids = fields.Many2many(
        "tiktok.account",
        string="Tài khoản đã đăng",
        compute="_compute_posted_account_ids",
        help="Các tài khoản TikTok đã đăng video này thành công.",
    )
    # Temporary local file — cleared after R2 upload (not kept in Odoo)
    upload_file = fields.Binary(string="Video file", attachment=False)
    upload_filename = fields.Char()
    preview_html = fields.Html(
        string="Preview",
        compute="_compute_preview_html",
        sanitize=False,
    )

    @api.depends(
        "upload_history_ids",
        "upload_history_ids.status",
        "upload_history_ids.tiktok_account_id",
    )
    def _compute_is_posted(self):
        for video in self:
            video.is_posted = any(h.status == "success" for h in video.upload_history_ids)

    @api.depends(
        "upload_history_ids",
        "upload_history_ids.status",
        "upload_history_ids.tiktok_account_id",
    )
    def _compute_posted_account_ids(self):
        for video in self:
            success = video.upload_history_ids.filtered(lambda h: h.status == "success")
            video.posted_account_ids = success.mapped("tiktok_account_id")

    @api.depends("storage_id", "storage_id.cdn_domain", "storage_id.bucket_name", "storage_path")
    def _compute_cdn_url(self):
        for video in self:
            if video.storage_id and video.storage_path:
                video.cdn_url = R2Client(video.storage_id).cdn_url(video.storage_path)
            else:
                video.cdn_url = False

    @api.depends("cdn_url", "name")
    def _compute_preview_html(self):
        for video in self:
            if video.cdn_url:
                url = video.cdn_url.replace('"', "&quot;")
                title = (video.name or "Video").replace("<", "&lt;").replace(">", "&gt;")
                video.preview_html = (
                    f'<div class="o_va_video_preview">'
                    f'<video controls preload="metadata" style="max-width:100%;max-height:420px;background:#000;">'
                    f'<source src="{url}" type="video/mp4"/>'
                    f"Trình duyệt không hỗ trợ video. "
                    f'<a href="{url}" target="_blank" rel="noopener">Mở {title}</a>'
                    f"</video></div>"
                )
            else:
                video.preview_html = (
                    '<p class="text-muted">Chưa có CDN URL — upload lên R2 trước.</p>'
                )

    def action_open_cdn_url(self):
        self.ensure_one()
        if not self.cdn_url:
            raise UserError("Chưa có CDN URL.")
        return {
            "type": "ir.actions.act_url",
            "url": self.cdn_url,
            "target": "new",
        }

    def action_extract_audio(self):
        self.ensure_one()
        if not self.storage_path:
            raise UserError("Video chưa upload lên R2 — không thể tách audio.")
        return {
            "type": "ir.actions.act_window",
            "name": "Extract Audio",
            "res_model": "video.extract.audio.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_video_id": self.id,
                "default_name": f"{self.name} - Audio" if self.name else "Extracted Audio",
            },
        }

    def action_gen_video(self):
        self.ensure_one()
        if not self.storage_path:
            raise UserError("Video chưa upload lên R2 — không thể Gen Video.")
        return {
            "type": "ir.actions.act_window",
            "name": "Generate Video",
            "res_model": "video.generate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_video_id": self.id,
                "default_output_name": f"{self.name}_generated" if self.name else "generated",
            },
        }

    @api.model
    def _default_storage(self):
        return self.env["video.storage"].search([("active", "=", True)], limit=1)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "storage_id" in fields_list and not res.get("storage_id"):
            storage = self._default_storage()
            if storage:
                res["storage_id"] = storage.id
        return res

    def action_mark_available(self):
        self.write({"state": "available"})

    def action_rebuild_cdn_url(self):
        self._compute_cdn_url()
        return True

    def action_upload_to_r2(self):
        """Import file from device and upload to Cloudflare R2."""
        for video in self:
            video._upload_local_file_to_r2()
        return True

    def _upload_local_file_to_r2(self):
        self.ensure_one()
        if not self.upload_file:
            raise UserError("Chọn file video từ thiết bị trước khi upload.")
        if not self.storage_id:
            raise UserError("Chọn R2 Storage trước.")

        client = R2Client(self.storage_id)
        original_name = self.upload_filename or self.filename or "video.mp4"
        if not self.name:
            self.name = os.path.splitext(os.path.basename(original_name))[0]

        # Flat key at bucket root — renamed (not original filename)
        ext = os.path.splitext(original_name)[1] or ".mp4"
        object_key = make_flat_object_key("v", ext, record_id=self.id)
        work_dir = _make_workdir("va_vupload_")
        local_path = os.path.join(work_dir, os.path.basename(object_key))
        try:
            with open(local_path, "wb") as fh:
                fh.write(base64.b64decode(self.upload_file))
            client.upload_file(local_path, object_key, content_type="video/mp4")
            meta = probe_media(local_path)
            self.write(
                {
                    "filename": object_key,
                    "storage_path": object_key,
                    "duration": meta["duration"],
                    "width": meta["width"],
                    "height": meta["height"],
                    "fps": meta["fps"],
                    "bitrate": meta["bitrate"],
                    "file_size": meta["file_size"],
                    "state": "uploaded",
                    "upload_file": False,
                    "upload_filename": False,
                }
            )
            self.message_post(body=f"Uploaded to R2: {object_key}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @api.model
    def _fifo_candidates(self, account, allow_republish_rule, limit):
        """Select available videos FIFO for one TikTok account."""
        History = self.env["tiktok.upload.history"]
        Queue = self.env["tiktok.publish.queue"]

        busy_video_ids = Queue.search(
            [
                ("tiktok_account_id", "=", account.id),
                ("state", "in", ("pending", "uploading")),
            ]
        ).mapped("video_id").ids

        domain = [
            ("state", "=", "available"),
            ("generated", "=", True),
            ("id", "not in", busy_video_ids or [0]),
        ]
        if account.bucket_id:
            domain.append(("storage_id", "=", account.bucket_id.id))
        videos = self.search(domain, order="create_date asc, id asc")
        selected = self.env["video.library"]
        posted_ids = set(History.posted_video_ids(account))
        for video in videos:
            if len(selected) >= limit:
                break
            if video.id in posted_ids:
                continue
            selected |= video
        return selected

    def _get_raw_video(self):
        """Walk source_video_id until the original raw clip."""
        self.ensure_one()
        video = self
        seen = {video.id}
        while video.source_video_id and video.source_video_id.id not in seen:
            video = video.source_video_id
            seen.add(video.id)
        return video

    def _raw_storage_path(self):
        """R2 key of the original (pre-generate) file."""
        self.ensure_one()
        raw = self._get_raw_video()
        return raw.original_storage_path or raw.storage_path

    @api.model
    def _pick_audio(self, storage=None):
        """Least-used active audio (prefer same storage)."""
        Audio = self.env["audio.library"]
        domain = [("active", "=", True), ("storage_path", "!=", False)]
        audios = Audio.search(domain)
        if storage:
            same = audios.filtered(lambda a: a.storage_id == storage)
            if same:
                audios = same
        if not audios:
            return Audio
        counts = {
            audio.id: self.search_count(
                [("audio_id", "=", audio.id), ("generated", "=", True)]
            )
            for audio in audios
        }
        return min(audios, key=lambda a: (counts.get(a.id, 0), a.id))

    def generate_output(self, audio=None, logo_path=None, output_name=None):
        """
        Create a NEW generated video.library from this (or its raw parent).
        Does not overwrite the source record or its R2 object.
        """
        self.ensure_one()
        raw = self._get_raw_video()
        source_path = self._raw_storage_path()
        if not raw.storage_id or not source_path:
            raise UserError("Video gốc chưa có trên R2 (thiếu storage_path).")

        audio = audio or self._pick_audio(raw.storage_id)
        if not audio:
            raise UserError("Không có audio active trên R2 để generate.")
        if not audio.storage_path or not audio.storage_id:
            raise UserError("Audio chưa có trên R2.")

        name = output_name or (raw.name or "Video")
        child = self.create(
            {
                "name": name,
                "storage_id": raw.storage_id.id,
                "source_video_id": raw.id,
                "original_storage_path": source_path,
                "state": "processing",
                "generated": False,
                "allow_republish": raw.allow_republish,
            }
        )
        if not output_name:
            child.name = f"{raw.name} Generated #{child.id}"

        video_client = R2Client(raw.storage_id)
        audio_client = R2Client(audio.storage_id)
        work_dir = _make_workdir("va_gen_")
        video_local = os.path.join(work_dir, "input.mp4")
        audio_local = os.path.join(work_dir, "audio.mp3")
        output_local = os.path.join(work_dir, "output.mp4")
        try:
            try:
                video_client.download_file(source_path, video_local)
            except FileNotFoundError:
                if raw.storage_path and raw.storage_path != source_path:
                    _logger.warning(
                        "Raw original path missing (%s); fallback to storage_path %s",
                        source_path,
                        raw.storage_path,
                    )
                    video_client.download_file(raw.storage_path, video_local)
                else:
                    raise
            audio_client.download_file(audio.storage_path, audio_local)
            generate_video(video_local, audio_local, output_local, logo_path=logo_path)

            object_key = make_flat_object_key("g", ".mp4", record_id=child.id)
            video_client.upload_file(output_local, object_key, content_type="video/mp4")
            meta = probe_media(output_local)
            child.write(
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
            raw.message_post(
                body=(
                    f"Generated output <b>{child.name}</b> (id={child.id}) "
                    f"với audio <b>{audio.name}</b> → {object_key}"
                )
            )
            child.message_post(
                body=f"Tạo từ video gốc <b>{raw.name}</b> + audio <b>{audio.name}</b>."
            )
            return child
        except FileNotFoundError as exc:
            child.unlink()
            raise UserError(str(exc)) from exc
        except Exception:
            child.unlink()
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @api.model
    def _raw_candidates(self, storage):
        """Raw clips that can still be used to generate more outputs."""
        return self.search(
            [
                ("storage_id", "=", storage.id),
                ("generated", "=", False),
                ("source_video_id", "=", False),
                ("storage_path", "!=", False),
                ("state", "in", ("uploaded", "available")),
            ],
            order="create_date asc, id asc",
        )

    @api.model
    def cron_top_up_pool(self):
        storages = self.env["video.storage"].search(
            [("active", "=", True), ("auto_top_up", "=", True)]
        )
        for storage in storages:
            try:
                storage.action_top_up_pool()
            except Exception:
                _logger.exception("Auto top-up failed for storage %s", storage.id)
