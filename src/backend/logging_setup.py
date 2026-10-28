"""Backend process logging configuration."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

DEFAULT_LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_log_dir() -> Path:
    """Return the configured shared log directory."""
    return Path(os.getenv("PROGENA_LOG_DIR", str(DEFAULT_LOGS_DIR)))


def setup_logging() -> None:
    """Configure console and split rotating file logging for the backend."""
    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    root_logger = logging.getLogger()
    for existing_handler in root_logger.handlers[:]:
        existing_handler.close()
        root_logger.removeHandler(existing_handler)
    root_logger.setLevel(logging.INFO)

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(_BelowErrorFilter())
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    try:
        log_dir = get_log_dir() / "backend"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "backend.stdout.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        stdout_file_handler.setLevel(logging.INFO)
        stdout_file_handler.addFilter(_BelowErrorFilter())
        stdout_file_handler.setFormatter(formatter)
        root_logger.addHandler(stdout_file_handler)

        stderr_file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "backend.stderr.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        stderr_file_handler.setLevel(logging.ERROR)
        stderr_file_handler.setFormatter(formatter)
        root_logger.addHandler(stderr_file_handler)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("Could not create backend log files: %s", exc)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # Each request is logged once by the app's "API request completed" line
    # (src/backend/http/middleware.py); uvicorn's access line would duplicate it.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


class _BelowErrorFilter(logging.Filter):
    """Allow only records below ERROR."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether a record belongs on stdout."""
        return record.levelno < logging.ERROR
