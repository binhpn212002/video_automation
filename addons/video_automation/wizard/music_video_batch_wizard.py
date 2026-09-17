from datetime import timedelta
import logging
import random

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

LAYOUT_OPTIONS = [
    ("spotify_card", "Card Âm Nhạc Sang Trọng (Spotify / Lofi Card)"),
    ("vinyl_retro", "Đĩa Than Cổ Điển Xoay 360° (Vinyl Retro)"),
    ("circular_avatar", "Avatar Tròn Tinh Tế (Circular Avatar)"),
    ("floating_portrait", "Chân Dung Nghệ Thuật (Floating Portrait)"),
    ("center_cutout", "Nhân vật Tách nền (Center Cutout)"),
    ("spinning_vinyl", "Đĩa than xoay (Spinning Vinyl)"),
    ("glass_card", "Khung kính mờ (Glassmorphism Card)"),
]

VISUALIZER_OPTIONS = [
    ("none", "Không hiển thị"),
    ("spectrum_bars", "Cột sóng Equalizer (Spectrum Bars)"),
    ("sine_wave", "Đường sóng lượn (Smooth Wave)"),
    ("radial_circle", "Sóng tròn bao quanh (Radial Wave)"),
]

COLOR_OPTIONS = [
    ("cyan_neon", "Xanh Neon (Cyan Glow)"),
    ("pink_purple", "Hồng Tím (Synthwave Pink)"),
    ("golden_warm", "Vàng Ánh Kim (Golden Glow)"),
    ("white_minimal", "Trắng Tối Giản (Pure White)"),
]

PARTICLE_OPTIONS = [
    ("none", "Không có"),
    ("snow_fall", "Tuyết rơi lãng mạn (Snow Fall)"),
    ("rain_drops", "Giọt mưa rơi (Rain Drops)"),
    ("dust_bokeh", "Hạt bụi sáng (Dust & Bokeh)"),
    ("stage_lights", "Tia đèn sân khấu (Stage Lights)"),
]

PRESET_OPTIONS = [
    ("lofi_chill", "Lofi / Chill (Đĩa than xoay, Nhẹ nhàng)"),
    ("edm_remix", "EDM / Remix / Vinahouse (Flash mạnh, Bass Bounce)"),
    ("ballad_acoustic", "Ballad / Acoustic (Mộng ảo, Mưa rơi)"),
    ("hiphop_cyber", "Rap / HipHop / Cyberpunk (Neon Glow)"),
]


