import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.progenitor.a2a.client import A2AClient


@pytest.mark.asyncio
async def test_call_agent_blocking_polls_until_terminal_state(monkeypatch) -> None:
    client = A2AClient()

    submitted = {
        "kind": "task",
        "id": "task-123",
        "status": {
            "state": "submitted",
            "message": {"parts": [{"kind": "text", "text": "Accepted: hello"}]},
        },
    }
    completed = {
        "kind": "task",
        "id": "task-123",
        "status": {
            "state": "completed",
            "message": {"parts": [{"kind": "text", "text": "Hello from greetings_agent!"}]},
        },
    }

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send_message(self, message, configuration):
            del message, configuration
            yield submitted

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())
    get_task = AsyncMock(return_value={"success": True, "result": completed})
    monkeypatch.setattr(client, "get_task", get_task)

    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=True,
        timeout=5,
    )

    assert result["success"] is True
    assert result["result"]["status"]["state"] == "completed"
    assert (
        result["result"]["status"]["message"]["parts"][0]["text"] == "Hello from greetings_agent!"
    )
    get_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_agent_blocking_does_not_poll_terminal_payload(monkeypatch) -> None:
    client = A2AClient()
    completed = {
        "kind": "task",
        "id": "task-123",
        "status": {
            "state": "completed",
            "message": {"parts": [{"kind": "text", "text": "done"}]},
        },
    }

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send_message(self, message, configuration):
            del message, configuration
            yield completed

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())
    get_task = AsyncMock()
    monkeypatch.setattr(client, "get_task", get_task)

    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=True,
        timeout=5,
    )

    assert result["success"] is True
    assert result["result"]["status"]["state"] == "completed"
    get_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_agent_non_blocking_stops_after_first_event_under_streaming(monkeypatch) -> None:
    """Regression: once streaming is negotiated, send_message()'s generator
    keeps yielding until the server closes the stream (i.e. until the whole
    task finishes) -- draining it fully for a non-blocking dispatch would
    silently turn "submit and return" into a full wait. Only the first
    snapshot should be consumed; later events must never be touched.
    """
    client = A2AClient()
    submitted = {"kind": "task", "id": "task-123", "status": {"state": "submitted"}}
    later_events_touched = False

    class _FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            nonlocal later_events_touched
            if not hasattr(self, "_yielded"):
                self._yielded = True
                return submitted
            later_events_touched = True
            raise StopAsyncIteration

        async def aclose(self):
            self.closed = True

    fake_stream = _FakeStream()

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def send_message(self, message, configuration):
            del message, configuration
            return fake_stream

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())

    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=False,
        timeout=5,
    )

    assert result["success"] is True
    assert result["result"]["status"]["state"] == "submitted"
    assert later_events_touched is False
    assert getattr(fake_stream, "closed", False) is True


@pytest.mark.asyncio
async def test_call_agent_blocking_times_out_against_endless_non_terminal_stream(
    monkeypatch,
) -> None:
    """Regression: a peer that keeps emitting non-terminal status updates more
    often than any per-read timeout must not be able to hold a blocking
    call_agent() open forever -- the whole streamed phase needs its own
    end-to-end deadline, since _await_terminal_task's deadline only starts
    after this loop exits."""
    client = A2AClient()
    submitted = {"kind": "task", "id": "task-123", "status": {"state": "submitted"}}
    update = {"kind": "status-update", "status": {"state": "working"}}

    class _FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            # A well-behaved peer keeps the connection alive with frequent
            # progress pings -- never terminal, never closing the stream.
            await asyncio.sleep(0.01)
            return (submitted, update)

        async def aclose(self):
            self.closed = True

    fake_stream = _FakeStream()

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def send_message(self, message, configuration):
            del message, configuration
            return fake_stream

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())

    result = await asyncio.wait_for(
        client.call_agent(
            {"url": "http://localhost:8109", "transport": "http", "status": "running"},
            "hello",
            blocking=True,
            timeout=0.05,
        ),
        timeout=5,
    )

    assert result["success"] is False
    assert "timeout" in result["error"].lower()
    assert getattr(fake_stream, "closed", False) is True


