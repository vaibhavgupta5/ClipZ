"""Structured JSON logging with request-ID context."""

import logging
import sys
from typing import Any

import structlog


def setup_logging(level: str = "INFO") -> None:
    """Configure stdlib + structlog for JSON output with request IDs."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level.upper()),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )


def get_logger(**context: Any) -> Any:
    """Return a bound logger with the given context."""
    return structlog.get_logger().bind(**context)
