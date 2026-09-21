"""Structured, stderr-only logging for the stdio MCP server.

stdout is the MCP transport, so all logs go to **stderr**. The runtime
configures this once in ``mcp_server.main()``. Logs carry metadata only - provider
ids, statuses, latencies, counts, cost - never secret values, ``api_key_env`` names,
``Authorization`` headers, or full prompts/answers."""

from __future__ import annotations

import logging
import os
import sys
from typing import TextIO

import structlog

_DEFAULT_LEVEL = "INFO"


def configure_logging(level: str | None = None, *, stream: TextIO | None = None) -> None:
    """Configure structlog to emit JSON lines to stderr (or ``stream`` for tests).

    Level comes from the argument, then ``$MANDOS_LOG_LEVEL``, then INFO."""
    name = (level or os.environ.get("MANDOS_LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    numeric = getattr(logging, name, logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "mandos"):
    """Return a bound logger. Safe to call at import time (lazy proxy)."""
    return structlog.get_logger(name)
