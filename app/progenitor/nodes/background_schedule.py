"""Background schedule nodes.

These nodes are used by the background job graph (not the user-chat outer loop).
They:
- Find due schedule entries
- Materialize one scheduled instruction into `current_input`
- Advance/persist the schedule after running

No backward-compatibility paths: scheduled execution only flows through the
background job graph.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

from ..llm.prompts import PromptTemplates
from ..runtime.scheduler import Scheduler
from ..runtime.time_utils import utcnow
from ..state.models import ProgenitorState

_prompts = PromptTemplates()


def create_pick_due_node(
    timezone: str,
    state_manager,
    event_pusher: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    scheduler = Scheduler(timezone=timezone)

    async def pick_due_node(state: ProgenitorState) -> ProgenitorState:
        current_time = utcnow()

        schedule_entries = await state_manager.get_schedule()
        schedule_dicts: list[dict[str, Any]] = [
            e.model_dump() if hasattr(e, "model_dump") else e for e in schedule_entries
        ]

        due_items = scheduler.get_due_items(schedule_dicts, current_time)
        if not due_items:
            return {
                "current_time": current_time,
                "bg_had_work": False,
                "bg_schedule_id": None,
            }

        entry = due_items[0]
        schedule_id = str(entry["id"])
        instruction = entry.get("instruction") or entry.get("description") or "Scheduled task"

        prefix = _prompts.get("scheduled_prefix.md").strip()
        scheduled_input = f"{prefix}\n\n{instruction}".strip()

        if event_pusher:
            await event_pusher(
                "work_started",
                "Processing scheduled work",
                {"work_type": "scheduled", "schedule_id": schedule_id},
            )

        return cast(ProgenitorState, {
            "current_time": current_time,
            "bg_had_work": True,
            "bg_schedule_id": schedule_id,
            "current_input": scheduled_input,
            "current_input_role": "user",
            "current_input_metadata": {
                "type": "scheduled",
                "schedule_id": schedule_id,
                "schedule_description": entry.get("description"),
                "schedule_run_at": entry.get("run_at"),
                "schedule_repeat_minutes": entry.get("repeat_minutes"),
            },
        })

    return pick_due_node


def create_advance_node(
    timezone: str,
    state_manager,
    event_pusher: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    scheduler = Scheduler(timezone=timezone)

    async def advance_node(state: ProgenitorState) -> ProgenitorState:
        schedule_id = state.get("bg_schedule_id")
        if not schedule_id:
            return {}

        schedule_entries = await state_manager.get_schedule()
        schedule_dicts: list[dict[str, Any]] = [
            e.model_dump() if hasattr(e, "model_dump") else e for e in schedule_entries
        ]

        updated_schedule = scheduler.advance_recurring(
            schedule_dicts,
            schedule_id,
            utcnow(),
        )

        entry = next((item for item in updated_schedule if str(item.get("id")) == str(schedule_id)), None)
        if not entry:
            return {
                "bg_schedule_id": None,
            }

        updates: dict[str, Any] = {"last_run": entry.get("last_run")}
        if entry.get("repeat_minutes"):
            updates["run_at"] = entry.get("run_at")

        await state_manager.update_schedule_entry(schedule_id, updates)

        response_text = state.get("last_cycle_response")
        if event_pusher and isinstance(response_text, str) and response_text.strip():
            await event_pusher(
                "scheduled_response",
                response_text,
                {
                    "schedule_id": schedule_id,
                    "schedule_description": entry.get("description"),
                    "schedule_run_at": entry.get("run_at"),
                },
            )

        if event_pusher:
            await event_pusher(
                "work_completed",
                "Completed scheduled work",
                {"work_type": "scheduled", "schedule_id": schedule_id},
            )

        return {
            "bg_schedule_id": None,
            "bg_had_work": False,
        }

    return advance_node
