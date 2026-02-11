"""Temporal worker — registers workflows and activities.

Separate Railway service in production. Run locally with:
    python -m app.worker

Requires a running Temporal server (see docker-compose.yml).
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from app.activities.mock_stubs import (
    edit_design,
    generate_designs,
    generate_shopping_list,
)
from app.activities.purge import purge_project_data
from app.config import settings
from app.logging import configure_logging
from app.workflows.design_project import DesignProjectWorkflow

logger = structlog.get_logger()

# Activities registered with the worker. During P2 integration,
# mock stubs are replaced with real implementations from T2/T3.
_MOCK_ACTIVITY_MODULE = "app.activities.mock_stubs"

ACTIVITIES = [
    generate_designs,
    edit_design,
    generate_shopping_list,
    purge_project_data,
]

WORKFLOWS = [DesignProjectWorkflow]


async def create_temporal_client() -> Client:
    """Create a Temporal client using settings.

    Supports both local Temporal (plain TCP) and Temporal Cloud (TLS + API key).
    """
    if settings.temporal_api_key:
        return await Client.connect(
            target_host=settings.temporal_address,
            namespace=settings.temporal_namespace,
            tls=True,
            api_key=settings.temporal_api_key,
            data_converter=pydantic_data_converter,
        )
    return await Client.connect(
        target_host=settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )


async def run_worker() -> None:
    """Connect to Temporal and run the worker until interrupted."""
    logger.info(
        "worker_connecting",
        address=settings.temporal_address,
        namespace=settings.temporal_namespace,
        task_queue=settings.temporal_task_queue,
    )

    try:
        client = await create_temporal_client()
    except Exception:
        logger.exception(
            "worker_connection_failed",
            address=settings.temporal_address,
            namespace=settings.temporal_namespace,
        )
        raise

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,  # type: ignore[arg-type]
    )

    # Warn if mock stubs are registered in a non-development environment
    mock_activities = [
        a for a in ACTIVITIES if getattr(a, "__module__", "") == _MOCK_ACTIVITY_MODULE
    ]
    if mock_activities and settings.environment != "development":
        logger.warning(
            "worker_using_mock_stubs",
            environment=settings.environment,
            mock_count=len(mock_activities),
            hint="Replace with real T2/T3 activity implementations",
        )

    logger.info(
        "worker_started",
        task_queue=settings.temporal_task_queue,
        workflow_count=len(WORKFLOWS),
        activity_count=len(ACTIVITIES),
    )

    await worker.run()
    logger.info("worker_stopped")


def main() -> None:
    """Entrypoint for `python -m app.worker`."""
    configure_logging()
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
