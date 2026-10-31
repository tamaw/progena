"""Behavioral coverage for the runtime-owned chat queue browser adapter."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import src.backend.chat.responses as chat_responses
import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.integrations.runtime as runtime_integration
import src.backend.lifecycle.state as lifecycle_state
from src.backend.chat.models import AgentResponse
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
async def test_queued_admission_reconciles_missed_runtime_lifecycle():
    request_id = "queued-request-1"

    class RuntimeClient:
        async def send_message_stream_events(self, *args, **kwargs):
            del args, kwargs
            yield {
                "type": "response_started",
                "request_id": request_id,
                "queue_execution": True,
            }
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
    chat_responses._response_execution_tasks[request_id] = asyncio.current_task()
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
        chat_responses._response_execution_tasks.pop(request_id, None)
        chat_responses._queue_started_request_ids.discard(request_id)
        chat_responses._queue_terminal_request_ids.discard(request_id)

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
    assert published == ["chat.response.started", "chat.response.completed"]


@pytest.mark.asyncio
async def test_queued_cancellation_cleans_relay_after_terminal_interleaving():
    request_id = "queued-cancel-1"
    queued = asyncio.Event()
    release_terminal = asyncio.Event()

    class RuntimeClient:
        async def send_message_stream_events(self, *args, **kwargs):
            del args, kwargs
            queued.set()
            await release_terminal.wait()
            yield {"type": "response_started", "request_id": request_id, "queue_execution": True}
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
    chat_responses._response_execution_tasks[request_id] = asyncio.current_task()
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
        assert request_id not in chat_responses._active_response_streams
        assert (
            chat_responses.handle_runtime_queue_lifecycle(
                events_contracts.UiEvent.CHAT_RESPONSE_CANCELLED,
                {
                    "request_id": request_id,
                    "thread_id": "thread-queue",
                    "queue_execution": True,
                },
            )
            is False
        )
    finally:
        lifecycle_state.client = original_client
        events_stream.broadcast_ui_event = original_broadcast
        chat_responses._response_streams.pop(request_id, None)
        chat_responses._response_execution_tasks.pop(request_id, None)
        chat_responses._queue_terminal_request_ids.discard(request_id)
        chat_responses._terminal_response_request_ids.discard(request_id)

    assert state.terminal_reached is True
    assert published.count(events_contracts.UiEvent.CHAT_RESPONSE_STARTED) == 0
    assert published.count(events_contracts.UiEvent.CHAT_RESPONSE_CANCELLED) == 1


@pytest.mark.asyncio
async def test_deferred_cancel_relays_delta_until_runtime_terminal():
    request_id = "queued-cancel-after-start"
    started = asyncio.Event()
    release_delta = asyncio.Event()
    delta_consumed = asyncio.Event()
    release_terminal = asyncio.Event()

    class RuntimeClient:
        async def send_message_stream_events(self, *args, **kwargs):
            del args, kwargs
            yield {"type": "response_started", "request_id": request_id, "queue_execution": True}
            started.set()
            await release_delta.wait()
            yield {"type": "stream_chunk", "content": "late delta"}
            delta_consumed.set()
            await release_terminal.wait()
            yield {"type": "response_terminal", "status": "cancelled", "request_id": request_id}

    state = chat_responses._ensure_response_stream(
        request_id, "response-cancel-after-start", "thread-queue", "progena-v1"
    )
    published: list[events_contracts.UiEvent] = []
    original_client = lifecycle_state.client
    original_broadcast = events_stream.broadcast_ui_event
    lifecycle_state.client = RuntimeClient()
    events_stream.broadcast_ui_event = lambda event_type, *_args, **_kwargs: published.append(
        event_type
    )
    chat_responses._response_execution_tasks[request_id] = asyncio.current_task()
    try:
        runner = asyncio.create_task(
            chat_responses._run_response_event_stream(
                message="Queued prompt",
                model="progena-v1",
                request_id=request_id,
                response_id="response-cancel-after-start",
                thread_id="thread-queue",
                stream=True,
            )
        )
        await started.wait()
        chat_responses._active_response_streams[request_id].set()
        release_delta.set()
        await delta_consumed.wait()
        assert [frame.payload.get("delta") for frame in state.frames] == ["late delta"]
        assert published == [events_contracts.UiEvent.CHAT_RESPONSE_STARTED]
        release_terminal.set()
        await runner
    finally:
        lifecycle_state.client = original_client
        events_stream.broadcast_ui_event = original_broadcast
        chat_responses._response_streams.pop(request_id, None)
        chat_responses._response_execution_tasks.pop(request_id, None)
        chat_responses._queue_started_request_ids.discard(request_id)
        chat_responses._queue_terminal_request_ids.discard(request_id)
        chat_responses._terminal_response_request_ids.discard(request_id)

    assert published == [
        events_contracts.UiEvent.CHAT_RESPONSE_STARTED,
        events_contracts.UiEvent.CHAT_RESPONSE_CANCELLED,
    ]


def test_runtime_terminal_suppresses_late_queued_start():
    request_id = "queued-terminal-before-start"
    terminal = {
        "request_id": request_id,
        "thread_id": "thread-queue",
        "queue_execution": True,
    }
    start = {
        **terminal,
        "id": "response-terminal-before-start",
        "content_request_id": request_id,
    }
    try:
        assert (
            chat_responses.handle_runtime_queue_lifecycle(
                events_contracts.UiEvent.CHAT_RESPONSE_CANCELLED, terminal
            )
            is True
        )
        assert (
            chat_responses.handle_runtime_queue_lifecycle(
                events_contracts.UiEvent.CHAT_RESPONSE_STARTED, start
            )
            is False
        )
        assert request_id not in chat_responses._response_activities
        assert request_id not in chat_responses._active_response_streams
    finally:
        chat_responses._queue_started_request_ids.discard(request_id)
        chat_responses._queue_terminal_request_ids.discard(request_id)
        chat_responses._terminal_response_request_ids.discard(request_id)


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
async def test_nonstream_deferred_execution_emits_one_start_and_terminal():
    request_id = "queued-nonstream-1"

    class RuntimeClient:
        async def send_message(self, *args, **kwargs):
            del args, kwargs
            return AgentResponse(
                response="not-delivered",
                deferred=True,
                deferred_started=True,
                terminal_status="completed",
            )

    published: list[events_contracts.UiEvent] = []
    original_client = lifecycle_state.client
    original_broadcast = events_stream.broadcast_ui_event
    lifecycle_state.client = RuntimeClient()
    events_stream.broadcast_ui_event = lambda event_type, *_args, **_kwargs: published.append(
        event_type
    )
    chat_responses._response_execution_tasks[request_id] = asyncio.current_task()
    try:
        await chat_responses._run_response_event_stream(
            message="Queued prompt",
            model="progena-v1",
            request_id=request_id,
            response_id="response-nonstream-1",
            thread_id="thread-queue",
            stream=False,
        )
    finally:
        lifecycle_state.client = original_client
        events_stream.broadcast_ui_event = original_broadcast
        chat_responses._response_execution_tasks.pop(request_id, None)
        chat_responses._queue_started_request_ids.discard(request_id)
        chat_responses._queue_terminal_request_ids.discard(request_id)

    assert published == [
        events_contracts.UiEvent.CHAT_RESPONSE_STARTED,
        events_contracts.UiEvent.CHAT_RESPONSE_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_runtime_send_message_rejects_failed_deferred_terminal():
    def handler(request: httpx.Request) -> httpx.Response:
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
                b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                b"event: response.failed\n"
                b'data: {"type":"response.failed","response":{"error":{"message":"failed"}}}\n\n'
            ),
        )

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(RuntimeError, match="failed"):
            await client.send_message("Queued prompt", request_id="failed-queued-request")
    finally:
        await client.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("item_type", ["response_terminal", "stream_resync_required"])
async def test_text_projection_rejects_failed_or_desynchronized_deferred_stream(item_type):
    async def events():
        yield {"type": "stream_chunk", "content": "partial"}
        if item_type == "response_terminal":
            yield {"type": item_type, "status": "failed", "error": "failed"}
        else:
            yield {"type": item_type}

    with pytest.raises(RuntimeError):
        _ = [chunk async for chunk in chat_responses._stream_text_chunks(events())]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError],
)
async def test_runtime_follower_retries_transient_transport_failures(error_type):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error_type("temporary failure", request=request)
        return httpx.Response(
            200,
            content=(
                b"event: response.completed\n"
                b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
        )

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        events = [event async for event in client.follow_response_stream_events("response-1")]
    finally:
        await client.stop()

    assert attempts == 2
    assert events[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_runtime_follower_fails_after_bounded_unreachable_outage(monkeypatch):
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_BUDGET_SECONDS", 0.02)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RECONNECT_DELAY_SECONDS", 0.005)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_MAX_DELAY_SECONDS", 0.005)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("runtime unavailable", request=request)

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        events = [
            event async for event in client.follow_response_stream_events("unreachable-response")
        ]
    finally:
        await client.stop()

    assert attempts > 1
    assert events == [
        {
            "type": "response_terminal",
            "status": "failed",
            "error": "Runtime response stream is unreachable",
            "request_id": "unreachable-response",
        }
    ]


@pytest.mark.asyncio
async def test_deferred_stop_settles_after_sustained_runtime_follower_outage(monkeypatch):
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_BUDGET_SECONDS", 0.02)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RECONNECT_DELAY_SECONDS", 0.005)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_MAX_DELAY_SECONDS", 0.005)
    request_id = "unreachable-cancel-response"
    attempted = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.set()
        raise httpx.ConnectError("runtime unavailable", request=request)

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    state = chat_responses._ensure_response_stream(
        request_id, "response-unreachable-cancel", "thread-queue", "progena-v1"
    )
    published: list[events_contracts.UiEvent] = []
    original_client = lifecycle_state.client
    original_broadcast = events_stream.broadcast_ui_event
    lifecycle_state.client = client
    events_stream.broadcast_ui_event = lambda event_type, *_args, **_kwargs: published.append(
        event_type
    )
    chat_responses._response_execution_tasks[request_id] = asyncio.current_task()
    try:
        runner = asyncio.create_task(
            chat_responses._run_response_event_stream(
                message="",
                model="progena-v1",
                request_id=request_id,
                response_id="response-unreachable-cancel",
                thread_id="thread-queue",
                stream=True,
                deferred_only=True,
                content_request_id=request_id,
            )
        )
        await attempted.wait()
        chat_responses._active_response_streams[request_id].set()
        await runner
    finally:
        lifecycle_state.client = original_client
        events_stream.broadcast_ui_event = original_broadcast
        chat_responses._response_streams.pop(request_id, None)
        chat_responses._response_execution_tasks.pop(request_id, None)
        chat_responses._queue_terminal_request_ids.discard(request_id)
        chat_responses._terminal_response_request_ids.discard(request_id)
        await client.stop()

    assert state.terminal_reached is True
    assert published == [events_contracts.UiEvent.CHAT_RESPONSE_CANCELLED]


@pytest.mark.asyncio
async def test_runtime_follower_resumes_from_cursor_after_transient_read_failure(monkeypatch):
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RECONNECT_DELAY_SECONDS", 0.001)
    attempts: list[httpx.Request] = []

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"id: 7\nevent: response.output_text.delta\n"
            yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
            raise httpx.ReadError("temporary stream break")

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(200, stream=BrokenStream())
        return httpx.Response(
            200,
            content=(
                b"event: response.completed\n"
                b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
        )

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        events = [event async for event in client.follow_response_stream_events("cursor-response")]
    finally:
        await client.stop()

    assert attempts[1].url.params["cursor"] == "7"
    assert [event["type"] for event in events] == ["stream_chunk", "response_terminal"]


@pytest.mark.asyncio
async def test_runtime_follower_fails_after_repeated_header_only_timeouts(monkeypatch):
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_BUDGET_SECONDS", 0.02)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RECONNECT_DELAY_SECONDS", 0.005)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_MAX_DELAY_SECONDS", 0.005)
    attempts = 0

    class TimeoutStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadTimeout("response stream stalled")
            yield b""

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, stream=TimeoutStream())

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        events = [
            event async for event in client.follow_response_stream_events("header-timeout-response")
        ]
    finally:
        await client.stop()

    assert attempts > 1
    assert events[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_runtime_follower_keepalive_resets_outage_before_later_blip(monkeypatch):
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_BUDGET_SECONDS", 0.02)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RECONNECT_DELAY_SECONDS", 0.001)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_MAX_DELAY_SECONDS", 0.001)
    attempts = 0

    class KeepaliveThenFailureStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": keepalive\n\n"
            await asyncio.sleep(0.03)
            raise httpx.ReadTimeout("later transient break")
            yield b""

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("initial transient break", request=request)
        if attempts == 2:
            return httpx.Response(200, stream=KeepaliveThenFailureStream())
        return httpx.Response(
            200,
            content=(
                b"event: response.completed\n"
                b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
        )

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        events = [
            event async for event in client.follow_response_stream_events("keepalive-response")
        ]
    finally:
        await client.stop()

    assert attempts == 3
    assert events == [
        {
            "type": "response_terminal",
            "status": "completed",
            "response": {"status": "completed"},
            "request_id": "keepalive-response",
        }
    ]


@pytest.mark.asyncio
async def test_stop_waits_for_runtime_terminal_after_one_transient_failure(monkeypatch):
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RETRY_BUDGET_SECONDS", 0.1)
    monkeypatch.setattr(runtime_integration, "FOLLOWER_RECONNECT_DELAY_SECONDS", 0.001)
    attempts = 0
    second_connection = asyncio.Event()
    release_terminal = asyncio.Event()
    cancel_event = asyncio.Event()
    cancel_event.set()

    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadTimeout("temporary stream break")
            yield b""

        async def aclose(self) -> None:
            return None

    class TerminalStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await release_terminal.wait()
            yield b"event: response.incomplete\n"
            yield b'data: {"type":"response.incomplete","response":{"status":"cancelled"}}\n\n'

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, stream=FailingStream())
        second_connection.set()
        return httpx.Response(200, stream=TerminalStream())

    client = SSEProgenaClient(ChatServerConfig(progena_runtime_url="http://runtime"))
    client._started = True
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(handler)
    )
    try:
        iterator = client.follow_response_stream_events(
            "transient-stop-response", cancel_event=cancel_event
        )
        task = asyncio.create_task(anext(iterator))
        await second_connection.wait()
        assert task.done() is False
        release_terminal.set()
        terminal = await task
    finally:
        await client.stop()

    assert attempts == 2
    assert terminal["status"] == "cancelled"


@pytest.mark.asyncio
async def test_deferred_response_started_preserves_registered_execution_activity():
    request_id = "queued-activity-1"
    started = asyncio.Event()
    release = asyncio.Event()

    class RuntimeClient:
        async def send_message_stream_events(self, *args, **kwargs):
            del args, kwargs
            yield {"type": "response_started", "queue_execution": True}
            started.set()
            await release.wait()
            yield {"type": "response_terminal", "status": "completed"}

    original_client = lifecycle_state.client
    lifecycle_state.client = RuntimeClient()
    chat_responses._register_response_activity(request_id, "thread-queue")
    chat_responses._response_execution_tasks[request_id] = asyncio.current_task()
    try:
        task = asyncio.create_task(
            chat_responses._run_response_event_stream(
                message="Queued prompt",
                model="progena-v1",
                request_id=request_id,
                response_id="response-activity-1",
                thread_id="thread-queue",
            )
        )
        await started.wait()
        assert chat_responses._active_response_for_thread("thread-queue") == (
            request_id,
            "generating",
        )
        release.set()
        await task
    finally:
        lifecycle_state.client = original_client
        chat_responses._response_activities.pop(request_id, None)
        chat_responses._active_response_streams.pop(request_id, None)
        chat_responses._response_execution_tasks.pop(request_id, None)
        chat_responses._queue_started_request_ids.discard(request_id)
        chat_responses._queue_terminal_request_ids.discard(request_id)


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
