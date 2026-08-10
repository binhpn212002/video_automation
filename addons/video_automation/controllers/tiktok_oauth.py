import html
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
            return self._html_page(
                "TikTok Login failed",
                f"<p>Error: <b>{html.escape(error)}</b></p>"
                f"<p>{html.escape(kwargs.get('error_description') or '')}</p>"
                f"<p>Kiểm tra Redirect URI trên TikTok Developer Portal khớp với TikTok App trong Odoo.</p>",
                status=400,
            )
        if not code or not state:
            return self._html_page(
                "Missing code or state",
                "<p>Thiếu code/state từ TikTok. Bấm <b>Login TikTok</b> lại từ Odoo.</p>",
                status=400,
            )

        account = request.env["tiktok.account"].sudo().search(
            [("oauth_state", "=", state)], limit=1
        )
        if not account:
            return self._html_page(
                "Unknown OAuth state",
                "<p>State không khớp. Mở account trong Odoo → Login TikTok lại.</p>",
                status=400,
            )
        try:
            from odoo.addons.video_automation.services.tiktok_client import TikTokClient

            if not account.oauth_code_verifier:
                return self._html_page(
                    "Missing PKCE verifier",
                    "<p>Thiếu code_verifier. Bấm <b>Login TikTok</b> lại.</p>",
                    status=400,
                )
            client = TikTokClient(account.tiktok_app_id, account)
            decoded = unquote(code)
            data = client.exchange_code(decoded, account.oauth_code_verifier)
            account.apply_token_response(data, fetch_profile=True)
            account.write({"oauth_state": False, "oauth_code_verifier": False})
        except Exception as exc:
            _logger.exception("OAuth callback failed")
            return self._html_page(
                "OAuth failed",
                f"<p>{html.escape(str(exc))}</p>",
                status=500,
            )

        back_url = f"/web#id={account.id}&model=tiktok.account&view_type=form"
        return self._html_page(
            "Login TikTok thành công",
            (
                f"<p>Account: <b>{html.escape(account.display_name)}</b></p>"
                f"<p>open_id: <code>{html.escape(account.open_id or '-')}</code></p>"
                f"<p>username: <b>{html.escape(account.username or '-')}</b></p>"
                f"<p>auth_state: <b>{html.escape(account.auth_state)}</b></p>"
                f"<p>token_expires_at: {html.escape(str(account.token_expires_at or '-'))}</p>"
                f"<p>Access / refresh token đã được lưu vào Odoo.</p>"
                f'<p><a href="{back_url}">Quay lại TikTok Account trong Odoo</a></p>'
            ),
            status=200,
        )

    def _html_page(self, title, body_html, status=200):
        content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.5}}
code{{background:#f4f4f4;padding:2px 6px;border-radius:4px}}
a{{color:#1664c0}}
</style></head>
<body><h1>{html.escape(title)}</h1>{body_html}</body></html>"""
        return request.make_response(
            content,
            headers=[("Content-Type", "text/html; charset=utf-8")],
            status=status,
        )
