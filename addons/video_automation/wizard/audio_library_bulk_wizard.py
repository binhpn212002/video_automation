import base64
import io
import logging
import os
import unicodedata
import zipfile

from odoo import api, fields, models
from odoo.exceptions import UserError
from ..models.audio_library import _normalize_audio_name

_logger = logging.getLogger(__name__)


class AudioLibraryBulkWizard(models.TransientModel):
    _name = "audio.library.bulk.wizard"
    _description = "Tải Lên Hàng Loạt File Âm Thanh (Audio)"

    storage_id = fields.Many2one(
        "video.storage",
        string="R2 Storage",
        required=True,
    )
    handle_duplicates = fields.Selection(
        [
            ("skip", "Bỏ qua nếu đã tồn tại (Khuyên dùng - Tránh trùng lặp)"),
            ("update", "Cập nhật file mới & Phân tích lại Beat"),
            ("create", "Luôn tạo mới (Cho phép trùng tên)"),
        ],
        default="skip",
        string="Xử lý khi trùng tên",
        required=True,
        help="Cách xử lý khi file tải lên có tên trùng với bài hát đã có trong Thư viện.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        string="Chọn danh sách file âm thanh",
        help="Chọn một hoặc nhiều file MP3 / WAV / AAC / M4A từ thiết bị.",
    )
    zip_file = fields.Binary(
        string="Hoặc Tải lên file ZIP chứa nhạc",
        help="File ZIP chứa danh sách các bài nhạc cần upload lên R2.",
    )
    zip_filename = fields.Char(string="Tên file ZIP")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "storage_id" in fields_list and not res.get("storage_id"):
            storage = self.env["video.storage"].search([("active", "=", True)], limit=1)
            if storage:
                res["storage_id"] = storage.id
        return res

    def action_process_upload(self):
        """Xử lý giải nén hoặc đọc danh sách file audio, chống trùng lặp, đẩy lên R2 và phân tích beat."""
        self.ensure_one()
        AudioLib = self.env["audio.library"]
        created_records = AudioLib
        updated_records = AudioLib
        skipped_records = AudioLib
        errors = []

        valid_extensions = (".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac")
        handle_dup = self.handle_duplicates or "skip"

        # Index existing audios by normalized name for fast O(1) duplicate checks
        existing_audios = {
            _normalize_audio_name(a.name): a
            for a in AudioLib.search([])
            if a.name
        }
        seen_in_batch = {}  # norm_name -> record

        items_to_process = []  # list of tuples: (clean_name, norm_name, base_filename, binary_data, is_b64)

        # 1. Read ZIP file
        if self.zip_file:
            try:
                zip_bytes = base64.b64decode(self.zip_file)
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    for filename in zf.namelist():
                        base = os.path.basename(filename)
                        if not base or base.startswith(".") or base.startswith("__MACOSX"):
                            continue
                        ext = os.path.splitext(base)[1].lower()
                        if ext not in valid_extensions:
                            continue

                        raw_name = os.path.splitext(base)[0]
                        clean_name = unicodedata.normalize("NFC", raw_name).strip()
                        norm_name = _normalize_audio_name(clean_name)
                        file_data = zf.read(filename)
                        items_to_process.append((clean_name, norm_name, base, file_data, False))
            except Exception as exc:
                raise UserError(f"Không thể giải nén file ZIP: {exc}") from exc

        # 2. Read Multi-attachments
        if self.attachment_ids:
            for att in self.attachment_ids:
                filename = att.name or "audio.mp3"
                ext = os.path.splitext(filename)[1].lower()
                if ext not in valid_extensions:
                    continue
                raw_name = os.path.splitext(filename)[0]
                clean_name = unicodedata.normalize("NFC", raw_name).strip()
                norm_name = _normalize_audio_name(clean_name)
                items_to_process.append((clean_name, norm_name, filename, att.datas, True))

        if not items_to_process:
            raise UserError("Vui lòng đính kèm ít nhất 1 file âm thanh (MP3/WAV/AAC) hoặc file ZIP hợp lệ.")

        # 3. Process and upload
        for clean_name, norm_name, base_filename, data, is_b64 in items_to_process:
            b64_content = data if is_b64 else base64.b64encode(data)
            try:
                # Check duplicate within this upload batch
                if norm_name in seen_in_batch:
                    if handle_dup == "skip":
                        skipped_records |= seen_in_batch[norm_name]
                        continue
                    elif handle_dup == "update":
                        target_rec = seen_in_batch[norm_name]
                        target_rec.write(
                            {
                                "upload_file": b64_content,
                                "upload_filename": base_filename,
                            }
                        )
                        target_rec._upload_local_file_to_r2()
                        updated_records |= target_rec
                        continue

                # Check duplicate against existing audio library
                if norm_name in existing_audios and handle_dup != "create":
                    existing_rec = existing_audios[norm_name]
                    if handle_dup == "skip":
                        skipped_records |= existing_rec
                        seen_in_batch[norm_name] = existing_rec
                        continue
                    elif handle_dup == "update":
                        existing_rec.write(
                            {
                                "upload_file": b64_content,
                                "upload_filename": base_filename,
                            }
                        )
                        existing_rec._upload_local_file_to_r2()
                        updated_records |= existing_rec
                        seen_in_batch[norm_name] = existing_rec
                        continue

                # Create new record
                record = AudioLib.create(
                    {
                        "name": clean_name,
                        "storage_id": self.storage_id.id,
                        "upload_file": b64_content,
                        "upload_filename": base_filename,
                    }
                )
                record._upload_local_file_to_r2()
                created_records |= record
                seen_in_batch[norm_name] = record
                existing_audios[norm_name] = record
            except Exception as exc:
                _logger.exception("Error processing audio %s (%s): %s", clean_name, base_filename, exc)
                errors.append(f"{base_filename}: {exc}")

        # Summary message
        parts = []
        if created_records:
            parts.append(f"Tạo mới <b>{len(created_records)}</b> bài hát")
        if updated_records:
            parts.append(f"Cập nhật <b>{len(updated_records)}</b> bài hát")
        if skipped_records:
            parts.append(f"Bỏ qua <b>{len(skipped_records)}</b> bài hát đã tồn tại")

        summary_text = ", ".join(parts) if parts else "Không có file nào được xử lý."
        msg = f"Đã hoàn thành: {summary_text}."
        if errors:
            msg += f"<br/><b class='text-danger'>Lỗi {len(errors)} file:</b><br/>" + "<br/>".join(errors[:5])

        result_ids = (created_records | updated_records | skipped_records).ids

        return {
            "type": "ir.actions.act_window",
            "name": "Thư Viện Audio",
            "res_model": "audio.library",
            "view_mode": "tree,form",
            "domain": [("id", "in", result_ids)] if result_ids else [],
            "target": "current",
            "context": {"search_default_all": 1},
        }
