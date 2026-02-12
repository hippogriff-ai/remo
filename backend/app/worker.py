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

from app.activities.purge import purge_project_data
from app.config import settings
from app.logging import configure_logging
from app.workflows.design_project import DesignProjectWorkflow

logger = structlog.get_logger()


def _load_activities() -> list:
    """Load mock or real activity implementations based on config."""
    if settings.use_mock_activities:
        from app.activities.mock_stubs import (
            edit_design,
            generate_designs,
            generate_shopping_list,
        )
    else:
        try:
            from app.activities.edit import edit_design
            from app.activities.generate import generate_designs
            from app.activities.shopping import generate_shopping_list
        except ImportError as exc:
            raise ImportError(
                f"Failed to import real activity modules (USE_MOCK_ACTIVITIES=false): {exc}. "
                "Set USE_MOCK_ACTIVITIES=true for mock stubs."
            ) from exc

    return [generate_designs, edit_design, generate_shopping_list, purge_project_data]


ACTIVITIES = _load_activities()

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

    if settings.use_mock_activities and settings.environment != "development":
        logger.warning(
            "worker_using_mock_stubs",
            environment=settings.environment,
            hint="Set USE_MOCK_ACTIVITIES=false for real AI activities",
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
        logger.exception("worker_fatal_error")
        sys.exit(1)


if __name__ == "__main__":
    main()
