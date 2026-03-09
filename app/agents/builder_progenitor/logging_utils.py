"""Logging utilities for builder_progenitor.

Provides request/response logging to JSONL files for debugging and audit trails.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def log_llm_request_response(
    log_dir: Path | None,
    request_type: str,
    model: str,
    duration_ms: float,
    success: bool,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an LLM API request/response to JSONL audit trail.

    Args:
        log_dir: Directory to write logs to. If None, skips file logging.
        request_type: Type of request (e.g., "plan_step", "model_reasoning").
        model: Model name (e.g., "gpt-5-mini").
        duration_ms: Request duration in milliseconds.
        success: Whether the request succeeded.
        error: Error message if failed.
        metadata: Additional context (e.g., retry_count, tokens_used).
    """
    entry = {
        "timestamp": time.time(),
        "request_type": request_type,
        "model": model,
        "duration_ms": round(duration_ms, 2),
        "success": success,
        "error": error,
        "metadata": metadata or {},
    }

    # Always log to Python logger
    status = "OK" if success else "FAILED"
    logger.info(
        "LLM %s: %s (%.1fms) %s",
        request_type,
        model,
        duration_ms,
        status,
    )

    # Write to JSONL file if log_dir is provided
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "llm_requests.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.warning("Failed to write LLM log to %s: %s", log_file, e)
