"""Global lifecycle and page-scoped detail delivery, without polling."""

import asyncio
from unittest.mock import AsyncMock

import pytest

import src.backend.events.stream as events_stream
from src.backend.storage.documents import (
    EVENT_LOG_START,
    PROGENA_STATE_PREFIX,
    CheckpointThreadSnapshot,
    InMemoryDocumentStore,
)
from src.backend.workflows.events import RoutineEvents
from src.progena.routines.events import RUN_EVENT_PREFIX


@pytest.fixture
def feed(monkeypatch):
    import src.backend.workflows.events as module

    store = InMemoryDocumentStore()
    feed = RoutineEvents()
    events = []
    monkeypatch.setattr(module, "get_document_store", lambda: store)
    monkeypatch.setattr(
        events_stream,
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
        store.save_document(PROGENA_STATE_PREFIX, f"{RUN_EVENT_PREFIX}attempt:{status}", payload)
    store.delete_document(PROGENA_STATE_PREFIX, f"{RUN_EVENT_PREFIX}attempt:started")
    logged = store.read_document_events(EVENT_LOG_START, 100)
    # Replaying the log (e.g. dispatcher catch-up) must not re-announce a transition.
    for event in [*logged, *logged]:
        await listener.document_event(event)
    assert [e[0] for e in events] == ["agent.routine.run.started", "agent.routine.run.completed"]
    assert all(not e[2] for e in events)


async def test_detail_only_reads_selected_threads_and_stops_after_unsubscribe(feed, monkeypatch):
    import src.backend.workflows.events as module

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
            PROGENA_STATE_PREFIX,
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
    monkeypatch.setattr(events_stream, "_ui_stream_subscribers", {first: None, other: None})
    monkeypatch.setattr(events_stream, "_ui_stream_clients", {first: "first", other: "other"})
    replay = []
    monkeypatch.setattr(events_stream, "_ui_event_replay", replay)
    events_stream.broadcast_ui_event(
        "agent.routine.run_state.loaded", {"data": {"answer": 9}}, client_id="first"
    )
    assert first.qsize() == 1
    assert other.empty()
    assert not replay
    events_stream.broadcast_ui_event("agent.routine.run.completed", {"agent_id": "demo"})
    assert first.qsize() == 2 and other.qsize() == 1
    assert len(replay) == 1


async def test_control_lifecycle_statuses_publish_and_appear_in_snapshot(feed):
    listener, store, events = feed
    statuses = ["stopping", "cancelling", "stopped", "cancelled"]
    for index, status in enumerate(statuses):
        payload = {
            "agent_id": "demo",
            "thread_id": "routine:demo:revision:session",
            "request_id": f"attempt-{status}",
            "session_id": "session",
            "status": status,
            "timestamp": f"2026-10-26T00:00:0{index}Z",
        }
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}{payload['request_id']}:{status}",
            payload,
        )
        listener.publish_lifecycle(payload)

    assert [event[0] for event in events] == [
        "agent.routine.run.stopping",
        "agent.routine.run.cancelling",
        "agent.routine.run.stopped",
        "agent.routine.run.cancelled",
    ]

    events.clear()
    await listener.lifecycle_snapshot("background")
    snapshot_statuses = {payload["status"] for payload in events[0][1]["runs"]}
    assert set(statuses) <= snapshot_statuses


def _routine_event(store, request_id, status, timestamp, **extra):
    payload = {
        "agent_id": "demo",
        "thread_id": extra.pop("thread_id", f"routine:demo:rev:{request_id}"),
        "request_id": request_id,
        "session_id": request_id,
        "status": status,
        "timestamp": timestamp,
        **extra,
    }
    store.save_document(PROGENA_STATE_PREFIX, f"{RUN_EVENT_PREFIX}{request_id}:{status}", payload)


async def test_snapshot_derives_interrupted_only_for_runs_owned_by_a_gone_runtime(feed):
    listener, store, events = feed
    store.save_document(
        PROGENA_STATE_PREFIX,
        "health",
        {"runtime_instance": "live", "started_at": "2026-10-28T10:00:00+00:00"},
    )
    _routine_event(
        store,
        "dead",
        "started",
        "2026-10-28T09:00:00Z",
        runtime_instance="old",
        inputs={"topic": "btc"},
    )
    _routine_event(store, "live", "started", "2026-10-28T10:05:00Z", runtime_instance="live")
    _routine_event(store, "legacy", "started", "2026-10-28T09:00:00Z")
    _routine_event(store, "waiting", "started", "2026-10-28T09:00:00Z", runtime_instance="old")
    _routine_event(store, "waiting", "paused", "2026-10-28T09:01:00Z")
    _routine_event(store, "done", "started", "2026-10-28T09:00:00Z", runtime_instance="old")
    _routine_event(store, "done", "completed", "2026-10-28T09:02:00Z")

    await listener.lifecycle_snapshot("background")

    runs = {run["request_id"]: run for run in events[0][1]["runs"]}
    assert runs["dead"]["status"] == "interrupted"
    assert runs["dead"]["inputs"] == {"topic": "btc"}
    assert runs["legacy"]["status"] == "interrupted"
    assert runs["live"]["status"] == "started"
    assert runs["waiting"]["status"] == "paused"
    assert runs["done"]["status"] == "completed"
    # Derivation is read-only: no lifecycle document says interrupted.
    stored = [
        doc.payload["status"]
        for _, doc in store.list_documents(PROGENA_STATE_PREFIX, RUN_EVENT_PREFIX)
    ]
    assert "interrupted" not in stored


async def test_snapshot_without_live_runtime_never_guesses_interrupted(feed):
    listener, store, events = feed
    _routine_event(store, "dead", "started", "2026-10-28T09:00:00Z", runtime_instance="old")
    await listener.lifecycle_snapshot("background")
    assert events[0][1]["runs"][0]["status"] == "started"


async def test_resumed_thread_is_described_by_its_newest_attempt(feed):
    from src.backend.workflows.events import routine_lifecycles_by_thread

    _, store, _ = feed
    store.save_document(
        PROGENA_STATE_PREFIX,
        "health",
        {"runtime_instance": "live", "started_at": "2026-10-28T10:00:00+00:00"},
    )
    thread = "routine:demo:rev:session"
    _routine_event(
        store, "first", "started", "2026-10-28T09:00:00Z", thread_id=thread, runtime_instance="old"
    )
    _routine_event(store, "first", "stopped", "2026-10-28T10:01:00Z", thread_id=thread)
    _routine_event(
        store,
        "second",
        "started",
        "2026-10-28T10:02:00Z",
        thread_id=thread,
        runtime_instance="live",
    )

    lifecycles = await routine_lifecycles_by_thread("demo", store)

    assert lifecycles[thread]["request_id"] == "second"
    assert lifecycles[thread]["status"] == "started"
