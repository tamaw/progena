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
        "content_request_id": "queued-request-1",
        "message_id": "queue-message-7",
        "queued_message": {
            "id": "queue-message-7",
            "text": "Queued prompt\n\n<!--progena:attachment-context-->\nAttachment context",
            "attachments": ["attachment-9"],
        },
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
    runtime_client._forward_event(
        {"event_type": "chat_queue_execution_failed", "metadata": metadata}
    )
    runtime_client._forward_event(
        {"event_type": "chat_queue_execution_cancelled", "metadata": metadata}
    )

    assert observed == [
        (
            "chat.response.started",
            {
                "id": "queued-request-1",
                "thread_id": "thread-queue",
                "message_id": "queue-message-7",
                "request_id": "queued-request-1",
                "content_request_id": "queued-request-1",
                "queue_execution": True,
                "queued_message": {
                    "id": "queue-message-7",
                    "text": "Queued prompt",
                    "attachments": ["attachment-9"],
                },
            },
        ),
        (
            "chat.response.completed",
            {
                "id": "queued-request-1",
                "thread_id": "thread-queue",
                "message_id": "queue-message-7",
                "request_id": "queued-request-1",
                "content_request_id": "queued-request-1",
                "queue_execution": True,
                "response": metadata["response"],
                "completed": True,
            },
        ),
        (
            "chat.response.failed",
            {
                "id": "queued-request-1",
                "thread_id": "thread-queue",
                "message_id": "queue-message-7",
                "request_id": "queued-request-1",
                "content_request_id": "queued-request-1",
                "queue_execution": True,
                "response": metadata["response"],
                "error": "Queued response failed",
            },
        ),
        (
            "chat.response.cancelled",
            {
                "id": "queued-request-1",
                "thread_id": "thread-queue",
                "message_id": "queue-message-7",
                "request_id": "queued-request-1",
                "content_request_id": "queued-request-1",
                "queue_execution": True,
                "response": metadata["response"],
                "cancelled": True,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_queued_admission_relays_deferred_content_without_local_terminal():
    request_id = "queued-request-1"

    class RuntimeClient:
        async def send_message_stream_events(self, *args, **kwargs):
            del args, kwargs
            yield {"type": "queued", "request_id": request_id}
            yield {"type": "stream_chunk", "content": "Deferred "}
            yield {
                "type": "stream_event",
                "event_type": "tool_start",
                "content": "lookup",
                "metadata": {"tool_call_id": "call-1"},
            }
            yield {"type": "stream_chunk", "content": "answer"}
            yield {"type": "response_terminal", "status": "completed", "request_id": request_id}

    state = chat_responses._ensure_response_stream(
        request_id, "response-queue-1", "thread-queue", "progena-v1"
    )
    published: list[str] = []
    original_client = lifecycle_state.client
    lifecycle_state.client = RuntimeClient()
    original_broadcast = events_stream.broadcast_ui_event
    events_stream.broadcast_ui_event = lambda event_type, *_args, **_kwargs: published.append(
        event_type
    )
    try:
        await chat_responses._run_response_event_stream(
            message="Queued prompt",
            model="progena-v1",
            request_id=request_id,
            response_id="response-queue-1",
            thread_id="thread-queue",
            stream=True,
        )
    finally:
        lifecycle_state.client = original_client
        events_stream.broadcast_ui_event = original_broadcast
        chat_responses._response_streams.pop(request_id, None)

    assert [frame.event_type for frame in state.frames] == [
        "chat.response.delta",
        "chat.response.event",
        "chat.response.delta",
    ]
    assert [frame.payload.get("delta", frame.payload.get("content")) for frame in state.frames] == [
        "Deferred ",
        "lookup",
        "answer",
    ]
    assert state.terminal_reached is True
    assert published == []


@pytest.mark.asyncio
async def test_queued_cancellation_closes_content_without_duplicate_terminal_lifecycle():
    request_id = "queued-cancel-1"
    queued = asyncio.Event()
    release_terminal = asyncio.Event()

    class RuntimeClient:
        async def send_message_stream_events(self, *args, **kwargs):
            del args, kwargs
            yield {"type": "queued", "request_id": request_id}
            queued.set()
            await release_terminal.wait()
            yield {"type": "response_terminal", "status": "incomplete", "request_id": request_id}

    state = chat_responses._ensure_response_stream(
        request_id, "response-cancel-1", "thread-queue", "progena-v1"
    )
    published: list[str] = []
    original_client = lifecycle_state.client
    original_broadcast = events_stream.broadcast_ui_event
    lifecycle_state.client = RuntimeClient()
    events_stream.broadcast_ui_event = lambda event_type, *_args, **_kwargs: published.append(
        event_type
    )
    try:
        runner = asyncio.create_task(
            chat_responses._run_response_event_stream(
                message="Queued prompt",
                model="progena-v1",
                request_id=request_id,
                response_id="response-cancel-1",
                thread_id="thread-queue",
                stream=True,
            )
        )
        await queued.wait()
        chat_responses._active_response_streams[request_id].set()
        release_terminal.set()
        await runner
    finally:
        lifecycle_state.client = original_client
        events_stream.broadcast_ui_event = original_broadcast
        chat_responses._response_streams.pop(request_id, None)
        chat_responses._active_response_streams.pop(request_id, None)

    assert state.terminal_reached is True
    assert published == []


@pytest.mark.asyncio
async def test_runtime_client_follows_queued_response_stream_after_admission():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                content=(
                    b"event: response.completed\n"
                    b'data: {"type":"response.completed","response":{"status":"queued"}}\n\n'
                ),
            )
        return httpx.Response(
            200,
            content=(
                b"event: response.in_progress\n"
                b'data: {"type":"response.in_progress"}\n\n'
                b"event: response.output_text.delta\n"
                b'data: {"type":"response.output_text.delta","delta":"Deferred",'
                b'"runtime_stream_seq":4}\n\n'
                b"event: response.completed\n"
                b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
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

    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[1].url.path == "/v1/responses/queued-request-1/stream"
    assert events == [
        {
            "type": "response_started",
            "request_id": "queued-request-1",
            "queue_execution": True,
        },
        {
            "type": "stream_chunk",
            "content": "Deferred",
            "is_final": False,
            "request_id": "queued-request-1",
            "runtime_emitted_at_ms": None,
            "runtime_stream_seq": 4,
            "queue_execution": True,
        },
        {
            "type": "response_terminal",
            "status": "completed",
            "response": {"status": "completed"},
            "request_id": "queued-request-1",
            "queue_execution": True,
        },
    ]


@pytest.mark.asyncio
async def test_runtime_start_recovers_deferred_content_relay_after_gateway_restart():
    request_id = "recovered-request-1"
    content_request_id = "runtime-response-1"
    follow_calls: list[str] = []

    class RuntimeClient:
        async def follow_response_stream_events(self, response_id: str):
            assert response_id == content_request_id
            follow_calls.append(response_id)
            yield {"type": "stream_chunk", "content": "Recovered"}
            yield {"type": "response_terminal", "status": "completed"}

    original_client = lifecycle_state.client
    lifecycle_state.client = RuntimeClient()
    try:
        chat_responses.handle_runtime_queue_lifecycle(
            "chat.response.started",
            {
                "id": request_id,
                "thread_id": "thread-queue",
                "request_id": request_id,
                "content_request_id": content_request_id,
                "message_id": "queue-message-7",
                "queue_execution": True,
                "queued_message": {"id": "queue-message-7", "text": "Recovered", "attachments": []},
            },
        )
        chat_responses.handle_runtime_queue_lifecycle(
            "chat.response.started",
            {
                "id": request_id,
                "thread_id": "thread-queue",
                "request_id": request_id,
                "content_request_id": content_request_id,
                "message_id": "queue-message-7",
                "queue_execution": True,
                "queued_message": {"id": "queue-message-7", "text": "Recovered", "attachments": []},
            },
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        state = chat_responses._response_streams[request_id]
    finally:
        lifecycle_state.client = original_client
        chat_responses._response_streams.pop(request_id, None)
        chat_responses._response_activities.pop(request_id, None)
        chat_responses._active_response_streams.pop(request_id, None)

    assert [frame.payload["delta"] for frame in state.frames] == ["Recovered"]
    assert state.terminal_reached is True
    assert follow_calls == [content_request_id]


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
