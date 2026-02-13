"""Tests for R2 storage client — verifies upload, download URL, delete, and prefix operations.

All boto3 calls are mocked since R2 is an external service.
Success metric: Upload/download test object succeeds (via mock verification).
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.config import settings
from app.utils import r2


@pytest.fixture(autouse=True)
def _reset_r2_client():
    """Reset the singleton R2 client before each test."""
    r2.reset_client()
    yield
    r2.reset_client()


@pytest.fixture()
def mock_s3():
    """Provide a mocked boto3 S3 client."""
    with patch.object(r2, "_build_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        yield mock_client


class TestUploadObject:
    """Tests for R2 object upload."""

    def test_upload_calls_put_object(self, mock_s3):
        """Verifies upload_object calls put_object with correct params and returns the key."""
        key = "projects/abc/photos/room_0.jpg"
        data = b"fake-image-bytes"

        result = r2.upload_object(key, data, content_type="image/jpeg")

        mock_s3.put_object.assert_called_once_with(
            Bucket=settings.r2_bucket_name,
            Key=key,
            Body=data,
            ContentType="image/jpeg",
        )
        assert result == key

    def test_upload_default_content_type(self, mock_s3):
        """Verifies upload_object defaults to image/jpeg content type."""
        r2.upload_object("test/key.jpg", b"data")

        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "image/jpeg"

    def test_upload_custom_content_type(self, mock_s3):
        """Verifies upload_object respects custom content type."""
        r2.upload_object("test/key.png", b"data", content_type="image/png")

        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "image/png"


class TestGeneratePresignedUrl:
    """Tests for pre-signed URL generation."""

    def test_generates_url(self, mock_s3):
        """Verifies generate_presigned_url returns a URL string."""
        mock_s3.generate_presigned_url.return_value = "https://r2.example.com/signed"

        url = r2.generate_presigned_url("projects/abc/photos/room_0.jpg")

        assert url == "https://r2.example.com/signed"
        mock_s3.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "remo-images", "Key": "projects/abc/photos/room_0.jpg"},
            ExpiresIn=3600,
        )

    def test_uses_configured_expiry(self, mock_s3):
        """Verifies pre-signed URL uses the expiry from settings."""
        mock_s3.generate_presigned_url.return_value = "https://r2.example.com/signed"
        with patch("app.utils.r2.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.presigned_url_expiry_seconds = 7200

            r2.generate_presigned_url("test/key.jpg")

            call_kwargs = mock_s3.generate_presigned_url.call_args
            assert call_kwargs[1]["ExpiresIn"] == 7200

    def test_logs_client_error(self, mock_s3):
        """Verifies generate_presigned_url logs ClientError before re-raising."""
        mock_s3.generate_presigned_url.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
            "GetObject",
        )

        with patch.object(r2, "logger") as mock_logger, pytest.raises(ClientError):
            r2.generate_presigned_url("projects/abc/missing.jpg")

        mock_logger.error.assert_called_once()
        call_kwargs = mock_logger.error.call_args
        assert call_kwargs[0][0] == "r2_presign_failed"
        assert call_kwargs[1]["key"] == "projects/abc/missing.jpg"


class TestHeadObject:
    """Tests for object existence check."""

    def test_returns_true_when_exists(self, mock_s3):
        """Verifies head_object returns True when object exists."""
        mock_s3.head_object.return_value = {"ContentLength": 1024}

        assert r2.head_object("projects/abc/photos/room_0.jpg") is True

    def test_returns_false_when_not_found(self, mock_s3):
        """Verifies head_object returns False for 404 errors."""
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )

        assert r2.head_object("projects/abc/nonexistent.jpg") is False

    def test_raises_on_other_errors(self, mock_s3):
        """Verifies head_object re-raises non-404 ClientErrors."""
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}},
            "HeadObject",
        )

        with pytest.raises(ClientError):
            r2.head_object("projects/abc/photos/room_0.jpg")

    def test_logs_non_404_error(self, mock_s3):
        """Verifies head_object logs non-404 errors before re-raising."""
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}},
            "HeadObject",
        )

        with patch.object(r2, "logger") as mock_logger, pytest.raises(ClientError):
            r2.head_object("projects/abc/secret.jpg")

        mock_logger.error.assert_called_once()
        call_kwargs = mock_logger.error.call_args
        assert call_kwargs[0][0] == "r2_head_failed"
        assert call_kwargs[1]["key"] == "projects/abc/secret.jpg"


class TestDeleteObject:
    """Tests for single object deletion."""

    def test_deletes_object(self, mock_s3):
        """Verifies delete_object calls S3 delete_object with correct params."""
        r2.delete_object("projects/abc/photos/room_0.jpg")

        mock_s3.delete_object.assert_called_once_with(
            Bucket=settings.r2_bucket_name,
            Key="projects/abc/photos/room_0.jpg",
        )


class TestDeletePrefix:
    """Tests for prefix-based bulk deletion."""

    def test_deletes_all_objects_under_prefix(self, mock_s3):
        """Verifies delete_prefix lists and deletes all objects under a prefix."""
        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "projects/abc/photos/room_0.jpg"},
                    {"Key": "projects/abc/photos/room_1.jpg"},
                ]
            },
        ]
        mock_s3.delete_objects.return_value = {"Deleted": [{"Key": "k"}]}

        r2.delete_prefix("projects/abc/")

        mock_s3.get_paginator.assert_called_once_with("list_objects_v2")
        mock_s3.delete_objects.assert_called_once_with(
            Bucket=settings.r2_bucket_name,
            Delete={
                "Objects": [
                    {"Key": "projects/abc/photos/room_0.jpg"},
                    {"Key": "projects/abc/photos/room_1.jpg"},
                ]
            },
        )

    def test_handles_empty_prefix(self, mock_s3):
        """Verifies delete_prefix handles empty results gracefully."""
        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{"Contents": []}]

        r2.delete_prefix("projects/nonexistent/")

        mock_s3.delete_objects.assert_not_called()

    def test_handles_multiple_pages(self, mock_s3):
        """Verifies delete_prefix handles pagination across multiple pages."""
        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "projects/abc/photos/room_0.jpg"}]},
            {"Contents": [{"Key": "projects/abc/generated/option_0.png"}]},
        ]
        mock_s3.delete_objects.return_value = {"Deleted": [{"Key": "k"}]}

        r2.delete_prefix("projects/abc/")

        assert mock_s3.delete_objects.call_count == 2

    def test_partial_failure_logs_warning(self, mock_s3):
        """Verifies delete_prefix warns when some objects fail to delete."""
        paginator = MagicMock()
        mock_s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "projects/abc/photos/room_0.jpg"},
                    {"Key": "projects/abc/photos/room_1.jpg"},
                ]
            },
        ]
        mock_s3.delete_objects.return_value = {
            "Deleted": [{"Key": "projects/abc/photos/room_0.jpg"}],
            "Errors": [{"Key": "projects/abc/photos/room_1.jpg", "Code": "AccessDenied"}],
        }

        with patch.object(r2, "logger") as mock_logger:
            r2.delete_prefix("projects/abc/")

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "r2_delete_partial_failure"
        assert call_args[1]["prefix"] == "projects/abc/"
        assert len(call_args[1]["errors"]) == 1


class TestClientSingleton:
    """Tests for the lazy-init singleton pattern."""

    def test_client_is_reused(self, mock_s3):
        """Verifies the client is created once and reused."""
        r2.upload_object("key1", b"data1")
        r2.upload_object("key2", b"data2")

        # _build_client is called once (via the mock_s3 fixture)
        assert mock_s3.put_object.call_count == 2

    def test_reset_forces_rebuild(self):
        """Verifies reset_client allows a fresh client to be created."""
        with patch.object(r2, "_build_client") as mock_build:
            mock_build.return_value = MagicMock()
            r2.upload_object("key1", b"data1")
            r2.reset_client()
            r2.upload_object("key2", b"data2")

            assert mock_build.call_count == 2


class TestResolveUrl:
    """Tests for resolve_url — storage key → presigned URL passthrough."""

    def test_storage_key_is_resolved(self, mock_s3):
        """Storage key (no protocol) is converted to a presigned URL."""
        mock_s3.generate_presigned_url.return_value = "https://r2.example.com/signed"
        result = r2.resolve_url("projects/abc/photos/room_0.jpg")
        assert result == "https://r2.example.com/signed"
        mock_s3.generate_presigned_url.assert_called_once()

    def test_https_url_passes_through(self, mock_s3):
        """HTTPS URL is returned unchanged — no presigned URL generated."""
        result = r2.resolve_url("https://example.com/image.jpg")
        assert result == "https://example.com/image.jpg"
        mock_s3.generate_presigned_url.assert_not_called()

    def test_http_url_passes_through(self, mock_s3):
        """HTTP URL is returned unchanged."""
        result = r2.resolve_url("http://localhost:8000/image.jpg")
        assert result == "http://localhost:8000/image.jpg"
        mock_s3.generate_presigned_url.assert_not_called()


class TestResolveUrls:
    """Tests for resolve_urls — batch storage key → presigned URL."""

    def test_mixed_keys_and_urls(self, mock_s3):
        """Mix of storage keys and URLs are resolved correctly."""
        mock_s3.generate_presigned_url.return_value = "https://r2.example.com/signed"
        result = r2.resolve_urls(
            [
                "projects/abc/photos/room_0.jpg",
                "https://existing.com/image.jpg",
            ]
        )
        assert result == [
            "https://r2.example.com/signed",
            "https://existing.com/image.jpg",
        ]
        # Only the storage key triggered a presigned URL call
        assert mock_s3.generate_presigned_url.call_count == 1

    def test_empty_list(self, mock_s3):
        """Empty list returns empty list."""
        assert r2.resolve_urls([]) == []
        mock_s3.generate_presigned_url.assert_not_called()
