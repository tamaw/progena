"""Runtime logging setup for Progena processes."""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import time
from datetime import UTC, datetime
from typing import Any


def setup_logging(config: Any, agent_name: str) -> None:
    """Configure stdout/stderr and rotating file logging."""
    log_levels = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    level = log_levels.get(str(config.runtime.log_level).lower(), logging.INFO)

    if str(config.runtime.log_format).lower() == "json":
        formatter: logging.Formatter = JsonLogFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        # Log timestamps are UTC.
        formatter.converter = time.gmtime

    root_logger = logging.getLogger()
    for existing_handler in root_logger.handlers[:]:
        existing_handler.close()
        root_logger.removeHandler(existing_handler)
    root_logger.setLevel(level)

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.addFilter(_BelowErrorFilter())
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    try:
        log_dir = config.storage.log_dir / agent_name
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_file_handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{agent_name}.stdout.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        stdout_file_handler.setLevel(level)
        stdout_file_handler.addFilter(_BelowErrorFilter())
        stdout_file_handler.setFormatter(formatter)
        root_logger.addHandler(stdout_file_handler)

        stderr_file_handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{agent_name}.stderr.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        stderr_file_handler.setLevel(logging.ERROR)
        stderr_file_handler.setFormatter(formatter)
        root_logger.addHandler(stderr_file_handler)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "Could not create split log files for %s: %s", agent_name, exc
        )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


class _BelowErrorFilter(logging.Filter):
    """Allow only records below ERROR."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return True for records that should stay on stdout."""
        return record.levelno < logging.ERROR


class JsonLogFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        for key in ("thread_id", "cycle_count", "node", "work_item_id"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry)
