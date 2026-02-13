"""Tests verifying the project scaffold is correctly set up."""

from unittest.mock import AsyncMock, patch

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

    @pytest.mark.asyncio
    async def test_health_services_report_valid_status(self, client):
        """Health check reports valid status strings for all services."""
        resp = await client.get("/health")
        body = resp.json()
        assert body["status"] == "ok"
        assert body["postgres"] in ("connected", "disconnected")
        assert body["temporal"] in ("connected", "disconnected")
        assert body["r2"] in ("connected", "disconnected")

    @pytest.mark.asyncio
    async def test_health_all_connected(self, client):
        """When all services are up, health reports all 'connected'."""
        with (
            patch(
                "app.api.routes.health._check_postgres",
                new_callable=AsyncMock,
                return_value="connected",
            ),
            patch(
                "app.api.routes.health._check_temporal",
                new_callable=AsyncMock,
                return_value="connected",
            ),
            patch(
                "app.api.routes.health._check_r2",
                new_callable=AsyncMock,
                return_value="connected",
            ),
        ):
            resp = await client.get("/health")
        body = resp.json()
        assert body["status"] == "ok"
        assert body["postgres"] == "connected"
        assert body["temporal"] == "connected"
        assert body["r2"] == "connected"

    @pytest.mark.asyncio
    async def test_health_partial_connected(self, client):
        """One service down, others up — reports mixed status."""
        with (
            patch(
                "app.api.routes.health._check_postgres",
                new_callable=AsyncMock,
                return_value="disconnected",
            ),
            patch(
                "app.api.routes.health._check_temporal",
                new_callable=AsyncMock,
                return_value="connected",
            ),
            patch(
                "app.api.routes.health._check_r2",
                new_callable=AsyncMock,
                return_value="connected",
            ),
        ):
            resp = await client.get("/health")
        body = resp.json()
        assert body["status"] == "ok"
        assert body["postgres"] == "disconnected"
        assert body["temporal"] == "connected"
        assert body["r2"] == "connected"


