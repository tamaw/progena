"""Global lifecycle and page-scoped detail delivery, without polling."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.backend import ui_routes
from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    CheckpointThreadSnapshot,
    InMemoryDocumentStore,
)
from app.backend.routine_events import RoutineEvents
from app.progenitor.routines.events import RUN_EVENT_PREFIX


@pytest.fixture
def feed(monkeypatch):
    import app.backend.routine_events as module

    store = InMemoryDocumentStore()
    feed = RoutineEvents()
    events = []
    monkeypatch.setattr(module, "get_document_store", lambda: store)
    monkeypatch.setattr(
        ui_routes,
        "broadcast_ui_event",
        lambda kind, payload, **kw: events.append((kind, payload, kw)),
    )
    return feed, store, events


async def test_checkpoint_without_page_subscription_does_not_read_state(feed, monkeypatch):
    listener, store, events = feed
    reader = AsyncMock()
    monkeypatch.setattr(store, "list_routine_runs", reader)
    listener.checkpoint_changed('{"thread_id":"routine:demo:rev:session"}')
    await asyncio.sleep(0)
    reader.assert_not_called()
    assert not events


async def test_global_lifecycle_survives_no_page_subscription(feed):
    listener, store, events = feed
    for status in ["started", "completed"]:
        payload = {
            "agent_id": "demo",
            "request_id": "attempt",
            "session_id": "session",
            "status": status,
        }
        store.save_document(PROGENITOR_STATE_PREFIX, f"{RUN_EVENT_PREFIX}attempt:{status}", payload)
    store.delete_document(PROGENITOR_STATE_PREFIX, f"{RUN_EVENT_PREFIX}attempt:started")
    logged = store.read_document_events(0, 100)
    # Replaying the log (e.g. dispatcher catch-up) must not re-announce a transition.
    for event in [*logged, *logged]:
        await listener.document_event(event)
    assert [e[0] for e in events] == ["agent.routine.run.started", "agent.routine.run.completed"]
    assert all(not e[2] for e in events)


async def test_detail_only_reads_selected_threads_and_stops_after_unsubscribe(feed, monkeypatch):
    import app.backend.routine_events as module

    listener, store, events = feed
    for session in ["one", "two"]:
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                f"routine:demo:rev:{session}",
                "checkpoint",
                "2026-10-07T00:00:00Z",
                [],
            )
        )
    listener.connections.update({"watching", "background"})
    listener.interests["watching"] = {"agent_id": "demo", "thread_id": "routine:demo:rev:one"}
    reader = AsyncMock(return_value={"thread_id": "routine:demo:rev:one", "data": {"answer": 9}})
    monkeypatch.setattr(module, "read_run_state", reader)
    await listener.refresh("demo")
    reader.assert_awaited_once_with("demo", "routine:demo:rev:one")
    assert all(e[2]["client_id"] == "watching" for e in events)
    listener.disconnected("watching")
    await listener.refresh("demo")
    assert reader.await_count == 1


async def test_recovery_snapshot_keeps_latest_attempt_status_without_outputs(feed):
    listener, store, events = feed
    for index, status in enumerate(["enqueued", "started", "completed"]):
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}attempt:{status}",
            {
                "agent_id": "demo",
                "request_id": "attempt",
                "status": status,
                "timestamp": f"2026-10-07T00:00:0{index}Z",
            },
        )
    await listener.lifecycle_snapshot("background")
    assert events[0][1]["runs"][0]["status"] == "completed"
    assert len(events[0][1]["runs"]) == 1
    assert events[0][2]["client_id"] == "background"


async def test_targeted_detail_never_enters_global_replay(monkeypatch):
    first, other = asyncio.Queue(), asyncio.Queue()
    monkeypatch.setattr(ui_routes, "_ui_stream_subscribers", {first: None, other: None})
    monkeypatch.setattr(ui_routes, "_ui_stream_clients", {first: "first", other: "other"})
    replay = []
    monkeypatch.setattr(ui_routes, "_ui_event_replay", replay)
    ui_routes.broadcast_ui_event(
        "agent.routine.run_state.loaded", {"data": {"answer": 9}}, client_id="first"
    )
    assert first.qsize() == 1
    assert other.empty()
    assert not replay
    ui_routes.broadcast_ui_event("agent.routine.run.completed", {"agent_id": "demo"})
    assert first.qsize() == 2 and other.qsize() == 1
    assert len(replay) == 1
