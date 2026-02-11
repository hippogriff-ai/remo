"""Initial schema — 9 tables matching db.py models.

Revision ID: 001
Revises: (none)
Create Date: 2025-02-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("device_fingerprint", sa.String(255), nullable=False),
        sa.Column("has_lidar", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # --- photos ---
    op.create_table(
        "photos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("validation_passed", sa.Boolean(), nullable=True),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_photos_project_type", "photos", ["project_id", "type"])

    # --- lidar_scans ---
    op.create_table(
        "lidar_scans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("room_dimensions", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # --- design_briefs ---
    op.create_table(
        "design_briefs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("intake_mode", sa.String(20), nullable=False),
        sa.Column("brief_data", JSONB(), nullable=False),
        sa.Column("conversation_history", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # --- generated_images ---
    op.create_table(
        "generated_images",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("selected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_final", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("generation_model", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_generated_images_project", "generated_images", ["project_id", "type"],
    )

    # --- revisions ---
    op.create_table(
        "revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column(
            "base_image_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generated_images.id"),
            nullable=True,
        ),
        sa.Column(
            "result_image_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generated_images.id"),
            nullable=True,
        ),
        sa.Column("edit_payload", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_revisions_project", "revisions", ["project_id", "revision_number"],
    )

    # --- lasso_regions ---
    op.create_table(
        "lasso_regions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("region_number", sa.Integer(), nullable=False),
        sa.Column("path_points", JSONB(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("avoid_tokens", JSONB(), nullable=True),
        sa.Column("style_nudges", JSONB(), nullable=True),
    )

    # --- shopping_lists ---
    op.create_table(
        "shopping_lists",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "generated_image_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generated_images.id"),
            nullable=True,
        ),
        sa.Column("total_estimated_cost_cents", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # --- product_matches ---
    op.create_table(
        "product_matches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "shopping_list_id",
            UUID(as_uuid=True),
            sa.ForeignKey("shopping_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category_group", sa.String(100), nullable=False),
        sa.Column("product_name", sa.String(500), nullable=False),
        sa.Column("retailer", sa.String(200), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("product_url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("why_matched", sa.Text(), nullable=False),
        sa.Column("fit_status", sa.String(20), nullable=True),
        sa.Column("fit_detail", sa.Text(), nullable=True),
        sa.Column("dimensions", sa.String(100), nullable=True),
    )
    op.create_index(
        "idx_product_matches_list", "product_matches", ["shopping_list_id"],
    )


def downgrade() -> None:
    op.drop_table("product_matches")
    op.drop_table("shopping_lists")
    op.drop_table("lasso_regions")
    op.drop_table("revisions")
    op.drop_table("generated_images")
    op.drop_table("design_briefs")
    op.drop_table("lidar_scans")
    op.drop_table("photos")
    op.drop_table("projects")
