"""Tests for purge activity — R2 cleanup and DB deletion."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.activities.purge import _pg_dsn, purge_project_data


class TestPgDsn:
    """Tests for _pg_dsn helper."""

    @patch("app.activities.purge.settings")
    def test_converts_asyncpg_to_plain(self, mock_settings: MagicMock) -> None:
        mock_settings.database_url = "postgresql+asyncpg://user:pass@host:5432/remo"
        assert _pg_dsn() == "postgresql://user:pass@host:5432/remo"

    @patch("app.activities.purge.settings")
    def test_no_change_if_already_plain(self, mock_settings: MagicMock) -> None:
        mock_settings.database_url = "postgresql://user:pass@host:5432/remo"
        assert _pg_dsn() == "postgresql://user:pass@host:5432/remo"


class TestPurgeProjectData:
    """Tests for purge_project_data activity."""

    @pytest.mark.asyncio
    @patch("app.activities.purge.asyncpg")
    @patch("app.activities.purge.delete_prefix")
    async def test_deletes_r2_prefix(self, mock_delete: MagicMock, mock_asyncpg: MagicMock) -> None:
        """Should delete all R2 objects under projects/{id}/."""
        mock_conn = AsyncMock()
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        project_id = str(uuid.uuid4())

        await purge_project_data(project_id)

        mock_delete.assert_called_once_with(f"projects/{project_id}/")

    @pytest.mark.asyncio
    @patch("app.activities.purge.asyncpg")
    @patch("app.activities.purge.delete_prefix")
    async def test_correct_prefix_format(
        self, mock_delete: MagicMock, mock_asyncpg: MagicMock
    ) -> None:
        """R2 prefix should follow projects/{project_id}/ pattern."""
        mock_conn = AsyncMock()
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        project_id = str(uuid.uuid4())

        await purge_project_data(project_id)

        prefix = mock_delete.call_args[0][0]
        assert prefix.startswith("projects/")
        assert prefix.endswith("/")
        assert project_id in prefix

    @pytest.mark.asyncio
    @patch("app.activities.purge.asyncpg")
    @patch("app.activities.purge.delete_prefix")
    async def test_r2_error_propagates(
        self, mock_delete: MagicMock, mock_asyncpg: MagicMock
    ) -> None:
        """R2 errors should propagate (Temporal handles retries)."""
        mock_delete.side_effect = Exception("R2 connection failed")

        with pytest.raises(Exception, match="R2 connection failed"):
            await purge_project_data(str(uuid.uuid4()))

    @pytest.mark.asyncio
    @patch("app.activities.purge.asyncpg")
    @patch("app.activities.purge.delete_prefix")
    async def test_deletes_db_record(self, mock_delete: MagicMock, mock_asyncpg: MagicMock) -> None:
        """Should delete the project row from PostgreSQL after R2 cleanup."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 1")
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        project_id = str(uuid.uuid4())

        await purge_project_data(project_id)

        mock_conn.execute.assert_called_once_with(
            "DELETE FROM projects WHERE id = $1", uuid.UUID(project_id)
        )
        mock_conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.activities.purge.asyncpg")
    @patch("app.activities.purge.delete_prefix")
    async def test_db_error_does_not_propagate(
        self, mock_delete: MagicMock, mock_asyncpg: MagicMock
    ) -> None:
        """DB errors are logged but don't fail the activity (R2 already cleaned)."""
        mock_asyncpg.connect = AsyncMock(side_effect=Exception("DB unreachable"))
        project_id = str(uuid.uuid4())

        # Should not raise — DB failure is non-fatal
        await purge_project_data(project_id)

        # R2 cleanup still happened
        mock_delete.assert_called_once_with(f"projects/{project_id}/")

    @pytest.mark.asyncio
    @patch("app.activities.purge.asyncpg")
    @patch("app.activities.purge.delete_prefix")
    async def test_db_conn_closed_on_execute_error(
        self, mock_delete: MagicMock, mock_asyncpg: MagicMock
    ) -> None:
        """Connection is closed even when execute() fails."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("SQL error"))
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)

        # Should not raise — use valid UUID so we reach execute()
        await purge_project_data(str(uuid.uuid4()))

        mock_conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.activities.purge.asyncpg")
    @patch("app.activities.purge.delete_prefix")
    async def test_nonexistent_project_is_noop(
        self, mock_delete: MagicMock, mock_asyncpg: MagicMock
    ) -> None:
        """Deleting a nonexistent project is a no-op (DELETE 0)."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 0")
        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)

        # Should succeed without error
        await purge_project_data(str(uuid.uuid4()))

        mock_conn.execute.assert_called_once()
        mock_conn.close.assert_awaited_once()
