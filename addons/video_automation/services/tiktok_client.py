import hashlib
import logging
import secrets
import string
from urllib.parse import urlencode

import requests

_logger = logging.getLogger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
INIT_POST_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
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
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            _logger.error("TikTok token exchange failed: %s", data or resp.text)
            message = (
                (data.get("error") or {}).get("message")
                if isinstance(data.get("error"), dict)
                else data.get("error_description") or data.get("message") or resp.text
            )
            raise RuntimeError(message or f"Token exchange HTTP {resp.status_code}")
        # Some responses wrap tokens under data
        if isinstance(data.get("data"), dict) and data["data"].get("access_token"):
            return data["data"]
        return data

    def refresh_access_token(self, refresh_token):
        payload = {
            "client_key": self.app.client_key,
            "client_secret": self.app.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        resp = requests.post(TOKEN_URL, data=payload, timeout=30)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            _logger.error("TikTok token refresh failed: %s", data or resp.text)
            resp.raise_for_status()
        if isinstance(data.get("data"), dict) and data["data"].get("access_token"):
            return data["data"]
        return data

    def get_user_info(self, access_token, fields="open_id,display_name,avatar_url"):
        """Fetch basic profile after OAuth (scope user.info.basic)."""
        resp = requests.get(
            USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields},
            timeout=30,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            _logger.warning("TikTok user info failed: %s", data or resp.text)
            return {}
        user = (data.get("data") or {}).get("user") or {}
        return user

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.account.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _raise_api_error(self, resp, data, label):
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("code") and err.get("code") != "ok":
            _logger.error("%s failed: %s", label, data)
            raise RuntimeError(err.get("message") or str(data))
        if resp.status_code >= 400:
            _logger.error("%s HTTP %s: %s", label, resp.status_code, data or resp.text)
            message = (
                err.get("message")
                if isinstance(err, dict)
                else (data.get("message") if isinstance(data, dict) else None)
            )
            raise RuntimeError(message or resp.text or f"{label} HTTP {resp.status_code}")

    def init_inbox_pull_from_url(self, video_url):
        """
        Draft/Inbox init via PULL_FROM_URL (scope video.upload).
        POST /v2/post/publish/inbox/video/init/

        TikTok pulls the video from video_url (domain/URL prefix must be verified
        in TikTok Developer Portal). No binary PUT needed.
        Returns data.publish_id.
        """
        if not video_url:
            raise RuntimeError("Missing video_url for TikTok inbox PULL_FROM_URL.")
        body = {
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            }
        }
        resp = requests.post(INBOX_INIT_URL, headers=self._headers(), json=body, timeout=60)
        data = resp.json() if resp.content else {}
        self._raise_api_error(resp, data, "TikTok inbox init")
        payload = data.get("data") or {}
        if not payload.get("publish_id"):
            raise RuntimeError(f"Inbox init missing publish_id: {data}")
        payload["_video_url"] = video_url
        return payload

    def publish_inbox_draft_from_url(self, video_url):
        """Inbox draft: init with PULL_FROM_URL → TikTok downloads → user Edit → Post."""
        return self.init_inbox_pull_from_url(video_url)

    def init_direct_post_pull_from_url(self, video_url, caption, privacy_level="SELF_ONLY"):
        """
        Direct post (not inbox) with PULL_FROM_URL — optional / legacy.
        POST /v2/post/publish/video/init/
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
        data = resp.json() if resp.content else {}
        self._raise_api_error(resp, data, "TikTok direct post init")
        return data

    def fetch_publish_status(self, publish_id):
        body = {"publish_id": publish_id}
        resp = requests.post(STATUS_URL, headers=self._headers(), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()
