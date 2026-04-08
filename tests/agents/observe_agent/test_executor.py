from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Part,
    Role,
    TaskQueryParams,
    TaskState,
    TextPart,
)

from app.agents.observe_agent.agent import ObserveExecutor
from app.agents.observe_agent.queue_worker import ObserveTaskQueue


def _make_text_message(text: str) -> Message:
    return Message(messageId="msg-1", role=Role.user, parts=[Part(root=TextPart(text=text))])


@pytest.mark.asyncio
async def test_observe_executor_runs_to_terminal_state(tmp_path: Path, monkeypatch) -> None:
    event_queue = AsyncMock()
    event_queue.enqueue_event = AsyncMock()

    service = AsyncMock()
    service.process = AsyncMock(return_value="Observe complete")

    queue = ObserveTaskQueue(service, log_path=tmp_path / "observe_tasks.jsonl")
    queue.start()
    monkeypatch.setattr("app.agents.observe_agent.agent._task_queue", queue)

    context = MagicMock()
    context.task_id = "observe-1"
    context.context_id = "ctx-1"
    context.message = _make_text_message("Inspect state")

    await ObserveExecutor().execute(context, event_queue)

    states = [call.args[0].status.state for call in event_queue.enqueue_event.call_args_list]
    assert states == [TaskState.submitted, TaskState.working, TaskState.completed]


@pytest.mark.asyncio
async def test_nonblocking_observe_handler_persists_completed_task(
    tmp_path: Path, monkeypatch
) -> None:
    service = AsyncMock()

    async def _process(request_text: str, request_ctx: dict[str, str]) -> str:
        assert request_text == "Inspect state"
        assert request_ctx["a2a_task_id"]
        await asyncio.sleep(0.01)
        return "Observe complete"

    service.process = AsyncMock(side_effect=_process)

    queue = ObserveTaskQueue(service, log_path=tmp_path / "observe_tasks.jsonl")
    queue.start()
    monkeypatch.setattr("app.agents.observe_agent.agent._task_queue", queue)

    handler = DefaultRequestHandler(
        agent_executor=ObserveExecutor(),
        task_store=InMemoryTaskStore(),
    )

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("Inspect state"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    assert result.status.state == TaskState.submitted

    latest = None
    for _ in range(50):
        latest = await handler.on_get_task(TaskQueryParams(id=result.id))
        if latest is not None and latest.status.state == TaskState.completed:
            break
        await asyncio.sleep(0.01)

    assert latest is not None
    assert latest.status.state == TaskState.completed
