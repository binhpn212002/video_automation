import logging
from collections import defaultdict
from odoo import api, fields, models
from odoo.exceptions import UserError
from ..models.audio_library import _normalize_audio_name

_logger = logging.getLogger(__name__)


class AudioDeduplicateWizard(models.TransientModel):
    _name = "audio.deduplicate.wizard"
    _description = "Dọn dẹp Audio trùng lặp theo Tên"

    total_audio_count = fields.Integer(
        string="Tổng số file Audio",
        readonly=True,
    )
    duplicate_group_count = fields.Integer(
        string="Số nhóm tên bị trùng",
        readonly=True,
    )
    duplicate_record_count = fields.Integer(
        string="Số bản ghi trùng sẽ xóa",
        readonly=True,
    )
    reassign_videos = fields.Boolean(
        string="Chuyển liên kết Video sang Audio giữ lại",
        default=True,
        help="Nếu chọn, các Video / Render Job đang gắn với Audio trùng bị xóa sẽ tự động chuyển sang Audio gốc được giữ lại.",
    )
    preview_html = fields.Html(
        string="Chi tiết Audio trùng lặp",
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Audio = self.env["audio.library"].sudo()
        all_audios = Audio.search([], order="id asc")
        total_count = len(all_audios)

        by_name = defaultdict(list)
        for a in all_audios:
            norm_name = _normalize_audio_name(a.name)
            if norm_name:
                by_name[norm_name].append(a)

        dup_groups = {k: v for k, v in by_name.items() if len(v) > 1}
        dup_record_count = sum(len(v) - 1 for v in dup_groups.values())

        # Build preview HTML
        if not dup_groups:
            html = """
            <div class="alert alert-success d-flex align-items-center" role="alert">
                <div>
                    <h5 class="alert-heading mb-1">Không phát hiện Audio trùng lặp!</h5>
                    <p class="mb-0">Thư viện Audio hiện tại không có bản ghi nào bị trùng tên.</p>
                </div>
            </div>
            """
        else:
            rows = []
            for name, recs in sorted(dup_groups.items(), key=lambda item: len(item[1]), reverse=True):
                # Pick keeper for preview
                keeper = Audio._pick_best_keeper(recs)
                duplicates = [r for r in recs if r.id != keeper.id]
                
                keeper_badge = (
                    f"<span class='badge rounded-pill text-bg-success'>ID {keeper.id}</span> "
                    f"<b>{keeper.name}</b>"
                )
                if keeper.duration:
                    keeper_badge += f" ({keeper.duration:.1f}s)"
                if keeper.beat_status == "detected":
                    keeper_badge += " <span class='badge bg-info text-dark'>Beat OK</span>"

                dup_badges = " ".join([
                    f"<span class='badge rounded-pill text-bg-danger' title='Sẽ xóa'>ID {d.id}</span>"
                    for d in duplicates
                ])

                rows.append(
                    f"<tr>"
                    f"<td class='fw-bold'>{recs[0].name}</td>"
                    f"<td class='text-center'><span class='badge bg-warning text-dark'>{len(recs)} bản ghi</span></td>"
                    f"<td>{keeper_badge}</td>"
                    f"<td>{dup_badges} <span class='text-muted small'>({len(duplicates)} bản ghi)</span></td>"
                    f"</tr>"
                )

            html = f"""
            <div class="alert alert-warning" role="alert">
                Phát hiện <b>{len(dup_groups)}</b> tên bài hát bị trùng lặp với tổng cộng <b>{dup_record_count}</b> bản ghi dư thừa.
            </div>
            <div class="table-responsive" style="max-height: 380px; overflow-y: auto;">
                <table class="table table-bordered table-striped table-hover align-middle mb-0">
                    <thead class="table-light sticky-top">
                        <tr>
                            <th>Tên Audio</th>
                            <th class="text-center" style="width: 120px;">Số lượng</th>
                            <th>Bản Ghi Giữ Lại (Keeper)</th>
                            <th>Bản Ghi Sẽ Dọn Dẹp (Delete)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(rows)}
                    </tbody>
                </table>
            </div>
            """

        res.update(
            {
                "total_audio_count": total_count,
                "duplicate_group_count": len(dup_groups),
                "duplicate_record_count": dup_record_count,
                "preview_html": html,
            }
        )
        return res

    def action_deduplicate(self):
        """Thực hiện dọn dẹp các bản ghi duplicate."""
        self.ensure_one()
        Audio = self.env["audio.library"].sudo()
        stats = Audio.deduplicate_by_name(reassign_videos=self.reassign_videos)

        deleted_count = stats.get("deleted_count", 0)
        group_count = stats.get("group_count", 0)
        reassigned_videos = stats.get("reassigned_videos", 0)

        msg = (
            f"Đã dọn dẹp thành công {deleted_count} bản ghi Audio trùng lặp thuộc {group_count} nhóm. "
            f"Đã chuyển liên kết {reassigned_videos} video sang audio gốc."
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Dọn dẹp Audio hoàn tất!",
                "message": msg,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
