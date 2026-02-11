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

import structlog
from temporalio import activity

from app.utils.r2 import delete_prefix

logger = structlog.get_logger()


@activity.defn
async def purge_project_data(project_id: str) -> None:
    """Delete all R2 objects and DB records for a project.

    R2 objects are stored under /projects/{project_id}/.
    DB deletion is deferred to P2 integration (requires async DB session).
    """
    r2_prefix = f"projects/{project_id}/"

    logger.info("purge_start", project_id=project_id, r2_prefix=r2_prefix)
    await asyncio.to_thread(delete_prefix, r2_prefix)
    logger.info("purge_r2_complete", project_id=project_id)

    # DB deletion deferred to P2 — requires wiring async SQLAlchemy session
    # into the activity. For now, R2 cleanup is the critical path (R2 lifecycle
    # rule at 120h is the safety net for any missed deletions).
