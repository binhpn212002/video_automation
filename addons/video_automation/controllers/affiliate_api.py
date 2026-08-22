import json
import logging

from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


class AffiliateApiController(http.Controller):

    @http.route("/api/video/create", type="http", auth="public", methods=["POST"], csrf=False)
    def api_create_video(self, **kwargs):
        """
        REST API endpoint to trigger or queue TikTok affiliate video rendering from product image.
        Accepts application/json POST body.
        """
        try:
            try:
                body = json.loads(request.httprequest.data.decode("utf-8") or "{}")
            except Exception:
                body = kwargs

            image_id = body.get("image_id")
            if not image_id:
                return Response(
                    json.dumps({"error": "Missing required parameter 'image_id'"}),
                    status=400,
                    content_type="application/json",
                )

            image = request.env["product.image"].sudo().browse(int(image_id))
            if not image.exists():
                return Response(
                    json.dumps({"error": f"Product image with ID {image_id} not found"}),
                    status=404,
                    content_type="application/json",
                )

            audio_id = body.get("audio_id")
            effect_cfg = body.get("effect") or {}
            effect_preset = effect_cfg.get("preset", "normal")
            flash_effect = effect_cfg.get("flash", True)

            text_cfg = body.get("text") or {}
            hook_data = text_cfg.get("hook") or {}
            hook_text = hook_data.get("text") if isinstance(hook_data, dict) else body.get("hook_text")
            cta_data = text_cfg.get("cta") or {}
            cta_text = cta_data.get("text") if isinstance(cta_data, dict) else body.get("cta_text")

            job = request.env["video.generate.job"].sudo().create(
                {
                    "image_id": image.id,
                    "audio_id": int(audio_id) if audio_id else False,
                    "effect_preset": effect_preset,
                    "flash_effect": flash_effect,
                    "hook_text": hook_text or image.default_hook or "",
                    "cta_text": cta_text or image.default_cta or "",
                    "state": "draft",
                }
            )

            # Execute rendering
            job.action_run_job()

            if job.state == "completed" and job.video_id:
                response_data = {
                    "status": "completed",
                    "job_id": job.name,
                    "stage": "COMPLETED",
                    "video_id": job.video_id.id,
                    "video_url": job.video_url,
                    "duration": job.duration,
                    "width": job.video_id.width,
                    "height": job.video_id.height,
                    "fps": job.video_id.fps,
                    "message": "Video generated successfully.",
                }
            elif job.state == "failed":
                response_data = {
                    "status": "failed",
                    "job_id": job.name,
                    "error": job.error_message,
                }
            else:
                response_data = {
                    "status": "processing",
                    "job_id": job.name,
                    "stage": "RENDERING",
                    "message": "Video creation job has been queued.",
                }

            return Response(
                json.dumps(response_data),
                status=200 if job.state != "failed" else 500,
                content_type="application/json",
            )

        except Exception as exc:
            _logger.exception("Error in /api/video/create: %s", exc)
            return Response(
                json.dumps({"error": str(exc)}),
                status=500,
                content_type="application/json",
            )

    @http.route("/api/video/jobs/<string:job_id>", type="http", auth="public", methods=["GET"], csrf=False)
    def api_get_job_status(self, job_id, **kwargs):
        """
        REST API endpoint to check job progress and retrieve rendered video URL.
        """
        try:
            job = request.env["video.generate.job"].sudo().search([("name", "=", job_id)], limit=1)
            if not job:
                return Response(
                    json.dumps({"error": f"Job {job_id} not found"}),
                    status=404,
                    content_type="application/json",
                )

            if job.state == "completed":
                data = {
                    "job_id": job.name,
                    "status": "completed",
                    "stage": "COMPLETED",
                    "video_id": job.video_id.id if job.video_id else False,
                    "video_url": job.video_url,
                    "duration": job.duration,
                    "width": job.video_id.width if job.video_id else 1080,
                    "height": job.video_id.height if job.video_id else 1920,
                    "fps": job.video_id.fps if job.video_id else 30.0,
                }
            elif job.state == "failed":
                data = {
                    "job_id": job.name,
                    "status": "failed",
                    "stage": "FAILED",
                    "error": job.error_message,
                }
            else:
                data = {
                    "job_id": job.name,
                    "status": "processing",
                    "stage": "RENDERING",
                }

            return Response(json.dumps(data), status=200, content_type="application/json")

        except Exception as exc:
            _logger.exception("Error in /api/video/jobs/%s: %s", job_id, exc)
            return Response(
                json.dumps({"error": str(exc)}),
                status=500,
                content_type="application/json",
            )
