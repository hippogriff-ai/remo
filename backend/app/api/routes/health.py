"""Health check endpoint with real service connectivity probes.

Each service check has a short timeout to avoid blocking the response.
A service reporting "disconnected" does not affect the overall status ("ok")
— the health endpoint always returns 200 so load balancers keep routing.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter

from app.config import settings

logger = structlog.get_logger()

router = APIRouter(tags=["health"])

_CHECK_TIMEOUT = 3.0  # seconds per service check


async def _check_postgres() -> str:
    """Ping PostgreSQL with a simple SELECT 1 query."""
    import asyncpg

    # Parse just the connection parts from the SQLAlchemy URL
    # database_url format: postgresql+asyncpg://user:pass@host:port/dbname
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(url), timeout=_CHECK_TIMEOUT)
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
        return "connected"
    except Exception as exc:
        logger.debug("health_postgres_failed", error=str(exc))
        return "disconnected"


async def _check_temporal() -> str:
    """Connect to Temporal server with a short timeout."""
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter

    try:
        if settings.temporal_api_key:
            client = await asyncio.wait_for(
                Client.connect(
                    target_host=settings.temporal_address,
                    namespace=settings.temporal_namespace,
                    tls=True,
                    api_key=settings.temporal_api_key,
                    data_converter=pydantic_data_converter,
                ),
                timeout=_CHECK_TIMEOUT,
            )
        else:
            client = await asyncio.wait_for(
                Client.connect(
                    target_host=settings.temporal_address,
                    namespace=settings.temporal_namespace,
                    data_converter=pydantic_data_converter,
                ),
                timeout=_CHECK_TIMEOUT,
            )
        # Light operation to confirm connectivity
        await client.service_client.check_health()
        return "connected"
    except Exception as exc:
        logger.debug("health_temporal_failed", error=str(exc))
        return "disconnected"


async def _check_r2() -> str:
    """Check R2 bucket accessibility via head_bucket."""
    from app.utils.r2 import _get_client

    try:

        def _head_bucket() -> None:
            client = _get_client()
            client.head_bucket(Bucket=settings.r2_bucket_name)

        await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _head_bucket),
            timeout=_CHECK_TIMEOUT,
        )
        return "connected"
    except Exception as exc:
        logger.debug("health_r2_failed", error=str(exc))
        return "disconnected"


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint — confirms the API process is alive.

    Probes PostgreSQL, Temporal, and R2 in parallel with short timeouts.
    Always returns 200 so load balancers keep routing.
    """
    postgres, temporal, r2 = await asyncio.gather(
        _check_postgres(),
        _check_temporal(),
        _check_r2(),
    )

    return {
        "status": "ok",
        "version": "0.1.0",
        "environment": settings.environment,
        "postgres": postgres,
        "temporal": temporal,
        "r2": r2,
    }
