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
from app.worker import ACTIVITIES, WORKFLOWS, _load_activities, create_temporal_client, run_worker
from app.workflows.design_project import DesignProjectWorkflow


class TestActivityRegistration:
    """Verify that the correct activities are registered with the worker."""

    def test_all_activities_registered(self) -> None:
        """All 5 activities (4 mock + 1 real purge) should be in the ACTIVITIES list."""
        assert len(ACTIVITIES) == 5

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


class TestLoadActivities:
    """Verify _load_activities correctly switches between mock and real implementations."""

    def test_mock_branch_loads_mock_stubs(self) -> None:
        """use_mock_activities=True loads from mock_stubs module."""
        with patch("app.worker.settings") as mock_settings:
            mock_settings.use_mock_activities = True
            activities = _load_activities()
        assert len(activities) == 5
        # Check all loaded activities have @activity.defn names
        names = [getattr(a, "__temporal_activity_definition").name for a in activities]
        assert "generate_designs" in names
        assert "edit_design" in names
        assert "generate_shopping_list" in names
        assert "load_style_skill" in names
        assert "purge_project_data" in names

    def test_real_branch_loads_real_modules(self) -> None:
        """use_mock_activities=False loads from real T2/T3 modules."""
        with patch("app.worker.settings") as mock_settings:
            mock_settings.use_mock_activities = False
            activities = _load_activities()
        assert len(activities) == 5
        names = [getattr(a, "__temporal_activity_definition").name for a in activities]
        assert "generate_designs" in names
        assert "edit_design" in names
        assert "generate_shopping_list" in names
        assert "load_style_skill" in names
        assert "purge_project_data" in names
        # Verify these come from the real modules, not mock_stubs
        from app.activities import edit, generate, shopping

        fn_modules = {a.__module__ for a in activities}
        assert edit.__name__ in fn_modules
        assert generate.__name__ in fn_modules
        assert shopping.__name__ in fn_modules

    def test_real_branch_import_error_gives_helpful_message(self) -> None:
        """Missing real modules gives actionable error mentioning USE_MOCK_ACTIVITIES."""
        with (
            patch("app.worker.settings") as mock_settings,
            patch.dict("sys.modules", {"app.activities.generate": None}),
        ):
            mock_settings.use_mock_activities = False
            with pytest.raises(ImportError, match="USE_MOCK_ACTIVITIES"):
                _load_activities()


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


