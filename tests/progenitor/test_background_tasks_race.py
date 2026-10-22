"""Regression test for cross-process lost update on background_tasks queue.

Before the fix, concurrent backend enqueues and runtime dequeues can lose or
duplicate tasks because both processes read-modify-write the shared JSONB
document without serialization.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.backend.storage.documents import PROGENITOR_STATE_PREFIX, PostgresDocumentStore
from src.backend.workflows.routine_runs import append_background_task
from src.progenitor.orchestration.state import BackgroundTaskEntry
from src.progenitor.persistence.langgraph import _open_store
from src.progenitor.persistence.store import LangGraphRuntimeStore
from src.progenitor.state.manager import StateManager

_CONCURRENCY_TIMEOUT = 15


@pytest.fixture
async def pg_stores(postgres_url):
    """Provide a backend document store and runtime state manager on an isolated database."""
    doc_store = PostgresDocumentStore(postgres_url)
    doc_store.open()

    native_store, pool = await _open_store(postgres_url)
    runtime_store = LangGraphRuntimeStore(native_store)
    state_manager = StateManager(runtime_store=runtime_store, database_url=postgres_url)
    await state_manager.initialize()

    try:
        yield doc_store, state_manager
    finally:
        await state_manager.close()
        await pool.close()
        doc_store.close()


@pytest.mark.asyncio
async def test_concurrent_backend_and_runtime_enqueues_no_lost_updates(pg_stores):
    """10 backend enqueues and 10 runtime enqueues running concurrently must all survive."""
    doc_store, state_manager = pg_stores

    def backend_enqueue(i: int) -> None:
        append_background_task(
            doc_store,
            {
                "id": f"be-{i}",
                "content": f"backend task {i}",
                "metadata": {},
                "created_at": "2024-01-01T00:00:00Z",
            },
        )

    async def runtime_enqueue(i: int) -> None:
        await state_manager.enqueue_background_task(
            BackgroundTaskEntry(
                id=f"rt-{i}",
                content=f"runtime task {i}",
                metadata={},
                created_at="2024-01-01T00:00:00Z",
            )
        )

    executor = ThreadPoolExecutor(max_workers=10)
    try:
        backend_futures = [executor.submit(backend_enqueue, i) for i in range(10)]
        runtime_tasks = [asyncio.create_task(runtime_enqueue(i)) for i in range(10)]
        completed = await asyncio.wait_for(
            asyncio.gather(
                *runtime_tasks,
                *(asyncio.wrap_future(f) for f in backend_futures),
                return_exceptions=True,
            ),
            timeout=_CONCURRENCY_TIMEOUT,
        )
        assert not [result for result in completed if isinstance(result, BaseException)]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    final = doc_store.get_document(PROGENITOR_STATE_PREFIX, "background_tasks", [])
    payload = list(final.payload) if final is not None else []
    ids = {t["id"] for t in payload}
    expected = {f"be-{i}" for i in range(10)} | {f"rt-{i}" for i in range(10)}
    assert ids == expected, (
        f"Missing: {expected - ids}, Extra: {ids - expected}, Count: {len(payload)}"
    )


@pytest.mark.asyncio
async def test_concurrent_enqueue_and_dequeue_no_duplicates_or_loss(pg_stores):
    """Race five backend enqueues, five runtime enqueues and five dequeues after ten seeds."""
    doc_store, state_manager = pg_stores

    def backend_enqueue(i: int) -> None:
        append_background_task(
            doc_store,
            {
                "id": f"be-{i}",
                "content": f"backend task {i}",
                "metadata": {},
                "created_at": "2024-01-01T00:00:00Z",
            },
        )

    async def runtime_enqueue(i: int) -> None:
        await state_manager.enqueue_background_task(
            BackgroundTaskEntry(
                id=f"rt-{i}",
                content=f"runtime task {i}",
                metadata={},
                created_at="2024-01-01T00:00:00Z",
            )
        )

    async def runtime_dequeue() -> str | None:
        task = await state_manager.dequeue_background_task()
        return task.id if task else None

    for i in range(10):
        backend_enqueue(i)

    executor = ThreadPoolExecutor(max_workers=5)
    try:
        enqueue_futures = [executor.submit(backend_enqueue, i + 10) for i in range(5)]
        runtime_enqueue_tasks = [asyncio.create_task(runtime_enqueue(i)) for i in range(5)]
        dequeue_tasks = [asyncio.create_task(runtime_dequeue()) for _ in range(5)]
        completed = await asyncio.wait_for(
            asyncio.gather(
                *runtime_enqueue_tasks,
                *dequeue_tasks,
                *(asyncio.wrap_future(f) for f in enqueue_futures),
                return_exceptions=True,
            ),
            timeout=_CONCURRENCY_TIMEOUT,
        )
        assert not [result for result in completed if isinstance(result, BaseException)]
        dequeued_ids = [
            task_id
            for task_id in completed[
                len(runtime_enqueue_tasks) : len(runtime_enqueue_tasks) + len(dequeue_tasks)
            ]
            if task_id is not None
        ]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    final = doc_store.get_document(PROGENITOR_STATE_PREFIX, "background_tasks", [])
    payload = list(final.payload) if final is not None else []
    final_ids = {t["id"] for t in payload}

    # Should have exactly 15 remaining (10 seeded + 5 backend + 5 runtime - 5 dequeued)
    expected_remaining = {f"be-{i}" for i in range(15)} | {f"rt-{i}" for i in range(5)}
    expected_remaining -= set(dequeued_ids)
    assert final_ids == expected_remaining, (
        f"Missing: {expected_remaining - final_ids}; extra: {final_ids - expected_remaining}"
    )
    assert len(final_ids) == 15, f"Expected 15 remaining, got {len(final_ids)}: {final_ids}"
    assert len(dequeued_ids) == 5, f"Expected 5 dequeued, got {len(dequeued_ids)}: {dequeued_ids}"
    assert len(set(dequeued_ids)) == len(dequeued_ids), f"Duplicate dequeues: {dequeued_ids}"
