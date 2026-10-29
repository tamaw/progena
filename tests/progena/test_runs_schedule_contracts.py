"""Schedule run logs consume the scheduler's persisted contract."""

from __future__ import annotations

from datetime import UTC, datetime

import yaml
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from src.progena.agent.filesystem.runs import RunDirectoryBackend
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.schedules.models import ScheduleRunRecord, ScheduleRunStatus
from src.progena.state.manager import StateManager


async def test_native_schedule_error_remains_visible_in_rendered_file() -> None:
    manager = StateManager(LangGraphRuntimeStore(InMemoryStore()))
    await manager.save_preferences_config({"timezone": "UTC"})
    started = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    ended = started.replace(second=1)
    record = ScheduleRunRecord(
        run_id="schedrun_native_error",
        schedule_id="schedule",
        thread_id="schedule:error",
        message="Run failing work",
        status=ScheduleRunStatus.ERROR,
        error="Provider authentication failed",
        created_at=started,
        updated_at=ended,
        started_at=started,
        completed_at=ended,
    )
    await manager.upsert_schedule_run(record)
    backend = RunDirectoryBackend(manager, InMemorySaver())
    result = await backend.aread(
        "/runs/schedules/2026-10-30_schedule_schedrun_native_error.md", 0, 10000
    )
    assert result.error is None
    assert result.file_data is not None
    text = result.file_data["content"]
    front = yaml.safe_load(text.split("---\n")[1])
    assert front["status"] == ScheduleRunStatus.ERROR
    assert front["error"] == record.error
    assert record.error in text.split("---\n", 2)[2]
