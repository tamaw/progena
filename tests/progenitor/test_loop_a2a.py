"""Tests for graph-native A2A completion recording."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.runtime import Runtime

from app.progenitor.nodes.a2a import apply_completed_tasks_node
from app.progenitor.nodes.context import HeartbeatContext
from app.progenitor.state.models import A2ATaskRecord


@pytest.mark.asyncio
async def test_a2a_completion_node_persists_one_tool_message(tmp_path: Path) -> None:
    state_manager = MagicMock()
    state_manager.log_conversation = AsyncMock()
    state_manager.save_messages = AsyncMock()

    record = A2ATaskRecord(
        task_id="observe-5",
        agent_name="observe_agent",
        status="completed",
        summary="Observe task completed.",
    )
    state = {"messages": [], "a2a_completed_tasks": [record, record]}
    runtime = Runtime(
        context=HeartbeatContext(
            queues=MagicMock(),
            agent_graph=MagicMock(),
            live_state={},
            state_manager=state_manager,
            health_state_dir=tmp_path,
            audit_logs_dir=None,
            startup_audit_payload=None,
            tool_registry=MagicMock(),
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
    )

    updates = await apply_completed_tasks_node(state, runtime)

    assert len(updates["messages"]) == 1
    message = updates["messages"][0]
    assert message.role == "tool"
    assert message.metadata["type"] == "a2a_task_completion"
    assert message.metadata["task_id"] == "observe-5"
    assert "observe agent task completed" in message.content
    state_manager.log_conversation.assert_awaited_once()
    state_manager.save_messages.assert_awaited_once()
