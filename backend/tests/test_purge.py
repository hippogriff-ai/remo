"""Tests for purge activity — R2 cleanup and DB deletion."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.activities.purge import purge_project_data


class TestPurgeProjectData:
    """Tests for purge_project_data activity."""

    @pytest.mark.asyncio
    @patch("app.activities.purge.delete_prefix")
    async def test_deletes_r2_prefix(self, mock_delete: MagicMock) -> None:
        """Should delete all R2 objects under projects/{id}/."""
        await purge_project_data("test-project-123")

        mock_delete.assert_called_once_with("projects/test-project-123/")

    @pytest.mark.asyncio
    @patch("app.activities.purge.delete_prefix")
    async def test_correct_prefix_format(self, mock_delete: MagicMock) -> None:
        """R2 prefix should follow projects/{project_id}/ pattern."""
        await purge_project_data("abc-def-ghi")

        prefix = mock_delete.call_args[0][0]
        assert prefix.startswith("projects/")
        assert prefix.endswith("/")
        assert "abc-def-ghi" in prefix

    @pytest.mark.asyncio
    @patch("app.activities.purge.delete_prefix")
    async def test_r2_error_propagates(self, mock_delete: MagicMock) -> None:
        """R2 errors should propagate (Temporal handles retries)."""
        mock_delete.side_effect = Exception("R2 connection failed")

        with pytest.raises(Exception, match="R2 connection failed"):
            await purge_project_data("test-project")