class TestHealthCheckFunctions:
    """Unit tests for health check probe functions (not the endpoint).

    The TestHealthEndpoint tests above mock at the function boundary
    (_check_postgres → "connected"), verifying only the endpoint's gather()
    and response shape. These tests call the probe functions directly with
    mocked dependencies to cover the internal logic: connection setup,
    query, cleanup, and error branches.
    """

    @pytest.mark.asyncio
    async def test_check_postgres_connected(self):
        """_check_postgres returns 'connected' when DB responds to SELECT 1."""
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_conn.close = AsyncMock()

        # asyncpg is imported lazily inside _check_postgres, so patch at module level
        with patch("asyncpg.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_conn
            from app.api.routes.health import _check_postgres

            result = await _check_postgres()
        assert result == "connected"
        mock_conn.fetchval.assert_awaited_once_with("SELECT 1")
        mock_conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_postgres_close_runs_on_failure(self):
        """_check_postgres still closes connection when fetchval raises."""
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=RuntimeError("query failed"))
        mock_conn.close = AsyncMock()

        with patch("asyncpg.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_conn
            from app.api.routes.health import _check_postgres

            result = await _check_postgres()
        assert result == "disconnected"
        mock_conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_temporal_connected_no_api_key(self):
        """_check_temporal returns 'connected' when Temporal responds (no TLS)."""
        mock_client = AsyncMock()
        mock_client.service_client.check_health = AsyncMock()

        # Client is imported lazily inside _check_temporal
        with (
            patch("app.api.routes.health.settings") as mock_settings,
            patch("temporalio.client.Client.connect", new_callable=AsyncMock) as mock_connect,
        ):
            mock_settings.temporal_api_key = None
            mock_settings.temporal_address = "localhost:7233"
            mock_settings.temporal_namespace = "default"
            mock_connect.return_value = mock_client
            from app.api.routes.health import _check_temporal

            result = await _check_temporal()
        assert result == "connected"
        mock_client.service_client.check_health.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_temporal_connected_with_api_key(self):
        """_check_temporal returns 'connected' when using TLS + API key."""
        mock_client = AsyncMock()
        mock_client.service_client.check_health = AsyncMock()

        with (
            patch("app.api.routes.health.settings") as mock_settings,
            patch("temporalio.client.Client.connect", new_callable=AsyncMock) as mock_connect,
        ):
            mock_settings.temporal_api_key = "test-api-key-123"
            mock_settings.temporal_address = "cloud.temporal.io:7233"
            mock_settings.temporal_namespace = "prod"
            mock_connect.return_value = mock_client
            from app.api.routes.health import _check_temporal

            result = await _check_temporal()
        assert result == "connected"
        # Verify TLS path was used (api_key present → tls=True)
        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["tls"] is True
        assert call_kwargs["api_key"] == "test-api-key-123"

    @pytest.mark.asyncio
    async def test_check_r2_connected(self):
        """_check_r2 returns 'connected' when head_bucket succeeds."""
        mock_s3 = AsyncMock()
        mock_s3.head_bucket = lambda **kwargs: None

        with (
            patch("app.utils.r2._get_client", return_value=mock_s3),
            patch("app.api.routes.health.settings") as mock_settings,
        ):
            mock_settings.r2_bucket_name = "test-bucket"
            from app.api.routes.health import _check_r2

            result = await _check_r2()
        assert result == "connected"

    @pytest.mark.asyncio
    async def test_check_temporal_disconnected(self):
        """_check_temporal returns 'disconnected' when Client.connect raises."""
        with (
            patch("app.api.routes.health.settings") as mock_settings,
            patch(
                "temporalio.client.Client.connect",
                new_callable=AsyncMock,
                side_effect=ConnectionError("refused"),
            ),
        ):
            mock_settings.temporal_api_key = None
            mock_settings.temporal_address = "localhost:7233"
            mock_settings.temporal_namespace = "default"
            from app.api.routes.health import _check_temporal

            result = await _check_temporal()
        assert result == "disconnected"

    @pytest.mark.asyncio
    async def test_check_r2_disconnected(self):
        """_check_r2 returns 'disconnected' when head_bucket raises."""
        with (
            patch(
                "app.utils.r2._get_client",
                side_effect=ConnectionError("R2 unavailable"),
            ),
            patch("app.api.routes.health.settings") as mock_settings,
        ):
            mock_settings.r2_bucket_name = "test-bucket"
            from app.api.routes.health import _check_r2

            result = await _check_r2()
        assert result == "disconnected"


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

    @pytest.mark.asyncio
    async def test_request_id_on_404_response(self, client):
        """IMP-32: 404 error responses include X-Request-ID for log correlation."""
        resp = await client.get("/api/v1/projects/nonexistent-id")
        assert resp.status_code == 404
        assert "X-Request-ID" in resp.headers

    @pytest.mark.asyncio
    async def test_request_id_on_409_response(self, client):
        """IMP-32: 409 error responses include X-Request-ID for log correlation."""
        from app.api.routes.projects import _mock_states
        from app.models.contracts import WorkflowState

        pid = "test-409-rid"
        _mock_states[pid] = WorkflowState(step="photos")
        resp = await client.post(f"/api/v1/projects/{pid}/approve")
        assert resp.status_code == 409
        assert "X-Request-ID" in resp.headers
        del _mock_states[pid]

    @pytest.mark.asyncio
    async def test_request_id_on_422_response(self, client):
        """IMP-32: 422 validation error responses include X-Request-ID."""
        resp = await client.post("/api/v1/projects", json={})
        assert resp.status_code == 422
        assert "X-Request-ID" in resp.headers

    @pytest.mark.asyncio
    async def test_client_request_id_echoed_on_error(self, client):
        """IMP-32: Client-provided X-Request-ID is echoed on error responses.

        iOS sends request IDs for error tracking. Without echo on errors,
        iOS can't match the error response to the log entry.
        """
        custom_id = "ios-error-trace-abc123"
        resp = await client.get(
            "/api/v1/projects/nonexistent-id",
            headers={"X-Request-ID": custom_id},
        )
        assert resp.status_code == 404
        assert resp.headers["X-Request-ID"] == custom_id


class TestAccessLogging:
    """PRE-3: Verify HTTP access logging in the request_id middleware."""

    @pytest.mark.asyncio
    async def test_access_log_emitted(self, client, capsys):
        """Every HTTP request produces an http_request log entry."""
        await client.get("/health")
        captured = capsys.readouterr()
        assert "http_request" in captured.out
        assert "/health" in captured.out

    @pytest.mark.asyncio
    async def test_access_log_includes_status_and_duration(self, client, capsys):
        """Access log includes status code and duration_ms."""
        await client.get("/api/v1/projects/nonexistent-id")
        captured = capsys.readouterr()
        assert "http_request" in captured.out
        assert "404" in captured.out
        assert "duration_ms" in captured.out


class TestLifespan:
    """Verify FastAPI lifespan handles Temporal client connection."""

    @pytest.mark.asyncio
    async def test_lifespan_connects_temporal_when_enabled(self):
        """use_temporal=True: lifespan creates and stores Temporal client."""
        from unittest.mock import MagicMock

        from app.main import app, lifespan

        mock_client = MagicMock()
        with (
            patch("app.main.settings") as mock_settings,
            patch("app.main.logger"),
            patch(
                "app.worker.create_temporal_client",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
        ):
            mock_settings.use_temporal = True
            mock_settings.temporal_address = "localhost:7233"
            mock_settings.temporal_namespace = "default"
            async with lifespan(app):
                assert app.state.temporal_client is mock_client

    @pytest.mark.asyncio
    async def test_lifespan_skips_temporal_when_disabled(self):
        """use_temporal=False: lifespan does not attempt Temporal connection."""
        from app.main import app, lifespan

        with (
            patch("app.main.settings") as mock_settings,
            patch(
                "app.worker.create_temporal_client",
                new_callable=AsyncMock,
            ) as mock_create,
        ):
            mock_settings.use_temporal = False
            async with lifespan(app):
                mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_lifespan_raises_on_temporal_failure(self):
        """use_temporal=True with connection failure: exception propagates."""
        from app.main import app, lifespan

        with (
            patch("app.main.settings") as mock_settings,
            patch("app.main.logger"),
            patch(
                "app.worker.create_temporal_client",
                new_callable=AsyncMock,
                side_effect=ConnectionError("Temporal down"),
            ),
        ):
            mock_settings.use_temporal = True
            mock_settings.temporal_address = "bad-host:7233"
            mock_settings.temporal_namespace = "default"
            with pytest.raises(ConnectionError, match="Temporal down"):
                async with lifespan(app):
                    pass


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
        # IMP-32: even 500 errors include X-Request-ID for log correlation
        assert "X-Request-ID" in resp.headers
        # Request ID must be non-empty (UUID fallback guarantees this)
        assert resp.headers["X-Request-ID"] != ""
        import uuid

        uuid.UUID(resp.headers["X-Request-ID"])  # must be valid UUID


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
        """All project + health + debug endpoints appear in the OpenAPI schema."""
        from app.main import app

        schema = app.openapi()
        paths = set(schema["paths"].keys())

        expected_paths = {
            "/health",
            "/api/v1/projects",
            "/api/v1/projects/{project_id}",
            "/api/v1/projects/{project_id}/photos",
            "/api/v1/projects/{project_id}/photos/{photo_id}",
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
            "/api/v1/debug/force-failure",
        }
        assert expected_paths == paths

    def test_key_models_in_schema(self):
        """Key contract models appear in OpenAPI components/schemas."""
        from app.main import app

        schema = app.openapi()
        schema_names = set(schema["components"]["schemas"].keys())

        # Pydantic V2 may split models used in both input and output contexts
        # into "Model-Input" and "Model-Output" variants. Check for either.
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
        missing = set()
        for model in required_models:
            # Pydantic V2 may create Model-Input / Model-Output variants
            if (
                model not in schema_names
                and f"{model}-Input" not in schema_names
                and f"{model}-Output" not in schema_names
            ):
                missing.add(model)
        assert not missing, f"Missing models in OpenAPI schema: {missing}"

    def test_http_methods_match_spec(self):
        """IMP-33: HTTP methods match iOS expectations for each endpoint.

        T1 iOS code generator derives Swift method signatures from the OpenAPI
        schema's HTTP methods. A POST accidentally registered as GET (or vice
        versa) would generate non-functional Swift code.
        """
        from app.main import app

        schema = app.openapi()
        paths = schema["paths"]

        expected_methods = {
            "/health": {"get"},
            "/api/v1/projects": {"post"},
            "/api/v1/projects/{project_id}": {"get", "delete"},
            "/api/v1/projects/{project_id}/photos": {"post"},
            "/api/v1/projects/{project_id}/photos/{photo_id}": {"delete"},
            "/api/v1/projects/{project_id}/scan": {"post"},
            "/api/v1/projects/{project_id}/scan/skip": {"post"},
            "/api/v1/projects/{project_id}/intake/start": {"post"},
            "/api/v1/projects/{project_id}/intake/message": {"post"},
            "/api/v1/projects/{project_id}/intake/confirm": {"post"},
            "/api/v1/projects/{project_id}/intake/skip": {"post"},
            "/api/v1/projects/{project_id}/select": {"post"},
            "/api/v1/projects/{project_id}/start-over": {"post"},
            "/api/v1/projects/{project_id}/iterate/annotate": {"post"},
            "/api/v1/projects/{project_id}/iterate/feedback": {"post"},
            "/api/v1/projects/{project_id}/approve": {"post"},
            "/api/v1/projects/{project_id}/retry": {"post"},
        }

        for path, methods in expected_methods.items():
            actual = set(paths[path].keys()) - {"parameters"}
            assert actual == methods, f"{path}: expected {methods}, got {actual}"


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
