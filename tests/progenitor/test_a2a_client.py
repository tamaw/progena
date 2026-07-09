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
