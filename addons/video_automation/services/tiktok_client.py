import hashlib
import logging
import os
import secrets
import string
from urllib.parse import urlencode

import requests
import urllib3

_logger = logging.getLogger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
INIT_POST_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# FILE_UPLOAD: 1 chunk nếu ≤64MB; lớn hơn dùng chunk ~10MB.
MAX_SINGLE_CHUNK = 64 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024

PKCE_CHARS = string.ascii_letters + string.digits + "-._~"


def _ssl_verify():
    val = (os.environ.get("TIKTOK_SSL_VERIFY") or "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def _request(method, url, **kwargs):
    """HTTP helper: tôn trọng TIKTOK_SSL_VERIFY; SSL fail → retry verify=False."""
    if "verify" not in kwargs:
        kwargs["verify"] = _ssl_verify()
    try:
        return requests.request(method, url, **kwargs)
    except requests.exceptions.SSLError:
        if kwargs.get("verify") is False:
            raise
        _logger.warning(
            "SSL verify failed for %s; retrying with verify=False "
            "(corporate SSL inspection / set TIKTOK_SSL_VERIFY=0).",
            url,
        )
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        kwargs["verify"] = False
        return requests.request(method, url, **kwargs)


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

    def build_authorize_url(self, state, code_verifier, scope=None):
        code_challenge = generate_code_challenge(code_verifier)
        if not scope:
            scope = "user.info.basic,user.info.profile,user.info.stats,video.publish,video.upload,video.list"
        params = {
            "client_key": (self.app.client_key or "").strip(),
            "scope": scope,
            "response_type": "code",
            "redirect_uri": (self.app.redirect_uri or "").strip(),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code, code_verifier):
        payload = {
            "client_key": (self.app.client_key or "").strip(),
            "client_secret": (self.app.client_secret or "").strip(),
            "code": (code or "").strip(),
            "grant_type": "authorization_code",
            "redirect_uri": (self.app.redirect_uri or "").strip(),
            "code_verifier": (code_verifier or "").strip(),
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = _request("POST", TOKEN_URL, headers=headers, data=payload, timeout=30)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            _logger.error("TikTok token exchange failed: %s", data or resp.text)
            message = (
                (data.get("error") or {}).get("message")
                if isinstance(data.get("error"), dict)
                else data.get("error_description") or data.get("message") or resp.text
            )
            raise RuntimeError(message or f"Token exchange HTTP {resp.status_code}")
        if isinstance(data.get("data"), dict) and data["data"].get("access_token"):
            return data["data"]
        return data

    def refresh_access_token(self, refresh_token):
        payload = {
            "client_key": (self.app.client_key or "").strip(),
            "client_secret": (self.app.client_secret or "").strip(),
            "grant_type": "refresh_token",
            "refresh_token": (refresh_token or "").strip(),
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = _request("POST", TOKEN_URL, headers=headers, data=payload, timeout=30)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            _logger.error("TikTok token refresh failed: %s", data or resp.text)
            resp.raise_for_status()
        if isinstance(data.get("data"), dict) and data["data"].get("access_token"):
            return data["data"]
        return data

    def get_user_info(self, access_token, fields=None):
        """Fetch user profile after OAuth (returns user dict for backward compatibility)."""
        res = self.get_user_info_full((access_token or "").strip(), fields=fields)
        return res.get("user") or {}

    def get_user_info_full(self, access_token, fields=None):
        """
        Fetch full user profile from /v2/user/info/.
        Returns structured dict with success status, user info, error and raw response.
        """
        if not fields:
            fields = (
                "open_id,union_id,avatar_url,avatar_url_100,avatar_url_200,avatar_large_url,"
                "display_name,bio_description,profile_deep_link,is_verified,"
                "follower_count,following_count,likes_count,video_count"
            )

        resp = _request(
            "GET",
            USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields},
            timeout=30,
        )
        data = resp.json() if resp.content else {}

        # Nếu lỗi do một số field không thuộc scope được cấp, retry với basic fields
        if resp.status_code == 400 or (
            isinstance(data.get("error"), dict)
            and data["error"].get("code") in ("scope_not_authorized", "invalid_params", "field_not_authorized")
        ):
            fallback_fields = "open_id,union_id,avatar_url,display_name,bio_description,profile_deep_link,is_verified"
            _logger.info("Retrying TikTok user info with fallback fields: %s", fallback_fields)
            resp = _request(
                "GET",
                USER_INFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"fields": fallback_fields},
                timeout=30,
            )
            data = resp.json() if resp.content else {}

        err = data.get("error") if isinstance(data, dict) else {}
        err_code = err.get("code") if isinstance(err, dict) else None
        err_msg = err.get("message") if isinstance(err, dict) else ""
        is_success = resp.status_code == 200 and (not err_code or err_code == "ok")
        user = (data.get("data") or {}).get("user") or {}

        return {
            "success": is_success,
            "status_code": resp.status_code,
            "user": user,
            "error_code": err_code,
            "error_message": err_msg or (resp.text if not is_success else ""),
            "raw": data,
        }

    def get_creator_info(self, access_token):
        """
        Query creator info & posting permissions from /v2/post/publish/creator_info/query/.
        Scope: video.publish or video.upload.
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        resp = _request("POST", CREATOR_INFO_URL, headers=headers, json={}, timeout=30)
        data = resp.json() if resp.content else {}
        err = data.get("error") if isinstance(data, dict) else {}
        err_code = err.get("code") if isinstance(err, dict) else None
        err_msg = err.get("message") if isinstance(err, dict) else ""
        is_success = resp.status_code == 200 and (not err_code or err_code == "ok")
        creator = data.get("data") if isinstance(data.get("data"), dict) else {}

        return {
            "success": is_success,
            "status_code": resp.status_code,
            "creator": creator,
            "error_code": err_code,
            "error_message": err_msg or (resp.text if not is_success else ""),
            "raw": data,
        }

    def fetch_full_profile(self, access_token=None):
        """
        Aggregate user info and creator info for inspection & token validation.
        """
        token = access_token or (self.account.access_token if self.account else None)
        if not token:
            return {
                "is_valid": False,
                "token_status": "disconnected",
                "error_message": "Chưa có Access Token.",
                "user_info": {},
                "creator_info": {},
                "raw_user": {},
                "raw_creator": {},
            }

        user_res = self.get_user_info_full(token)
        creator_res = self.get_creator_info(token)

        # Kiểm tra token hợp lệ
        is_token_invalid = (
            user_res.get("status_code") in (401, 403)
            or user_res.get("error_code") in ("access_token_invalid", "token_expired", "invalid_token", "invalid_grant", "unauthorized")
            or (not user_res.get("success") and creator_res.get("status_code") in (401, 403))
        )

        if is_token_invalid:
            token_status = "expired"
            error_message = (
                user_res.get("error_message")
                or creator_res.get("error_message")
                or "Access Token đã hết hạn hoặc không hợp lệ trên TikTok."
            )
            is_valid = False
        elif user_res.get("success") or creator_res.get("success"):
            token_status = "valid"
            error_message = ""
            is_valid = True
        else:
            token_status = "invalid"
            error_message = user_res.get("error_message") or creator_res.get("error_message") or "Lỗi kết nối TikTok API."
            is_valid = False

        return {
            "is_valid": is_valid,
            "token_status": token_status,
            "error_message": error_message,
            "user_info": user_res.get("user") or {},
            "creator_info": creator_res.get("creator") or {},
            "raw_user": user_res.get("raw") or {},
            "raw_creator": creator_res.get("raw") or {},
        }

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

    def init_inbox_file_upload(self, video_size):
        """
        Inbox init FILE_UPLOAD (scope video.upload).
        POST /v2/post/publish/inbox/video/init/
        → publish_id + upload_url
        """
        video_size = int(video_size)
        if video_size <= 0:
            raise RuntimeError("Invalid video_size for TikTok inbox upload.")

        if video_size <= MAX_SINGLE_CHUNK:
            chunk_size = video_size
            total_chunk_count = 1
        else:
            chunk_size = DEFAULT_CHUNK_SIZE
            total_chunk_count = (video_size + chunk_size - 1) // chunk_size

        body = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            }
        }
        resp = _request("POST", INBOX_INIT_URL, headers=self._headers(), json=body, timeout=60)
        data = resp.json() if resp.content else {}
        self._raise_api_error(resp, data, "TikTok inbox init")
        payload = data.get("data") or {}
        if not payload.get("upload_url") or not payload.get("publish_id"):
            raise RuntimeError(f"Inbox init missing upload_url/publish_id: {data}")
        payload["_chunk_size"] = chunk_size
        payload["_total_chunk_count"] = total_chunk_count
        payload["_video_size"] = video_size
        return payload

    def upload_video_binary(self, upload_url, file_path, video_size=None, chunk_size=None):
        """PUT video binary lên upload_url (Content-Range)."""
        video_size = int(video_size or os.path.getsize(file_path))
        chunk_size = int(chunk_size or video_size)
        if chunk_size <= 0:
            chunk_size = video_size

        with open(file_path, "rb") as fh:
            offset = 0
            chunk_index = 0
            while offset < video_size:
                end = min(offset + chunk_size, video_size) - 1
                length = end - offset + 1
                chunk = fh.read(length)
                headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(length),
                    "Content-Range": f"bytes {offset}-{end}/{video_size}",
                }
                resp = _request(
                    "PUT",
                    upload_url,
                    headers=headers,
                    data=chunk,
                    timeout=300,
                )
                if resp.status_code not in (200, 201, 206):
                    _logger.error(
                        "TikTok PUT chunk %s failed HTTP %s: %s",
                        chunk_index,
                        resp.status_code,
                        resp.text[:1000],
                    )
                    raise RuntimeError(
                        f"Upload chunk {chunk_index} failed HTTP {resp.status_code}: "
                        f"{resp.text[:500]}"
                    )
                _logger.info(
                    "TikTok uploaded bytes %s-%s/%s (HTTP %s)",
                    offset,
                    end,
                    video_size,
                    resp.status_code,
                )
                offset = end + 1
                chunk_index += 1
        return True

    def publish_inbox_draft_from_file(self, file_path):
        """FILE_UPLOAD: init inbox → PUT binary từ file local (temp)."""
        video_size = os.path.getsize(file_path)
        init = self.init_inbox_file_upload(video_size)
        self.upload_video_binary(
            init["upload_url"],
            file_path,
            video_size=init["_video_size"],
            chunk_size=init["_chunk_size"],
        )
        return init

    def init_inbox_pull_from_url(self, video_url):
        """Inbox PULL_FROM_URL (cần verify domain CDN trên TikTok Portal)."""
        if not video_url:
            raise RuntimeError("Missing video_url for TikTok inbox PULL_FROM_URL.")
        body = {
            "source_info": {
                "source": "PULL_FROM_URL",
                "video_url": video_url,
            }
        }
        resp = _request("POST", INBOX_INIT_URL, headers=self._headers(), json=body, timeout=60)
        data = resp.json() if resp.content else {}
        self._raise_api_error(resp, data, "TikTok inbox init")
        payload = data.get("data") or {}
        if not payload.get("publish_id"):
            raise RuntimeError(f"Inbox init missing publish_id: {data}")
        payload["_video_url"] = video_url
        return payload

    def fetch_publish_status(self, publish_id):
        body = {"publish_id": publish_id}
        resp = _request("POST", STATUS_URL, headers=self._headers(), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()
