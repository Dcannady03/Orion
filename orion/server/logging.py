"""Server logging: rotating bounded file log plus stderr.

The server log lives at ``~/.orion/logs/server.log`` (rotating, 1 MB
per file, 3 backups). Access lines are structured JSON; operational
lines are plain text. Request bodies and approval tokens are never
logged.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "orion.server"
MAX_LOG_BYTES = 1_048_576
LOG_BACKUPS = 3


def server_log_path(paths: object) -> Path:
    """Resolve ~/.orion/logs/server.log from an OrionPaths object."""
    logs = getattr(paths, "logs", None)
    if logs is None:
        raise ValueError("Orion paths do not expose a logs directory.")
    return Path(logs) / "server.log"


def setup_server_logging(paths: object) -> logging.Logger:
    """Configure the orion.server logger (idempotent)."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_path = server_log_path(paths)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUPS,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"
    ))
    logger.addHandler(stream_handler)
    return logger
