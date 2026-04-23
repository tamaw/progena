"""Schedule dispatch nodes for the background job graph.

Two nodes handle the full scheduled-task lifecycle:

  create_schedule_dispatch_node
    Entry point for every background cycle. Fetches the schedule, finds the
    first due item, and either:
      - Routes to the scheduled path (sets current_input from the message)
      - Routes to the backlog path (bg_work_type = "backlog")
      - Routes to END (no work)

  create_schedule_finalize_node
    Runs after the scheduled chat path (intake → chat → respond) completes.
    Responsibilities:
      - Advances recurring task next_run (or deletes one-off entry)
      - Always pushes the result back to the user, including failure/empty cases
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

from ..llm.prompts import PromptTemplates
from ..runtime.scheduler import SCHEDULER_ERROR_METADATA_KEY, Scheduler
from ..runtime.time_utils import utcnow
from ..state.helpers import get_actionable_items
from ..state.models import ProgenitorState

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()


def create_schedule_dispatch_node(
    timezone: str,
    state_manager,
    event_pusher: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the dispatch node.

    Fetches the live schedule from state_manager, finds the first due entry,
    and materialises work for the background cycle.

    Returns state updates with:
      bg_had_work   : True if work was found
      bg_work_type  : "scheduled" | "backlog" | None
      bg_schedule_id: ID of the chosen schedule entry (scheduled path only)
      current_input : scheduled message text (scheduled path only)
    """
    scheduler = Scheduler(timezone=timezone)

    async def schedule_dispatch_node(state: ProgenitorState) -> ProgenitorState:
        current_time = utcnow()

        schedule_entries = await state_manager.get_schedule()
        schedule_dicts: list[dict[str, Any]] = [
            e.model_dump() if hasattr(e, "model_dump") else e for e in schedule_entries
        ]

        due_items = scheduler.get_due_items(schedule_dicts, current_time)
        for schedule_id, updates in scheduler.drain_pending_updates(schedule_dicts):
            await state_manager.update_schedule_entry(schedule_id, updates)
            if event_pusher:
                scheduler_error = (
                    updates.get("metadata", {}).get(SCHEDULER_ERROR_METADATA_KEY)
                    if isinstance(updates.get("metadata"), dict)
                    else None
                )
                await event_pusher(
                    "scheduled_task_paused",
                    f"Paused scheduled task {schedule_id} due to an invalid cron expression.",
                    {
                        "schedule_id": schedule_id,
                        "reason": "invalid_cron_expression",
                        "error": scheduler_error,
                    },
                )

        if not due_items:
            # Nothing scheduled — check whether the backlog has pending items.
            backlog = state.get("backlog", [])
            if get_actionable_items(backlog):
                return cast(
                    ProgenitorState,
                    {
                        "current_time": current_time,
                        "bg_had_work": True,
                        "bg_schedule_id": None,
                        "bg_work_type": "backlog",
                    },
                )
            return cast(
                ProgenitorState,
                {
                    "current_time": current_time,
                    "bg_had_work": False,
                    "bg_schedule_id": None,
                    "bg_work_type": None,
                },
            )

        entry = due_items[0]
        schedule_id = str(entry["id"])
        message = entry.get("message") or "Scheduled task"
        user_request = (entry.get("user_request") or "").strip()
        await state_manager.update_schedule_entry(
            schedule_id,
            {"last_fired_at": current_time.isoformat()},
        )

        prefix = _prompts.get("progenitor/scheduled_prefix.md").strip()
        if user_request:
            scheduled_input = f"{prefix}\n\n<original_message>{user_request}</original_message>\n\n{message}".strip()
        else:
            scheduled_input = f"{prefix}\n\n{message}".strip()

        if event_pusher:
            await event_pusher(
                "work_started",
                "Processing scheduled work",
                {"work_type": "scheduled", "schedule_id": schedule_id},
            )

        return cast(
            ProgenitorState,
            {
                "current_time": current_time,
                "bg_had_work": True,
                "bg_schedule_id": schedule_id,
                "bg_work_type": "scheduled",
                "current_input": scheduled_input,
                "current_input_role": "user",
                "current_input_metadata": {
                    "type": "scheduled",
                    "schedule_id": schedule_id,
                    "schedule_message": entry.get("message"),
                    "schedule_cron_expression": entry.get("cron_expression"),
                },
            },
        )

    return schedule_dispatch_node


def create_schedule_finalize_node(
    timezone: str,
    state_manager,
    event_pusher: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the finalize node.

    Runs after the scheduled chat path completes (regardless of success/failure).
    Responsibilities:
      - One-shot expressions: deleted immediately from storage after execution
      - Recurring expressions: last_run set after execution
      - Result event   : always pushed back to the user (empty response is
                         reported explicitly rather than silently dropped)
    """
    scheduler = Scheduler(timezone=timezone)

    async def schedule_finalize_node(state: ProgenitorState) -> ProgenitorState:
        schedule_id = state.get("bg_schedule_id")
        if not schedule_id:
            return {}

        schedule_entries = await state_manager.get_schedule()
        schedule_dicts: list[dict[str, Any]] = [
            e.model_dump() if hasattr(e, "model_dump") else e for e in schedule_entries
        ]

        # Locate the entry before mutating
        entry = next(
            (e for e in schedule_dicts if str(e.get("id")) == str(schedule_id)),
            None,
        )
        if not entry:
            logger.warning("schedule_finalize: entry %s not found — already removed?", schedule_id)
            return {
                "bg_schedule_id": None,
                "bg_had_work": False,
                "bg_work_type": None,
            }

        cron_expression = str(entry.get("cron_expression") or "")
        is_one_shot = scheduler.is_one_shot_expression(cron_expression)

        if is_one_shot:
            removed = await state_manager.remove_schedule_entry(schedule_id)
            logger.info(
                "schedule_finalize: removed one-shot entry %s (removed=%s)",
                schedule_id,
                removed,
            )
        else:
            completed_at = utcnow().isoformat()
            await state_manager.update_schedule_entry(
                schedule_id,
                {
                    "last_run": completed_at,
                    "last_fired_at": completed_at,
                },
            )
            logger.info("schedule_finalize: marked recurring entry %s as executed", schedule_id)

        # Always report the result back, even if empty or failed.
        response_text: str = str(state.get("pending_response") or "").strip()
        result_status = "completed" if response_text.strip() else "completed_no_response"

        if event_pusher:
            event_content = (
                response_text
                if response_text.strip()
                else "Scheduled task completed, but the agent returned no response text."
            )
            await event_pusher(
                "scheduled_response",
                event_content,
                {
                    "schedule_id": schedule_id,
                    "schedule_message": entry.get("message"),
                    "is_recurring": not is_one_shot,
                    "status": result_status,
                },
            )
            await event_pusher(
                "work_completed",
                f"Completed scheduled work: {entry.get('message', schedule_id)}",
                {"work_type": "scheduled", "schedule_id": schedule_id, "status": result_status},
            )

        return {"bg_schedule_id": None}
        # bg_had_work is intentionally preserved — the background worker uses it
        # after the graph cycle to reset exponential backoff.

    return schedule_finalize_node
