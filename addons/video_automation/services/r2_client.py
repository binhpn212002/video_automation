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
    Object key under image/, video/ or audio/ folder (renamed file, no deep date folders).
    Examples:
      image/i_3_1786012345_ab12cd.jpg
      video/v_3_1786012345_ab12cd.mp4
      video/g_3_1786012345_xy98ef.mp4
      audio/a_1786012345_cd34ef.mp3
    kind: 'i' image | 'v' original video | 'g' generated video | 'a' audio
    """
    ext = extension if extension.startswith(".") else f".{extension}"
    ts = int(time.time())
    suffix = secrets.token_hex(3)
    rid = record_id or 0
    if kind in ("i", "img", "image"):
        return f"image/i_{rid}_{ts}_{suffix}{ext}"
    if kind in ("a", "audio"):
        return f"audio/a_{ts}_{suffix}{ext}"
    return f"video/{kind}_{rid}_{ts}_{suffix}{ext}"



class R2Client:
    """Cloudflare R2 via S3-compatible API."""

    def __init__(self, storage):
        self.storage = storage
        self.bucket = (storage.bucket_name or "").strip()
        endpoint = (storage.endpoint or "").rstrip("/")
        # If user included bucket name at the end of endpoint (e.g. https://...r2.cloudflarestorage.com/clothes), strip it
        if self.bucket and endpoint.endswith(f"/{self.bucket}"):
            endpoint = endpoint[: -len(f"/{self.bucket}")].rstrip("/")
        self.endpoint = endpoint
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
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
        return key.lstrip("/")

    def resolve_object_key(self, object_key):
        """
        Find an existing R2 key in bucket.
        Tries exact stored key, with/without bucket prefix, basename, and type folders.
        """
        key = self.normalize_object_key(object_key)
        if not key:
            raise FileNotFoundError("Empty object key")

        bucket = self.bucket
        candidates = [key]

        # If key contains bucket prefix (e.g. clothes/audio/...) try without it
        if bucket and key.startswith(bucket + "/"):
            candidates.append(key[len(bucket) + 1 :])
        elif bucket:
            candidates.append(f"{bucket}/{key}")

        base = key.split("/")[-1]
        if base and base != key:
            candidates.append(base)
            if bucket:
                candidates.append(f"{bucket}/{base}")

        lower = base.lower()
        if lower.endswith((".mp4", ".mov", ".webm", ".mkv")) or base.startswith(("v_", "g_")):
            candidates.append(f"video/{base}")
            if bucket:
                candidates.append(f"{bucket}/video/{base}")
        if lower.endswith((".mp3", ".m4a", ".aac", ".wav")) or base.startswith("a_"):
            candidates.append(f"audio/{base}")
            if bucket:
                candidates.append(f"{bucket}/audio/{base}")
        if lower.endswith((".jpg", ".jpeg", ".png", ".webp")) or base.startswith("i_"):
            candidates.append(f"image/{base}")
            candidates.append(f"images/{base}")
            candidates.append(f"img/{base}")
            if bucket:
                candidates.append(f"{bucket}/image/{base}")
                candidates.append(f"{bucket}/images/{base}")
                candidates.append(f"{bucket}/img/{base}")

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
                        "R2 key %r found as %r in bucket %s",
                        key,
                        cand,
                        self.bucket,
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
        - Uses storage CDN domain + object key
        """
        domain = (self.storage.cdn_domain or "").rstrip("/")
        key = (object_key or "").lstrip("/")
        if not key:
            return False

        if key.startswith("http://") or key.startswith("https://"):
            return key

        if not domain.startswith("http://") and not domain.startswith("https://"):
            domain = f"https://{domain}"

        return f"{domain}/{key}"

