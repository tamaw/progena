"""Behavioral regressions for compact per-task persistence."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from langgraph.store.memory import InMemoryStore

from src.progena.a2a.models import A2ATaskRecord, A2ATaskStatus
from src.progena.a2a.store import A2A_TASK_KEY_PREFIX
from src.progena.a2a.tasks import upsert_tracked_task_from_payload
from src.progena.async_subagents.models import AsyncSubagentTaskRecord, AsyncSubagentTaskStatus
from src.progena.async_subagents.store import ASYNC_SUBAGENT_TASK_KEY_PREFIX
from src.progena.async_subagents.tasks import refresh_tracked_async_subagent_task
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.state.manager import StateManager
from src.progena.utils import utcnow


def _manager() -> tuple[StateManager, LangGraphRuntimeStore]:
    runtime = LangGraphRuntimeStore(InMemoryStore())
    return StateManager(runtime), runtime


async def test_legacy_task_singletons_migrate_to_compact_records_and_hydrate_evidence():
    manager, runtime = _manager()
    a2a_payload = {
        "id": "a2a-legacy",
        "contextId": "context-1",
        "status": {"state": "working", "message": {"parts": [{"text": "working"}]}},
        "large": "x" * 32_000,
    }
    async_payload = {"status": "running", "large": "y" * 32_000}
    await runtime.save_document(
        "a2a_tasks",
        [
            A2ATaskRecord(
                task_id="a2a-legacy",
                agent_name="agent",
                status=A2ATaskStatus.WORKING,
                latest_task=a2a_payload,
            ).model_dump(mode="json")
        ],
    )
    await runtime.save_document(
        "async_subagent_tasks",
        [
            AsyncSubagentTaskRecord(
                task_id="async-legacy",
                agent_name="agent",
                assistant_id="assistant",
                agent_url="http://agent",
                run_id="run-1",
                latest_run=async_payload,
            ).model_dump(mode="json")
        ],
    )

    a2a_records = await manager.get_a2a_tasks()
    async_records = await manager.get_async_subagent_tasks()

    assert await runtime.peek_document("a2a_tasks") is None
    compact_a2a = await runtime.peek_document(f"{A2A_TASK_KEY_PREFIX}a2a-legacy")
    compact_async = await runtime.peek_document(f"{ASYNC_SUBAGENT_TASK_KEY_PREFIX}async-legacy")
    assert len(json.dumps(compact_a2a).encode()) < 24 * 1024
    assert len(json.dumps(compact_async).encode()) < 24 * 1024
    assert await runtime.peek_document("async_subagent_tasks") is None
    assert a2a_records[0].latest_task is None
    assert async_records[0].latest_run is None
    assert "latest_task" in a2a_records[0].evidence_refs
    assert "latest_run" in async_records[0].evidence_refs
    migrated_a2a = await manager.get_a2a_task("a2a-legacy")
    migrated_async = await manager.get_async_subagent_task("async-legacy")
    assert migrated_a2a is not None and migrated_a2a.latest_task == a2a_payload
    assert migrated_async is not None and migrated_async.latest_run == async_payload


async def test_interrupted_legacy_migration_retries_without_overwriting_newer_record():
    manager, runtime = _manager()
    old = utcnow() - timedelta(days=1)
    await runtime.save_document(
        "a2a_tasks",
        [
            A2ATaskRecord(
                task_id="resume",
                agent_name="agent",
                status=A2ATaskStatus.WORKING,
                latest_task={"status": "working"},
                updated_at=old,
            ).model_dump(mode="json")
        ],
    )
    original_delete = runtime.delete_document

    async def interrupt_retirement(*_args, **_kwargs):
        raise RuntimeError("simulated interruption")

    runtime.delete_document = interrupt_retirement  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="simulated interruption"):
        await manager.get_a2a_tasks()

    newer = A2ATaskRecord(
        task_id="resume",
        agent_name="agent",
        status=A2ATaskStatus.COMPLETED,
        latest_task={"status": "completed"},
        updated_at=utcnow(),
    )
    await runtime.save_document(f"{A2A_TASK_KEY_PREFIX}resume", newer.model_dump(mode="json"))
    runtime.delete_document = original_delete  # type: ignore[method-assign]

    resumed = await manager.get_a2a_task("resume")

    assert resumed is not None
    assert resumed.status == A2ATaskStatus.COMPLETED
    assert await runtime.peek_document("a2a_tasks") is None


async def test_unchanged_successful_polls_do_not_rewrite_task_records():
    manager, runtime = _manager()
    payload = {"id": "a2a-noop", "status": {"state": "working"}, "result": {"value": 1}}
    persisted = await upsert_tracked_task_from_payload(
        manager,
        task_payload=payload,
        agent_name="agent",
        agent_config={"url": "http://agent"},
        source="a2a_send",
        request_text="do work",
    )
    assert persisted.latest_task is None
    writes = 0
    original_save = runtime.save_document

    async def count_state_writes(name, value):
        nonlocal writes
        writes += 1
        await original_save(name, value)

    runtime.save_document = count_state_writes  # type: ignore[method-assign]
    await upsert_tracked_task_from_payload(
        manager,
        task_payload=payload,
        agent_name="agent",
        agent_config={"url": "http://agent"},
        source="a2a_send",
        request_text="do work",
    )

    assert writes == 0

    await manager.update_a2a_task(
        "a2a-noop", {"last_error": "temporary failure", "updated_at": utcnow()}
    )
    writes = 0
    await manager.update_a2a_task(
        "a2a-noop", {"last_error": "temporary failure", "updated_at": utcnow()}
    )
    assert writes == 0

    large_error = "e" * 32_000
    await manager.update_a2a_task("a2a-noop", {"last_error": large_error, "updated_at": utcnow()})
    compact = await runtime.peek_document(f"{A2A_TASK_KEY_PREFIX}a2a-noop")
    restored = await manager.get_a2a_task("a2a-noop")
    assert len(json.dumps(compact).encode()) < 24 * 1024
    assert restored is not None and restored.last_error == large_error


async def test_async_unchanged_successful_poll_does_not_rewrite_task_record():
    manager, runtime = _manager()
    await manager.upsert_async_subagent_task(
        AsyncSubagentTaskRecord(
            task_id="async-noop",
            agent_name="agent",
            assistant_id="assistant",
            agent_url="http://agent",
            run_id="run-1",
            latest_run={"status": "running"},
        )
    )
    writes = 0
    original_save = runtime.save_document

    async def count_state_writes(name, value):
        nonlocal writes
        writes += 1
        await original_save(name, value)

    class FakeClient:
        class runs:
            @staticmethod
            async def get(thread_id, run_id):
                return {"status": "running"}

    runtime.save_document = count_state_writes  # type: ignore[method-assign]
    with patch("src.progena.async_subagents.tasks.get_client", return_value=FakeClient()):
        await refresh_tracked_async_subagent_task(manager, "async-noop")

    assert writes == 0


async def test_per_record_updates_preserve_concurrent_fields_and_other_task_bodies():
    manager, runtime = _manager()
    first = A2ATaskRecord(task_id="first", agent_name="agent", latest_task={"body": "first"})
    second = A2ATaskRecord(task_id="second", agent_name="agent", latest_task={"body": "second"})
    await manager.upsert_a2a_task(first)
    await manager.upsert_a2a_task(second)
    untouched_before = await runtime.peek_document(f"{A2A_TASK_KEY_PREFIX}second")

    await asyncio.gather(
        manager.update_a2a_task("first", {"summary": "complete", "updated_at": utcnow()}),
        manager.update_a2a_task("first", {"last_error": "transient", "updated_at": utcnow()}),
    )

    updated = await manager.get_a2a_task("first")
    assert updated is not None
    assert updated.summary == "complete"
    assert updated.last_error == "transient"
    assert await runtime.peek_document(f"{A2A_TASK_KEY_PREFIX}second") == untouched_before


async def test_terminal_retention_archives_full_evidence_but_never_prunes_unnotified_tasks():
    manager, runtime = _manager()
    old = utcnow() - timedelta(days=31)
    payload = {"id": "retained-evidence", "large": "z" * 32_000}
    await manager.upsert_a2a_task(
        A2ATaskRecord(
            task_id="retained-evidence",
            agent_name="agent",
            status=A2ATaskStatus.COMPLETED,
            latest_task=payload,
            completed_at=old,
            notification_sent_at=old,
            updated_at=old,
        )
    )
    await manager.upsert_async_subagent_task(
        AsyncSubagentTaskRecord(
            task_id="unnotified",
            agent_name="agent",
            assistant_id="assistant",
            agent_url="http://agent",
            run_id="run-1",
            status=AsyncSubagentTaskStatus.SUCCESS,
            latest_run={"status": "success"},
            completed_at=old,
            updated_at=old,
            notify_on_completion=True,
        )
    )

    assert await manager.get_a2a_tasks() == []
    assert await manager.get_a2a_task_evidence("retained-evidence") == payload
    assert [task.task_id for task in await manager.get_async_subagent_tasks()] == ["unnotified"]
    assert await runtime.peek_document(f"{A2A_TASK_KEY_PREFIX}retained-evidence") is None
    assert await runtime.peek_document(f"{ASYNC_SUBAGENT_TASK_KEY_PREFIX}unnotified") is not None


@pytest.mark.parametrize("kind", ["a2a", "async"])
async def test_replayed_migration_cannot_overwrite_per_task_updates_with_equal_timestamp(kind):
    manager, runtime = _manager()
    if kind == "a2a":
        record = A2ATaskRecord(task_id="migration-replay", agent_name="agent")
        legacy_key = "a2a_tasks"
        get_task = manager.get_a2a_task
        update_task = manager.update_a2a_task
        terminal_status = "completed"
    else:
        record = AsyncSubagentTaskRecord(
            task_id="migration-replay",
            agent_name="agent",
            assistant_id="assistant",
            agent_url="http://agent",
            run_id="run-1",
        )
        legacy_key = "async_subagent_tasks"
        get_task = manager.get_async_subagent_task
        update_task = manager.update_async_subagent_task
        terminal_status = "success"
    legacy_payload = [record.model_dump(mode="json")]
    await runtime.save_document(legacy_key, legacy_payload)
    assert await get_task(record.task_id) is not None
    assert await update_task(record.task_id, {"status": terminal_status})
    # Simulate retry after a partially completed migration: the source still
    # exists, but a live writer already changed the authoritative task row.
    await runtime.save_document(legacy_key, legacy_payload)
    restored = await get_task(record.task_id)
    assert restored is not None and restored.status == terminal_status
    assert restored.updated_at == record.updated_at


@pytest.mark.parametrize("kind", ["a2a", "async"])
async def test_shorter_replacement_summary_does_not_restore_stale_archived_value(kind):
    manager, _runtime = _manager()
    if kind == "a2a":
        record = A2ATaskRecord(task_id="new-summary", agent_name="agent", summary="old" * 4000)
        upsert = manager.upsert_a2a_task
        get_task = manager.get_a2a_task
    else:
        record = AsyncSubagentTaskRecord(
            task_id="new-summary",
            agent_name="agent",
            assistant_id="assistant",
            agent_url="http://agent",
            run_id="run-1",
            summary="old" * 4000,
        )
        upsert = manager.upsert_async_subagent_task
        get_task = manager.get_async_subagent_task
    await upsert(record)
    restored = await get_task(record.task_id)
    assert restored is not None and restored.summary == record.summary
    restored.summary = "new summary"
    restored.updated_at = utcnow()
    await upsert(restored)
    replaced = await get_task(record.task_id)
    assert replaced is not None and replaced.summary == "new summary"


async def test_replacement_progress_does_not_restore_stale_archived_log():
    manager, _runtime = _manager()
    old_progress = [
        {"state": "working", "message_text": f"step {i}", "agent_name": "agent"} for i in range(40)
    ]
    await manager.upsert_a2a_task(
        A2ATaskRecord(task_id="new-progress", agent_name="agent", progress_log=old_progress)
    )
    restored = await manager.get_a2a_task("new-progress")
    assert restored is not None and restored.progress_log == old_progress
    restored.progress_log = [
        {"state": "completed", "message_text": "final result", "agent_name": "agent"}
    ]
    restored.updated_at = utcnow()
    await manager.upsert_a2a_task(restored)
    replaced = await manager.get_a2a_task("new-progress")
    assert replaced is not None and replaced.progress_log == restored.progress_log


async def test_async_legacy_naive_terminal_timestamps_do_not_block_initialization():
    manager, runtime = _manager()
    old = (utcnow() - timedelta(days=31)).replace(tzinfo=None)
    legacy = AsyncSubagentTaskRecord(
        task_id="naive-legacy",
        agent_name="agent",
        assistant_id="assistant",
        agent_url="http://agent",
        run_id="run-1",
        status=AsyncSubagentTaskStatus.SUCCESS,
        latest_run={"status": "success"},
        completed_at=old,
        updated_at=old,
        notification_sent_at=old,
    ).model_dump(mode="json")
    # Preserve the historical timezone-naive representation in storage.
    for field in ("completed_at", "updated_at", "notification_sent_at"):
        legacy[field] = old.isoformat()
    await runtime.save_document("async_subagent_tasks", [legacy])
    await manager.initialize()
    assert await manager.get_async_subagent_tasks() == []
    assert await manager.get_async_subagent_task_evidence("naive-legacy") == {"status": "success"}
