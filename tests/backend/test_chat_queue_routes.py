"""Behavioral coverage for the runtime-owned chat queue browser adapter."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import src.backend.chat.responses as chat_responses
import src.backend.events.stream as events_stream
import src.backend.lifecycle.state as lifecycle_state
from src.backend.config import ChatServerConfig
from src.backend.integrations.runtime import RuntimeQueueError, SSEProgenaClient
from src.backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _snapshot(revision: int = 4) -> dict[str, object]:
    return {
        "conversation_id": "thread-queue",
        "revision": revision,
        "messages": [
            {
                "id": "queue-message-7",
                "text": "Queued prompt",
                "attachments": ["attachment-9"],
                "status": "queued",
                "created_at": "2026-10-31T00:00:00Z",
                "request_id": "originating-request",
            }
        ],
    }


def test_queue_snapshot_acknowledges_and_emits_correlated_reconnect_snapshot(client):
    class RuntimeClient:
        async def get_chat_queue(self, conversation_id: str):
            assert conversation_id == "thread-queue"
            return _snapshot()

    original_client = lifecycle_state.client
    subscriber: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[subscriber] = None
    lifecycle_state.client = RuntimeClient()
    try:
        first = client.get(
            "/api/chat/sessions/thread-queue/queue", headers={"X-Operation-Id": "queue-load-1"}
        )
        second = client.get(
            "/api/chat/sessions/thread-queue/queue", headers={"X-Operation-Id": "queue-load-2"}
        )
        events = [subscriber.get_nowait()["envelope"] for _ in range(subscriber.qsize())]
    finally:
        lifecycle_state.client = original_client
        events_stream._ui_stream_subscribers.pop(subscriber, None)

    assert first.status_code == second.status_code == 202
    assert first.json() == {"request_id": "queue-load-1", "event_types": ["chat.queue.updated"]}
    assert second.json() == {"request_id": "queue-load-2", "event_types": ["chat.queue.updated"]}
    assert [event.type for event in events] == ["chat.queue.updated", "chat.queue.updated"]
    assert [event.payload["request_id"] for event in events] == ["queue-load-1", "queue-load-2"]
    assert all(event.session_id == "thread-queue" for event in events)
    assert all(event.payload["messages"][0]["id"] == "queue-message-7" for event in events)


def test_queue_action_preserves_ids_order_revision_and_command_correlation(client):
    class RuntimeClient:
        received: tuple[str, dict[str, object]] | None = None

        async def apply_chat_queue_action(self, conversation_id: str, action: dict[str, object]):
            self.received = (conversation_id, action)
            return _snapshot(revision=5)

    runtime_client = RuntimeClient()
    original_client = lifecycle_state.client
    subscriber: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[subscriber] = None
    lifecycle_state.client = runtime_client
    try:
        response = client.post(
            "/api/chat/sessions/thread-queue/queue/actions",
            headers={"X-Operation-Id": "queue-action-1"},
            json={
                "action": "reorder",
                "order": ["queue-message-7", "queue-message-2"],
                "revision": 4,
                "command_id": "command-queue-1",
            },
        )
        event = subscriber.get_nowait()["envelope"]
    finally:
        lifecycle_state.client = original_client
        events_stream._ui_stream_subscribers.pop(subscriber, None)

    assert response.status_code == 202
    assert response.json() == {
        "request_id": "queue-action-1",
        "event_types": ["chat.queue.updated"],
    }
    assert runtime_client.received == (
        "thread-queue",
        {
            "action": "reorder",
            "order": ["queue-message-7", "queue-message-2"],
            "revision": 4,
            "command_id": "command-queue-1",
        },
    )
    assert event.payload["revision"] == 5
    assert event.payload["request_id"] == "queue-action-1"


def test_queued_submission_started_event_preserves_active_content_owner(client):
    class RuntimeClient:
        async def send_message_stream_events(self, *args, **kwargs):
            del args, kwargs
            yield {"type": "queued", "request_id": "queued-request-b"}

    original_client = lifecycle_state.client
    subscriber: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[subscriber] = None
    chat_responses._register_response_activity("foreground-request-a", "thread-queue")
    lifecycle_state.client = RuntimeClient()
    try:
        response = client.post(
            "/api/ui/responses",
            headers={"X-Operation-Id": "queued-request-b"},
            json={
                "model": "progena-v1",
                "input": "Queued prompt",
                "stream": True,
                "metadata": {"thread_id": "thread-queue"},
            },
        )
        event = subscriber.get_nowait()["envelope"]
    finally:
        lifecycle_state.client = original_client
        chat_responses._clear_response_activity("foreground-request-a")
        events_stream._ui_stream_subscribers.pop(subscriber, None)

    assert response.status_code == 202
    assert event.type == "chat.response.started"
    assert event.payload["request_id"] == "queued-request-b"
    assert event.payload["content_request_id"] == "foreground-request-a"


def test_queue_revision_conflict_emits_no_success_snapshot(client):
    class RuntimeClient:
        async def apply_chat_queue_action(self, conversation_id: str, action: dict[str, object]):
            del conversation_id, action
            raise RuntimeQueueError(409, "Queue revision is stale", snapshot=_snapshot(revision=8))

    original_client = lifecycle_state.client
    subscriber: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[subscriber] = None
    lifecycle_state.client = RuntimeClient()
    try:
        response = client.post(
            "/api/chat/sessions/thread-queue/queue/actions",
            headers={"X-Operation-Id": "queue-conflict-1"},
            json={
                "action": "send_now",
                "message_id": "queue-message-7",
                "revision": 4,
                "command_id": "command-conflict-1",
            },
        )
    finally:
        lifecycle_state.client = original_client
        events_stream._ui_stream_subscribers.pop(subscriber, None)

    assert response.status_code == 409
    assert response.json() == {"detail": "Queue revision is stale"}
    assert subscriber.empty()


def test_runtime_queue_event_translates_to_canonical_snapshot_without_losing_ids():
    observed: list[tuple[str, dict[str, object]]] = []
    runtime_client = SSEProgenaClient.__new__(SSEProgenaClient)
    runtime_client._event_callback = lambda event_type, payload: observed.append(
        (event_type, payload)
    )

    runtime_client._forward_event(
        {
            "event_type": "chat_queue_updated",
            "content": "",
            "metadata": _snapshot(),
        }
    )

    assert observed == [("chat.queue.updated", _snapshot())]


def test_runtime_deferred_queue_events_translate_to_correlated_lifecycle():
    observed: list[tuple[str, dict[str, object]]] = []
    runtime_client = SSEProgenaClient.__new__(SSEProgenaClient)
    runtime_client._event_callback = lambda event_type, payload: observed.append(
        (event_type, payload)
    )
    metadata = {
        "conversation_id": "thread-queue",
        "request_id": "queued-request-1",
        "message_id": "queue-message-7",
        "response": {"status": "completed", "output": "Deferred answer"},
    }

    runtime_client._forward_event(
        {
            "event_type": "chat_queue_execution_started",
            "metadata": {key: value for key, value in metadata.items() if key != "response"},
        }
    )
    runtime_client._forward_event(
        {"event_type": "chat_queue_execution_completed", "metadata": metadata}
    )

    assert observed == [
        (
            "chat.response.started",
            {
                "id": "queued-request-1",
                "thread_id": "thread-queue",
                "message_id": "queue-message-7",
                "request_id": "queued-request-1",
                "queue_execution": True,
            },
        ),
        (
            "chat.response.completed",
            {
                "id": "queued-request-1",
                "thread_id": "thread-queue",
                "message_id": "queue-message-7",
                "response": metadata["response"],
                "request_id": "queued-request-1",
                "queue_execution": True,
                "completed": True,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_queued_initial_response_never_emits_terminal_completion():
    class RuntimeClient:
        async def send_message_stream_events(self, *args, **kwargs):
            del args, kwargs
            yield {"type": "queued", "request_id": "queued-request-1"}

    original_client = lifecycle_state.client
    subscriber: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[subscriber] = None
    lifecycle_state.client = RuntimeClient()
    try:
        await chat_responses._run_response_event_stream(
            message="Queued prompt",
            model="progena-v1",
            request_id="queued-request-1",
            response_id="response-queue-1",
            thread_id="thread-queue",
            stream=True,
        )
        events = [subscriber.get_nowait()["envelope"] for _ in range(subscriber.qsize())]
    finally:
        lifecycle_state.client = original_client
        events_stream._ui_stream_subscribers.pop(subscriber, None)

    assert events == []


@pytest.mark.asyncio
async def test_runtime_client_marks_queued_response_without_terminal_chunk():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b"event: response.completed\n"
                b'data: {"type":"response.completed","response":{"status":"queued"}}\n\n'
            ),
        )

    runtime_client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    runtime_client._started = True
    runtime_client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        events = [
            event
            async for event in runtime_client.send_message_stream_events(
                "Queued prompt", request_id="queued-request-1"
            )
        ]
    finally:
        await runtime_client.stop()

    assert events == [{"type": "queued", "request_id": "queued-request-1"}]


@pytest.mark.asyncio
async def test_runtime_queue_client_preserves_action_payload_and_conflict_snapshot():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "POST":
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "queue_revision_conflict",
                        "message": "Queue revision is stale",
                    },
                    "snapshot": _snapshot(revision=9),
                },
            )
        return httpx.Response(200, json=_snapshot())

    runtime_client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    runtime_client._started = True
    runtime_client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        snapshot = await runtime_client.get_chat_queue("thread-queue")
        with pytest.raises(RuntimeQueueError) as raised:
            await runtime_client.apply_chat_queue_action(
                "thread-queue",
                {
                    "action": "delete",
                    "message_id": "queue-message-7",
                    "revision": 4,
                    "command_id": "queue-command-7",
                },
            )
    finally:
        await runtime_client.stop()

    assert snapshot == _snapshot()
    assert captured[0].method == "GET"
    assert captured[0].url.path == "/v1/conversations/thread-queue/queue"
    assert captured[1].method == "POST"
    assert captured[1].url.path == "/v1/conversations/thread-queue/queue/actions"
    assert json.loads(captured[1].content) == {
        "action": "delete",
        "message_id": "queue-message-7",
        "revision": 4,
        "command_id": "queue-command-7",
    }
    assert raised.value.status_code == 409
    assert raised.value.snapshot == _snapshot(revision=9)
