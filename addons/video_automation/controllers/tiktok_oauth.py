import logging
from urllib.parse import unquote

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class TikTokOAuthController(http.Controller):

    @http.route("/tiktok/oauth/callback", type="http", auth="public", csrf=False)
    def tiktok_oauth_callback(self, **kwargs):
        code = kwargs.get("code")
        state = kwargs.get("state")
        error = kwargs.get("error")
        if error:
            return request.make_response(
                f"TikTok OAuth error: {error} {kwargs.get('error_description', '')}",
                headers=[("Content-Type", "text/plain")],
                status=400,
            )
        if not code or not state:
            return request.make_response(
                "Missing code or state",
                headers=[("Content-Type", "text/plain")],
                status=400,
            )

        # state format: account-<id>-<ts>
        account = request.env["tiktok.account"].sudo().search(
            [("oauth_state", "=", state)], limit=1
        )
        if not account:
            return request.make_response(
                "Unknown OAuth state. Start Connect again from Odoo.",
                headers=[("Content-Type", "text/plain")],
                status=400,
            )
        try:
            from odoo.addons.video_automation.services.tiktok_client import TikTokClient

            if not account.oauth_code_verifier:
                return request.make_response(
                    "Missing PKCE verifier. Click Connect TikTok again from Odoo.",
                    headers=[("Content-Type", "text/plain")],
                    status=400,
                )
            client = TikTokClient(account.tiktok_app_id, account)
            decoded = unquote(code)
            data = client.exchange_code(decoded, account.oauth_code_verifier)
            account.apply_token_response(data)
            account.write({"oauth_state": False, "oauth_code_verifier": False})
        except Exception as exc:
            _logger.exception("OAuth callback failed")
            return request.make_response(
                f"OAuth failed: {exc}",
                headers=[("Content-Type", "text/plain")],
                status=500,
            )

        return request.redirect("/web#action=&model=tiktok.account&view_type=form&id=%s" % account.id)
