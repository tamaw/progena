"""Tests for the BuilderAgentExecutor A2A implementation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    Message,
    Part,
    Role,
    TaskState,
    TextPart,
)

from app.agents.builder_agent.agent import _normalize_build_action
from app.agents.builder_agent.queue_worker import _QueuedTask


def _make_text_message(text: str) -> Message:
    """Helper to create a simple A2A text message."""
    text_part = TextPart(text=text)
    # Wrap in Part (RootModel)
    part = Part(root=text_part)
    return Message(messageId="msg-1", role=Role.user, parts=[part])


@pytest.fixture
def executor():
    from app.agents.builder_agent.agent import BuilderAgentExecutor

    return BuilderAgentExecutor()


@pytest.fixture
def mock_event_queue():
    q = AsyncMock()
    q.enqueue_event = AsyncMock()
    return q


@pytest.fixture
def mock_context_empty():
    ctx = MagicMock()
    ctx.task_id = "task-test-001"
    ctx.context_id = "ctx-test-001"
    ctx.message = _make_text_message("")  # empty message
    return ctx


@pytest.fixture
def mock_context_build():
    ctx = MagicMock()
    ctx.task_id = "task-test-002"
    ctx.context_id = "ctx-test-002"
    ctx.message = _make_text_message("Build a hello world Python script")
    return ctx


@pytest.mark.asyncio
async def test_execute_empty_request_emits_failed(executor, mock_event_queue, mock_context_empty):
    """An empty build request should immediately emit a 'failed' terminal event."""
    with patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock):
        await executor.execute(mock_context_empty, mock_event_queue)

    assert mock_event_queue.enqueue_event.called
    events = [call.args[0] for call in mock_event_queue.enqueue_event.call_args_list]
    terminal = [e for e in events if getattr(e, "final", False)]
    assert len(terminal) >= 1
    assert terminal[-1].status.state == TaskState.failed


@pytest.mark.asyncio
async def test_cancel_is_a_no_op(executor, mock_event_queue):
    """cancel() should not raise."""
    ctx = MagicMock()
    ctx.task_id = "task-cancel-001"
    ctx.context_id = "ctx-cancel-001"
    await executor.cancel(ctx, mock_event_queue)


@pytest.mark.asyncio
async def test_execute_no_message_emits_failed(executor, mock_event_queue):
    """A context with no message at all should emit a 'failed' terminal event."""
    ctx = MagicMock()
    ctx.task_id = "task-no-msg"
    ctx.context_id = "ctx-no-msg"
    ctx.message = None

    with patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock):
        await executor.execute(ctx, mock_event_queue)

    events = [call.args[0] for call in mock_event_queue.enqueue_event.call_args_list]
    terminal = [e for e in events if getattr(e, "final", False)]
    assert len(terminal) >= 1
    assert terminal[-1].status.state == TaskState.failed


@pytest.mark.asyncio
async def test_run_build_request_cancels_active_background_build() -> None:
    from app.agents.builder_agent.agent import _run_build_request

    build_settings = MagicMock(project_name="demo-agent")
    pending_task: asyncio.Future[None] = asyncio.Future()
    runner = MagicMock()
    runner.start_build = AsyncMock(return_value=("build-123", "ack"))
    runner.cancel_build = AsyncMock(return_value=True)
    runner._current_task = pending_task
    runner.get_build_status = MagicMock(return_value=None)

    with (
        patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock),
        patch("app.agents.builder_agent.agent._runner", runner),
        patch("app.agents.builder_agent.agent._llm_client", MagicMock()),
        patch(
            "app.agents.builder_agent.agent._tool_registry",
            MagicMock(get_tool_context=MagicMock(return_value="")),
        ),
        patch("app.agents.builder_agent.agent._normalize_build_action", return_value="build_agent"),
        patch(
            "app.agents.builder_agent.runtime.decide._request_decision_data",
            new_callable=AsyncMock,
            return_value={"action": "build_agent"},
        ),
        patch(
            "app.agents.builder_agent.runtime.decide._generate_build_settings",
            new_callable=AsyncMock,
            return_value=build_settings,
        ),
        patch(
            "app.agents.builder_agent.runtime.decide._infer_target_capabilities",
            return_value=["agents"],
        ),
    ):
        task = asyncio.create_task(_run_build_request("task-123", "ctx-123", "build demo", {}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    runner.cancel_build.assert_awaited_once_with("build-123")


def test_normalize_build_action_maps_execute_aliases():
    assert _normalize_build_action("execute") == "build_agent"
    assert _normalize_build_action("answer_directly") == "build_agent"


def test_normalize_build_action_keeps_build_actions():
    assert _normalize_build_action("build_mcp") == "build_mcp"
    assert _normalize_build_action("upgrade") == "upgrade"


@pytest.mark.asyncio
async def test_execute_enqueues_and_returns_submitted(
    executor, mock_event_queue, mock_context_build
):
    queue = AsyncMock()
    queue.enqueue = AsyncMock()

    with (
        patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock),
        patch("app.agents.builder_agent.agent._get_or_create_task_queue", return_value=queue),
    ):
        await executor.execute(mock_context_build, mock_event_queue)

    queue.enqueue.assert_awaited_once()
    queued_task = queue.enqueue.await_args.args[0]
    assert isinstance(queued_task, _QueuedTask)
    assert queued_task.request_text == "Build a hello world Python script"
    assert queued_task.event_queue is mock_event_queue

    events = [call.args[0] for call in mock_event_queue.enqueue_event.call_args_list]
    assert len(events) == 1
    assert events[0].status.state == TaskState.submitted
    assert events[0].final is False


@pytest.mark.asyncio
async def test_execute_prefers_metadata_task_ids(executor, mock_event_queue, mock_context_build):
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    mock_context_build.message.metadata = {
        "a2a_task_id": "remote-task-123",
        "a2a_context_id": "remote-ctx-456",
    }

    with (
        patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock),
        patch("app.agents.builder_agent.agent._get_or_create_task_queue", return_value=queue),
    ):
        await executor.execute(mock_context_build, mock_event_queue)

    queued_task = queue.enqueue.await_args.args[0]
    assert queued_task.task_id == "remote-task-123"
    assert queued_task.context_id == "remote-ctx-456"
    assert queued_task.event_queue is mock_event_queue
