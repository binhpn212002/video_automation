import logging
import os
import secrets
import time
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

_logger = logging.getLogger(__name__)


def make_flat_object_key(kind, extension=".mp4", record_id=None):
    """
    Object key under video/ or audio/ folder (renamed file, no deep date folders).
    Examples:
      video/v_3_1786012345_ab12cd.mp4
      video/g_3_1786012345_xy98ef.mp4
      audio/a_1786012345_cd34ef.mp3
    kind: 'v' original video | 'g' generated | 'a' audio
    """
    ext = extension if extension.startswith(".") else f".{extension}"
    ts = int(time.time())
    suffix = secrets.token_hex(3)
    rid = record_id or 0
    if kind == "a":
        return f"audio/a_{ts}_{suffix}{ext}"
    return f"video/{kind}_{rid}_{ts}_{suffix}{ext}"


class R2Client:
    """Cloudflare R2 via S3-compatible API."""

    def __init__(self, storage):
        self.storage = storage
        self.bucket = storage.bucket_name
        self.client = boto3.client(
            "s3",
            endpoint_url=storage.endpoint,
            aws_access_key_id=storage.access_key_id,
            aws_secret_access_key=storage.secret_key,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )

    def upload_file(self, local_path, object_key, content_type=None):
        kwargs = {}
        if content_type:
            kwargs["ExtraArgs"] = {"ContentType": content_type}
        self.client.upload_file(local_path, self.bucket, object_key, **kwargs)
        return self.cdn_url(object_key)

    def upload_fileobj(self, fileobj, object_key, content_type=None):
        kwargs = {}
        if content_type:
            kwargs["ExtraArgs"] = {"ContentType": content_type}
        self.client.upload_fileobj(fileobj, self.bucket, object_key, **kwargs)
        return self.cdn_url(object_key)

    def normalize_object_key(self, object_key):
        key = (object_key or "").strip()
        if key.startswith("http://") or key.startswith("https://"):
            parsed = urlparse(key)
            key = (parsed.path or "").lstrip("/")
            bucket = (self.bucket or "").strip()
            if bucket and key.startswith(bucket + "/"):
                key = key[len(bucket) + 1 :]
        return key.lstrip("/")

    def resolve_object_key(self, object_key):
        """
        Find an existing R2 key. Tries stored path, basename, and video/audio prefixes.
        Older uploads may live at bucket root; newer ones under video/ or audio/.
        """
        key = self.normalize_object_key(object_key)
        if not key:
            raise FileNotFoundError("Empty object key")

        base = key.split("/")[-1]
        candidates = [key]
        if base and base != key:
            candidates.append(base)
        lower = base.lower()
        if lower.endswith((".mp4", ".mov", ".webm", ".mkv")) or base.startswith(("v_", "g_")):
            candidates.append(f"video/{base}")
        if lower.endswith((".mp3", ".m4a", ".aac", ".wav")) or base.startswith("a_"):
            candidates.append(f"audio/{base}")

        seen = set()
        last_error = None
        for cand in candidates:
            if not cand or cand in seen:
                continue
            seen.add(cand)
            try:
                self.client.head_object(Bucket=self.bucket, Key=cand)
                if cand != key:
                    _logger.warning(
                        "R2 key %r missing in %s; using %r",
                        key,
                        self.bucket,
                        cand,
                    )
                return cand
            except ClientError as exc:
                code = (exc.response.get("Error") or {}).get("Code") or ""
                status = (exc.response.get("ResponseMetadata") or {}).get(
                    "HTTPStatusCode"
                )
                if code in ("404", "NoSuchKey", "NotFound") or status == 404:
                    last_error = exc
                    continue
                raise
        tried = ", ".join(seen) or key
        raise FileNotFoundError(
            f"R2 object not found in bucket '{self.bucket}'. "
            f"Tried keys: {tried}. Last error: {last_error}"
        ) from last_error

    def download_file(self, object_key, local_path):
        key = self.resolve_object_key(object_key)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        self.client.download_file(self.bucket, key, local_path)
        return key

    def cdn_url(self, object_key):
        """
        Build public URL for object.

        - Custom CDN / r2.dev: https://cdn.example.com/{key}
        - R2 S3 endpoint as base: https://<account>.r2.cloudflarestorage.com/{bucket}/{key}
        """
        domain = (self.storage.cdn_domain or "").rstrip("/")
        key = (object_key or "").lstrip("/")
        bucket = (self.bucket or "").strip()

        if not domain.startswith("http://") and not domain.startswith("https://"):
            domain = f"https://{domain}"

        parsed = urlparse(domain)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").rstrip("/")

        # S3 API host without bucket in path → insert bucket
        if host.endswith(".r2.cloudflarestorage.com") and bucket:
            path_parts = [p for p in path.split("/") if p]
            if not path_parts or path_parts[0] != bucket:
                path = f"/{bucket}"
            else:
                path = "/" + "/".join(path_parts)
            return f"{parsed.scheme}://{host}{path}/{key}"

        # Custom domain / r2.dev public URL — key only
        if path:
            return f"{domain}/{key}"
        return f"{domain}/{key}"
