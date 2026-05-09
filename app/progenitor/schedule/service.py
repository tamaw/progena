"""Schedule-domain service helpers used by tools and tests."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import BaseModel

from ..state.models import ScheduleEntry
from ..utils import utcnow
from .engine import Scheduler, parse_schedule_datetime

logger = logging.getLogger(__name__)


class ScheduleResult(BaseModel):
    """Result of a schedule operation."""

    success: bool
    schedule_id: str | None = None
    message: str
    next_run: str | None = None


async def add_scheduled_task(
    user_request: str,
    instruction: str,
    description: str | None = None,
    cron_expression: str | None = None,
    scheduled_at: str | None = None,
) -> tuple[ScheduleResult, dict | None]:
    scheduler = Scheduler()
    cron_expression = (cron_expression or "").strip() or None
    scheduled_at = (scheduled_at or "").strip() or None
    if bool(cron_expression) == bool(scheduled_at):
        return ScheduleResult(
            success=False,
            message="Provide exactly one of cron_expression or scheduled_at",
        ), None

    schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
    task_message = instruction.strip() or f"Scheduled: {user_request[:100]}"
    if description:
        task_message = f"{description.strip()} — {task_message}"
    entry = {
        "id": schedule_id,
        "message": task_message,
        "user_request": user_request,
        "last_run": None,
        "created_at": utcnow().isoformat(),
    }

    if scheduled_at:
        scheduled_dt = parse_schedule_datetime(scheduled_at)
        if scheduled_dt is None:
            return ScheduleResult(
                success=False,
                message=f"Invalid scheduled_at timestamp: {scheduled_at}",
            ), None
        entry["scheduled_at"] = scheduled_dt.isoformat()
        next_run = scheduler.next_run_for_entry(entry)
    else:
        entry["cron_expression"] = cron_expression
        next_run = scheduler.next_run_for_entry(entry)
        if next_run is None:
            return ScheduleResult(
                success=False,
                message=f"Invalid cron_expression: {cron_expression}",
            ), None

    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Scheduled task created: {task_message}",
        next_run=next_run.isoformat() if next_run else None,
    ), entry


async def delete_scheduled_task(
    schedule_id: str, state_manager: Any | None = None
) -> ScheduleResult:
    if not schedule_id or not schedule_id.strip():
        return ScheduleResult(success=False, message="schedule_id is required")
    if state_manager is not None:
        removed = await state_manager.remove_schedule_entry(schedule_id)
        if not removed:
            return ScheduleResult(
                success=False,
                schedule_id=schedule_id,
                message=f"Task {schedule_id} not found",
            )
        logger.info("Deleted schedule entry %s", schedule_id)
    return ScheduleResult(
        success=True, schedule_id=schedule_id, message=f"Task {schedule_id} deleted"
    )


async def update_scheduled_task(
    schedule_id: str,
    updates: dict[str, Any],
    state_manager: Any | None = None,
) -> ScheduleResult:
    if not schedule_id or not schedule_id.strip():
        return ScheduleResult(success=False, message="schedule_id is required")
    if not updates:
        return ScheduleResult(
            success=False, schedule_id=schedule_id, message="updates dict must not be empty"
        )
    protected = {"id", "created_at", "last_run"}
    bad_keys = protected & set(updates.keys())
    if bad_keys:
        return ScheduleResult(
            success=False,
            schedule_id=schedule_id,
            message=f"Cannot update protected field(s): {', '.join(sorted(bad_keys))}",
        )
    if state_manager is not None:
        updated = await state_manager.update_schedule_entry(schedule_id, updates)
        if not updated:
            return ScheduleResult(
                success=False,
                schedule_id=schedule_id,
                message=f"Task {schedule_id} not found",
            )
        logger.info("Updated schedule entry %s: %s", schedule_id, list(updates.keys()))
    return ScheduleResult(
        success=True, schedule_id=schedule_id, message=f"Task {schedule_id} updated"
    )


async def pause_scheduled_task(
    schedule_id: str, state_manager: Any | None = None
) -> ScheduleResult:
    if not schedule_id or not schedule_id.strip():
        return ScheduleResult(success=False, message="schedule_id is required")
    if state_manager is not None:
        updated = await state_manager.update_schedule_entry(schedule_id, {"paused": True})
        if not updated:
            return ScheduleResult(
                success=False,
                schedule_id=schedule_id,
                message=f"Task {schedule_id} not found",
            )
        logger.info("Paused schedule entry %s", schedule_id)
    return ScheduleResult(
        success=True, schedule_id=schedule_id, message=f"Task {schedule_id} paused"
    )


async def resume_scheduled_task(
    schedule_id: str, state_manager: Any | None = None
) -> ScheduleResult:
    if not schedule_id or not schedule_id.strip():
        return ScheduleResult(success=False, message="schedule_id is required")
    if state_manager is not None:
        updated = await state_manager.update_schedule_entry(schedule_id, {"paused": False})
        if not updated:
            return ScheduleResult(
                success=False,
                schedule_id=schedule_id,
                message=f"Task {schedule_id} not found",
            )
        logger.info("Resumed schedule entry %s", schedule_id)
    return ScheduleResult(
        success=True, schedule_id=schedule_id, message=f"Task {schedule_id} resumed"
    )


async def remove_scheduled_task(schedule_id: str) -> ScheduleResult:
    return ScheduleResult(
        success=True, schedule_id=schedule_id, message=f"Task {schedule_id} marked for removal"
    )


async def list_scheduled_tasks(
    schedule: list[dict[str, Any] | ScheduleEntry],
) -> list[dict[str, Any]]:
    scheduler = Scheduler()
    listed: list[dict[str, Any]] = []
    for entry in schedule:
        entry_dict = entry if isinstance(entry, dict) else entry.model_dump()
        next_run_dt = scheduler.next_run_for_entry(entry_dict)
        listed.append(
            {
                "id": entry_dict.get("id"),
                "message": entry_dict.get("message"),
                "status": "scheduled",
                "next_run": next_run_dt.isoformat() if next_run_dt else "Unknown",
                "cron_expression": entry_dict.get("cron_expression"),
                "last_run": entry_dict.get("last_run"),
                "created_at": entry_dict.get("created_at"),
            }
        )
    return listed
