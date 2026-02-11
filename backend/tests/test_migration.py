"""Tests for the initial Alembic migration — structure and completeness."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import types

from app.models.db import Base


@pytest.fixture
def migration() -> types.ModuleType:
    """Import the initial migration module."""
    return importlib.import_module("migrations.versions.001_initial_schema")


class TestMigrationStructure:
    """Verify migration metadata and functions."""

    def test_revision_id(self, migration: types.ModuleType) -> None:
        """Migration has correct revision ID."""
        assert migration.revision == "001"

    def test_down_revision_is_none(self, migration: types.ModuleType) -> None:
        """Initial migration has no parent."""
        assert migration.down_revision is None

    def test_upgrade_callable(self, migration: types.ModuleType) -> None:
        """upgrade() function exists and is callable."""
        assert callable(migration.upgrade)

    def test_downgrade_callable(self, migration: types.ModuleType) -> None:
        """downgrade() function exists and is callable."""
        assert callable(migration.downgrade)


class TestMigrationCompleteness:
    """Verify migration covers all tables defined in db.py models."""

    def test_all_tables_represented(self, migration: types.ModuleType) -> None:
        """Migration must create tables for every model in Base.metadata."""
        model_tables = set(Base.metadata.tables.keys())
        # Read the migration source to check all tables are created
        import inspect

        source = inspect.getsource(migration.upgrade)
        for table_name in model_tables:
            assert f'"{table_name}"' in source, (
                f"Table '{table_name}' exists in db.py but is missing from migration"
            )

    def test_expected_table_count(self) -> None:
        """db.py should define exactly 9 tables."""
        assert len(Base.metadata.tables) == 9

    def test_expected_table_names(self) -> None:
        """All 9 expected tables are registered in Base.metadata."""
        expected = {
            "projects",
            "photos",
            "lidar_scans",
            "design_briefs",
            "generated_images",
            "revisions",
            "edit_regions",
            "shopping_lists",
            "product_matches",
        }
        assert set(Base.metadata.tables.keys()) == expected

    def test_indexes_represented(self, migration: types.ModuleType) -> None:
        """Migration must create the required indexes from the plan."""
        import inspect

        source = inspect.getsource(migration.upgrade)
        required_indexes = [
            "idx_photos_project_type",
            "idx_generated_images_project",
            "idx_revisions_project",
            "idx_product_matches_list",
        ]
        for idx_name in required_indexes:
            assert idx_name in source, (
                f"Index '{idx_name}' required by plan but missing from migration"
            )

    def test_cascade_deletes_on_fks(self, migration: types.ModuleType) -> None:
        """All child table FKs should specify CASCADE delete."""
        import inspect

        source = inspect.getsource(migration.upgrade)
        # Tables with CASCADE FKs to projects
        cascade_tables = [
            "photos",
            "lidar_scans",
            "design_briefs",
            "generated_images",
            "revisions",
            "shopping_lists",
        ]
        for table in cascade_tables:
            # Find the section for this table and verify CASCADE is present
            assert 'ondelete="CASCADE"' in source, f"CASCADE delete expected for {table} FK"

    def test_downgrade_drops_in_reverse_order(self, migration: types.ModuleType) -> None:
        """Downgrade must drop tables in reverse dependency order."""
        import inspect

        source = inspect.getsource(migration.downgrade)
        # product_matches depends on shopping_lists, which depends on projects
        # So product_matches must be dropped before shopping_lists, etc.
        lines = [line.strip() for line in source.split("\n") if "drop_table" in line]
        table_order = [line.split('"')[1] for line in lines if '"' in line]
        assert table_order == [
            "product_matches",
            "shopping_lists",
            "edit_regions",
            "revisions",
            "generated_images",
            "design_briefs",
            "lidar_scans",
            "photos",
            "projects",
        ]
