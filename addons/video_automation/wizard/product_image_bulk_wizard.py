import base64
import io
import logging
import os
import zipfile

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProductImageBulkWizard(models.TransientModel):
    _name = "product.image.bulk.wizard"
    _description = "Tải Lên Hàng Loạt Ảnh Sản Phẩm"

    storage_id = fields.Many2one(
        "image.storage",
        string="R2 Image Storage",
        required=True,
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        string="Chọn danh sách file ảnh",
        help="Chọn một hoặc nhiều file ảnh từ máy tính (JPG/PNG/WEBP).",
    )
    zip_file = fields.Binary(
        string="Hoặc Tải lên file ZIP",
        help="File ZIP chứa các ảnh sản phẩm cần upload.",
    )
    zip_filename = fields.Char(string="Tên file ZIP")
    default_hook = fields.Char(
        string="Hook Text Mặc định",
        default="Mẫu này đang cực hot 🔥",
    )
    default_cta = fields.Char(
        string="CTA Text Mặc định",
        default="Xem sản phẩm bên dưới ↓",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "storage_id" in fields_list and not res.get("storage_id"):
            storage = self.env["image.storage"].search([("active", "=", True)], limit=1)
            if storage:
                res["storage_id"] = storage.id
        return res

    def action_process_upload(self):
        """Xử lý giải nén hoặc đọc danh sách file và đẩy lên Cloudflare R2."""
        self.ensure_one()
        ProductImage = self.env["product.image"]
        created_records = ProductImage
        errors = []

        valid_extensions = (".jpg", ".jpeg", ".png", ".webp")

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
                        img_name = os.path.splitext(base)[0]
                        try:
                            record = ProductImage.create(
                                {
                                    "name": img_name,
                                    "storage_id": self.storage_id.id,
                                    "upload_file": base64.b64encode(file_data),
                                    "upload_filename": base,
                                    "default_hook": self.default_hook,
                                    "default_cta": self.default_cta,
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
                filename = att.name or "image.jpg"
                ext = os.path.splitext(filename)[1].lower()
                if ext not in valid_extensions:
                    continue
                img_name = os.path.splitext(filename)[0]
                try:
                    record = ProductImage.create(
                        {
                            "name": img_name,
                            "storage_id": self.storage_id.id,
                            "upload_file": att.datas,
                            "upload_filename": filename,
                            "default_hook": self.default_hook,
                            "default_cta": self.default_cta,
                        }
                    )
                    record._upload_local_file_to_r2()
                    created_records |= record
                except Exception as exc:
                    errors.append(f"{filename}: {exc}")

        if not created_records and not errors:
            raise UserError("Vui lòng đính kèm ít nhất 1 file ảnh hoặc file ZIP.")

        msg = f"Đã tải lên thành công {len(created_records)} ảnh sản phẩm lên R2."
        if errors:
            msg += f"<br/>Lỗi khi tải lên {len(errors)} ảnh:<br/>" + "<br/>".join(errors[:5])

        return {
            "type": "ir.actions.act_window",
            "name": "Kho Ảnh Sản Phẩm",
            "res_model": "product.image",
            "view_mode": "tree,form",
            "domain": [("id", "in", created_records.ids)],
            "target": "current",
        }
