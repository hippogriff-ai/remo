"""Shared structlog configuration — used by both API and worker entrypoints."""

from __future__ import annotations

import logging
import sys
from typing import IO

import structlog

from app.config import settings

_LOG_LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class _TeeWriter:
    """Write to both stdout and a log file (JSON lines for E2E observability).

    Degrades gracefully: if the file cannot be opened or a write fails,
    logging continues to stdout. File logging is a convenience, never a
    reason to crash the API process.
    """

    def __init__(self, file_path: str) -> None:
        self._file: IO[str] | None = None
        try:
            self._file = open(file_path, "a")  # noqa: SIM115
        except OSError as exc:
            # Cannot use structlog yet (called during configure_logging)
            print(
                f"WARNING: Could not open log file {file_path!r}: {exc}. "
                "Falling back to stdout-only logging.",
                file=sys.stderr,
            )

    def write(self, data: str) -> None:
        sys.stdout.write(data)
        if self._file is not None:
            try:
                self._file.write(data)
                self._file.flush()
            except (OSError, ValueError):
                self._file = None
                print(
                    "WARNING: Log file write failed. File logging disabled.",
                    file=sys.stderr,
                )

    def flush(self) -> None:
        sys.stdout.flush()
        if self._file is not None:
            try:
                self._file.flush()
            except (OSError, ValueError):
                self._file = None
                print(
                    "WARNING: Log file flush failed. File logging disabled.",
                    file=sys.stderr,
                )


def configure_logging() -> None:
    """Configure structlog with console renderer in dev, JSON in prod.

    When LOG_FILE is set, logs are written to both stdout and the specified file
    (JSON lines format for programmatic analysis during E2E runs).
    """
    renderer = (
        structlog.dev.ConsoleRenderer()
        if settings.environment == "development"
        else structlog.processors.JSONRenderer()
    )

    level = _LOG_LEVEL_MAP.get(settings.log_level.upper(), logging.INFO)

    logger_factory: structlog.types.WrappedLogger
    if settings.log_file:
        # PrintLoggerFactory only uses write() and flush() from the file object
        logger_factory = structlog.PrintLoggerFactory(file=_TeeWriter(settings.log_file))  # type: ignore[arg-type]
    else:
        logger_factory = structlog.PrintLoggerFactory()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=logger_factory,
        cache_logger_on_first_use=True,
    )
