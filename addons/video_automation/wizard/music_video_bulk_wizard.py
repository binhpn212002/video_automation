import logging
import random
import threading

from odoo import api, fields, models
from odoo.exceptions import UserError
from ..models.video_generate_job import _run_jobs_in_background

_logger = logging.getLogger(__name__)


class MusicVideoBulkWizard(models.TransientModel):
    _name = "music.video.bulk.wizard"
    _description = "Tạo Video Ca Nhạc Hàng Loạt (Bulk Music Video Generator)"

    name_prefix = fields.Char(
        string="Tiền tố tên Video",
        default="MV",
        help="Ví dụ: 'MV' -> Tên video sẽ là 'MV - [Tên bài hát]'",
    )
    storage_id = fields.Many2one(
        "video.storage",
        string="R2 Video Storage",
        required=True,
        default=lambda self: self.env["video.storage"].search([("active", "=", True)], limit=1),
        help="Nơi lưu trữ file video sau khi render.",
    )

    # 1. Selection Inputs
    audio_ids = fields.Many2many(
        "audio.library",
        "music_video_bulk_audio_rel",
        "wizard_id",
        "audio_id",
        string="Danh Sách Audio / Bài Hát",
        domain=[("active", "=", True), ("storage_path", "!=", False)],
        required=True,
        help="Chọn danh sách các bài hát. Số lượng video tạo ra sẽ tương ứng theo số lượng audio được chọn.",
    )
    bg_image_ids = fields.Many2many(
        "product.image",
        "music_video_bulk_bg_rel",
        "wizard_id",
        "image_id",
        string="Danh Sách Ảnh Background",
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "background")],
        required=True,
        help="Chọn một hoặc nhiều ảnh nền phong cảnh/sân khấu/lofi.",
    )
    character_image_ids = fields.Many2many(
        "product.image",
        "music_video_bulk_char_rel",
        "wizard_id",
        "image_id",
        string="Danh Sách Ảnh Nhân Vật / Ca Sĩ",
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "character")],
        required=True,
        help="Chọn một hoặc nhiều ảnh nhân vật/ca sĩ (khuyên dùng PNG đã tách nền).",
    )

    bg_selection_mode = fields.Selection(
        [
            ("random", "Ngẫu nhiên (Random từ danh sách đã chọn)"),
            ("round_robin", "Xoay vòng tuần tự (Round-Robin)"),
        ],
        default="random",
        string="Cách phối Background",
        required=True,
    )
    character_selection_mode = fields.Selection(
        [
            ("random", "Ngẫu nhiên (Random từ danh sách đã chọn)"),
            ("round_robin", "Xoay vòng tuần tự (Round-Robin)"),
        ],
        default="random",
        string="Cách phối Nhân vật",
        required=True,
    )

    # 2. Layout & Effects
    music_layout = fields.Selection(
        [
            ("random", "🎲 Ngẫu nhiên các kiểu bố cục"),
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
            ("random", "🎲 Ngẫu nhiên các kiểu sóng nhạc"),
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
            ("random", "🎲 Ngẫu nhiên màu sắc"),
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
            ("random", "🎲 Ngẫu nhiên hiệu ứng"),
            ("none", "Không có"),
            ("snow_fall", "Tuyết rơi lãng mạn (Snow Fall)"),
            ("rain_drops", "Giọt mưa rơi (Rain Drops)"),
            ("dust_bokeh", "Hạt bụi sáng (Dust & Bokeh)"),
            ("stage_lights", "Tia đèn sân khấu (Stage Lights)"),
        ],
        default="snow_fall",
        string="Hiệu Ứng Không Khí",
        required=True,
    )
    music_preset = fields.Selection(
        [
            ("random", "🎲 Ngẫu nhiên phong cách"),
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
        string="Thời lượng tối đa mỗi video (giây)",
        default=0.0,
        help="Để 0 để render trọn vẹn toàn bộ độ dài của từng bài nhạc.",
    )

    audio_count = fields.Integer(
        string="Số lượng Audio đã chọn",
        compute="_compute_counts",
    )
    bg_count = fields.Integer(
        string="Số lượng Background đã chọn",
        compute="_compute_counts",
    )
    char_count = fields.Integer(
        string="Số lượng Nhân vật đã chọn",
        compute="_compute_counts",
    )

    @api.depends("audio_ids", "bg_image_ids", "character_image_ids")
    def _compute_counts(self):
        for wiz in self:
            wiz.audio_count = len(wiz.audio_ids)
            wiz.bg_count = len(wiz.bg_image_ids)
            wiz.char_count = len(wiz.character_image_ids)

    def action_select_all_audios(self):
        """Tiện ích: Chọn tất cả Audio có sẵn trên R2."""
        self.ensure_one()
        all_audios = self.env["audio.library"].search(
            [("active", "=", True), ("storage_path", "!=", False)]
        )
        self.audio_ids = [(6, 0, all_audios.ids)]
        return {"type": "ir.actions.do_nothing"}

    def action_select_all_backgrounds(self):
        """Tiện ích: Chọn tất cả Ảnh Background có sẵn trên R2."""
        self.ensure_one()
        all_bgs = self.env["product.image"].search(
            [("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "background")]
        )
        self.bg_image_ids = [(6, 0, all_bgs.ids)]
        return {"type": "ir.actions.do_nothing"}

    def action_select_all_characters(self):
        """Tiện ích: Chọn tất cả Ảnh Nhân vật có sẵn trên R2."""
        self.ensure_one()
        all_chars = self.env["product.image"].search(
            [("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "character")]
        )
        self.character_image_ids = [(6, 0, all_chars.ids)]
        return {"type": "ir.actions.do_nothing"}

    def action_generate_bulk(self):
        """Tạo các Render Jobs ngầm và thực thi trong background thread để tránh HTTP timeout."""
        self.ensure_one()

        audios = self.audio_ids
        bgs = self.bg_image_ids
        chars = self.character_image_ids

        if not audios:
            raise UserError("Vui lòng chọn ít nhất 1 bài hát (Audio).")
        if not bgs:
            raise UserError("Vui lòng chọn ít nhất 1 Ảnh Background.")
        if not chars:
            raise UserError("Vui lòng chọn ít nhất 1 Ảnh Nhân vật / Ca sĩ.")

        video_storage = self.storage_id or self.env["video.storage"].search([("active", "=", True)], limit=1)
        if not video_storage:
            raise UserError("Chưa có cấu hình R2 Video Storage active để lưu video thành phẩm.")

        JobModel = self.env["video.generate.job"]
        created_jobs = JobModel

        available_layouts = ["spotify_card", "vinyl_retro", "circular_avatar", "floating_portrait", "center_cutout", "spinning_vinyl", "glass_card"]
        available_vis_styles = ["none", "spectrum_bars", "sine_wave", "radial_circle"]
        available_vis_colors = ["cyan_neon", "pink_purple", "golden_warm", "white_minimal"]
        available_particles = ["none", "snow_fall", "rain_drops", "dust_bokeh", "stage_lights"]
        available_presets = ["lofi_chill", "edm_remix", "ballad_acoustic", "hiphop_cyber"]

        bg_list = list(bgs)
        char_list = list(chars)
        prefix = (self.name_prefix or "MV").strip()

        for i, audio in enumerate(audios):
            # Pick background
            if self.bg_selection_mode == "random":
                bg = random.choice(bg_list)
            else:
                bg = bg_list[i % len(bg_list)]

            # Pick character
            if self.character_selection_mode == "random":
                char = random.choice(char_list)
            else:
                char = char_list[i % len(char_list)]

            # Resolve styles
            layout = random.choice(available_layouts) if self.music_layout == "random" else self.music_layout
            vis_style = random.choice(available_vis_styles) if self.visualizer_style == "random" else self.visualizer_style
            vis_color = random.choice(available_vis_colors) if self.visualizer_color == "random" else self.visualizer_color
            particle = random.choice(available_particles) if self.particle_effect == "random" else self.particle_effect
            preset = random.choice(available_presets) if self.music_preset == "random" else self.music_preset

            video_name = f"{prefix} - {audio.name} ({char.name})"

            job = JobModel.create(
                {
                    "job_type": "music_video",
                    "storage_id": video_storage.id,
                    "bg_image_id": bg.id,
                    "character_image_id": char.id,
                    "audio_id": audio.id,
                    "music_layout": layout,
                    "visualizer_style": vis_style,
                    "visualizer_color": vis_color,
                    "particle_effect": particle,
                    "music_preset": preset,
                    "effect_preset": self.effect_preset,
                    "max_duration": self.max_duration,
                    "output_video_name": video_name,
                    "state": "draft",
                }
            )
            created_jobs |= job

        job_ids = created_jobs.ids
        _logger.info("Created %s video render jobs. Launching background worker thread...", len(job_ids))

        # Commit để cursor của background thread đọc được dữ liệu jobs ngay lập tức
        self.env.cr.commit()

        # Khởi động background thread để render ngầm độc lập với HTTP Request
        db_name = self.env.cr.dbname
        uid = self.env.uid
        threading.Thread(target=_run_jobs_in_background, args=(db_name, uid, job_ids), daemon=True).start()

        return {
            "type": "ir.actions.act_window",
            "name": f"Hàng Đợi Render ({len(created_jobs)} Jobs Đang Xử Lý)",
            "res_model": "video.generate.job",
            "view_mode": "tree,form",
            "domain": [("id", "in", job_ids)],
            "target": "current",
            "context": {"search_default_all": 1},
        }
