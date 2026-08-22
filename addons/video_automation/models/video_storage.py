from odoo import fields, models


class VideoStorage(models.Model):
    _name = "video.storage"
    _description = "Cloudflare R2 Storage"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True)
    account_id = fields.Char(string="Cloudflare Account ID")
    bucket_name = fields.Char(required=True)
    access_key_id = fields.Char(required=True)
    secret_key = fields.Char(required=True)
    endpoint = fields.Char(
        required=True,
        help="S3 API endpoint: https://<accountid>.r2.cloudflarestorage.com",
    )
    cdn_domain = fields.Char(
        required=True,
        help=(
            "Public CDN base for this bucket (no trailing slash). "
            "Example r2.dev: https://pub-xxxx.r2.dev — CDN URL = cdn_domain + / + object_path"
        ),
    )
    active = fields.Boolean(default=True)
    buffer_days = fields.Integer(
        string="Buffer (ngày)",
        default=3,
        help="Giữ sẵn pool generated = slots/ngày × số ngày này. VD: 20 slot/ngày × 3 = 60.",
    )
    auto_top_up = fields.Boolean(
        string="Auto gen khi thiếu pool",
        default=True,
        help="Cron tự generate từ video raw / ảnh sản phẩm khi pool dưới ngưỡng.",
    )
    auto_gen_from_images = fields.Boolean(
        string="Auto gen từ Kho Ảnh",
        default=True,
        help="Tự động sinh video từ các ảnh sản phẩm chưa gen (FIFO) khi thiếu pool.",
    )
    max_gen_per_run = fields.Integer(
        string="Max gen / lần cron",
        default=10,
        help="Giới hạn số video generate mỗi lần top-up (tránh timeout FFmpeg).",
    )
    slots_per_day = fields.Integer(compute="_compute_pool_stats")
    pool_available_count = fields.Integer(
        string="Pool generated", compute="_compute_pool_stats"
    )
    pool_raw_count = fields.Integer(string="Raw còn lại", compute="_compute_pool_stats")
    pool_images_pending_count = fields.Integer(
        string="Ảnh chưa gen", compute="_compute_pool_stats"
    )
    pool_images_generated_count = fields.Integer(
        string="Ảnh đã gen", compute="_compute_pool_stats"
    )
    pool_needed = fields.Integer(string="Cần tối thiểu", compute="_compute_pool_stats")
    pool_status = fields.Selection(
        [
            ("ok", "Đủ hàng"),
            ("warning", "Sắp thiếu"),
            ("critical", "Thiếu — lịch có thể trống"),
        ],
        compute="_compute_pool_stats",
    )

    def _accounts_using_bucket(self):
        self.ensure_one()
        return self.env["tiktok.account"].search(
            [("active", "=", True), ("bucket_id", "=", self.id)]
        )

    def _compute_pool_stats(self):
        Video = self.env["video.library"]
        Rule = self.env["tiktok.schedule.rule"]
        Image = self.env["product.image"] if "product.image" in self.env else None
        for storage in self:
            accounts = storage._accounts_using_bucket()
            rules = Rule.search(
                [("active", "=", True), ("tiktok_account_id", "in", accounts.ids or [0])]
            )
            slots = sum(len(rule.upload_time_ids) for rule in rules)
            buffer = storage.buffer_days or 3
            needed = slots * buffer
            available = Video.search_count(
                [
                    ("storage_id", "=", storage.id),
                    ("generated", "=", True),
                    ("state", "=", "available"),
                ]
            )
            raw = Video.search_count(
                [
                    ("storage_id", "=", storage.id),
                    ("generated", "=", False),
                    ("source_video_id", "=", False),
                    ("storage_path", "!=", False),
                    ("state", "in", ("uploaded", "available")),
                ]
            )
            pending_images = 0
            generated_images = 0
            if Image is not None:
                pending_images = Image.search_count(
                    [
                        ("storage_id", "=", storage.id),
                        ("active", "=", True),
                        ("storage_path", "!=", False),
                        ("generated", "=", False),
                        ("state", "in", ("uploaded", "failed")),
                    ]
                )
                generated_images = Image.search_count(
                    [
                        ("storage_id", "=", storage.id),
                        ("generated", "=", True),
                    ]
                )

            storage.slots_per_day = slots
            storage.pool_needed = needed
            storage.pool_available_count = available
            storage.pool_raw_count = raw
            storage.pool_images_pending_count = pending_images
            storage.pool_images_generated_count = generated_images
            if needed <= 0:
                storage.pool_status = "ok"
            elif available < slots:
                storage.pool_status = "critical"
            elif available < needed:
                storage.pool_status = "warning"
            else:
                storage.pool_status = "ok"

    def action_top_up_pool(self):
        """Generate new outputs from pending affiliate images (FIFO) until pool >= needed (capped per run)."""
        Image = self.env["product.image"] if "product.image" in self.env else None
        created = self.env["video.library"]
        for storage in self:
            storage._compute_pool_stats()
            missing = storage.pool_needed - storage.pool_available_count
            if missing <= 0:
                continue
            cap = storage.max_gen_per_run or 10
            to_make = min(missing, cap)
            made = 0
            errors = []

            # Generate exclusively from Pending Product Images (FIFO)
            if storage.auto_gen_from_images and Image is not None:
                pending_imgs = Image._pending_image_candidates(storage, limit=to_make)
                for img in pending_imgs:
                    if made >= to_make:
                        break
                    try:
                        child = img.generate_affiliate_video()
                        created |= child
                        made += 1
                    except Exception as exc:
                        errors.append(f"Ảnh '{img.display_name}': {exc}")

            if made == 0 and missing > 0:
                storage.message_post(
                    body=(
                        f"Pool thiếu {missing} video nhưng không còn ảnh sản phẩm chưa gen trên R2 "
                        f"để auto gen. Hãy upload thêm ảnh sản phẩm."
                    )
                )
                continue

            body = f"Auto top-up: tạo {made}/{to_make} video từ ảnh sản phẩm (thiếu {missing})."
            if errors:
                body += "<br/>Lỗi:<br/>" + "<br/>".join(errors[:10])
            storage.message_post(body=body)
        return created


    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ("cdn_domain", "bucket_name")):
            videos = self.env["video.library"].search([("storage_id", "in", self.ids)])
            videos._compute_cdn_url()
            audios = self.env["audio.library"].search([("storage_id", "in", self.ids)])
            audios._compute_cdn_url()
        return res
