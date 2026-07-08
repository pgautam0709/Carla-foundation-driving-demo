"""
src/utils/logging.py — Structured logging setup using structlog.

Usage::

    from src.utils.logging import get_logger, configure_logging

    configure_logging(level="DEBUG", fmt="console")   # call once at startup
    log = get_logger(__name__)

    log.info("simulation.started", map="Town03", seed=42)
    log.warning("sensor.dropped_frame", frame_id=1234)
    log.error("carla.connection_failed", host="localhost", port=2000)
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Literal

import structlog

# ── Types ──────────────────────────────────────────────────────────────────────
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["console", "json"]

_CONFIGURED = False


def configure_logging(
    level: LogLevel = "INFO",
    fmt: LogFormat = "console",
    *,
    log_file: str | None = None,
) -> None:
    """Configure structlog for the entire application.

    Call once at the application entry point before importing any module that
    calls :func:`get_logger`.

    Args:
        level: Minimum log level (``"DEBUG"`` | ``"INFO"`` | ``"WARNING"`` |
               ``"ERROR"`` | ``"CRITICAL"``).
        fmt: Output format. ``"console"`` produces human-readable coloured
             output; ``"json"`` produces machine-readable JSON for log
             aggregation pipelines.
        log_file: Optional path to write logs to in addition to stderr.
    """
    global _CONFIGURED

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=numeric_level,
        handlers=handlers,
        format="%(message)s",
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a bound structlog logger for *name*.

    If :func:`configure_logging` has not been called, defaults to INFO/console.
    """
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name)
