"""Cloudflare R2 client wrapper — S3-compatible object storage.

Provides upload, download URL generation, existence checks, and deletion
for project assets. All operations use the storage key convention:
    projects/{project_id}/photos/room_0.jpg
    projects/{project_id}/generated/option_0.png
    etc.
"""

from __future__ import annotations

from typing import Any

import boto3
import structlog
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

logger = structlog.get_logger()


def _build_client() -> Any:
    """Create an S3 client pointed at Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


_client: Any = None


def _get_client() -> Any:
    """Lazy-init singleton client."""
    global _client  # noqa: PLW0603
    if _client is None:
        _client = _build_client()
    return _client


def reset_client() -> None:
    """Reset the singleton client (for testing)."""
    global _client  # noqa: PLW0603
    _client = None


def upload_object(key: str, data: bytes, content_type: str = "image/jpeg") -> str:
    """Upload bytes to R2. Returns the storage key."""
    client = _get_client()
    client.put_object(
        Bucket=settings.r2_bucket_name,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    logger.info("r2_upload", key=key, size=len(data), content_type=content_type)
    return key


def generate_presigned_url(key: str) -> str:
    """Generate a pre-signed GET URL for downloading an object.

    URL expires after `settings.presigned_url_expiry_seconds` (default 1 hour).
    """
    client = _get_client()
    try:
        url: str = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket_name, "Key": key},
            ExpiresIn=settings.presigned_url_expiry_seconds,
        )
    except ClientError as e:
        logger.error("r2_presign_failed", key=key, error=str(e))
        raise
    return url


def head_object(key: str) -> bool:
    """Check if an object exists in R2. Returns True if found."""
    client = _get_client()
    try:
        client.head_object(Bucket=settings.r2_bucket_name, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        logger.error("r2_head_failed", key=key, error=str(e))
        raise


def delete_object(key: str) -> None:
    """Delete a single object from R2."""
    client = _get_client()
    client.delete_object(Bucket=settings.r2_bucket_name, Key=key)
    logger.info("r2_delete", key=key)


def resolve_url(key_or_url: str) -> str:
    """Convert an R2 storage key to a presigned URL; pass through existing URLs."""
    if key_or_url.startswith(("http://", "https://")):
        return key_or_url
    return generate_presigned_url(key_or_url)


def resolve_urls(keys_or_urls: list[str]) -> list[str]:
    """Convert a list of R2 storage keys to presigned URLs; pass through existing URLs."""
    return [resolve_url(item) for item in keys_or_urls]


def delete_prefix(prefix: str) -> None:
    """Delete all objects under a prefix (e.g., 'projects/{id}/')."""
    client = _get_client()
    paginator = client.get_paginator("list_objects_v2")
    deleted_count = 0
    for page in paginator.paginate(Bucket=settings.r2_bucket_name, Prefix=prefix):
        objects = page.get("Contents", [])
        if not objects:
            continue
        delete_keys = [{"Key": obj["Key"]} for obj in objects]
        response = client.delete_objects(
            Bucket=settings.r2_bucket_name,
            Delete={"Objects": delete_keys},
        )
        errors = response.get("Errors", [])
        if errors:
            logger.warning("r2_delete_partial_failure", prefix=prefix, errors=errors)
        deleted_count += len(delete_keys) - len(errors)
    logger.info("r2_delete_prefix", prefix=prefix, deleted_count=deleted_count)