class TestMainEntrypoint:
    """Verify the main() CLI entrypoint handles all exit scenarios."""

    @patch("app.worker.run_worker")
    @patch("app.worker.asyncio.run")
    @patch("app.worker.configure_logging")
    def test_main_calls_configure_logging_and_run(
        self, mock_configure: MagicMock, mock_run: MagicMock, mock_worker: MagicMock
    ) -> None:
        """main() configures logging then runs the async worker."""
        from app.worker import main

        main()
        mock_configure.assert_called_once()
        mock_run.assert_called_once()

    @patch("app.worker.run_worker")
    @patch("app.worker.asyncio.run", side_effect=KeyboardInterrupt)
    @patch("app.worker.configure_logging")
    def test_main_handles_keyboard_interrupt(
        self, mock_configure: MagicMock, mock_run: MagicMock, mock_worker: MagicMock
    ) -> None:
        """Ctrl+C exits cleanly without error."""
        from app.worker import main

        # Should not raise — KeyboardInterrupt is caught and suppressed
        main()

    @patch("app.worker.sys")
    @patch("app.worker.run_worker")
    @patch("app.worker.asyncio.run", side_effect=RuntimeError("fatal"))
    @patch("app.worker.configure_logging")
    def test_main_fatal_error_exits_with_code_1(
        self,
        mock_configure: MagicMock,
        mock_run: MagicMock,
        mock_worker: MagicMock,
        mock_sys: MagicMock,
    ) -> None:
        """Unhandled exceptions log the error and call sys.exit(1)."""
        from app.worker import main

        main()
        mock_sys.exit.assert_called_once_with(1)


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

    def test_json_renderer_in_production(self, monkeypatch) -> None:
        """Production environment uses JSONRenderer for structured log output."""
        import structlog

        monkeypatch.setenv("ENVIRONMENT", "production")

        from app.config import Settings

        fresh_settings = Settings()
        monkeypatch.setattr("app.logging.settings", fresh_settings)

        from app.logging import configure_logging

        configure_logging()

        config = structlog.get_config()
        renderer = config["processors"][-1]
        assert isinstance(renderer, structlog.processors.JSONRenderer)

    def test_unknown_log_level_falls_back_to_info(self, monkeypatch) -> None:
        """Unknown LOG_LEVEL string falls back to INFO filtering."""
        import structlog

        monkeypatch.setenv("LOG_LEVEL", "BOGUS")

        from app.config import Settings

        fresh_settings = Settings()
        monkeypatch.setattr("app.logging.settings", fresh_settings)

        from app.logging import configure_logging

        configure_logging()

        bound = structlog.get_logger().bind()
        class_name = type(bound).__name__
        assert "Info" in class_name, f"Expected filtering at INFO, got {class_name}"

    def test_log_file_creates_tee_writer(self, monkeypatch, tmp_path) -> None:
        """PRE-3: LOG_FILE setting enables dual output (stdout + file)."""
        import structlog

        log_path = str(tmp_path / "test.log")
        monkeypatch.setenv("LOG_FILE", log_path)

        from app.config import Settings

        fresh_settings = Settings()
        monkeypatch.setattr("app.logging.settings", fresh_settings)

        from app.logging import configure_logging

        configure_logging()

        from app.logging import _TeeWriter

        config = structlog.get_config()
        factory = config["logger_factory"]
        # PrintLoggerFactory._file should be a _TeeWriter instance
        assert isinstance(factory._file, _TeeWriter)

    def test_tee_writer_writes_to_both_stdout_and_file(self, tmp_path, capsys) -> None:
        """PRE-3: _TeeWriter writes to both stdout and the log file."""
        from app.logging import _TeeWriter

        log_path = str(tmp_path / "tee.log")
        writer = _TeeWriter(log_path)
        writer.write("hello from tee\n")
        writer.flush()

        with open(log_path) as f:
            content = f.read()
        assert "hello from tee" in content

        captured = capsys.readouterr()
        assert "hello from tee" in captured.out

    def test_tee_writer_degrades_on_bad_path(self, capsys) -> None:
        """PRE-3: _TeeWriter falls back to stdout-only when file path is invalid."""
        from app.logging import _TeeWriter

        writer = _TeeWriter("/nonexistent/dir/impossible.log")
        # Should not raise — degrades gracefully
        writer.write("still works\n")
        writer.flush()
        captured = capsys.readouterr()
        assert "still works" in captured.out
        assert "WARNING" in captured.err  # stderr warning about bad path

    def test_tee_writer_disables_file_on_write_error(self, tmp_path, capsys) -> None:
        """PRE-3: _TeeWriter disables file logging when write fails."""
        from app.logging import _TeeWriter

        log_path = str(tmp_path / "fragile.log")
        writer = _TeeWriter(log_path)
        writer.write("before\n")
        # Simulate I/O error by closing the file handle
        writer._file.close()
        # Next write should disable file logging, not crash
        writer.write("after\n")
        assert writer._file is None  # file logging disabled
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "write failed" in captured.err.lower()

    def test_tee_writer_flush_error_disables_file(self, tmp_path, capsys) -> None:
        """PRE-3: _TeeWriter.flush() disables file logging on I/O error."""
        from app.logging import _TeeWriter

        log_path = str(tmp_path / "flush_fail.log")
        writer = _TeeWriter(log_path)
        writer.write("data\n")
        assert writer._file is not None
        # Close underlying file to trigger ValueError on flush
        writer._file.close()
        writer.flush()
        assert writer._file is None  # file logging disabled after flush error
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "flush failed" in captured.err.lower()

    def test_console_renderer_in_development(self, monkeypatch) -> None:
        """Development environment uses ConsoleRenderer for human-readable output."""
        import structlog

        monkeypatch.setenv("ENVIRONMENT", "development")

        from app.config import Settings

        fresh_settings = Settings()
        monkeypatch.setattr("app.logging.settings", fresh_settings)

        from app.logging import configure_logging

        configure_logging()

        config = structlog.get_config()
        renderer = config["processors"][-1]
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)
