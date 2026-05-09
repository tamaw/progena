"""Config-backed state helpers for schedule and registry data."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .models import ScheduleEntry


async def get_registry(
    *,
    file_lock: Callable[[str], Any],
    read_json: Callable[[Path], Awaitable[Any]],
    registry_path: Path,
) -> dict[str, Any]:
    async with file_lock("registry"):
        return await read_json(registry_path) or {}


async def save_registry(
    *,
    registry: dict[str, Any],
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
    registry_path: Path,
) -> None:
    async with file_lock("registry"):
        await atomic_write_json(registry_path, registry)
        state_cache["registry"] = registry


async def get_schedule(
    *,
    file_lock: Callable[[str], Any],
    read_json: Callable[[Path], Awaitable[Any]],
    schedule_path: Path,
) -> list[ScheduleEntry]:
    async with file_lock("schedule"):
        data = await read_json(schedule_path)
        return [ScheduleEntry(**item) for item in (data or [])]


async def save_schedule(
    *,
    schedule: list[ScheduleEntry],
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
    schedule_path: Path,
) -> None:
    async with file_lock("schedule"):
        data = [item.model_dump() for item in schedule]
        await atomic_write_json(schedule_path, data)
        state_cache["schedule"] = data


async def add_schedule_entry(
    *,
    entry: Any,
    get_schedule_fn: Callable[[], Awaitable[list[ScheduleEntry]]],
    save_schedule_fn: Callable[[list[ScheduleEntry]], Awaitable[None]],
    log_schedule_event_fn: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    schedule = await get_schedule_fn()
    if hasattr(entry, "model_dump"):
        schedule.append(entry)
        entry_dict = entry.model_dump()
    else:
        schedule.append(ScheduleEntry(**entry))
        entry_dict = entry
    await save_schedule_fn(schedule)
    await log_schedule_event_fn(
        {
            "event": "entry_added",
            "schedule_id": entry_dict.get("id"),
            "description": entry_dict.get("description"),
        }
    )


async def remove_schedule_entry(
    *,
    schedule_id: str,
    get_schedule_fn: Callable[[], Awaitable[list[ScheduleEntry]]],
    save_schedule_fn: Callable[[list[ScheduleEntry]], Awaitable[None]],
    log_schedule_event_fn: Callable[[dict[str, Any]], Awaitable[None]],
) -> bool:
    schedule = await get_schedule_fn()
    filtered = [entry for entry in schedule if entry.id != schedule_id]
    if len(filtered) == len(schedule):
        return False
    await save_schedule_fn(filtered)
    await log_schedule_event_fn({"event": "entry_removed", "schedule_id": schedule_id})
    return True


async def update_schedule_entry(
    *,
    schedule_id: str,
    updates: dict[str, Any],
    get_schedule_fn: Callable[[], Awaitable[list[ScheduleEntry]]],
    save_schedule_fn: Callable[[list[ScheduleEntry]], Awaitable[None]],
    log_schedule_event_fn: Callable[[dict[str, Any]], Awaitable[None]],
) -> bool:
    schedule = await get_schedule_fn()
    for index, entry in enumerate(schedule):
        if entry.id != schedule_id:
            continue
        payload = entry.model_dump()
        payload.update(updates)
        schedule[index] = ScheduleEntry(**payload)
        await save_schedule_fn(schedule)
        await log_schedule_event_fn(
            {
                "event": "entry_updated",
                "schedule_id": schedule_id,
                "updates": list(updates.keys()),
            }
        )
        return True
    return False
