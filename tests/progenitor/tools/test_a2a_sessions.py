"""Behavior tests for the A2A task-session system tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.progenitor.a2a.models import A2ATaskRecord
from src.progenitor.tools.a2a_sessions import (
    A2ACancelTool,
    A2AListTool,
    A2ASendAsyncTool,
    A2ASendTool,
    A2AStatusTool,
)
from src.progenitor.tools.protocol import ToolContext


class _State:
    def __init__(self, records: list[A2ATaskRecord] | None = None) -> None:
        self.records = {record.task_id: record for record in records or []}

    async def get_a2a_task(self, task_id: str) -> A2ATaskRecord | None:
        return self.records.get(task_id)

    async def get_a2a_tasks(self) -> list[A2ATaskRecord]:
        return list(self.records.values())

    async def upsert_a2a_task(self, record: A2ATaskRecord) -> None:
        self.records[record.task_id] = record


class _Client:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = iter(payloads)
        self.send_message = AsyncMock(side_effect=self._send)
        self.get_task = AsyncMock(side_effect=self._get)
        self.cancel_task = AsyncMock(side_effect=self._cancel)

    async def _send(self, **_kwargs):
        return {"success": True, "result": next(self.payloads)}

    async def _get(self, *_args, **_kwargs):
        return {"success": True, "result": next(self.payloads)}

    async def _cancel(self, _config, _task_id, **_kwargs):
        return {"success": True, "result": next(self.payloads)}


def _payload(task_id: str = "task-1", state: str = "submitted", context_id: str = "ctx-1") -> dict:
    return {
        "kind": "task",
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": state,
            "message": {"parts": [{"kind": "text", "text": state}]},
        },
    }


def _context(
    state: _State,
    registry: MagicMock,
    args: dict,
    *,
    tool_call_id: str = "call-1",
) -> ToolContext:
    return ToolContext(
        tool_registry=registry,
        llm_client=None,
        state={"thread_id": "thread-1"},
        state_manager=state,
        tool_args=args,
        tool_call_id=tool_call_id,
    )


def _registry(config: dict | None = None) -> MagicMock:
    registry = MagicMock()
    registry.find_by_name.return_value = config or {
        "name": "builder",
        "url": "http://example.test",
        "transport": "http",
    }
    return registry


@pytest.mark.asyncio
async def test_send_waits_and_returns_settled_task(monkeypatch) -> None:
    client = _Client([_payload(state="completed")])
    monkeypatch.setattr("src.progenitor.a2a.tasks.A2AClient", lambda: client)
    state = _State()

    result = await A2ASendTool().execute(
        _context(state, _registry(), {"agent": "builder", "message": "build it"})
    )

    assert result.success
    assert result.data and result.data["status"] == "completed"
    assert result.data["progress_log"] == []
    assert client.send_message.await_args.kwargs["blocking"] is True
    assert client.send_message.await_args.kwargs["timeout"] == 60.0


@pytest.mark.asyncio
async def test_send_returns_in_flight_task_after_wait_timeout(monkeypatch) -> None:
    client = _Client([_payload(state="working")])
    monkeypatch.setattr("src.progenitor.a2a.tasks.A2AClient", lambda: client)

    result = await A2ASendTool().execute(
        _context(
            _State(), _registry(), {"agent": "builder", "message": "build it", "wait_seconds": 999}
        )
    )

    assert result.success
    assert result.data and result.data["status"] == "working"
    assert client.send_message.await_args.kwargs["timeout"] == 300.0


@pytest.mark.asyncio
async def test_send_async_returns_immediately_and_notifies(monkeypatch) -> None:
    client = _Client([_payload()])
    monkeypatch.setattr("src.progenitor.a2a.tasks.A2AClient", lambda: client)

    result = await A2ASendAsyncTool().execute(
        _context(_State(), _registry(), {"agent": "builder", "message": "build it"})
    )

    assert result.success
    assert result.data and result.data["notify_on_completion"] is True
    assert client.send_message.await_args.kwargs["blocking"] is False


@pytest.mark.asyncio
async def test_send_followup_carries_task_and_context_ids(monkeypatch) -> None:
    client = _Client([_payload("task-1", "input-required", "ctx-1")])
    monkeypatch.setattr("src.progenitor.a2a.tasks.A2AClient", lambda: client)
    prior = A2ATaskRecord(task_id="task-1", agent_name="builder", status="input-required")

    result = await A2ASendTool().execute(
        _context(
            _State([prior]),
            _registry(),
            {
                "agent": "builder",
                "message": "the answer",
                "task_id": "task-1",
                "context_id": "ctx-1",
            },
        )
    )

    assert result.success
    assert client.send_message.await_args.kwargs["task_id"] == "task-1"
    assert client.send_message.await_args.kwargs["context_id"] == "ctx-1"


@pytest.mark.asyncio
async def test_status_wait_refreshes_until_settled(monkeypatch) -> None:
    client = _Client([_payload(state="completed")])
    monkeypatch.setattr("src.progenitor.a2a.tasks.A2AClient", lambda: client)
    record = A2ATaskRecord(
        task_id="task-1", agent_name="builder", agent_url="http://example.test", status="working"
    )

    result = await A2AStatusTool().execute(
        _context(_State([record]), _registry(), {"task_id": "task-1", "wait_seconds": 1})
    )

    assert result.success
    assert result.data and result.data["status"] == "completed"
    client.get_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_returns_persisted_task(monkeypatch) -> None:
    client = _Client([_payload(state="canceled")])
    monkeypatch.setattr("src.progenitor.a2a.tasks.A2AClient", lambda: client)
    record = A2ATaskRecord(
        task_id="task-1", agent_name="builder", agent_url="http://example.test", status="working"
    )

    result = await A2ACancelTool().execute(
        _context(_State([record]), _registry(), {"task_id": "task-1"})
    )

    assert result.success
    assert result.data and result.data["status"] == "canceled"
    client.cancel_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_filters_records() -> None:
    records = [
        A2ATaskRecord(task_id="one", agent_name="builder", status="working"),
        A2ATaskRecord(task_id="two", agent_name="reviewer", status="completed"),
    ]

    result = await A2AListTool().execute(
        _context(_State(records), _registry(), {"agent": "builder", "status": "working"})
    )

    assert result.success
    assert result.data and result.data["count"] == 1
    assert result.data["tasks"][0]["task_id"] == "one"


@pytest.mark.asyncio
async def test_send_rejects_acp_unknown_disabled_and_missing_agent() -> None:
    state = _State()
    acp = await A2ASendTool().execute(
        _context(
            state,
            _registry({"name": "copilot", "transport": "acp"}),
            {"agent": "copilot", "message": "hi"},
        )
    )
    assert not acp.success and "acp_start" in (acp.error or "")

    unknown_registry = _registry()
    unknown_registry.find_by_name.return_value = None
    unknown_registry.find_agent_by_capability.return_value = None
    unknown = await A2ASendTool().execute(
        _context(state, unknown_registry, {"agent": "missing", "message": "hi"})
    )
    assert not unknown.success and "Unknown agent" in (unknown.error or "")

    disabled = await A2ASendTool().execute(
        _context(
            state,
            _registry({"name": "builder", "enabled": False}),
            {"agent": "builder", "message": "hi"},
        )
    )
    assert not disabled.success and "disabled" in (disabled.error or "")

    missing = await A2ASendTool().execute(_context(state, _registry(), {"message": "hi"}))
    assert not missing.success and missing.error == "Missing 'agent'"
