"""Scheduled work polling and enqueueing for the runtime loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .scheduler import Scheduler
from .time_utils import utcnow

logger = logging.getLogger(__name__)

SCHEDULED_INPUT_PREFIX = "[Scheduled Task]\n"


class ScheduledWorkRunner:
    """Polls schedule entries and enqueues due work."""

    def __init__(
        self,
        *,
        config: Any,
        state_manager: Any,
        enqueue_input: Callable[[str, float | None], Awaitable[bool]],
        push_event: Callable[[str, str, dict[str, Any] | None], Awaitable[None]],
        is_running: Callable[[], bool],
    ) -> None:
        self._config = config
        self._state_manager = state_manager
        self._enqueue_input = enqueue_input
        self._push_event = push_event
        self._is_running = is_running

    def is_enabled(self) -> bool:
        scheduler_cfg = getattr(self._config, "scheduler", None)
        if scheduler_cfg is not None and hasattr(scheduler_cfg, "enabled"):
            return bool(scheduler_cfg.enabled)
        return False

    def poll_seconds(self) -> float:
        scheduler_cfg = getattr(self._config, "scheduler", None)
        if scheduler_cfg is not None and hasattr(scheduler_cfg, "poll_seconds"):
            try:
                return max(1.0, float(scheduler_cfg.poll_seconds))
            except (TypeError, ValueError):
                return 30.0
        return 30.0

    async def check_and_enqueue(self, scheduler: Scheduler) -> int:
        schedule_entries = await self._state_manager.get_schedule()
        schedule_dicts: list[dict[str, Any]] = [
            entry.model_dump() if hasattr(entry, "model_dump") else entry
            for entry in schedule_entries
        ]
        due_items = scheduler.get_due_items(schedule_dicts, current_time=utcnow())
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
            user_request = (entry.get("user_request") or "").strip()

            if user_request:
                full_message = f"<original_message>{user_request}</original_message>\n\n{message}"
            else:
                full_message = message

            queued = await self._enqueue_input(
                f"{SCHEDULED_INPUT_PREFIX}{full_message}",
                0.5,
            )
            if not queued:
                break

            cron_expression = str(entry.get("cron_expression") or "")
            is_one_shot = scheduler.is_one_shot_expression(cron_expression)
            if is_one_shot:
                await self._state_manager.remove_schedule_entry(schedule_id)
            else:
                await self._state_manager.update_schedule_entry(
                    schedule_id,
                    {"last_run": now.isoformat()},
                )

            await self._push_event(
                "scheduled_task_triggered",
                f"Scheduled task triggered: {entry.get('message') or schedule_id}",
                {"schedule_id": schedule_id, "one_shot": is_one_shot},
            )
            enqueued_count += 1

        return enqueued_count

    async def run_loop(self) -> None:
        scheduler = Scheduler(timezone=getattr(self._config.runtime, "timezone", "UTC"))
        poll_seconds = self.poll_seconds()
        while self._is_running():
            try:
                await self.check_and_enqueue(scheduler)
                await asyncio.sleep(poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Scheduler loop error: %s", e, exc_info=True)
                await asyncio.sleep(poll_seconds)
