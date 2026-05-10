"""System tool tests.

System tool implementations live in :mod:`app.progenitor.tools` and are
registered/executed via :mod:`app.progenitor.tools.system_registry`.
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

import app.progenitor.tools.list_agents as list_agents_module
from app.progenitor.state.models import ProgenitorState
from app.progenitor.tools.current_time import CurrentTimeTool
from app.progenitor.tools.list_agents import ListAgentsTool
from app.progenitor.tools.list_schedule import ListScheduleTool
from app.progenitor.tools.list_tools import ListToolsTool
from app.progenitor.tools.protocol import BaseSystemTool, ToolContext, ToolResult
from app.progenitor.tools.schedule import (
    DeleteScheduleTool,
    PauseScheduleTool,
    ResumeScheduleTool,
    UpdateScheduleTool,
)
from app.progenitor.tools.schedule_task import ScheduleTaskTool
from app.progenitor.tools.self_reflect_tool import SelfReflectTool
from app.progenitor.tools.system_registry import execute_system_tool


class TestToolContext:
    """Tests for ToolContext dataclass."""

    def test_creation_with_required_fields(self):
        """Should create with required fields."""
        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
        )
        assert ctx.tool_registry is not None
        assert ctx.llm_client is not None
        assert ctx.state is None
        assert ctx.state_manager is None

    def test_full_creation(self):
        """Should create with all fields."""
        tool_registry = MagicMock()
        llm_client = MagicMock()
        state_manager = MagicMock()

        ctx = ToolContext(
            tool_registry=tool_registry,
            llm_client=llm_client,
            state=cast(ProgenitorState, {"key": "value"}),
            state_manager=state_manager,
        )

        assert ctx.tool_registry is tool_registry
        assert ctx.llm_client is llm_client
        assert ctx.state == {"key": "value"}
        assert ctx.state_manager is state_manager


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_success_result(self):
        """Should create success result."""
        result = ToolResult.ok(data={"key": "value"})
        assert result.success is True
        assert result.data == {"key": "value"}
        assert result.error is None

    def test_failure_result(self):
        """Should create failure result."""
        result = ToolResult.fail(error="Operation failed")
        assert result.success is False
        assert result.error == "Operation failed"

    def test_direct_creation(self):
        """Should create result directly."""
        result = ToolResult(
            success=True,
            data={"count": 5},
        )
        assert result.success is True
        assert result.data == {"count": 5}


class TestBaseSystemTool:
    """Tests for BaseSystemTool abstract class."""

    def test_subclass_must_implement_name(self):
        """Subclass must implement name property."""

        class TestTool(BaseSystemTool):
            @property
            def name(self):
                return "test_tool"

            @property
            def description(self):
                return "Test tool"

            async def execute(self, context: ToolContext) -> ToolResult:
                return ToolResult.ok(data={"status": "done"})

        tool = TestTool()
        assert tool.name == "test_tool"

    def test_subclass_must_implement_description(self):
        """Subclass must implement description property."""

        class TestTool(BaseSystemTool):
            @property
            def name(self):
                return "test_tool"

            @property
            def description(self):
                return "Test description"

            async def execute(self, context: ToolContext) -> ToolResult:
                return ToolResult.ok(data={"status": "done"})

        tool = TestTool()
        assert tool.description == "Test description"

    def test_parameters_are_derived_from_args_model(self):
        """args_model should be the source of truth for public tool parameters."""

        class TestArgs(BaseModel):
            value: str = Field(description="Primary value")
            note: str | None = Field(default=None, description="Optional note")

        class TestTool(BaseSystemTool):
            @property
            def name(self):
                return "test_tool"

            @property
            def description(self):
                return "Test description"

            @property
            def args_model(self):
                return TestArgs

            async def execute(self, context: ToolContext) -> ToolResult:
                return ToolResult.ok(data={"status": "done"})

        params = TestTool().parameters

        assert params["type"] == "object"
        assert params["required"] == ["value"]
        assert params["properties"]["value"]["type"] == "string"
        assert params["properties"]["note"]["type"] == "string"
        assert "anyOf" not in params["properties"]["note"]
        assert "title" not in params


class TestListToolsTool:
    """Tests for ListToolsTool implementation."""

    @pytest.fixture
    def tool(self):
        return ListToolsTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "list_tools"

    def test_description(self, tool):
        """Should have description."""
        assert len(tool.description) > 0

    async def test_execute_with_registry(self, tool):
        """Should list tools from registry."""
        mock_registry = MagicMock()
        mock_registry.get_agents.return_value = [
            {"name": "agent1", "description": "First agent", "status": "running"},
        ]
        mock_registry.get_tools.return_value = [
            {"name": "tool1", "description": "First tool"},
            {"name": "tool2", "description": "Second tool"},
        ]
        mock_registry.get_local_mcp_tools.return_value = [
            {"name": "local_tool", "description": "Local MCP tool"},
        ]

        mock_llm = MagicMock()
        mock_llm.mcp_runtime = MagicMock()
        mock_llm.mcp_runtime.get_tool_registry_entries.return_value = [
            {"name": "remote_tool", "description": "Remote MCP tool"},
        ]

        ctx = ToolContext(
            tool_registry=mock_registry,
            llm_client=mock_llm,
            state={},
            state_manager=MagicMock(),
        )
        result = await tool.execute(ctx)
        assert result.success is True
        assert result.data is not None
        assert "system_tools" in result.data
        assert "agents" in result.data
        assert "mcp_tools" in result.data

        mcp_names = {t.get("name") for t in result.data["mcp_tools"]}
        assert {"tool1", "tool2", "local_tool", "remote_tool"} <= mcp_names

        system_names = {t.get("name") for t in result.data["system_tools"]}
        assert len(system_names) > 0

        agent_names = {a.get("name") for a in result.data["agents"]}
        assert "agent1" in agent_names


class TestCurrentTimeTool:
    """Tests for CurrentTimeTool implementation."""

    @pytest.fixture
    def tool(self):
        return CurrentTimeTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "current_time"

    async def test_execute_returns_time(self, tool):
        """Should return current time."""
        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state={},
            state_manager=MagicMock(),
        )
        result = await tool.execute(ctx)
        assert result.success is True


class TestListScheduleTool:
    """Tests for ListScheduleTool implementation."""

    @pytest.fixture
    def tool(self):
        return ListScheduleTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "list_schedule"

    async def test_execute_empty_schedule(self, tool):
        """Should handle empty schedule."""
        mock_state_manager = MagicMock()
        mock_state_manager.get_schedule = AsyncMock(return_value=[])

        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state={"schedule": []},
            state_manager=mock_state_manager,
        )
        result = await tool.execute(ctx)
        assert result.success is True

    async def test_execute_prefers_state_manager_over_graph_state(self, tool):
        """Should read the persisted schedule from state_manager when available."""
        mock_state_manager = MagicMock()
        mock_state_manager.get_schedule = AsyncMock(
            return_value=[
                {
                    "id": "sched_live",
                    "message": "Live schedule",
                    "cron_expression": "0 8 * * *",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "last_run": None,
                }
            ]
        )

        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state={"schedule": [{"id": "sched_stale", "message": "Stale schedule"}]},
            state_manager=mock_state_manager,
        )

        result = await tool.execute(ctx)

        assert result.success is True
        assert result.data is not None
        assert result.data["scheduled_tasks"][0]["id"] == "sched_live"


class TestScheduleLifecycleTools:
    """Tests for schedule lifecycle system tool wrappers."""

    @pytest.mark.parametrize(
        ("tool_cls", "expected_name"),
        [
            (DeleteScheduleTool, "delete_schedule"),
            (UpdateScheduleTool, "update_schedule"),
            (PauseScheduleTool, "pause_schedule"),
            (ResumeScheduleTool, "resume_schedule"),
        ],
    )
    def test_name(self, tool_cls, expected_name):
        tool = tool_cls()
        assert tool.name == expected_name


class TestScheduleTaskTool:
    """Tests for ScheduleTaskTool implementation."""

    @pytest.fixture
    def tool(self):
        return ScheduleTaskTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "schedule_task"

    async def test_execute_fails_without_schedule_fields(self, tool):
        """Should fail without explicit cron_expression or scheduled_at."""
        mock_llm = MagicMock()
        mock_state_manager = AsyncMock()

        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=mock_llm,
            state=cast(ProgenitorState, {"schedule": []}),
            state_manager=mock_state_manager,
            tool_args={
                "user_request": "remind me to stretch in 5 minutes",
                "instruction": "Stretch",
            },
        )

        result = await tool.execute(ctx)

        assert result.success is False
        assert "exactly one" in str(result.error).lower()

    async def test_execute_uses_explicit_tool_call_fields(self, tool):
        """Should create schedules from explicit tool-call fields without LLM extraction."""
        mock_llm = MagicMock()
        mock_llm.chat_structured = AsyncMock()
        mock_llm.chat = AsyncMock()
        mock_state_manager = AsyncMock()

        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=mock_llm,
            state=cast(ProgenitorState, {"schedule": []}),
            state_manager=mock_state_manager,
            tool_args={
                "user_request": "can you schedule to say hi to me tomorrow",
                "instruction": "Say hi to the user",
                "description": "Say hi reminder",
                "scheduled_at": "2026-03-20T09:42:27Z",
            },
        )

        result = await tool.execute(ctx)

        assert result.success is True
        mock_llm.chat_structured.assert_not_awaited()
        mock_llm.chat.assert_not_awaited()


class TestListAgentsTool:
    """Tests for ListAgentsTool implementation."""

    @pytest.fixture
    def tool(self):
        return ListAgentsTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "list_agents"

    @pytest.mark.asyncio
    async def test_execute_refreshes_cards_before_listing(self, tool):
        """list_agents should refresh metadata and health before returning output."""
        mock_registry = MagicMock()
        mock_registry.load = AsyncMock()
        mock_registry.get_agents.return_value = []
        ctx = ToolContext(tool_registry=mock_registry, llm_client=MagicMock())

        refresh_mock = AsyncMock(
            return_value={"agent_card_refresh": {}, "agent_health_refresh": {}},
        )

        with patch.object(list_agents_module, "refresh_agent_metadata", refresh_mock):
            result = await tool.execute(ctx)

        assert result.success is True
        mock_registry.load.assert_awaited_once()
        refresh_mock.assert_awaited_once_with(mock_registry)


class TestSelfReflectTool:
    """Tests for SelfReflectTool implementation."""

    @pytest.fixture
    def tool(self):
        return SelfReflectTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "self_reflect"


class TestExecuteSystemTool:
    """Tests for execute_system_tool registry function."""

    async def test_execute_known_tool(self):
        """Should execute known tool and return tuple."""
        mock_registry = MagicMock()
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        # execute_system_tool returns (result_dict, error, state_updates, should_retry_slow_path)
        result, error, state_updates, should_retry = await execute_system_tool(
            "current_time",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is None
        assert result is not None
        assert should_retry is False

    async def test_execute_unknown_tool(self):
        """Should return error for unknown tool."""
        mock_registry = MagicMock()
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        result, error, state_updates, should_retry = await execute_system_tool(
            "unknown_tool_xyz",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is not None
        assert "Unknown system tool" in error
        assert result is None
        assert should_retry is False

    async def test_execute_list_tools(self):
        """Should execute list_tools with registry."""
        mock_registry = MagicMock()
        mock_registry.get_all_tools.return_value = []
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        result, error, state_updates, should_retry = await execute_system_tool(
            "list_tools",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is None
        assert result is not None
        assert should_retry is False

    async def test_execute_delete_schedule_uses_state_manager(self):
        """Should execute delete_schedule through the discoverable system tool wrapper."""
        mock_registry = MagicMock()
        mock_llm = MagicMock()
        state_manager = AsyncMock()
        state_manager.remove_schedule_entry = AsyncMock(return_value=True)

        result, error, state_updates, should_retry = await execute_system_tool(
            "delete_schedule",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
            tool_args={"schedule_id": "sched_abc123"},
        )

        assert error is None
        assert result is not None
        assert result["schedule_id"] == "sched_abc123"
        assert should_retry is False
        state_manager.remove_schedule_entry.assert_awaited_once_with("sched_abc123")
