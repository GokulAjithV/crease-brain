"""
s3 object storage helpers.

Uploads and deletes files in crease-cricket-media bucket. boto3 reads the
credentials itself from the AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
"""

from storage3.types import BaseBucket
import os

import boto3
from dotenv import load_dotenv
load_dotenv()

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing {name}. Set it in your .env file.")
    return value

# Fail fast at startup (also catches typos in the credential variable names)
_require_env("AWS_ACCESS_KEY_ID")
_require_env("AWS_SECRET_ACCESS_KEY")
_region = _require_env("AWS_REGION")
_bucket = _require_env("S3_BUCKET")
_public_base_url = _require_env("S3_PUBLIC_BASE_URL").rstrip("/")


# one client per process - reuses its HTTPS connection pool across requests
_s3 = boto3.client("s3", region_name=_region)

# keys are unique per upload, so browsers/CDNs may cache them forever
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"

def upload_file(key: str, data: bytes, content_type: str) -> str:
    """upload bytes to s3 under 'key' and return the public URL."""
    _s3.put_object(
        Bucket=_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl=IMMUTABLE_CACHE,
    )
    return f"{_public_base_url}/{key}"

def delete_file(key: str) -> None:
    """Delete an object from s3 (succeds even if it doesn't exist)"""
    _s3.delete_object(Bucket=_bucket, Key=key)


def key_from_url(url: str | None) -> str | None:
    """return the s3 key for a URL in our bucket, or None if it isn't ours."""
    prefix = f"{_public_base_url}/"
    if url and url.startswith(prefix):
        return url[len(prefix):]
    return None