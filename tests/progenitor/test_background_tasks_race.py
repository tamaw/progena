"""Regression test for cross-process lost update on background_tasks queue.

Before the fix, concurrent backend enqueues and runtime dequeues can lose or
duplicate tasks because both processes read-modify-write the shared JSONB
document without serialization.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.backend.agent_catalog_routes import _append_background_task_locked
from app.backend.document_store import PROGENITOR_STATE_PREFIX, PostgresDocumentStore
from app.progenitor.database.langgraph import _open_store
from app.progenitor.state.langgraph_store import LangGraphRuntimeStore
from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import BackgroundTaskEntry

# Opt-in and deliberately NOT PROGENITOR_DATABASE_URL (which .env points at the
# dev database): this test rewrites the shared background_tasks queue, which a
# running runtime would race with.
DATABASE_URL = (os.getenv("PROGENITOR_TEST_DATABASE_URL") or "").strip() or None

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="requires PROGENITOR_TEST_DATABASE_URL (a scratch database)"
)


@pytest.fixture
async def pg_stores():
    """Provide a backend document store and a runtime state manager backed by real Postgres."""
    assert DATABASE_URL is not None
    doc_store = PostgresDocumentStore(DATABASE_URL)
    doc_store.open()

    native_store, pool = await _open_store(DATABASE_URL)
    runtime_store = LangGraphRuntimeStore(native_store)
    state_manager = StateManager(runtime_store=runtime_store, database_url=DATABASE_URL)
    await state_manager.initialize()

    # Start from an empty queue, restoring whatever the database held afterwards.
    original = doc_store.get_document(PROGENITOR_STATE_PREFIX, "background_tasks", [])
    doc_store.save_document(PROGENITOR_STATE_PREFIX, "background_tasks", [])

    yield doc_store, state_manager

    restored = list(original.payload) if original is not None else []
    doc_store.save_document(PROGENITOR_STATE_PREFIX, "background_tasks", restored)
    await state_manager.close()
    await pool.close()
    doc_store.close()


@pytest.mark.asyncio
async def test_concurrent_backend_and_runtime_enqueues_no_lost_updates(pg_stores):
    """10 backend enqueues and 10 runtime enqueues running concurrently must all survive."""
    doc_store, state_manager = pg_stores

    def backend_enqueue(i: int) -> None:
        _append_background_task_locked(
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

    with ThreadPoolExecutor(max_workers=10) as executor:
        backend_futures = [executor.submit(backend_enqueue, i) for i in range(10)]
        runtime_tasks = [asyncio.create_task(runtime_enqueue(i)) for i in range(10)]

        await asyncio.gather(*runtime_tasks)
        for f in backend_futures:
            f.result()

    final = doc_store.get_document(PROGENITOR_STATE_PREFIX, "background_tasks", [])
    payload = list(final.payload) if final is not None else []
    ids = {t["id"] for t in payload}
    expected = {f"be-{i}" for i in range(10)} | {f"rt-{i}" for i in range(10)}
    assert ids == expected, (
        f"Missing: {expected - ids}, Extra: {ids - expected}, Count: {len(payload)}"
    )


@pytest.mark.asyncio
async def test_concurrent_enqueue_and_dequeue_no_duplicates_or_loss(pg_stores):
    """Enqueue 10 tasks, then concurrently enqueue 5 more and dequeue 5.
    Exactly 15 tasks should remain (no loss or duplication) and the 5 dequeued IDs should be unique."""
    doc_store, state_manager = pg_stores

    def backend_enqueue(i: int) -> None:
        _append_background_task_locked(
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

    # Seed with 10 backend tasks
    for i in range(10):
        backend_enqueue(i)

    # Concurrently enqueue 5 runtime tasks and dequeue 5 tasks
    with ThreadPoolExecutor(max_workers=5) as executor:
        enqueue_futures = [executor.submit(backend_enqueue, i + 10) for i in range(5)]
        runtime_enqueue_tasks = [asyncio.create_task(runtime_enqueue(i)) for i in range(5)]
        dequeue_tasks = [asyncio.create_task(runtime_dequeue()) for _ in range(5)]

        await asyncio.gather(*runtime_enqueue_tasks)
        for f in enqueue_futures:
            f.result()
        dequeued_ids = [tid for tid in await asyncio.gather(*dequeue_tasks) if tid is not None]

    final = doc_store.get_document(PROGENITOR_STATE_PREFIX, "background_tasks", [])
    payload = list(final.payload) if final is not None else []
    final_ids = {t["id"] for t in payload}

    # Should have exactly 15 remaining (10 seeded + 5 backend + 5 runtime - 5 dequeued)
    expected_remaining = {f"be-{i}" for i in range(15)} | {f"rt-{i}" for i in range(5)}
    expected_remaining -= set(dequeued_ids)
    assert len(final_ids) == 15, f"Expected 15 remaining, got {len(final_ids)}: {final_ids}"
    assert len(dequeued_ids) == 5, f"Expected 5 dequeued, got {len(dequeued_ids)}: {dequeued_ids}"
    assert len(set(dequeued_ids)) == len(dequeued_ids), f"Duplicate dequeues: {dequeued_ids}"
