"""Tests for the BuilderAgentExecutor A2A implementation."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from a2a.types import (
    Message,
    Part,
    Role,
    TaskState,
    TextPart,
)


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
        with patch("app.agents.builder_agent.agent._runner", new_callable=MagicMock):
            with patch("app.agents.builder_agent.agent._llm_client", new_callable=MagicMock):
                with patch("app.agents.builder_agent.agent._tool_registry", new_callable=MagicMock):
                    with patch("app.agents.builder_agent.agent._config", new_callable=MagicMock):
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
        with patch("app.agents.builder_agent.agent._runner", new_callable=MagicMock):
            with patch("app.agents.builder_agent.agent._llm_client", new_callable=MagicMock):
                with patch("app.agents.builder_agent.agent._tool_registry", new_callable=MagicMock):
                    with patch("app.agents.builder_agent.agent._config", new_callable=MagicMock):
                        await executor.execute(ctx, mock_event_queue)

    events = [call.args[0] for call in mock_event_queue.enqueue_event.call_args_list]
    terminal = [e for e in events if getattr(e, "final", False)]
    assert len(terminal) >= 1
    assert terminal[-1].status.state == TaskState.failed