class MusicVideoBatchWizard(models.TransientModel):
    _name = "music.video.batch.wizard"
    _description = "Wizard Tạo Hàng Loạt Video Ca Nhạc"

    storage_id = fields.Many2one(
        "video.storage",
        string="R2 Video Storage",
        required=True,
        default=lambda self: self.env["video.storage"].search([("active", "=", True)], limit=1),
        help="Nơi lưu trữ file video sau khi render.",
    )

    # --- Lựa chọn Background ---
    bg_selection_mode = fields.Selection(
        [
            ("single", "1 Ảnh Background cố định"),
            ("multiple", "Nhiều Ảnh Background (Xoay vòng)"),
        ],
        default="single",
        string="Chế độ chọn Background",
        required=True,
    )
    bg_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Background",
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "background")],
    )
    bg_image_ids = fields.Many2many(
        "product.image",
        "music_video_batch_bg_rel",
        "wizard_id",
        "image_id",
        string="Danh sách Ảnh Background",
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "background")],
    )

    # --- Lựa chọn Nhân vật / Ca sĩ ---
    char_selection_mode = fields.Selection(
        [
            ("single", "1 Ảnh Nhân vật cố định"),
            ("multiple", "Nhiều Ảnh Nhân vật (Xoay vòng)"),
        ],
        default="single",
        string="Chế độ chọn Nhân vật",
        required=True,
    )
    character_image_id = fields.Many2one(
        "product.image",
        string="Ảnh Nhân vật",
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "character")],
    )
    character_image_ids = fields.Many2many(
        "product.image",
        "music_video_batch_char_rel",
        "wizard_id",
        "image_id",
        string="Danh sách Ảnh Nhân vật",
        domain=[("active", "=", True), ("storage_path", "!=", False), ("image_type", "=", "character")],
    )

    # --- Lựa chọn Âm thanh MP3 ---
    audio_selection_mode = fields.Selection(
        [
            ("selected", "Tự chọn danh sách Bài Nhạc cụ thể"),
            ("least_used", "Tự động chọn N bài nhạc ít tạo video nhất"),
            ("all", "Tất cả bài nhạc đang có trên R2"),
        ],
        default="least_used",
        string="Chế độ chọn Nhạc",
        required=True,
    )
    audio_ids = fields.Many2many(
        "audio.library",
        "music_video_batch_audio_rel",
        "wizard_id",
        "audio_id",
        string="Danh sách Bài Nhạc",
        domain=[("active", "=", True), ("storage_path", "!=", False)],
    )
    audio_count = fields.Integer(
        string="Số lượng bài nhạc",
        default=5,
        help="Số bài nhạc ít dùng nhất cần lấy tự động từ thư viện.",
    )

    # --- Chiến lược ghép cặp ---
    pair_strategy = fields.Selection(
        [
            ("per_audio", "1 Job cho mỗi Bài Nhạc (Xoay vòng Background & Nhân vật)"),
            ("cross_product", "Tổ hợp Nhân Vật x Bài Nhạc (Mỗi nhân vật ghép với tất cả bài nhạc)"),
        ],
        default="per_audio",
        string="Quy tắc tạo Job",
        required=True,
    )

    # --- Bố cục & Hiệu ứng ---
    music_layout = fields.Selection(
        [("random", "🎲 Ngẫu nhiên theo từng Job")] + LAYOUT_OPTIONS,
        default="spotify_card",
        string="Kiểu Bố Cục Nhân Vật",
        required=True,
    )
    visualizer_style = fields.Selection(
        [("random", "🎲 Ngẫu nhiên theo từng Job")] + VISUALIZER_OPTIONS,
        default="spectrum_bars",
        string="Kiểu Sóng Nhạc (Visualizer)",
        required=True,
    )
    visualizer_color = fields.Selection(
        [("random", "🎲 Ngẫu nhiên theo từng Job")] + COLOR_OPTIONS,
        default="cyan_neon",
        string="Màu Sóng Nhạc",
        required=True,
    )
    particle_effect = fields.Selection(
        [("random", "🎲 Ngẫu nhiên theo từng Job")] + PARTICLE_OPTIONS,
        default="snow_fall",
        string="Hiệu Ứng Không Khí",
        required=True,
    )
    music_preset = fields.Selection(
        [("random", "🎲 Ngẫu nhiên theo từng Job")] + PRESET_OPTIONS,
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
        string="Thời lượng tối đa (s)",
        default=0.0,
        help="0 để lấy trọn vẹn độ dài bài nhạc.",
    )

    # --- Chế độ Lập lịch & Thực thi ---
    execution_mode = fields.Selection(
        [
            ("draft", "Chỉ tạo Job Chờ (Draft) - Bấm chạy thủ công khi cần"),
            ("scheduled", "Lập lịch chạy tự động giãn cách (Staggered Cron)"),
            ("run_immediate", "Tạo Job và Chạy ngay lập tức"),
        ],
        default="draft",
        string="Chế độ thực thi",
        required=True,
    )
    start_date = fields.Datetime(
        string="Thời điểm bắt đầu chạy",
        default=fields.Datetime.now,
        help="Thời điểm bắt đầu thực thi job đầu tiên khi lập lịch.",
    )
    interval_minutes = fields.Integer(
        string="Giãn cách mỗi job (phút)",
        default=5,
        help="Khoảng cách thời gian giữa các job liên tiếp (ví dụ: 5 phút để tránh nghẽn CPU server).",
    )
    priority = fields.Selection(
        [
            ("0", "Thấp"),
            ("1", "Bình thường"),
            ("2", "Ưu tiên"),
            ("3", "Khẩn cấp"),
        ],
        default="1",
        string="Độ ưu tiên",
        required=True,
    )

    def _get_backgrounds(self):
        self.ensure_one()
        if self.bg_selection_mode == "single":
            if not self.bg_image_id:
                raise UserError("Vui lòng chọn Ảnh Background.")
            return [self.bg_image_id]
        if not self.bg_image_ids:
            raise UserError("Vui lòng chọn ít nhất một Ảnh Background trong danh sách.")
        return list(self.bg_image_ids)

    def _get_characters(self):
        self.ensure_one()
        if self.char_selection_mode == "single":
            if not self.character_image_id:
                raise UserError("Vui lòng chọn Ảnh Nhân vật.")
            return [self.character_image_id]
        if not self.character_image_ids:
            raise UserError("Vui lòng chọn ít nhất một Ảnh Nhân vật trong danh sách.")
        return list(self.character_image_ids)

    def _get_audios(self):
        self.ensure_one()
        AudioModel = self.env["audio.library"]
        if self.audio_selection_mode == "selected":
            if not self.audio_ids:
                raise UserError("Vui lòng chọn ít nhất một Bài Nhạc trong danh sách.")
            return list(self.audio_ids)

        if self.audio_selection_mode == "all":
            audios = AudioModel.search(
                [("active", "=", True), ("storage_path", "!=", False)],
                order="id asc",
            )
            if not audios:
                raise UserError("Không tìm thấy file nhạc MP3 active nào trên R2.")
            return list(audios)

        # least_used mode:
        audios = AudioModel.search([("active", "=", True), ("storage_path", "!=", False)])
        if not audios:
            raise UserError("Không tìm thấy file nhạc MP3 active nào trên R2.")

        # Thống kê số lần sử dụng của từng audio trong video.library
        usage_data = self.env["video.library"].read_group(
            [("audio_id", "in", audios.ids)],
            ["audio_id"],
            ["audio_id"],
        )
        usage_counts = {item["audio_id"][0]: item["audio_id_count"] for item in usage_data if item.get("audio_id")}

        sorted_audios = sorted(
            audios,
            key=lambda a: (usage_counts.get(a.id, 0), a.id),
        )
        limit = max(1, self.audio_count or 5)
        return sorted_audios[:limit]

    def _pick_style(self, selected_val, options_list):
        if selected_val == "random":
            return random.choice([k for k, _ in options_list])
        return selected_val

    def action_create_jobs(self):
        """Tạo hàng loạt các Video Generate Jobs theo cấu hình."""
        self.ensure_one()
        bgs = self._get_backgrounds()
        chars = self._get_characters()
        audios = self._get_audios()

        pairs = []
        if self.pair_strategy == "per_audio":
            for idx, audio in enumerate(audios):
                bg = bgs[idx % len(bgs)]
                char = chars[idx % len(chars)]
                pairs.append((bg, char, audio))
        else:  # cross_product: char x audio
            bg_idx = 0
            for char in chars:
                for audio in audios:
                    bg = bgs[bg_idx % len(bgs)]
                    bg_idx += 1
                    pairs.append((bg, char, audio))

        if not pairs:
            raise UserError("Không tạo được cặp dữ liệu nào từ cấu hình hiện tại.")

        JobModel = self.env["video.generate.job"]
        created_jobs = self.env["video.generate.job"]

        start_time = self.start_date or fields.Datetime.now()
        interval = max(1, self.interval_minutes or 5)

        for idx, (bg, char, audio) in enumerate(pairs):
            layout = self._pick_style(self.music_layout, LAYOUT_OPTIONS)
            visualizer = self._pick_style(self.visualizer_style, VISUALIZER_OPTIONS)
            color = self._pick_style(self.visualizer_color, COLOR_OPTIONS)
            particle = self._pick_style(self.particle_effect, PARTICLE_OPTIONS)
            preset = self._pick_style(self.music_preset, PRESET_OPTIONS)

            scheduled_date = False
            is_scheduled = False
            if self.execution_mode == "scheduled":
                scheduled_date = start_time + timedelta(minutes=idx * interval)
                is_scheduled = True

            job_vals = {
                "job_type": "music_video",
                "bg_image_id": bg.id,
                "character_image_id": char.id,
                "audio_id": audio.id,
                "storage_id": self.storage_id.id,
                "music_layout": layout,
                "visualizer_style": visualizer,
                "visualizer_color": color,
                "particle_effect": particle,
                "music_preset": preset,
                "effect_preset": self.effect_preset,
                "max_duration": self.max_duration or 0.0,
                "priority": self.priority,
                "is_scheduled": is_scheduled,
                "scheduled_date": scheduled_date,
                "state": "draft",
            }
            job = JobModel.create(job_vals)
            created_jobs |= job

        if self.execution_mode == "run_immediate":
            # Chạy ngay lập tức các job vừa tạo
            created_jobs.action_run_job()

        # Mở danh sách các job vừa tạo
        return {
            "name": f"Jobs Tạo Video Ca Nhạc ({len(created_jobs)} Jobs)",
            "type": "ir.actions.act_window",
            "res_model": "video.generate.job",
            "view_mode": "tree,form",
            "domain": [("id", "in", created_jobs.ids)],
            "target": "current",
        }
