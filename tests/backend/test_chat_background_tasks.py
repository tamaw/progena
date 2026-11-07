"""Chat-scoped durable async-subagent browser projection tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

import src.backend.chat.background_tasks as background_tasks
import src.backend.events.stream as events_stream
from src.backend.main import app


class _Store:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self.payload = payload

    def list_documents(self, _prefix: str, key_prefix: str):
        return [
            (f"{key_prefix}{task['task_id']}", SimpleNamespace(payload=task))
            for task in self.payload
        ]


def _task(task_id: str, parent_thread_id: str, status: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "agent_name": "researcher",
        "assistant_id": "researcher",
        "agent_url": "https://agent.test",
        "parent_thread_id": parent_thread_id,
        "run_id": f"run-{task_id}",
        "status": status,
        "created_at": "2026-11-03T00:00:00Z",
        "updated_at": "2026-11-03T00:01:00Z",
    }


def _document_event(seq: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(
            prefix="progena.state",
            key="async_subagent_task:owned",
            seq=seq,
        )
    )


def test_background_task_snapshot_acknowledges_and_is_thread_scoped(monkeypatch):
    client = TestClient(app)
    queue: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[queue] = None
    events_stream._ui_stream_clients[queue] = "tab-1"
    monkeypatch.setattr(
        background_tasks,
        "get_default_document_store",
        lambda: _Store(
            [_task("owned", "thread-1", "running"), _task("other", "thread-2", "success")]
        ),
    )
    try:
        response = client.get(
            "/api/ui/chats/thread-1/background-tasks?client_id=tab-1",
            headers={"X-Operation-Id": "background-load-1"},
        )
        event = queue.get_nowait()["envelope"]
    finally:
        events_stream._ui_stream_subscribers.pop(queue, None)
        events_stream._ui_stream_clients.pop(queue, None)

    assert response.status_code == 202
    assert response.json() == {
        "request_id": "background-load-1",
        "event_types": ["chat.background_tasks.snapshot"],
    }
    assert event.type == "chat.background_tasks.snapshot"
    assert event.session_id == "thread-1"
    assert event.payload["request_id"] == "background-load-1"
    assert [task["task_id"] for task in event.payload["tasks"]] == ["owned"]


def test_background_task_document_change_reads_current_store_and_targets_watched_chat():
    queue: asyncio.Queue = asyncio.Queue()
    store = _Store([_task("owned", "thread-1", "error"), _task("other", "thread-2", "running")])
    events = background_tasks.BackgroundTaskEvents(lambda: store)
    events_stream._ui_stream_subscribers[queue] = None
    events_stream._ui_stream_clients[queue] = "tab-1"
    events.subscribe("tab-1", "thread-1")
    try:
        asyncio.run(events.document_event(_document_event()))
        event = queue.get_nowait()["envelope"]
    finally:
        events.unsubscribe("tab-1")
        events_stream._ui_stream_subscribers.pop(queue, None)
        events_stream._ui_stream_clients.pop(queue, None)

    assert event.type == "chat.background_tasks.updated"
    assert event.payload["thread_id"] == "thread-1"
    assert [
        (task["task_id"], task["parent_thread_id"], task["status"])
        for task in event.payload["tasks"]
    ] == [("owned", "thread-1", "error")]
    assert isinstance(event.payload["revision"], int)


def test_late_background_task_event_does_not_regress_snapshot_state():
    queue: asyncio.Queue = asyncio.Queue()
    store = _Store([_task("owned", "thread-1", "success")])
    events = background_tasks.BackgroundTaskEvents(lambda: store)
    events_stream._ui_stream_subscribers[queue] = None
    events_stream._ui_stream_clients[queue] = "tab-1"
    events.subscribe("tab-1", "thread-1")
    tasks, revision = asyncio.run(events.snapshot(store, "thread-1"))
    try:
        asyncio.run(events.document_event(_document_event(seq=42)))
        event = queue.get_nowait()["envelope"]
    finally:
        events.unsubscribe("tab-1")
        events_stream._ui_stream_subscribers.pop(queue, None)
        events_stream._ui_stream_clients.pop(queue, None)

    assert [(task["task_id"], task["status"]) for task in tasks] == [("owned", "success")]
    assert revision == 0
    assert [(task["task_id"], task["status"]) for task in event.payload["tasks"]] == [
        ("owned", "success")
    ]
    assert event.payload["revision"] == 42
