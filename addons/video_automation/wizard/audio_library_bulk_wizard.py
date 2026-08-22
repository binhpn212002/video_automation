import base64
import io
import logging
import os
import zipfile

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AudioLibraryBulkWizard(models.TransientModel):
    _name = "audio.library.bulk.wizard"
    _description = "Tải Lên Hàng Loạt File Âm Thanh (Audio)"

    storage_id = fields.Many2one(
        "video.storage",
        string="R2 Storage",
        required=True,
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
        """Xử lý giải nén hoặc đọc danh sách file audio, đẩy lên R2 và phân tích beat."""
        self.ensure_one()
        AudioLib = self.env["audio.library"]
        created_records = AudioLib
        errors = []

        valid_extensions = (".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac")

        # 1. Process ZIP file
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

                        file_data = zf.read(filename)
                        audio_name = os.path.splitext(base)[0]
                        try:
                            record = AudioLib.create(
                                {
                                    "name": audio_name,
                                    "storage_id": self.storage_id.id,
                                    "upload_file": base64.b64encode(file_data),
                                    "upload_filename": base,
                                }
                            )
                            record._upload_local_file_to_r2()
                            created_records |= record
                        except Exception as exc:
                            errors.append(f"{base}: {exc}")
            except Exception as exc:
                raise UserError(f"Không thể giải nén file ZIP: {exc}") from exc

        # 2. Process Multi-attachments
        if self.attachment_ids:
            for att in self.attachment_ids:
                filename = att.name or "audio.mp3"
                ext = os.path.splitext(filename)[1].lower()
                if ext not in valid_extensions:
                    continue
                audio_name = os.path.splitext(filename)[0]
                try:
                    record = AudioLib.create(
                        {
                            "name": audio_name,
                            "storage_id": self.storage_id.id,
                            "upload_file": att.datas,
                            "upload_filename": filename,
                        }
                    )
                    record._upload_local_file_to_r2()
                    created_records |= record
                except Exception as exc:
                    errors.append(f"{filename}: {exc}")

        if not created_records and not errors:
            raise UserError("Vui lòng đính kèm ít nhất 1 file âm thanh (MP3/WAV/AAC) hoặc file ZIP.")

        msg = f"Đã tải lên và phân tích Beat thành công cho {len(created_records)} file âm thanh."
        if errors:
            msg += f"<br/>Lỗi khi tải lên {len(errors)} file:<br/>" + "<br/>".join(errors[:5])

        return {
            "type": "ir.actions.act_window",
            "name": "Thư Viện Audio",
            "res_model": "audio.library",
            "view_mode": "tree,form",
            "domain": [("id", "in", created_records.ids)],
            "target": "current",
        }
