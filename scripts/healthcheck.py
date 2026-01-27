#!/usr/bin/env python3
"""
Health check script for Progenitor Docker container.

Checks if the health.json file exists and was updated recently.
Exit codes:
  0 - healthy
  1 - unhealthy
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Health file location (matches HEALTH_FILE in loop.py)
HEALTH_FILE = Path("/app/data/state/health.json")
STALE_SECONDS = 120  # Consider unhealthy if not updated in 2 minutes


def check_health() -> bool:
    """Check if the service is healthy."""
    if not HEALTH_FILE.exists():
        print("Health file not found")
        return False

    try:
        data = json.loads(HEALTH_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Failed to read health file: {e}")
        return False

    last_active = data.get("last_active")
    if not last_active:
        print("No last_active timestamp in health file")
        return False

    try:
        # Parse ISO format timestamp
        last_time = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = (now - last_time).total_seconds()

        if age > STALE_SECONDS:
            print(f"Health file is stale ({age:.0f}s old, max {STALE_SECONDS}s)")
            return False

        print(f"Healthy: status={data.get('status')}, cycle={data.get('cycle_count')}, age={age:.0f}s")
        return True

    except (ValueError, TypeError) as e:
        print(f"Failed to parse timestamp: {e}")
        return False


if __name__ == "__main__":
    healthy = check_health()
    sys.exit(0 if healthy else 1)
