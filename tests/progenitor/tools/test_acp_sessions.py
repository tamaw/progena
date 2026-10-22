"""Consumer-visible tests for durable ACP session tools."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.progenitor.acp.models import ACPTaskRecord, ACPTaskStatus
from app.progenitor.persistence.store import LangGraphRuntimeStore
from app.progenitor.state.manager import StateManager
from app.progenitor.tools.acp_sessions import (
    ACPAnswerTool,
    ACPCancelTool,
    ACPListTool,
    ACPSendTool,
    ACPStartTool,
    ACPStatusTool,
)
from app.progenitor.tools.protocol import ToolContext


class _InMemoryStore:
    """Small native-store fake that exercises runtime-store atomic mutations."""

    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def aget(self, namespace: tuple[str, ...], key: str, **_: Any) -> Any:
        value = self.items.get((namespace, key))
        return SimpleNamespace(value=copy.deepcopy(value)) if value is not None else None

    async def aput(
        self, namespace: tuple[str, ...], key: str, value: dict[str, Any], **_: Any
    ) -> None:
        self.items[(namespace, key)] = copy.deepcopy(value)

    async def asearch(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        offset: int = 0,
        limit: int = 10,
        **_: Any,
    ) -> list[Any]:
        rows = [
            SimpleNamespace(key=key, value=copy.deepcopy(value))
            for (namespace, key), value in self.items.items()
            if namespace[: len(namespace_prefix)] == namespace_prefix
        ]
        return rows[offset : offset + limit]

    async def adelete(self, namespace: tuple[str, ...], key: str, **_: Any) -> None:
        self.items.pop((namespace, key), None)


@pytest.fixture
async def state_manager() -> StateManager:
    manager = StateManager(runtime_store=LangGraphRuntimeStore(_InMemoryStore()))
    await manager.initialize()
    return manager


def _registry() -> MagicMock:
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "name": "Claude",
        "transport": "acp",
        "acp": {"host": "claude", "port": 3001, "cwd": "/workspace"},
    }
    return registry


def _context(manager: StateManager, args: dict[str, Any], registry: Any = None) -> ToolContext:
    return ToolContext(
        llm_client=None,
        tool_args=args,
        tool_registry=registry,
        state_manager=manager,
        state={"thread_id": "thread-1"},
    )


@pytest.mark.asyncio
async def test_start_creates_queued_document(state_manager: StateManager) -> None:
    result = await ACPStartTool().execute(
        _context(
            state_manager,
            {"agent": "Claude", "prompt": "Inspect this repository", "mode": "plan"},
            _registry(),
        )
    )

    assert result.success
    assert result.data is not None
    assert result.data["status"] == "queued"
    task = await state_manager.get_acp_task(result.data["task_id"])
    assert task is not None
    assert task.agent_name == "Claude"
    assert task.prompt == "Inspect this repository"
    assert task.notify_on_completion is False
    assert task.source == "progenitor"
    assert task.mode == "plan"


@pytest.mark.asyncio
async def test_send_keeps_concurrent_appends(state_manager: StateManager, monkeypatch) -> None:
    task = ACPTaskRecord(task_id="acp_send", agent_name="Claude", prompt="initial")
    await state_manager.upsert_acp_task(task)
    monkeypatch.setattr("app.progenitor.tools.acp_sessions._SEND_WAIT_SECONDS", 0.0)

    results = await asyncio.gather(
        ACPSendTool().execute(_context(state_manager, {"task_id": task.task_id, "message": "one"})),
        ACPSendTool().execute(_context(state_manager, {"task_id": task.task_id, "message": "two"})),
    )

    assert all(result.success and result.data == {"outcome": "pending"} for result in results)
    stored = await state_manager.get_acp_task(task.task_id)
    assert stored is not None
    assert sorted(message["text"] for message in stored.messages) == ["one", "two"]


@pytest.mark.asyncio
async def test_status_wait_returns_after_turn_change(
    state_manager: StateManager, monkeypatch
) -> None:
    task = ACPTaskRecord(task_id="acp_wait", agent_name="Claude", prompt="initial")
    await state_manager.upsert_acp_task(task)
    monkeypatch.setattr("app.progenitor.tools.acp_sessions._STATUS_POLL_SECONDS", 0.001)

    wait = asyncio.create_task(
        ACPStatusTool().execute(
            _context(state_manager, {"task_id": task.task_id, "wait_seconds": 1})
        )
    )
    await asyncio.sleep(0.01)
    assert await state_manager.update_acp_task(task.task_id, {"turn": 1})
    result = await wait

    assert result.success
    assert result.data is not None
    assert result.data["turn"] == 1


@pytest.mark.asyncio
async def test_status_wait_returns_snapshot_on_timeout(
    state_manager: StateManager, monkeypatch
) -> None:
    task = ACPTaskRecord(task_id="acp_timeout", agent_name="Claude", prompt="initial")
    await state_manager.upsert_acp_task(task)
    monkeypatch.setattr("app.progenitor.tools.acp_sessions._STATUS_POLL_SECONDS", 0.001)

    result = await ACPStatusTool().execute(
        _context(state_manager, {"task_id": task.task_id, "wait_seconds": 0.005})
    )

    assert result.success
    assert result.data is not None
    assert result.data["status"] == "queued"
    assert result.data["turn"] == 0


@pytest.mark.asyncio
async def test_answer_rejects_wrong_pending_input(state_manager: StateManager) -> None:
    task = ACPTaskRecord(
        task_id="acp_answer",
        agent_name="Claude",
        prompt="initial",
        pending_input={"id": "correct", "kind": "question", "message": "Continue?"},
    )
    await state_manager.upsert_acp_task(task)

    result = await ACPAnswerTool().execute(
        _context(
            state_manager,
            {"task_id": task.task_id, "input_id": "wrong", "answer": {"yes": True}},
        )
    )

    assert not result.success
    assert "No matching pending input" in (result.error or "")


@pytest.mark.asyncio
async def test_cancel_requests_supervisor_cancellation(state_manager: StateManager) -> None:
    task = ACPTaskRecord(task_id="acp_cancel", agent_name="Claude", prompt="initial")
    await state_manager.upsert_acp_task(task)

    result = await ACPCancelTool().execute(_context(state_manager, {"task_id": task.task_id}))

    assert result.success
    assert result.data == {"task_id": task.task_id, "cancel_requested": True}
    updated = await state_manager.get_acp_task(task.task_id)
    assert updated is not None and updated.cancel_requested


@pytest.mark.asyncio
async def test_list_filters_sessions(state_manager: StateManager) -> None:
    await state_manager.upsert_acp_task(
        ACPTaskRecord(
            task_id="acp_match",
            agent_name="Claude",
            prompt="first",
            card_id="card-1",
            status=ACPTaskStatus.WORKING,
        )
    )
    await state_manager.upsert_acp_task(
        ACPTaskRecord(
            task_id="acp_other",
            agent_name="Other",
            prompt="second",
            card_id="card-2",
            status=ACPTaskStatus.QUEUED,
        )
    )

    result = await ACPListTool().execute(
        _context(state_manager, {"card_id": "card-1", "status": "working", "agent": "Claude"})
    )

    assert result.success
    assert result.data is not None
    assert len(result.data["tasks"]) == 1
    task = result.data["tasks"][0]
    assert task["updated_at"]
    assert task == {
        "task_id": "acp_match",
        "agent": "Claude",
        "status": "working",
        "turn": 0,
        "card_id": "card-1",
        "cwd": None,
        "updated_at": task["updated_at"],
        "summary": None,
    }