@pytest.mark.asyncio
async def test_call_agent_blocking_collects_intermediate_progress_updates(monkeypatch) -> None:
    """blocking=True must not discard intermediate TaskStatusUpdateEvents --
    they're collected into progress_updates rather than only keeping the
    last payload, now that streaming can genuinely deliver more than one."""
    client = A2AClient()

    class _FakeTask:
        def __init__(self, state):
            self.id = "task-123"
            self.status = type("S", (), {"state": state})()

        def model_dump(self, **_kwargs):
            return {"kind": "task", "id": self.id, "status": {"state": str(self.status.state)}}

    class _FakeUpdate:
        def __init__(self, text):
            self.text = text

        def model_dump(self, **_kwargs):
            return {"message": self.text}

    events = [
        (_FakeTask("working"), _FakeUpdate("phase 1")),
        (_FakeTask("working"), _FakeUpdate("phase 2")),
        (_FakeTask("completed"), _FakeUpdate("done")),
    ]

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send_message(self, message, configuration):
            del message, configuration
            for item in events:
                yield item

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())
    get_task = AsyncMock()
    monkeypatch.setattr(client, "get_task", get_task)

    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=True,
        timeout=5,
    )

    assert result["success"] is True
    assert result["result"]["status"]["state"] == "completed"
    assert [u["message"] for u in result["progress_updates"]] == ["phase 1", "phase 2", "done"]
    get_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_agent_blocking_stamps_received_at_onto_each_progress_update(
    monkeypatch,
) -> None:
    """Each progress_updates entry must carry its own real arrival time --
    extract_progress_log (app/progenitor/a2a/payloads.py) only runs once,
    after the whole blocking call has completed, so this is the one point
    where each update's true timing is still available to capture."""
    client = A2AClient()

    class _FakeTask:
        def __init__(self, state):
            self.id = "task-123"
            self.status = type("S", (), {"state": state})()

        def model_dump(self, **_kwargs):
            return {"kind": "task", "id": self.id, "status": {"state": str(self.status.state)}}

    class _FakeUpdate:
        def __init__(self, text):
            self.text = text

        def model_dump(self, **_kwargs):
            return {"message": self.text}

    events = [
        (_FakeTask("working"), _FakeUpdate("phase 1")),
        (_FakeTask("completed"), _FakeUpdate("done")),
    ]

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send_message(self, message, configuration):
            del message, configuration
            for item in events:
                yield item

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())
    monkeypatch.setattr(client, "get_task", AsyncMock())

    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=True,
        timeout=5,
    )

    stamps = [u["_progenitor_received_at"] for u in result["progress_updates"]]
    assert all(isinstance(stamp, str) and stamp for stamp in stamps)
    # ISO-parseable, and distinct calls to utcnow() -- not the same instant
    # reused for every entry.
    parsed = [datetime.fromisoformat(stamp) for stamp in stamps]
    assert parsed[0] <= parsed[1]


@pytest.mark.asyncio
async def test_call_agent_blocking_invokes_on_progress_per_intermediate_event(monkeypatch) -> None:
    """on_progress must fire once per intermediate TaskStatusUpdateEvent, in
    order, only when blocking=True -- it's the hook the chat UI uses to
    live-stream A2A call progress (see CallAgentTool._make_progress_callback)."""
    client = A2AClient()

    class _FakeTask:
        def __init__(self, state):
            self.id = "task-123"
            self.status = type("S", (), {"state": state})()

        def model_dump(self, **_kwargs):
            return {"kind": "task", "id": self.id, "status": {"state": str(self.status.state)}}

    class _FakeUpdate:
        def __init__(self, text):
            self.text = text

        def model_dump(self, **_kwargs):
            return {"message": self.text}

    events = [
        (_FakeTask("working"), _FakeUpdate("phase 1")),
        (_FakeTask("working"), _FakeUpdate("phase 2")),
        (_FakeTask("completed"), _FakeUpdate("done")),
    ]

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send_message(self, message, configuration):
            del message, configuration
            for item in events:
                yield item

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())
    get_task = AsyncMock()
    monkeypatch.setattr(client, "get_task", get_task)

    seen: list[str] = []

    async def _on_progress(update_payload):
        seen.append(update_payload["message"])

    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=True,
        timeout=5,
        on_progress=_on_progress,
    )

    assert result["success"] is True
    assert seen == ["phase 1", "phase 2", "done"]


