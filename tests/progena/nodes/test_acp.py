"""Regression coverage for ACP actionable-event handoffs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.progena.acp.models import ACPTaskRecord, ACPTaskStatus
from src.progena.config import RuntimeConfig
from src.progena.orchestration.nodes.acp import (
    apply_completed_acp_tasks_node,
    collect_completed_acp_tasks_node,
    notify_completed_acp_tasks_node,
)
from src.progena.runtime.queues import RuntimeQueues


class _StateManager:
    def __init__(self, tasks: list[ACPTaskRecord]) -> None:
        self.tasks = tasks

    async def get_acp_tasks(self) -> list[ACPTaskRecord]:
        return self.tasks

    async def update_acp_task(self, task_id: str, updates: dict[str, Any]) -> None:
        task = next(task for task in self.tasks if task.task_id == task_id)
        for key, value in updates.items():
            setattr(task, key, value)


class _Queues:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.inputs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue_input(self, content: str, *, metadata: dict[str, Any]) -> bool:
        if not self.accepted:
            return False
        self.inputs.append((content, metadata))
        return True


class _ShortWaitRuntimeQueues(RuntimeQueues):
    async def enqueue_input(
        self,
        content: str,
        timeout: float | None = 0.001,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await super().enqueue_input(content, timeout=0.001, metadata=metadata)


def _runtime(manager: _StateManager, queues: Any) -> Any:
    return SimpleNamespace(context=SimpleNamespace(state_manager=manager, queues=queues))


def _task() -> ACPTaskRecord:
    return ACPTaskRecord(
        task_id="acp_1",
        agent_name="claude",
        prompt="the original prompt must not be repeated",
        source="progena",
        request_metadata={"thread_id": "thread_1"},
        notify_on_completion=True,
        status=ACPTaskStatus.WORKING,
        turn=1,
    )


@pytest.mark.asyncio
async def test_only_material_acp_events_wake_once_and_new_turns_can_wake() -> None:
    task = _task()
    manager = _StateManager([task])
    queues = _Queues()
    runtime = _runtime(manager, queues)

    collected = await collect_completed_acp_tasks_node({}, runtime)
    assert collected["acp_completed_tasks"] == []

    task.pending_input = {"id": "question-1", "kind": "question", "message": "Continue?"}
    collected = await collect_completed_acp_tasks_node({}, runtime)
    await notify_completed_acp_tasks_node(collected, runtime)
    assert len(queues.inputs) == 1
    assert queues.inputs[-1][1]["acp_event_type"] == "pending_input"
    assert (await collect_completed_acp_tasks_node({}, runtime))["acp_completed_tasks"] == []

    task.pending_input = {"id": "question-2", "kind": "permission", "message": "Approve edit?"}
    collected = await collect_completed_acp_tasks_node({}, runtime)
    await notify_completed_acp_tasks_node(collected, runtime)
    assert len(queues.inputs) == 2

    task.pending_input = None
    task.stalled = True
    task.stall_episode = 1
    collected = await collect_completed_acp_tasks_node({}, runtime)
    await notify_completed_acp_tasks_node(collected, runtime)
    assert len(queues.inputs) == 3
    assert (await collect_completed_acp_tasks_node({}, runtime))["acp_completed_tasks"] == []

    task.stall_episode = 2
    collected = await collect_completed_acp_tasks_node({}, runtime)
    await notify_completed_acp_tasks_node(collected, runtime)
    assert len(queues.inputs) == 4

    task.stalled = False
    task.status = ACPTaskStatus.COMPLETED
    task.output = "result narrative must remain out of the conversation handoff"
    collected = await collect_completed_acp_tasks_node({}, runtime)
    applied = await apply_completed_acp_tasks_node(collected, runtime)
    message = applied["messages"][-1]
    assert "result narrative" not in message.content
    assert "acp_get_result" in message.content
    await notify_completed_acp_tasks_node(collected, runtime)
    assert len(queues.inputs) == 5
    assert "original prompt" not in queues.inputs[-1][0]
    assert "result narrative" not in queues.inputs[-1][0]
    assert (await collect_completed_acp_tasks_node({}, runtime))["acp_completed_tasks"] == []

    task.turn = 2
    task.status = ACPTaskStatus.WORKING
    task.output = None
    task.status = ACPTaskStatus.COMPLETED
    collected = await collect_completed_acp_tasks_node({}, runtime)
    applied = await apply_completed_acp_tasks_node({**collected, **applied}, runtime)
    assert applied["messages"][-1].metadata["acp_turn"] == 2
    await notify_completed_acp_tasks_node(collected, runtime)
    assert len(queues.inputs) == 6


@pytest.mark.asyncio
async def test_workflow_owned_and_pre_cutover_acp_tasks_do_not_replay() -> None:
    workflow_task = _task()
    workflow_task.source = "workflow:build"
    workflow_task.status = ACPTaskStatus.COMPLETED
    old_chat_task = _task()
    old_chat_task.task_id = "acp_old"
    old_chat_task.notify_on_completion = False
    old_chat_task.status = ACPTaskStatus.COMPLETED
    manager = _StateManager([workflow_task, old_chat_task])

    collected = await collect_completed_acp_tasks_node({}, _runtime(manager, _Queues()))

    assert collected["acp_completed_tasks"] == []


@pytest.mark.asyncio
async def test_active_legacy_chat_task_gets_terminal_wakeup_without_polling() -> None:
    task = _task()
    task.notify_on_completion = False
    task.request_metadata["caller"] = "progena"
    manager = _StateManager([task])
    queues = _Queues()
    runtime = _runtime(manager, queues)

    assert (await collect_completed_acp_tasks_node({}, runtime))["acp_completed_tasks"] == []
    task.status = ACPTaskStatus.COMPLETED
    collected = await collect_completed_acp_tasks_node({}, runtime)
    await notify_completed_acp_tasks_node(collected, runtime)
    assert len(queues.inputs) == 1
    assert queues.inputs[0][1]["thread_id"] == "thread_1"
    assert queues.inputs[0][1]["acp_event_type"] == "terminal"
    assert (await collect_completed_acp_tasks_node({}, runtime))["acp_completed_tasks"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["terminal", "stall"])
async def test_rejected_followups_do_not_ack_or_mark_terminal_sent(event_type: str) -> None:
    task = _task()
    if event_type == "terminal":
        task.status = ACPTaskStatus.COMPLETED
    else:
        task.stalled = True
        task.stall_episode = 1
    manager = _StateManager([task])
    runtime = _runtime(manager, _Queues(accepted=False))

    collected = await collect_completed_acp_tasks_node({}, runtime)
    notified = await notify_completed_acp_tasks_node(collected, runtime)

    assert notified["acp_notifications_sent"] == 0
    assert task.material_event_acknowledgements == {}
    assert task.notification_sent_at is None
    assert (await collect_completed_acp_tasks_node({}, runtime))["acp_completed_tasks"] == [task]


@pytest.mark.asyncio
async def test_rejected_pending_input_is_retried_after_runtime_queue_has_capacity() -> None:
    task = _task()
    task.pending_input = {"id": "q1", "kind": "question", "message": "Continue?"}
    manager = _StateManager([task])
    queues = _ShortWaitRuntimeQueues(RuntimeConfig(max_input_queue_size=1, max_output_queue_size=1))
    await queues.enqueue_input("existing input")
    runtime = _runtime(manager, queues)

    collected = await collect_completed_acp_tasks_node({}, runtime)
    rejected = await notify_completed_acp_tasks_node(collected, runtime)

    assert rejected["acp_notifications_sent"] == 0
    assert task.material_event_acknowledgements == {}
    assert (await collect_completed_acp_tasks_node({}, runtime))["acp_completed_tasks"] == [task]

    assert await queues.get_input(timeout=0.1) == "existing input"
    delivered = await notify_completed_acp_tasks_node(
        await collect_completed_acp_tasks_node({}, runtime), runtime
    )

    assert delivered["acp_notifications_sent"] == 1
    assert task.material_event_acknowledgements == {"pending_input": "pending_input:1:q1"}
    assert (await collect_completed_acp_tasks_node({}, runtime))["acp_completed_tasks"] == []
