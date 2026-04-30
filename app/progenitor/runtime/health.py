"""Runtime health file writing."""

import json
import logging
import os
from pathlib import Path

from .time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)

HEALTH_FILE = "health.json"


async def update_health_file(
    *,
    state_dir: Path,
    status: str = "running",
) -> None:
    """Update health file for Docker health checks."""
    health_path = state_dir / HEALTH_FILE
    health_data = {
        "status": status,
        "last_active": utcnow_isoformat(),
        "pid": os.getpid(),
    }

    try:
        health_path.write_text(json.dumps(health_data))
    except Exception as e:
        logger.warning("Failed to update health file: %s", e)
