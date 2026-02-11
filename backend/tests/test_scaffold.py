"""Tests verifying the project scaffold is correctly set up."""

from unittest.mock import patch

import pytest

from app.models.contracts import ErrorResponse


class TestHealthEndpoint:
    """Verify the health endpoint returns the expected shape."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        """Health endpoint returns 200 with status, version, environment, and service fields."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["version"] == "0.1.0"
        assert body["environment"] == "development"
        assert "postgres" in body
        assert "temporal" in body
        assert "r2" in body


class TestRequestIdMiddleware:
    """Verify request ID middleware adds correlation IDs."""

    @pytest.mark.asyncio
    async def test_response_includes_request_id_header(self, client):
        """Every response includes an X-Request-ID header."""
        resp = await client.get("/health")
        assert "X-Request-ID" in resp.headers
        # Should be a valid UUID
        import uuid

        uuid.UUID(resp.headers["X-Request-ID"])

    @pytest.mark.asyncio
    async def test_client_provided_request_id_echoed(self, client):
        """Client-provided X-Request-ID is echoed back."""
        custom_id = "client-trace-12345"
        resp = await client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.headers["X-Request-ID"] == custom_id


class TestExceptionHandler:
    """Verify unhandled exceptions return consistent ErrorResponse JSON."""

    @pytest.mark.asyncio
    @patch(
        "app.api.routes.projects._get_state",
        side_effect=RuntimeError("unexpected bug"),
    )
    async def test_unhandled_exception_returns_500_json(self, _mock):
        """Unhandled exception returns 500 with ErrorResponse shape, not HTML."""
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/projects/some-id")
        assert resp.status_code == 500
        er = ErrorResponse.model_validate(resp.json())
        assert er.error == "internal_error"
        assert er.retryable is True
        assert er.message != ""


class TestValidationErrorHandler:
    """Verify Pydantic validation errors return ErrorResponse JSON (not FastAPI's default)."""

    @pytest.mark.asyncio
    async def test_pydantic_validation_returns_error_response_shape(self, client):
        """Missing required field returns 422 with ErrorResponse shape, not FastAPI detail array."""
        resp = await client.post("/api/v1/projects", json={})
        assert resp.status_code == 422
        er = ErrorResponse.model_validate(resp.json())
        assert er.error == "validation_error"
        assert er.retryable is False
        assert "device_fingerprint" in er.message

    @pytest.mark.asyncio
    async def test_invalid_field_type_returns_error_response_shape(self, client):
        """Wrong field type returns 422 with ErrorResponse shape including field location."""
        resp = await client.post(
            "/api/v1/projects",
            json={"device_fingerprint": 12345},
        )
        assert resp.status_code == 422
        er = ErrorResponse.model_validate(resp.json())
        assert er.error == "validation_error"
        assert er.retryable is False


class TestOpenAPISchema:
    """Verify the OpenAPI schema includes all expected endpoints and models.

    T1 iOS uses the OpenAPI schema (via /docs) to generate Swift models.
    A missing endpoint or model in the schema means T1 can't generate
    the corresponding Swift code.
    """

    def test_all_project_endpoints_in_schema(self):
        """All 15 project endpoints + 1 health endpoint appear in the OpenAPI schema."""
        from app.main import app

        schema = app.openapi()
        paths = set(schema["paths"].keys())

        expected_paths = {
            "/health",
            "/api/v1/projects",
            "/api/v1/projects/{project_id}",
            "/api/v1/projects/{project_id}/photos",
            "/api/v1/projects/{project_id}/scan",
            "/api/v1/projects/{project_id}/scan/skip",
            "/api/v1/projects/{project_id}/intake/start",
            "/api/v1/projects/{project_id}/intake/message",
            "/api/v1/projects/{project_id}/intake/confirm",
            "/api/v1/projects/{project_id}/intake/skip",
            "/api/v1/projects/{project_id}/select",
            "/api/v1/projects/{project_id}/start-over",
            "/api/v1/projects/{project_id}/iterate/annotate",
            "/api/v1/projects/{project_id}/iterate/feedback",
            "/api/v1/projects/{project_id}/approve",
            "/api/v1/projects/{project_id}/retry",
        }
        assert expected_paths == paths

    def test_key_models_in_schema(self):
        """Key contract models appear in OpenAPI components/schemas."""
        from app.main import app

        schema = app.openapi()
        schema_names = set(schema["components"]["schemas"].keys())

        required_models = {
            "WorkflowState",
            "CreateProjectRequest",
            "CreateProjectResponse",
            "ActionResponse",
            "ErrorResponse",
            "PhotoData",
            "DesignBrief",
            "DesignOption",
            "IntakeChatOutput",
            "AnnotationEditRequest",
            "TextFeedbackRequest",
            "GenerateShoppingListOutput",
            "ProductMatch",
        }
        missing = required_models - schema_names
        assert not missing, f"Missing models in OpenAPI schema: {missing}"


class TestEnvExample:
    """Verify .env.example documents all config.py settings."""

    def test_all_settings_in_env_example(self):
        """Every Settings field appears in .env.example (prevents undocumented config drift)."""
        from pathlib import Path

        from app.config import Settings

        env_example = Path(__file__).parent.parent.parent / ".env.example"
        env_text = env_example.read_text().upper()

        missing = []
        for field_name in Settings.model_fields:
            if field_name.upper() not in env_text:
                missing.append(field_name)

        assert not missing, (
            f"Settings fields missing from .env.example: {missing}. "
            "Add them so other teams know they exist."
        )


class TestAppImports:
    """Verify core modules are importable."""

    def test_config_importable(self):
        """Settings class loads from environment with sensible defaults."""
        from app.config import settings

        assert settings.temporal_task_queue == "remo-tasks"
        assert settings.r2_bucket_name == "remo-images"
        assert settings.presigned_url_expiry_seconds == 3600

    def test_contracts_importable(self):
        """Contracts module exists and is importable (placeholder for now)."""
        import app.models.contracts  # noqa: F401

    def test_db_base_importable(self):
        """SQLAlchemy Base class exists for ORM models."""
        from app.models.db import Base

        assert Base is not None

    def test_fastapi_app_importable(self):
        """FastAPI app instance is importable."""
        from app.main import app

        assert app.title == "Remo API"
