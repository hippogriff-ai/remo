"""Add cost_breakdown JSONB column to shopping_lists.

Revision ID: 002
Revises: 001
Create Date: 2026-02-12

Phase 1a evolution: stores CostBreakdown (materials + labor + professional
fees + permits + total range) as a nullable JSONB blob alongside the existing
total_estimated_cost_cents integer. Additive-only — no data migration needed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shopping_lists", sa.Column("cost_breakdown", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("shopping_lists", "cost_breakdown")
