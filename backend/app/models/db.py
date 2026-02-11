"""SQLAlchemy ORM models for Remo.

Design principle: The database stores data artifacts only.
Workflow state (step, iteration_count, approved) lives exclusively in Temporal.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    has_lidar: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    photos: Mapped[list["Photo"]] = relationship(back_populates="project", cascade="all, delete")
    lidar_scan: Mapped["LidarScan | None"] = relationship(
        back_populates="project", cascade="all, delete"
    )
    design_brief: Mapped["DesignBriefRow | None"] = relationship(
        back_populates="project", cascade="all, delete"
    )
    generated_images: Mapped[list["GeneratedImage"]] = relationship(
        back_populates="project", cascade="all, delete"
    )
    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="project", cascade="all, delete"
    )
    shopping_list: Mapped["ShoppingList | None"] = relationship(
        back_populates="project", cascade="all, delete"
    )


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (Index("idx_photos_project_type", "project_id", "type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="photos")


class LidarScan(Base):
    __tablename__ = "lidar_scans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    room_dimensions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="lidar_scan")


class DesignBriefRow(Base):
    __tablename__ = "design_briefs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    intake_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    brief_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    conversation_history: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="design_brief")


class GeneratedImage(Base):
    __tablename__ = "generated_images"
    __table_args__ = (Index("idx_generated_images_project", "project_id", "type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    generation_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="generated_images")


class Revision(Base):
    __tablename__ = "revisions"
    __table_args__ = (Index("idx_revisions_project", "project_id", "revision_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    base_image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_images.id"), nullable=True
    )
    result_image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_images.id"), nullable=True
    )
    edit_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="revisions")
    lasso_regions: Mapped[list["LassoRegionRow"]] = relationship(
        back_populates="revision", cascade="all, delete"
    )


class LassoRegionRow(Base):
    __tablename__ = "lasso_regions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("revisions.id", ondelete="CASCADE"), nullable=False
    )
    region_number: Mapped[int] = mapped_column(Integer, nullable=False)
    path_points: Mapped[list] = mapped_column(JSONB, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    avoid_tokens: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    style_nudges: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    revision: Mapped["Revision"] = relationship(back_populates="lasso_regions")


class ShoppingList(Base):
    __tablename__ = "shopping_lists"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    generated_image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_images.id"), nullable=True
    )
    total_estimated_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="shopping_list")
    product_matches: Mapped[list["ProductMatchRow"]] = relationship(
        back_populates="shopping_list", cascade="all, delete"
    )


class ProductMatchRow(Base):
    __tablename__ = "product_matches"
    __table_args__ = (Index("idx_product_matches_list", "shopping_list_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shopping_list_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shopping_lists.id", ondelete="CASCADE"), nullable=False
    )
    category_group: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    retailer: Mapped[str] = mapped_column(String(200), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    product_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    why_matched: Mapped[str] = mapped_column(Text, nullable=False)
    fit_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fit_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    shopping_list: Mapped["ShoppingList"] = relationship(back_populates="product_matches")
