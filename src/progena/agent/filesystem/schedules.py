"""Schedules capabilities."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.progena.agent.filesystem.base import StateFile
from src.progena.documents.values import JsonDoc
from src.progena.schedules.models import ScheduleEntry
from src.progena.schedules.validation import validate_schedule_document

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

    async def save(doc: JsonDoc) -> None:
        if not isinstance(doc, dict):
            raise ValueError("schedule.json must be a JSON object keyed by schedule id")
        entries: dict[str, ScheduleEntry] = {}
        for entry_id, item in doc.items():
            if not isinstance(item, dict):
                raise ValueError(f"entry '{entry_id}' must be an object")
            fields = {k: v for k, v in item.items() if k != "id"}
            entries[entry_id] = ScheduleEntry(id=entry_id, **fields)

        previous = {
            entry_id: entry.model_dump(mode="json")
            for entry_id, entry in (await state_manager.get_schedule()).items()
        }
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
