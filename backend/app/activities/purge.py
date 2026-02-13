"""Purge project data — R2 objects and database records.

Called by the Temporal workflow on:
1. 24h after project completion (auto-purge)
2. 48h abandonment timeout
3. User-initiated project cancellation

Deletes all R2 objects under the project prefix, then deletes the project
row from PostgreSQL (children cascade via ON DELETE CASCADE).
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import structlog
from temporalio import activity

from app.config import settings
from app.utils.r2 import delete_prefix

logger = structlog.get_logger()


def _pg_dsn() -> str:
    """Convert SQLAlchemy-style URL to plain PostgreSQL DSN for asyncpg."""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


@activity.defn
async def purge_project_data(project_id: str) -> None:
    """Delete all R2 objects and DB records for a project.

    R2 objects are stored under /projects/{project_id}/.
    DB row deletion cascades to all child tables (photos, scans, briefs,
    generated images, revisions, shopping list) via ON DELETE CASCADE.
    """
    r2_prefix = f"projects/{project_id}/"

    logger.info("purge_start", project_id=project_id, r2_prefix=r2_prefix)
    await asyncio.to_thread(delete_prefix, r2_prefix)
    logger.info("purge_r2_complete", project_id=project_id)

    try:
        conn = await asyncpg.connect(dsn=_pg_dsn())
        try:
            result = await conn.execute("DELETE FROM projects WHERE id = $1", uuid.UUID(project_id))
            logger.info("purge_db_complete", project_id=project_id, result=result)
        finally:
            await conn.close()
    except Exception:
        logger.exception("purge_db_failed", project_id=project_id)
        # R2 cleanup already succeeded — log the DB failure but don't
        # fail the activity. The DB records are orphaned but harmless.
