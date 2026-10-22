"""Schedule dispatch and completion helpers."""

from __future__ import annotations

from typing import Any

from app.progenitor.schedules.engine import Scheduler
from app.progenitor.schedules.models import (
    ScheduleRunRecord,
    ScheduleRunStatus,
    generate_schedule_run_id,
    generate_schedule_thread_id,
)
from app.progenitor.utils import utcnow

SCHEDULED_INPUT_PREFIX = "[Scheduled Task]\n"


def _schedule_entries_to_dicts(entries: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry.model_dump() if hasattr(entry, "model_dump") else entry for entry in entries.values()
    ]


async def get_due_schedule_ids(
    state_manager: Any,
    *,
    current_time,
) -> list[str]:
    """Return ids for entries that are due right now."""
    entries = await state_manager.get_schedule()
    due_items = Scheduler().get_due_items(_schedule_entries_to_dicts(entries), current_time)
    return [str(item.get("id") or "") for item in due_items if item.get("id")]


async def enqueue_due_schedule_entries(
    state_manager: Any,
    queues: Any,
    *,
    current_time,
) -> int:
    """Enqueue due scheduled work and persist schedule state updates."""
    scheduler = Scheduler()
    entries = await state_manager.get_schedule()
    due_items = scheduler.get_due_items(_schedule_entries_to_dicts(entries), current_time)
    if not due_items:
        return 0

    enqueued_count = 0
    now = utcnow()
    for entry in due_items:
        schedule_id = str(entry.get("id") or "")
        if not schedule_id:
            continue

        message = str(entry.get("message") or "").strip()
        if not message:
            continue
        user_request = str(entry.get("user_request") or "").strip()

        if user_request:
            full_message = f"<original_message>{user_request}</original_message>\n\n{message}"
        else:
            full_message = message

        run_id = generate_schedule_run_id()
        thread_id = generate_schedule_thread_id(schedule_id)
        queued = await queues.enqueue_input(
            f"{SCHEDULED_INPUT_PREFIX}{full_message}",
            timeout=0.5,
            metadata={
                "thread_id": thread_id,
                "schedule_id": schedule_id,
                "schedule_run_id": run_id,
            },
        )
        if not queued:
            break

        await state_manager.upsert_schedule_run(
            ScheduleRunRecord(
                run_id=run_id,
                schedule_id=schedule_id,
                thread_id=thread_id,
                message=message,
                user_request=user_request or None,
                status=ScheduleRunStatus.QUEUED,
                created_at=now,
                updated_at=now,
            )
        )

        scheduled_at = str(entry.get("scheduled_at") or "")
        cron_expression = str(entry.get("cron_expression") or "")
        is_one_shot = bool(scheduled_at) or scheduler.is_one_shot_expression(cron_expression)
        next_entry = dict(entry)
        next_entry["last_run"] = now.isoformat()
        if is_one_shot:
            next_entry.update(
                {
                    "paused": True,
                    "enabled": False,
                    "last_run_status": "running",
                    "completed_at": None,
                    "updated_at": now.isoformat(),
                }
            )
        else:
            next_entry.update(
                {
                    "last_run_status": "running",
                    "updated_at": now.isoformat(),
                }
            )
        next_run = scheduler.next_run_for_entry(next_entry)
        next_entry["next_run"] = next_run.isoformat() if next_run else None
        next_entry["next_run_at"] = next_run.isoformat() if next_run else None
        updates = {
            key: next_entry.get(key)
            for key in (
                "last_run",
                "last_run_status",
                "updated_at",
                "paused",
                "enabled",
                "completed_at",
                "next_run",
                "next_run_at",
            )
            if key in next_entry
        }
        await state_manager.update_schedule_entry(schedule_id, updates)
        await state_manager.log_schedule_event(
            {
                "message": (
                    f"Schedule {schedule_id} fired and run {run_id} was queued"
                    if is_one_shot
                    else f"Schedule {schedule_id} fired and run {run_id} was queued"
                ),
                "schedule_id": schedule_id,
                "schedule_run_id": run_id,
                "thread_id": thread_id,
                "event": "schedule_fired",
                "one_shot": is_one_shot,
            }
        )

        await queues.push_event(
            "scheduled_task_triggered",
            f"Scheduled task triggered: {entry.get('message') or schedule_id}",
            {
                "schedule_id": schedule_id,
                "schedule_run_id": run_id,
                "thread_id": thread_id,
                "one_shot": is_one_shot,
                "status": "queued",
            },
        )
        enqueued_count += 1

    return enqueued_count


async def emit_scheduled_completion(
    state: dict[str, Any],
    state_manager: Any,
    queues: Any,
) -> dict[str, Any]:
    """Emit completion events for scheduled work and return state cleanup updates."""
    is_scheduled_input = bool(state.get("is_scheduled_input"))
    scheduled_schedule_id = str(state.get("scheduled_schedule_id") or "").strip() or None
    scheduled_run_id = str(state.get("scheduled_run_id") or "").strip() or None
    scheduled_message = str(state.get("scheduled_message") or "").strip() or None
    if not is_scheduled_input:
        return {
            "is_scheduled_input": False,
            "scheduled_schedule_id": None,
            "scheduled_run_id": None,
            "scheduled_message": None,
        }

    response_text = str(state.get("last_cycle_response") or "").strip()
    is_error = str(state.get("health_status_override") or "").strip().lower() == "error"
    result_status = (
        "error" if is_error else ("completed" if response_text else "completed_no_response")
    )
    now = utcnow().isoformat()
    if scheduled_run_id:
        await state_manager.update_schedule_run(
            scheduled_run_id,
            {
                "status": "error" if is_error else "completed",
                "result_status": result_status,
                "response": response_text or None,
                "updated_at": now,
                "completed_at": now,
            },
        )
    if scheduled_schedule_id:
        await state_manager.update_schedule_entry(
            scheduled_schedule_id,
            {
                "last_run_status": result_status,
                "updated_at": now,
                "completed_at": now,
            },
        )
    await queues.push_event(
        "scheduled_response",
        response_text or "(no response)",
        {
            "schedule_id": scheduled_schedule_id,
            "schedule_run_id": scheduled_run_id,
            "schedule_message": scheduled_message,
            "status": result_status,
        },
    )
    await queues.push_event(
        "work_completed",
        f"Completed scheduled work: {scheduled_message or 'Scheduled task'}",
        {
            "work_type": "scheduled",
            "schedule_id": scheduled_schedule_id,
            "schedule_run_id": scheduled_run_id,
            "status": result_status,
        },
    )
    return {
        "is_scheduled_input": False,
        "scheduled_schedule_id": None,
        "scheduled_run_id": None,
        "scheduled_message": None,
        "scheduled_result_status": result_status,
    }
