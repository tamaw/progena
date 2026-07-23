"""Tests for explicit Progenitor graph builders."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.graph.heartbeat_graph import (
    create_runtime_heartbeat_graph,
    validate_heartbeat_context,
)
from app.progenitor.graph.progenitor_graph import create_conversation_graph
from app.progenitor.nodes.context import HeartbeatContext
from app.progenitor.state.models import A2ATaskRecord, AsyncSubagentTaskRecord


def test_create_conversation_graph_returns_native_deepagent_graph() -> None:
    with patch("app.progenitor.graph.progenitor_graph.create_progenitor_agent") as mock_create:
        mock_graph = MagicMock()
        mock_create.return_value = mock_graph

        result = create_conversation_graph(config=MagicMock())

    assert result is mock_graph
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_runtime_heartbeat_graph_records_a2a_completion() -> None:
    state_manager = MagicMock()
    state_manager.get_schedule = AsyncMock(return_value=[])
    state_manager.get_active_a2a_tasks = AsyncMock(return_value=[])
    state_manager.log_conversation = AsyncMock()
    state_manager.update_a2a_task = AsyncMock()
    record = A2ATaskRecord(
        task_id="task-1",
        agent_name="observe_agent",
        status="completed",
        summary="Observed logs.",
    )
    queues = _FakeQueues(a2a_completions=[record])
    graph = create_runtime_heartbeat_graph()
    context = _heartbeat_context(
        state_manager=state_manager,
        queues=queues,
        tool_registry=_tool_registry(),
    )

    result = await graph.ainvoke({"messages": []}, context=context)

    state_manager.log_conversation.assert_awaited_once()
    state_manager.update_a2a_task.assert_awaited_once()
    assert result["messages"][0].metadata["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_runtime_heartbeat_graph_queues_same_thread_followup_for_a2a_completion() -> None:
    state_manager = MagicMock()
    state_manager.get_schedule = AsyncMock(return_value=[])
    state_manager.get_active_a2a_tasks = AsyncMock(return_value=[])
    state_manager.get_acp_tasks = AsyncMock(return_value=[])
    state_manager.dequeue_background_task = AsyncMock(return_value=None)
    state_manager.log_conversation = AsyncMock()
    state_manager.update_a2a_task = AsyncMock()
    record = A2ATaskRecord(
        task_id="task-2",
        agent_name="greetings_agent",
        status="completed",
        request_text="Ask for a greeting.",
        request_metadata={"thread_id": "thread-1"},
        summary="Hello from greetings_agent!",
    )
    queues = _FakeQueues(a2a_completions=[record])
    graph = create_runtime_heartbeat_graph()
    context = _heartbeat_context(
        state_manager=state_manager,
        queues=queues,
        tool_registry=_tool_registry(),
    )

    await graph.ainvoke({"messages": []}, context=context)

    queues.enqueue_input.assert_awaited_once()
    enqueued_message = queues.enqueue_input.await_args.args[0]
    enqueued_metadata = queues.enqueue_input.await_args.kwargs["metadata"]
    assert "async external agent task" in enqueued_message
    assert "Hello from greetings_agent!" in enqueued_message
    assert enqueued_metadata["thread_id"] == "thread-1"
    assert enqueued_metadata["source"] == "a2a_task_completion_followup"


@pytest.mark.asyncio
async def test_runtime_heartbeat_graph_notifies_async_subagent_completion(tmp_path) -> None:
    state_manager = MagicMock()
    state_manager.get_schedule = AsyncMock(return_value=[])
    state_manager.get_active_a2a_tasks = AsyncMock(return_value=[])
    state_manager.get_async_subagent_tasks = AsyncMock(
        return_value=[
            AsyncSubagentTaskRecord(
                task_id="async-task-1",
                agent_name="hello-world-async",
                assistant_id="hello-world-async",
                agent_url="http://127.0.0.1:9501/agent-protocol",
                parent_thread_id="thread-1",
                run_id="run-1",
                status="running",
                request_text="run later",
            )
        ]
    )
    state_manager.log_conversation = AsyncMock()
    state_manager.update_async_subagent_task = AsyncMock()
    queues = _FakeQueues()
    graph = create_runtime_heartbeat_graph()
    context = _heartbeat_context(
        state_manager=state_manager,
        queues=queues,
        llm_client=None,
    )
    context.due_maintenance = {"async_subagent_poll"}

    with patch(
        "app.progenitor.nodes.async_subagents.refresh_tracked_async_subagent_task",
        AsyncMock(
            return_value=AsyncSubagentTaskRecord(
                task_id="async-task-1",
                agent_name="hello-world-async",
                assistant_id="hello-world-async",
                agent_url="http://127.0.0.1:9501/agent-protocol",
                parent_thread_id="thread-1",
                run_id="run-1",
                status="success",
                request_text="run later",
                summary="HELLO_FROM_ASYNC_SUBAGENT",
                latest_message="HELLO_FROM_ASYNC_SUBAGENT",
            )
        ),
    ):
        result = await graph.ainvoke({"messages": []}, context=context)

    state_manager.log_conversation.assert_awaited_once()
    state_manager.update_async_subagent_task.assert_awaited_once()
    queues.push_event.assert_any_await(
        "agent_message",
        "Async subagent hello-world-async finished. task_id=async-task-1. Result: HELLO_FROM_ASYNC_SUBAGENT",
        {
            "source": "async_subagent_completion",
            "task_id": "async-task-1",
            "agent": "hello-world-async",
            "status": "success",
            "thread_id": "thread-1",
        },
    )
    assert result["messages"][0].metadata["task_id"] == "async-task-1"


@pytest.mark.asyncio
async def test_runtime_heartbeat_graph_processes_one_input_cycle() -> None:
    state_manager = MagicMock()
    state_manager.save_current_state = AsyncMock()
    state_manager.save_health = AsyncMock()

    tool_registry = _tool_registry()

    class _FakeAgentGraph:
        async def ainvoke(self, graph_input, config):  # noqa: ANN001
            assert config == {"configurable": {"thread_id": "thread-1"}}
            assert graph_input["messages"][0].content == "hello"
            return {
                "messages": [{"role": "assistant", "content": "ok"}],
                "pending_response": "ok",
            }

        async def aupdate_state(self, config, values):  # noqa: ANN001
            return config

    queues = _FakeQueues(wait_seconds=0.25, pending_count=2)
    live_state = {
        "thread_id": "thread-1",
        "messages": [],
        "tool_calls": [],
        "attachments": [],
        "queued_input": "hello",
    }
    audit_log_sink = MagicMock()
    audit_log_sink.write_log = AsyncMock()
    context = _heartbeat_context(
        queues=queues,
        agent_graph=_FakeAgentGraph(),
        live_state=live_state,
        state_manager=state_manager,
        startup_audit_payload={"config": "redacted"},
        tool_registry=tool_registry,
        project_root=None,
        llm_client=None,
        idle_backoff_seconds=30.0,
        scheduler_enabled=True,
        scheduler_poll_seconds=30.0,
        a2a_poll_seconds=5.0,
        mcp_refresh_seconds=60.0,
        registry_refresh_seconds=60.0,
        heartbeat_health_seconds=30.0,
        timezone="UTC",
        reasoning_enabled=True,
        llm_model="gpt-5.3-chat",
        llm_reasoning_effort="medium",
        audit_log_sink=audit_log_sink,
    )
    graph = create_runtime_heartbeat_graph()

    result = await graph.ainvoke(live_state, context=context)

    assert result["last_cycle_response"] == "ok"
    assert result["heartbeat_action"] == "processed"
    assert result["current_input"] is None
    # checkpoint_node writes per-thread document only (mirror removed)
    assert state_manager.save_current_state.await_count == 1
    state_manager.save_health.assert_awaited()
    health_payload = state_manager.save_health.await_args.args[0]
    assert health_payload["llm_model"] == "gpt-5.3-chat"
    assert health_payload["llm_reasoning_enabled"] is True
    assert health_payload["llm_reasoning_effort"] == "medium"
    assert audit_log_sink.write_log.await_count == 2
    checkpoint_log = audit_log_sink.write_log.await_args_list[-1].kwargs
    assert checkpoint_log["source"] == "checkpoints"
    assert checkpoint_log["payload"]["event"] == "state_checkpoint"
    assert checkpoint_log["payload"]["thread_id"] == "thread-1"


@pytest.mark.asyncio
async def test_runtime_heartbeat_graph_emits_agent_callback_for_notified_input() -> None:
    state_manager = MagicMock()
    state_manager.save_current_state = AsyncMock()
    state_manager.save_health = AsyncMock()

    tool_registry = _tool_registry()

    class _FakeAgentGraph:
        async def ainvoke(self, graph_input, config):  # noqa: ANN001
            assert config == {"configurable": {"thread_id": "thread-1"}}
            assert graph_input["messages"][0].content == "hello"
            return {
                "messages": [{"role": "assistant", "content": "reply-token"}],
                "pending_response": "reply-token",
            }

        async def aupdate_state(self, config, values):  # noqa: ANN001
            return config

    queued_input = {
        "content": "hello",
        "metadata": {
            "notify_on_completion": True,
            "completion_event_type": "agent_message",
        },
    }
    queues = _FakeQueues()
    live_state = {
        "thread_id": "thread-1",
        "messages": [],
        "tool_calls": [],
        "attachments": [],
        "queued_input": queued_input,
    }
    context = _heartbeat_context(
        queues=queues,
        agent_graph=_FakeAgentGraph(),
        live_state=live_state,
        state_manager=state_manager,
        startup_audit_payload={"config": "redacted"},
        tool_registry=tool_registry,
        project_root=None,
        llm_client=None,
        idle_backoff_seconds=30.0,
        scheduler_enabled=True,
        scheduler_poll_seconds=30.0,
        a2a_poll_seconds=5.0,
        mcp_refresh_seconds=60.0,
        registry_refresh_seconds=60.0,
        heartbeat_health_seconds=30.0,
        timezone="UTC",
        reasoning_enabled=False,
    )
    graph = create_runtime_heartbeat_graph()

    await graph.ainvoke(live_state, context=context)

    queues.push_event.assert_any_await(
        "agent_message",
        "reply-token",
        {"source": "heartbeat_input_completion", "thread_id": "thread-1"},
    )


@pytest.mark.asyncio
async def test_runtime_heartbeat_graph_reports_startup_node_errors() -> None:
    state_manager = MagicMock()
    state_manager.save_current_state = AsyncMock()
    state_manager.save_health = AsyncMock()

    tool_registry = _tool_registry()

    class _FakeAgentGraph:
        async def ainvoke(self, graph_input, config):  # noqa: ANN001
            assert config == {"configurable": {"thread_id": "thread-1"}}
            assert graph_input["messages"][0].content == "hello"
            return {
                "messages": [{"role": "assistant", "content": "ok"}],
                "pending_response": "ok",
            }

        async def aupdate_state(self, config, values):  # noqa: ANN001
            return config

    async def _raising_startup_audit(_state, _runtime):  # noqa: ANN001
        raise RuntimeError("startup audit exploded")

    with patch(
        "app.progenitor.graph.heartbeat_graph.startup_audit_node",
        _raising_startup_audit,
    ):
        live_state = {
            "thread_id": "thread-1",
            "messages": [],
            "tool_calls": [],
            "attachments": [],
            "queued_input": "hello",
        }
        queues = _FakeQueues(wait_seconds=0.25, pending_count=2)
        context = _heartbeat_context(
            queues=queues,
            agent_graph=_FakeAgentGraph(),
            live_state=live_state,
            state_manager=state_manager,
            startup_audit_payload={"config": "redacted"},
            tool_registry=tool_registry,
            project_root=None,
            llm_client=None,
            idle_backoff_seconds=30.0,
            scheduler_enabled=True,
            scheduler_poll_seconds=30.0,
            a2a_poll_seconds=5.0,
            mcp_refresh_seconds=60.0,
            registry_refresh_seconds=60.0,
            heartbeat_health_seconds=30.0,
            timezone="UTC",
            reasoning_enabled=False,
        )
        graph = create_runtime_heartbeat_graph()

    result = await graph.ainvoke(live_state, context=context)

    assert result["last_cycle_response"] == "ok"
    queues.push_event.assert_any_await(
        "error",
        "Heartbeat node startup_audit failed: startup audit exploded",
        {"heartbeat_node": "startup_audit"},
    )
    state_manager.save_health.assert_awaited()
    health_payload = state_manager.save_health.await_args.args[0]
    assert health_payload["status"] == "error"
    assert health_payload["error"] == "Heartbeat node startup_audit failed: startup audit exploded"


def test_runtime_heartbeat_graph_rejects_invalid_intervals(tmp_path) -> None:
    with pytest.raises(ValueError, match="input_timeout must be greater than 0"):
        validate_heartbeat_context(
            _heartbeat_context(
                input_timeout=0.0,
            )
        )


def test_runtime_heartbeat_graph_idle_chain_excludes_prompt_cache_refresh() -> None:
    """The periodic refresh node was removed: it shared graph_lock with live
    direct chat turns, so its ~4s rewarm could stall a real user's message
    behind heartbeat maintenance work. The prompt_cache_refresh node was
    fully removed with the prompt-cache-warming feature."""
    graph = create_runtime_heartbeat_graph().get_graph()
    node_names = set(graph.nodes.keys())
    assert "prompt_cache_refresh" not in node_names

    edges = {(edge.source, edge.target) for edge in graph.edges}
    # Idle maintenance chain now runs mcp_refresh directly into a2a_poll.
    assert ("mcp_refresh", "a2a_poll") in edges


def _heartbeat_context(**overrides):
    values = {
        "queues": _FakeQueues(),
        "agent_graph": AsyncMock(),
        "live_state": {},
        "state_manager": MagicMock(),
        "startup_audit_payload": None,
        "tool_registry": _tool_registry(),
        "project_root": None,
        "llm_client": None,
        "idle_backoff_seconds": 30.0,
        "scheduler_enabled": True,
        "scheduler_poll_seconds": 30.0,
        "a2a_poll_seconds": 5.0,
        "async_subagent_poll_seconds": 5.0,
        "mcp_refresh_seconds": 60.0,
        "registry_refresh_seconds": 60.0,
        "heartbeat_health_seconds": 30.0,
        "timezone": "UTC",
        "reasoning_enabled": False,
    }
    values.update(overrides)
    return HeartbeatContext(**values)


class _FakeQueues:
    def __init__(
        self,
        input_item=None,  # noqa: ANN001
        *,
        wait_seconds: float = 0.0,
        pending_count: int = 0,
        a2a_completions: list[A2ATaskRecord] | None = None,
    ) -> None:
        self.input_item = input_item
        self.last_input_timeout: float | None = None
        self.current_input_wait_seconds = wait_seconds
        self.current_input_pending_count = pending_count
        self.a2a_completions = list(a2a_completions or [])
        self.push_event = AsyncMock()
        self.enqueue_input = AsyncMock(return_value=True)

    async def get_input(self, timeout: float):  # noqa: ANN201
        self.last_input_timeout = timeout
        return self.input_item

    def get_queue_stats(self) -> dict[str, dict[str, int | bool]]:
        return {"input_queue": {"size": 0, "maxsize": 5, "full": False}}

    def drain_a2a_completions(self) -> list[A2ATaskRecord]:
        records = self.a2a_completions
        self.a2a_completions = []
        return records


def _tool_registry() -> MagicMock:
    tool_registry = MagicMock()
    tool_registry.get_agents.return_value = []
    return tool_registry
