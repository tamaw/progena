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
    runner.set_progress_callback = MagicMock()

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


@pytest.mark.asyncio
async def test_run_build_request_cancels_runner_task_before_build_acknowledgement() -> None:
    """Cancellation during start_build still interrupts the runner task."""
    from app.agents.builder_agent.agent import _run_build_request

    build_settings = MagicMock(project_name="demo-agent")
    start_called = asyncio.Event()
    active_task = asyncio.create_task(asyncio.sleep(60))
    runner = MagicMock()
    runner._current_task = active_task
    runner.cancel_build = AsyncMock(return_value=True)

    async def _start_build(*_args, **_kwargs):
        start_called.set()
        await asyncio.Event().wait()

    runner.start_build = _start_build
    runner.get_build_status = MagicMock(return_value=None)
    runner.set_progress_callback = MagicMock()

    try:
        with (
            patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock),
            patch("app.agents.builder_agent.agent._runner", runner),
            patch("app.agents.builder_agent.agent._llm_client", MagicMock()),
            patch(
                "app.agents.builder_agent.agent._tool_registry",
                MagicMock(get_tool_context=MagicMock(return_value="")),
            ),
            patch(
                "app.agents.builder_agent.agent._normalize_build_action", return_value="build_agent"
            ),
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
            request_task = asyncio.create_task(
                _run_build_request("task-race", "ctx-race", "build demo", {})
            )
            await asyncio.wait_for(start_called.wait(), timeout=2.0)
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task

        runner.cancel_build.assert_awaited_once_with(None)
        assert active_task.cancelled()
    finally:
        if not active_task.done():
            active_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active_task


@pytest.mark.asyncio
async def test_run_build_request_registers_and_clears_progress_callback_when_streaming() -> None:
    """When called with an event_queue (the streaming path from
    BuilderTaskQueue._run_task), _run_build_request must register a progress
    callback for the duration of the build and always clear it afterwards --
    it's a single mutable slot on a long-lived runner (see
    BackgroundBuildRunner.set_progress_callback), not a per-build
    registration, so leaving it set would leak into the next build.
    """
    from app.agents.builder_agent.agent import _run_build_request

    build_settings = MagicMock(project_name="demo-agent")
    runner = MagicMock()
    runner.start_build = AsyncMock(return_value=("build-456", "ack"))
    runner._current_task = None
    runner.get_build_status = MagicMock(return_value=MagicMock(status=MagicMock(value="completed")))
    runner.set_progress_callback = MagicMock()
    event_queue = AsyncMock()

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
        await _run_build_request("task-456", "ctx-456", "build demo", {}, event_queue)

    assert runner.set_progress_callback.call_count == 2
    first_call_arg = runner.set_progress_callback.call_args_list[0].args[0]
    assert callable(first_call_arg)
    last_call_arg = runner.set_progress_callback.call_args_list[-1].args[0]
    assert last_call_arg is None


def test_normalize_build_action_maps_execute_aliases():
    assert _normalize_build_action("execute") == "build_agent"
    assert _normalize_build_action("answer_directly") == "build_agent"


def test_normalize_build_action_keeps_build_actions():
    assert _normalize_build_action("build_mcp") == "build_mcp"
    assert _normalize_build_action("upgrade") == "upgrade"


@pytest.mark.asyncio
async def test_execute_awaits_the_build_inline(executor, mock_event_queue, mock_context_build):
    """execute() must not return until the queue's run() completes -- the
    A2A framework closes event_queue the instant execute() returns, so a
    detached/fire-and-forget handoff would silently drop every later event.
    """
    queue = AsyncMock()
    queue.run = AsyncMock()

    with (
        patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock),
        patch("app.agents.builder_agent.agent._get_or_create_task_queue", return_value=queue),
    ):
        await executor.execute(mock_context_build, mock_event_queue)

    queue.run.assert_awaited_once_with(
        "task-test-002",
        "ctx-test-002",
        "Build a hello world Python script",
        {"a2a_task_id": "task-test-002", "a2a_context_id": "ctx-test-002"},
        mock_event_queue,
    )

    events = [call.args[0] for call in mock_event_queue.enqueue_event.call_args_list]
    assert len(events) == 1
    assert events[0].status.state == TaskState.submitted
    assert events[0].final is False


@pytest.mark.asyncio
async def test_execute_prefers_request_context_task_ids(
    executor, mock_event_queue, mock_context_build
):
    queue = AsyncMock()
    queue.run = AsyncMock()
    mock_context_build.message.metadata = {
        "a2a_task_id": "remote-task-123",
        "a2a_context_id": "remote-ctx-456",
    }

    with (
        patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock),
        patch("app.agents.builder_agent.agent._get_or_create_task_queue", return_value=queue),
    ):
        await executor.execute(mock_context_build, mock_event_queue)

    # context.task_id/context_id (set on the RequestContext by the framework)
    # win over anything carried in message metadata.
    queue.run.assert_awaited_once_with(
        "task-test-002",
        "ctx-test-002",
        "Build a hello world Python script",
        {"a2a_task_id": "task-test-002", "a2a_context_id": "ctx-test-002"},
        mock_event_queue,
    )


@pytest.mark.asyncio
async def test_execute_and_cancel_emit_single_terminal_canceled_event(
    executor, mock_event_queue, mock_context_build, tmp_path
):
    from app.agents.builder_agent.queue_worker import BuilderTaskQueue
    from app.agents.builder_agent.runtime.background_build import BuildResult, BuildStatus
    from app.agents.builder_agent.runtime.time_utils import utcnow

    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(task_id, context_id, request_text, request_ctx, event_queue_arg):
        assert task_id == "task-test-002"
        assert context_id == "ctx-test-002"
        assert request_text == "Build a hello world Python script"
        assert request_ctx["a2a_task_id"] == "task-test-002"
        assert event_queue_arg is mock_event_queue
        started.set()
        await release.wait()
        return BuildResult(
            build_id="build-should-not-complete",
            status=BuildStatus.COMPLETED,
            task_description="build_agent: hello-world",
            started_at=utcnow(),
            completed_at=utcnow(),
        )

    queue = BuilderTaskQueue(handler, log_path=tmp_path / "tasks.jsonl")

    with (
        patch("app.agents.builder_agent.agent._ensure_initialized", new_callable=AsyncMock),
        patch("app.agents.builder_agent.agent._get_or_create_task_queue", return_value=queue),
        patch("app.agents.builder_agent.agent._task_queue", queue),
    ):
        execute_task = asyncio.create_task(executor.execute(mock_context_build, mock_event_queue))
        try:
            await asyncio.wait_for(started.wait(), timeout=2.0)
            await executor.cancel(mock_context_build, mock_event_queue)
            await asyncio.wait_for(execute_task, timeout=2.0)
        finally:
            release.set()
            if not execute_task.done():
                await execute_task

    events = [call.args[0] for call in mock_event_queue.enqueue_event.call_args_list]
    states = [event.status.state for event in events]
    terminal_states = [event.status.state for event in events if getattr(event, "final", False)]

    assert states == [TaskState.submitted, TaskState.working, TaskState.canceled]
    assert terminal_states == [TaskState.canceled]
