"""Schedules capabilities."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.progena.agent.filesystem.base import StateFile
from src.progena.cron import timezone_name
from src.progena.documents.values import JsonDoc
from src.progena.schedules.engine import parse_schedule_datetime
from src.progena.schedules.models import ScheduleEntry
from src.progena.schedules.validation import validate_schedule_document
from src.progena.sessions.context import get_current_thread_id

logger = logging.getLogger(__name__)


def create_schedule_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``schedule.json`` virtual file backed by StateManager.

    ``StateManager.get_schedule()``/``save_schedule()`` already use the same
    object-keyed-by-id shape this file exposes to the agent. The presentation
    layer omits ``id`` from each value because the object key is authoritative.
    """

    async def load() -> JsonDoc:
        entries = await state_manager.get_schedule()
        return {
            entry_id: {k: v for k, v in entry.model_dump(mode="json").items() if k != "id"}
            for entry_id, entry in entries.items()
        }

    async def current_conversation_id() -> str | None:
        """The chat the agent is answering right now, or ``None`` outside a chat.

        Only a thread with a ``session:`` document is a chat; schedule-run and
        system threads have none, so schedules created there stay standalone.
        """
        thread_id = get_current_thread_id()
        if thread_id and await state_manager.get_session(thread_id) is not None:
            return thread_id
        return None

    async def save(doc: JsonDoc) -> None:
        if not isinstance(doc, dict):
            raise ValueError("schedule.json must be a JSON object keyed by schedule id")
        previous_entries = await state_manager.get_schedule()
        previous = {
            entry_id: entry.model_dump(mode="json") for entry_id, entry in previous_entries.items()
        }
        preference_timezone = timezone_name(
            (await state_manager.get_preferences_config()).get("timezone")
        )
        conversation_id = await current_conversation_id()
        entries: dict[str, ScheduleEntry] = {}
        for entry_id, item in doc.items():
            if not isinstance(item, dict):
                raise ValueError(f"entry '{entry_id}' must be an object")
            fields = {k: v for k, v in item.items() if k != "id"}
            scheduled_at = fields.get("scheduled_at")
            if isinstance(scheduled_at, str):
                parsed_scheduled_at = parse_schedule_datetime(scheduled_at)
                if parsed_scheduled_at is not None:
                    fields["scheduled_at"] = parsed_scheduled_at.isoformat()
            if entry_id not in previous and not fields.get("timezone"):
                fields["timezone"] = preference_timezone
            prior = previous.get(entry_id)
            if prior is not None:
                # An existing schedule keeps its destination: the agent cannot retarget it.
                if fields.get("conversation_id", prior.get("conversation_id")) != prior.get(
                    "conversation_id"
                ):
                    raise ValueError(f"entry '{entry_id}' conversation_id cannot be changed")
                fields["conversation_id"] = prior.get("conversation_id")
            elif "conversation_id" not in fields:
                # A new schedule delivers to the chat that created it.
                fields["conversation_id"] = conversation_id
            elif fields["conversation_id"] not in (None, conversation_id):
                raise ValueError(
                    f"entry '{entry_id}' conversation_id is assigned by the runtime; "
                    "omit it, or set null for a standalone schedule"
                )
            entries[entry_id] = ScheduleEntry(id=entry_id, **fields)
        await state_manager.save_schedule(entries)

        seen_ids: set[str] = set()
        for entry_id, entry in entries.items():
            seen_ids.add(entry_id)
            current = entry.model_dump(mode="json")
            prior = previous.get(entry_id)
            if prior is None:
                logger.info("Schedule entry added: %s", entry_id)
            elif prior != current:
                changed = sorted(key for key in current if prior.get(key) != current.get(key))
                logger.info("Schedule entry updated: %s (%s)", entry_id, ", ".join(changed))
        for removed_id in previous.keys() - seen_ids:
            logger.info("Schedule entry removed: %s", removed_id)

    return StateFile(
        name="schedule.json",
        load=load,
        save=save,
        validate=validate_schedule_document,
        on_change=on_change,
    )
