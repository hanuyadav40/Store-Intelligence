"""
Structured JSON logging configuration.

Every log record emitted by the application includes:
  - timestamp (ISO-8601 UTC)
  - level
  - logger name
  - message
  - trace_id (injected by middleware)
  - any extra fields passed at the call site

This makes logs trivially parseable by log aggregators (CloudWatch, Loki, etc.).
"""
import logging
import sys

from pythonjsonlogger import jsonlogger

from app.config import get_settings


def configure_logging() -> None:
    settings = get_settings()

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