@pytest.mark.asyncio
async def test_call_agent_non_blocking_never_invokes_on_progress(monkeypatch) -> None:
    """Non-blocking dispatch breaks after the first snapshot and never
    accumulates progress_updates -- on_progress must not fire either."""
    client = A2AClient()
    submitted = {"kind": "task", "id": "task-123", "status": {"state": "submitted"}}

    class _FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "_yielded"):
                self._yielded = True
                return submitted
            raise StopAsyncIteration

        async def aclose(self):
            self.closed = True

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def send_message(self, message, configuration):
            del message, configuration
            return _FakeStream()

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())

    on_progress = AsyncMock()
    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=False,
        timeout=5,
        on_progress=on_progress,
    )

    assert result["success"] is True
    on_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_agent_on_progress_failure_does_not_break_the_call(monkeypatch) -> None:
    """A UI-emission failure inside on_progress must never break the actual
    A2A call -- it's swallowed and logged, not propagated."""
    client = A2AClient()

    class _FakeTask:
        def __init__(self, state):
            self.id = "task-123"
            self.status = type("S", (), {"state": state})()

        def model_dump(self, **_kwargs):
            return {"kind": "task", "id": self.id, "status": {"state": str(self.status.state)}}

    class _FakeUpdate:
        def model_dump(self, **_kwargs):
            return {"message": "hi"}

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send_message(self, message, configuration):
            del message, configuration
            yield (_FakeTask("completed"), _FakeUpdate())

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())
    get_task = AsyncMock()
    monkeypatch.setattr(client, "get_task", get_task)

    async def _broken_on_progress(_update_payload):
        raise RuntimeError("emission failed")

    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=True,
        timeout=5,
        on_progress=_broken_on_progress,
    )

    assert result["success"] is True
    assert result["result"]["status"]["state"] == "completed"


@pytest.mark.asyncio
async def test_call_agent_non_blocking_never_populates_progress_updates(monkeypatch) -> None:
    """Regression: progress_updates must stay absent for blocking=False even
    when the single pre-break snapshot happens to arrive in the same
    (task, update) tuple shape a streaming intermediate event would -- a
    plausible shape for a submission ack now that streaming is always
    negotiated. Only on_progress was previously gated on `blocking`; the
    progress_updates.append call itself was not, so this case used to leak
    one entry into a non-blocking result despite the "blocking=True only"
    contract on the field.
    """
    client = A2AClient()

    class _FakeTask:
        def __init__(self, state):
            self.id = "task-123"
            self.status = type("S", (), {"state": state})()

        def model_dump(self, **_kwargs):
            return {"kind": "task", "id": self.id, "status": {"state": str(self.status.state)}}

    class _FakeUpdate:
        def model_dump(self, **_kwargs):
            return {"message": "submitted"}

    class _FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "_yielded"):
                self._yielded = True
                return (_FakeTask("submitted"), _FakeUpdate())
            raise StopAsyncIteration

        async def aclose(self):
            self.closed = True

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def send_message(self, message, configuration):
            del message, configuration
            return _FakeStream()

    monkeypatch.setattr(client, "_sdk_client", lambda *args, **kwargs: _FakeContext())

    on_progress = AsyncMock()
    result = await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=False,
        timeout=5,
        on_progress=on_progress,
    )

    assert result["success"] is True
    assert "progress_updates" not in result
    on_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_agent_only_requests_streaming_when_on_progress_is_given(monkeypatch) -> None:
    """Streaming is only requested from the SDK when a caller actually wants
    intermediate progress (on_progress): callers that don't are better
    served by plain non-streaming request/response, which doesn't depend on
    the target agent's SSE support at all -- narrowing the blast radius to
    only the callers who opted in, rather than requesting streaming for
    every call_agent invocation repo-wide.
    """
    client = A2AClient()
    completed = {
        "kind": "task",
        "id": "task-123",
        "status": {
            "state": "completed",
            "message": {"parts": [{"kind": "text", "text": "done"}]},
        },
    }
    captured_kwargs: list[dict] = []

    class _FakeContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def send_message(self, message, configuration):
            del message, configuration
            yield completed

    def _fake_sdk_client(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return _FakeContext()

    monkeypatch.setattr(client, "_sdk_client", _fake_sdk_client)
    monkeypatch.setattr(client, "get_task", AsyncMock())

    await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=True,
        timeout=5,
    )
    assert captured_kwargs[-1]["streaming"] is False

    async def _on_progress(_update_payload):
        pass

    await client.call_agent(
        {"url": "http://localhost:8109", "transport": "http", "status": "running"},
        "hello",
        blocking=True,
        timeout=5,
        on_progress=_on_progress,
    )
    assert captured_kwargs[-1]["streaming"] is True
