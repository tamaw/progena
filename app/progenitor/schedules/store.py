"""Schedules document persistence."""

from __future__ import annotations

from typing import Any

from app.progenitor.persistence.documents import StateDocuments
from app.progenitor.persistence.logs import structured_log_message as _structured_log_message
from app.progenitor.schedules.models import ScheduleEntry, ScheduleRunRecord


class ScheduleStore:
    """Own schedules document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def log_schedule_event(self, event: dict[str, Any]) -> None:
        """Log a schedule event to the runtime store."""
        payload = dict(event)
        await self._access.store.write_log(
            source="schedule",
            message=_structured_log_message("schedule", payload),
            level="info",
            payload=payload,
            timestamp=payload.pop("timestamp", None),
            relative_path="schedule.jsonl",
        )

    @staticmethod
    def _normalize_schedule_payload(payload: Any) -> tuple[dict[str, Any], bool]:
        """Normalize the current object-keyed schedule document.

        Older runtime-store documents used a list of serialized schedule
        entries. Keep those entries readable during the shape migration, while
        rejecting malformed data instead of silently dropping scheduled work.
        """
        if isinstance(payload, dict):
            return payload, False
        if not isinstance(payload, list):
            raise ValueError("schedule document must be an object or legacy entry list")

        normalized: dict[str, Any] = {}
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("legacy schedule entries must be objects")
            entry_id = item.get("id")
            if not isinstance(entry_id, str) or not entry_id.strip():
                raise ValueError("legacy schedule entries must have a non-empty string id")
            if entry_id in normalized:
                raise ValueError(f"duplicate legacy schedule id: {entry_id}")
            normalized[entry_id] = item
        return normalized, True

    async def get_schedule(self) -> dict[str, ScheduleEntry]:
        """Return the schedule document, migrating the legacy list shape."""
        payload = await self._access.store.get_document("schedule", {})
        normalized, migrated = self._normalize_schedule_payload(payload)
        if migrated:
            await self._access.store.save_document("schedule", normalized)
        self._access.cache["schedule"] = normalized
        return {k: ScheduleEntry.model_validate(v) for k, v in normalized.items()}

    async def save_schedule(self, schedule: dict[str, ScheduleEntry]) -> None:
        """Persist the schedule document."""
        payload = {k: v.model_dump(mode="json") for k, v in schedule.items()}
        self._access.cache["schedule"] = payload
        await self._access.store.save_document("schedule", payload)

    async def update_schedule_entry(self, schedule_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to one schedule entry and persist the document."""
        schedule = await self.get_schedule()
        entry = schedule.get(schedule_id)
        if entry is None:
            return False

        payload = entry.model_dump(mode="json")
        payload.update(updates)
        schedule[schedule_id] = ScheduleEntry.model_validate(payload)
        await self.save_schedule(schedule)
        return True

    async def get_schedule_runs(self) -> list[ScheduleRunRecord]:
        """Return all schedule run records."""
        payload = await self._access.get("schedule_runs")
        return [ScheduleRunRecord.model_validate(item) for item in payload]

    async def save_schedule_runs(self, runs: list[ScheduleRunRecord]) -> None:
        """Persist all schedule run records."""
        await self._access.save(
            "schedule_runs",
            [run.model_dump(mode="json") for run in runs],
        )

    async def get_schedule_run(self, run_id: str) -> ScheduleRunRecord | None:
        """Return one schedule run by id, or ``None`` if not found."""
        for run in await self.get_schedule_runs():
            if run.run_id == run_id:
                return run
        return None

    async def upsert_schedule_run(self, run: ScheduleRunRecord) -> None:
        """Insert or update a schedule run record."""
        runs = await self.get_schedule_runs()
        for index, existing in enumerate(runs):
            if existing.run_id == run.run_id:
                runs[index] = run
                break
        else:
            runs.append(run)
        await self.save_schedule_runs(runs)

    async def update_schedule_run(self, run_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to a schedule run. Returns ``True`` if found."""
        runs = await self.get_schedule_runs()
        for index, existing in enumerate(runs):
            if existing.run_id != run_id:
                continue
            payload = existing.model_dump()
            payload.update(updates)
            runs[index] = ScheduleRunRecord(**payload)
            await self.save_schedule_runs(runs)
            return True
        return False
