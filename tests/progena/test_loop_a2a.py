"""Tests for graph-native A2A completion recording."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.runtime import Runtime

from src.progena.a2a.models import A2ATaskRecord
from src.progena.orchestration.nodes.a2a import apply_completed_tasks_node
from src.progena.orchestration.nodes.context import HeartbeatContext


@pytest.mark.asyncio
async def test_a2a_completion_node_logs_once_without_touching_the_loop_thread() -> None:
    state_manager = MagicMock()
    state_manager.log_conversation = AsyncMock()

    record = A2ATaskRecord(
        task_id="example-5",
        agent_name="example_agent",
        status="completed",
        summary="Example task completed.",
    )
    state = {"thread_id": "loop-thread", "messages": [], "a2a_completed_tasks": [record, record]}
    agent_graph = AsyncMock()
    runtime = Runtime(
        context=HeartbeatContext(
            queues=MagicMock(),
            agent_graph=agent_graph,
            live_state={},
            state_manager=state_manager,
            startup_audit_payload=None,
            tool_registry=MagicMock(),
            project_root=None,
            llm_client=None,
            idle_backoff_seconds=30.0,
            scheduler_enabled=True,
            scheduler_poll_seconds=30.0,
            a2a_poll_seconds=5.0,
            async_subagent_poll_seconds=5.0,
            mcp_refresh_seconds=60.0,
            plugin_update_check_seconds=86400.0,
            registry_refresh_seconds=60.0,
            repo_scan_seconds=3600.0,
            worktree_dir=None,
            heartbeat_health_seconds=30.0,
            timezone="UTC",
            reasoning_enabled=False,
        )
    )

    updates = await apply_completed_tasks_node(state, runtime)

    assert len(updates["messages"]) == 1
    message = updates["messages"][0]
    assert message.role == "tool"
    assert message.metadata["type"] == "a2a_task_completion"
    assert message.metadata["task_id"] == "example-5"
    assert "example agent task completed" in message.content
    state_manager.log_conversation.assert_awaited_once()
    # A tool result with no matching model tool call poisons the thread for the
    # provider; the originating thread gets its own follow-up turn instead.
    agent_graph.aupdate_state.assert_not_called()
