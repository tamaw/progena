"""Schedule-domain service helpers used by tools and tests."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


def generate_schedule_id(*, is_one_off: bool) -> str:
    prefix = "cron_" if is_one_off else "sched_"
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class ScheduleResult(BaseModel):
    """Result of a schedule operation."""

    success: bool
    schedule_id: str | None = None
    message: str
    next_run: str | None = None


async def remove_scheduled_task(schedule_id: str) -> ScheduleResult:
    return ScheduleResult(
        success=True, schedule_id=schedule_id, message=f"Task {schedule_id} marked for removal"
    )
