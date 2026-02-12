import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import health, projects
from app.logging import configure_logging

configure_logging()

logger = structlog.get_logger()

app = FastAPI(
    title="Remo API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach a unique request ID to every request for log correlation.

    Sets the ID in structlog context vars (appears in all log entries for the
    request) and returns it in the X-Request-ID response header so T1 iOS
    can report it when debugging errors.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return ErrorResponse JSON for Pydantic validation errors.

    FastAPI's default 422 returns {"detail": [...]}, which doesn't match
    our ErrorResponse contract. T1 iOS needs a single error shape.
    """
    messages = []
    for err in exc.errors():
        loc = " → ".join(str(part) for part in err["loc"])
        messages.append(f"{loc}: {err['msg']}")
    response = JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "; ".join(messages),
            "retryable": False,
        },
    )
    response.headers["X-Request-ID"] = getattr(
        request.state, "request_id", request.headers.get("X-Request-ID", "")
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return consistent ErrorResponse JSON for unhandled exceptions.

    Without this, FastAPI returns bare HTML 500 errors that T1 iOS can't
    parse. This handler ensures all errors use the same JSON shape.
    """
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error_type=type(exc).__name__,
        exc_info=exc,
    )
    response = JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred",
            "retryable": True,
        },
    )
    response.headers["X-Request-ID"] = getattr(
        request.state, "request_id", request.headers.get("X-Request-ID", "")
    )
    return response


app.include_router(health.router)
app.include_router(projects.router, prefix="/api/v1")
