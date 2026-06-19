"""Schedule dispatch and completion helpers."""

from __future__ import annotations

from typing import Any

from ..utils import utcnow
from .engine import Scheduler

SCHEDULED_INPUT_PREFIX = "[Scheduled Task]\n"


def _schedule_entries_to_dicts(entries: list[Any]) -> list[dict[str, Any]]:
    return [entry.model_dump() if hasattr(entry, "model_dump") else entry for entry in entries]


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

        queued = await queues.enqueue_input(f"{SCHEDULED_INPUT_PREFIX}{full_message}", timeout=0.5)
        if not queued:
            break

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
                    "last_run_status": "completed",
                    "completed_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            )
        else:
            next_entry.update(
                {
                    "last_run_status": "completed",
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
                    f"Schedule {schedule_id} fired and was marked completed"
                    if is_one_shot
                    else f"Schedule {schedule_id} fired"
                ),
                "schedule_id": schedule_id,
                "event": "schedule_fired",
                "one_shot": is_one_shot,
            }
        )

        await queues.push_event(
            "scheduled_task_triggered",
            f"Scheduled task triggered: {entry.get('message') or schedule_id}",
            {"schedule_id": schedule_id, "one_shot": is_one_shot},
        )
        enqueued_count += 1

    return enqueued_count


async def emit_scheduled_completion(state: dict[str, Any], queues: Any) -> dict[str, Any]:
    """Emit completion events for scheduled work and return state cleanup updates."""
    is_scheduled_input = bool(state.get("is_scheduled_input"))
    scheduled_message = str(state.get("scheduled_message") or "").strip() or None
    if not is_scheduled_input:
        return {"is_scheduled_input": False, "scheduled_message": None}

    response_text = str(state.get("last_cycle_response") or "").strip()
    result_status = "completed" if response_text else "completed_no_response"
    await queues.push_event(
        "scheduled_response",
        response_text or "(no response)",
        {
            "schedule_message": scheduled_message,
            "status": result_status,
        },
    )
    await queues.push_event(
        "work_completed",
        f"Completed scheduled work: {scheduled_message or 'Scheduled task'}",
        {
            "work_type": "scheduled",
            "status": result_status,
        },
    )
    return {
        "is_scheduled_input": False,
        "scheduled_message": None,
        "scheduled_result_status": result_status,
    }
