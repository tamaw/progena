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

    def get_document(self, _prefix: str, _key: str, _default: object):
        return SimpleNamespace(payload=self.payload)


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


def test_background_task_snapshot_acknowledges_and_is_thread_scoped(monkeypatch):
    client = TestClient(app)
    queue: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[queue] = None
    events_stream._ui_stream_clients[queue] = "tab-1"
    monkeypatch.setattr(
        background_tasks,
        "get_default_document_store",
        lambda: _Store([_task("owned", "thread-1", "running"), _task("other", "thread-2", "success")]),
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


def test_background_task_document_change_targets_only_the_watched_chat():
    queue: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[queue] = None
    events_stream._ui_stream_clients[queue] = "tab-1"
    background_tasks.background_task_events.subscribe("tab-1", "thread-1")
    try:
        asyncio.run(background_tasks.background_task_events.document_event(SimpleNamespace(event=SimpleNamespace(
            prefix="progena.state",
            key="async_subagent_tasks",
            after=[_task("owned", "thread-1", "error"), _task("other", "thread-2", "running")],
        ))))
        event = queue.get_nowait()["envelope"]
    finally:
        background_tasks.background_task_events.unsubscribe("tab-1")
        events_stream._ui_stream_subscribers.pop(queue, None)
        events_stream._ui_stream_clients.pop(queue, None)

    assert event.type == "chat.background_tasks.updated"
    assert event.payload["thread_id"] == "thread-1"
    assert [(task["task_id"], task["parent_thread_id"], task["status"]) for task in event.payload["tasks"]] == [
        ("owned", "thread-1", "error")
    ]
    assert isinstance(event.payload["revision"], int)
