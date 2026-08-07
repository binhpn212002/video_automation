import hashlib
import logging
import secrets
import string
from urllib.parse import urlencode

import requests

_logger = logging.getLogger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
INIT_POST_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

PKCE_CHARS = string.ascii_letters + string.digits + "-._~"


def generate_code_verifier(length=64):
    """PKCE verifier (43-128 chars, unreserved URI chars)."""
    length = max(43, min(length, 128))
    return "".join(secrets.choice(PKCE_CHARS) for _ in range(length))


def generate_code_challenge(code_verifier):
    """
    TikTok PKCE uses HEX(SHA256(verifier)), not standard base64url.
    See: https://developers.tiktok.com/doc/login-kit-desktop
    """
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return digest.hex()


class TikTokClient:
    """TikTok Content Posting API (Sandbox / Production)."""

    def __init__(self, app, account=None):
        self.app = app
        self.account = account

    def build_authorize_url(self, state, code_verifier):
        code_challenge = generate_code_challenge(code_verifier)
        params = {
            "client_key": self.app.client_key,
            "scope": "user.info.basic,video.publish,video.upload",
            "response_type": "code",
            "redirect_uri": self.app.redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code, code_verifier):
        payload = {
            "client_key": self.app.client_key,
            "client_secret": self.app.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.app.redirect_uri,
            "code_verifier": code_verifier,
        }
        resp = requests.post(TOKEN_URL, data=payload, timeout=30)
        if resp.status_code >= 400:
            _logger.error("TikTok token exchange failed: %s", resp.text)
            resp.raise_for_status()
        return resp.json()

    def refresh_access_token(self, refresh_token):
        payload = {
            "client_key": self.app.client_key,
            "client_secret": self.app.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        resp = requests.post(TOKEN_URL, data=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.account.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def init_pull_from_url(self, video_url, caption, privacy_level="SELF_ONLY"):
        """
        Initialize direct post with PULL_FROM_URL.
        Sandbox typically forces private (SELF_ONLY).
        """
        body = {
            "post_info": {
                "title": (caption or "")[:150],
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            },
        }
        resp = requests.post(INIT_POST_URL, headers=self._headers(), json=body, timeout=60)
        data = resp.json()
        if resp.status_code >= 400:
            _logger.error("TikTok init post failed: %s", data)
            raise RuntimeError(data.get("error", {}).get("message") or resp.text)
        return data

    def fetch_publish_status(self, publish_id):
        body = {"publish_id": publish_id}
        resp = requests.post(STATUS_URL, headers=self._headers(), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()
