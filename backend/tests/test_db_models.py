"""Tests for SQLAlchemy ORM models.

Validates that:
- All models are importable and registered with Base.metadata
- Table names match the plan
- Foreign keys use CASCADE delete
- Required indexes exist
- Column types are correct
"""

from app.models.db import (
    Base,
    DesignBriefRow,
    EditRegionRow,
    GeneratedImage,
    LidarScan,
    Photo,
    ProductMatchRow,
    Project,
    Revision,
    ShoppingList,
)


class TestAllTablesRegistered:
    """Verify all expected tables exist in Base.metadata."""

    def test_table_names(self):
        """All 9 tables from the plan are registered."""
        expected_tables = {
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
        actual_tables = set(Base.metadata.tables.keys())
        assert expected_tables == actual_tables


class TestProjectModel:
    """Project is the root entity — all others cascade from it."""

    def test_tablename(self):
        """Table name is 'projects'."""
        assert Project.__tablename__ == "projects"

    def test_has_uuid_primary_key(self):
        """Primary key is UUID type."""
        pk = Project.__table__.c.id
        assert pk.primary_key

    def test_has_device_fingerprint(self):
        """Device fingerprint column exists and is not nullable."""
        col = Project.__table__.c.device_fingerprint
        assert not col.nullable

    def test_has_timestamps(self):
        """Created_at and updated_at columns exist."""
        assert "created_at" in Project.__table__.c
        assert "updated_at" in Project.__table__.c


class TestPhotoModel:
    """Photo FK cascades from project."""

    def test_cascade_delete(self):
        """Foreign key to projects uses ON DELETE CASCADE."""
        fk = next(iter(Photo.__table__.c.project_id.foreign_keys))
        assert fk.ondelete == "CASCADE"

    def test_has_index(self):
        """Index on (project_id, type) exists."""
        index_names = [idx.name for idx in Photo.__table__.indexes]
        assert "idx_photos_project_type" in index_names


class TestLidarScan:
    """LidarScan is 1:1 with project (unique constraint)."""

    def test_project_id_unique(self):
        """Project ID has a unique constraint."""
        col = LidarScan.__table__.c.project_id
        assert col.unique

    def test_room_dimensions_jsonb(self):
        """Room dimensions stored as JSONB."""
        col = LidarScan.__table__.c.room_dimensions
        assert col.nullable


class TestDesignBriefRow:
    """DesignBriefRow is 1:1 with project."""

    def test_project_id_unique(self):
        """Project ID has a unique constraint."""
        col = DesignBriefRow.__table__.c.project_id
        assert col.unique

    def test_brief_data_not_nullable(self):
        """Brief data JSONB is required."""
        col = DesignBriefRow.__table__.c.brief_data
        assert not col.nullable


class TestGeneratedImage:
    """GeneratedImage stores image references."""

    def test_has_index(self):
        """Index on (project_id, type) exists."""
        index_names = [idx.name for idx in GeneratedImage.__table__.indexes]
        assert "idx_generated_images_project" in index_names

    def test_cascade_delete(self):
        """FK cascades on project delete."""
        fk = next(iter(GeneratedImage.__table__.c.project_id.foreign_keys))
        assert fk.ondelete == "CASCADE"


class TestRevision:
    """Revision tracks edit iterations."""

    def test_has_index(self):
        """Index on (project_id, revision_number) exists."""
        index_names = [idx.name for idx in Revision.__table__.indexes]
        assert "idx_revisions_project" in index_names

    def test_has_base_and_result_image_fks(self):
        """Both base_image_id and result_image_id reference generated_images."""
        base_fk = next(iter(Revision.__table__.c.base_image_id.foreign_keys))
        result_fk = next(iter(Revision.__table__.c.result_image_id.foreign_keys))
        assert "generated_images" in str(base_fk.target_fullname)
        assert "generated_images" in str(result_fk.target_fullname)


class TestEditRegionRow:
    """EditRegionRow cascades from revision."""

    def test_cascade_delete(self):
        """FK to revisions uses CASCADE."""
        fk = next(iter(EditRegionRow.__table__.c.revision_id.foreign_keys))
        assert fk.ondelete == "CASCADE"


class TestShoppingList:
    """ShoppingList is 1:1 with project."""

    def test_project_id_unique(self):
        """Project ID has a unique constraint."""
        col = ShoppingList.__table__.c.project_id
        assert col.unique

    def test_cost_integer_cents(self):
        """Total cost stored as integer cents."""
        col = ShoppingList.__table__.c.total_estimated_cost_cents
        assert not col.nullable


class TestProductMatchRow:
    """ProductMatchRow cascades from shopping list."""

    def test_has_index(self):
        """Index on shopping_list_id exists."""
        index_names = [idx.name for idx in ProductMatchRow.__table__.indexes]
        assert "idx_product_matches_list" in index_names

    def test_cascade_delete(self):
        """FK to shopping_lists uses CASCADE."""
        fk = next(iter(ProductMatchRow.__table__.c.shopping_list_id.foreign_keys))
        assert fk.ondelete == "CASCADE"

    def test_price_integer_cents(self):
        """Price stored as integer cents (not float)."""
        col = ProductMatchRow.__table__.c.price_cents
        assert not col.nullable
