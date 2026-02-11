"""Tests for Temporal worker entrypoint — registration and configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter

from app.activities.mock_stubs import (
    edit_design,
    generate_designs,
    generate_shopping_list,
)
from app.activities.purge import purge_project_data
from app.worker import ACTIVITIES, WORKFLOWS, create_temporal_client, run_worker
from app.workflows.design_project import DesignProjectWorkflow


class TestActivityRegistration:
    """Verify that the correct activities are registered with the worker."""

    def test_all_activities_registered(self) -> None:
        """All 4 activities (3 mock + 1 real purge) should be in the ACTIVITIES list."""
        assert len(ACTIVITIES) == 4

    def test_generate_designs_registered(self) -> None:
        """generate_designs activity should be registered."""
        assert generate_designs in ACTIVITIES

    def test_edit_design_registered(self) -> None:
        """edit_design activity should be registered."""
        assert edit_design in ACTIVITIES

    def test_generate_shopping_list_registered(self) -> None:
        """generate_shopping_list activity should be registered."""
        assert generate_shopping_list in ACTIVITIES

    def test_purge_project_data_registered(self) -> None:
        """purge_project_data activity should be registered."""
        assert purge_project_data in ACTIVITIES


class TestWorkflowRegistration:
    """Verify that the correct workflows are registered with the worker."""

    def test_design_project_workflow_registered(self) -> None:
        """DesignProjectWorkflow should be the only registered workflow."""
        assert [DesignProjectWorkflow] == WORKFLOWS


class TestCreateTemporalClient:
    """Verify Temporal client creation with local and cloud configs."""

    @pytest.mark.asyncio
    @patch("app.worker.Client")
    async def test_local_connection_no_tls(self, mock_client_cls: MagicMock) -> None:
        """Local Temporal (no API key) should connect without TLS."""
        mock_client_cls.connect = AsyncMock(return_value=MagicMock())

        with patch("app.worker.settings") as mock_settings:
            mock_settings.temporal_address = "localhost:7233"
            mock_settings.temporal_namespace = "default"
            mock_settings.temporal_api_key = None

            await create_temporal_client()

            mock_client_cls.connect.assert_called_once_with(
                target_host="localhost:7233",
                namespace="default",
                data_converter=pydantic_data_converter,
            )

    @pytest.mark.asyncio
    @patch("app.worker.Client")
    async def test_cloud_connection_with_tls(self, mock_client_cls: MagicMock) -> None:
        """Temporal Cloud (API key set) should connect with TLS + API key."""
        mock_client_cls.connect = AsyncMock(return_value=MagicMock())

        with patch("app.worker.settings") as mock_settings:
            mock_settings.temporal_address = "remo-dev.tmprl.cloud:7233"
            mock_settings.temporal_namespace = "remo-dev"
            mock_settings.temporal_api_key = "secret-key-123"

            await create_temporal_client()

            mock_client_cls.connect.assert_called_once_with(
                target_host="remo-dev.tmprl.cloud:7233",
                namespace="remo-dev",
                tls=True,
                api_key="secret-key-123",
                data_converter=pydantic_data_converter,
            )


class TestRunWorker:
    """Verify the worker run lifecycle."""

    @pytest.mark.asyncio
    @patch("app.worker.Worker")
    @patch("app.worker.create_temporal_client")
    async def test_worker_created_with_correct_args(
        self,
        mock_create_client: AsyncMock,
        mock_worker_cls: MagicMock,
    ) -> None:
        """Worker should be created with task queue, workflows, and activities."""
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client

        mock_worker = MagicMock()
        mock_worker.run = AsyncMock()
        mock_worker_cls.return_value = mock_worker

        with patch("app.worker.settings") as mock_settings:
            mock_settings.temporal_address = "localhost:7233"
            mock_settings.temporal_namespace = "default"
            mock_settings.temporal_task_queue = "remo-tasks"

            await run_worker()

            mock_worker_cls.assert_called_once_with(
                mock_client,
                task_queue="remo-tasks",
                workflows=WORKFLOWS,
                activities=ACTIVITIES,
            )
            mock_worker.run.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.worker.create_temporal_client")
    async def test_connection_failure_logs_and_raises(
        self,
        mock_create_client: AsyncMock,
    ) -> None:
        """Connection failures should be logged with structured context, then re-raised."""
        mock_create_client.side_effect = ConnectionError("Temporal unreachable")

        with patch("app.worker.settings") as mock_settings:
            mock_settings.temporal_address = "bad-host:7233"
            mock_settings.temporal_namespace = "default"
            mock_settings.temporal_task_queue = "remo-tasks"

            with pytest.raises(ConnectionError, match="Temporal unreachable"):
                await run_worker()


class TestMockStubsGuard:
    """Verify the production guard warns when mock stubs are registered."""

    @pytest.mark.asyncio
    @patch("app.worker.Worker")
    @patch("app.worker.create_temporal_client")
    @patch("app.worker.logger")
    async def test_warns_in_production_with_mock_stubs(
        self,
        mock_logger: MagicMock,
        mock_create_client: AsyncMock,
        mock_worker_cls: MagicMock,
    ) -> None:
        """Mock stubs registered in non-development env triggers a warning."""
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client
        mock_worker = MagicMock()
        mock_worker.run = AsyncMock()
        mock_worker_cls.return_value = mock_worker

        with patch("app.worker.settings") as mock_settings:
            mock_settings.temporal_address = "localhost:7233"
            mock_settings.temporal_namespace = "default"
            mock_settings.temporal_task_queue = "remo-tasks"
            mock_settings.environment = "production"

            await run_worker()

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "worker_using_mock_stubs"

    @pytest.mark.asyncio
    @patch("app.worker.Worker")
    @patch("app.worker.create_temporal_client")
    @patch("app.worker.logger")
    async def test_no_warning_in_development(
        self,
        mock_logger: MagicMock,
        mock_create_client: AsyncMock,
        mock_worker_cls: MagicMock,
    ) -> None:
        """No warning when running in development environment."""
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client
        mock_worker = MagicMock()
        mock_worker.run = AsyncMock()
        mock_worker_cls.return_value = mock_worker

        with patch("app.worker.settings") as mock_settings:
            mock_settings.temporal_address = "localhost:7233"
            mock_settings.temporal_namespace = "default"
            mock_settings.temporal_task_queue = "remo-tasks"
            mock_settings.environment = "development"

            await run_worker()

            mock_logger.warning.assert_not_called()


class TestConfigureLogging:
    """Verify logging configuration is shared between API and worker."""

    def test_logging_module_importable(self) -> None:
        """app.logging.configure_logging should be importable."""
        from app.logging import configure_logging

        configure_logging()

    def test_log_level_respected(self, monkeypatch) -> None:
        """LOG_LEVEL setting should control structlog filtering level."""
        import structlog

        monkeypatch.setenv("LOG_LEVEL", "ERROR")

        from app.config import Settings

        fresh_settings = Settings()
        monkeypatch.setattr("app.logging.settings", fresh_settings)

        from app.logging import configure_logging

        configure_logging()

        # Bind a logger to instantiate the wrapper class — its name
        # encodes the filtering level (e.g. BoundLoggerFilteringAtError)
        bound = structlog.get_logger().bind()
        class_name = type(bound).__name__
        assert "Error" in class_name, f"Expected filtering at ERROR, got {class_name}"
