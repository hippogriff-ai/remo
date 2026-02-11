from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint — confirms the API process is alive.

    Service connectivity checks (postgres, temporal, r2) report "not_connected"
    in mock mode since the API doesn't hold connections to those services yet.
    Real connectivity checks will be added in P2 when the API wires to DB/Temporal.
    """
    return {
        "status": "ok",
        "version": "0.1.0",
        "environment": settings.environment,
        "postgres": "not_connected",
        "temporal": "not_connected",
        "r2": "not_connected",
    }
