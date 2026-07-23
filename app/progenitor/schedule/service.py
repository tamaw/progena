"""Schedule identifiers used by the backend scheduling API."""

from __future__ import annotations

import uuid


def generate_schedule_id(*, is_one_off: bool) -> str:
    prefix = "cron_" if is_one_off else "sched_"
    return f"{prefix}{uuid.uuid4().hex[:12]}"
