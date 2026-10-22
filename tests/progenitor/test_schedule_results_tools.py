from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph

from src.progenitor.tools.protocol import ToolContext
from src.progenitor.tools.schedule_results import (
    GetScheduleRunResultTool,
    ListScheduleRunsTool,
)


async def _thread_state_reader(thread_id: str, messages: list[Any]) -> Any:
    """A real checkpointed graph whose run thread holds ``messages``."""
    builder = StateGraph(MessagesState)
    builder.add_node("record", lambda _state: {"messages": messages})
    builder.add_edge(START, "record")
    graph = builder.compile(checkpointer=InMemorySaver())
    await graph.ainvoke({"messages": []}, {"configurable": {"thread_id": thread_id}})

    async def thread_state(requested_thread_id: str) -> Any:
        return await graph.aget_state({"configurable": {"thread_id": requested_thread_id}})

    return thread_state


@pytest.mark.asyncio
async def test_list_schedule_runs_filters_and_sorts_results() -> None:
    state_manager = MagicMock()
    state_manager.get_schedule_runs = AsyncMock(
        return_value=[
            {
                "run_id": "schedrun_old",
                "schedule_id": "cron_alpha",
                "thread_id": "cron_alpha_old",
                "message": "Old run",
                "status": "completed",
                "result_status": "completed",
                "created_at": "2026-06-19T10:00:00+00:00",
            },
            {
                "run_id": "schedrun_new",
                "schedule_id": "cron_alpha",
                "thread_id": "cron_alpha_new",
                "message": "New run",
                "status": "error",
                "result_status": "error",
                "created_at": "2026-06-19T11:00:00+00:00",
                "error": "boom",
            },
            {
                "run_id": "schedrun_other",
                "schedule_id": "sched_beta",
                "thread_id": "sched_beta_new",
                "message": "Other run",
                "status": "completed",
                "result_status": "completed",
                "created_at": "2026-06-19T12:00:00+00:00",
            },
        ]
    )
    tool = ListScheduleRunsTool()

    result = await tool.execute(
        ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state_manager=state_manager,
            tool_args={"schedule_id": "cron_alpha", "limit": 5},
        )
    )

    assert result.success is True
    assert result.data is not None
    assert [run["run_id"] for run in result.data["runs"]] == ["schedrun_new", "schedrun_old"]
    assert result.data["runs"][0]["schedule_type"] == "cron"


@pytest.mark.asyncio
async def test_get_schedule_run_result_includes_checkpoint_snapshot() -> None:
    state_manager = MagicMock()
    state_manager.get_schedule_runs = AsyncMock(
        return_value=[
            {
                "run_id": "schedrun_123",
                "schedule_id": "sched_report",
                "thread_id": "sched_report_abcd1234",
                "message": "Write report",
                "status": "completed",
                "result_status": "completed",
                "created_at": "2026-06-19T12:00:00+00:00",
                "response": "Report complete",
            }
        ]
    )
    state_manager.get_schedule = AsyncMock(
        return_value={
            "sched_report": {
                "id": "sched_report",
                "message": "Write report",
                "cron_expression": "0 9 * * *",
            }
        }
    )
    tool = GetScheduleRunResultTool()
    thread_state = await _thread_state_reader(
        "sched_report_abcd1234",
        [
            HumanMessage(content="Run schedule"),
            AIMessage(content="Working on it"),
            HumanMessage(content="Continue"),
            AIMessage(content="Report complete"),
        ],
    )

    result = await tool.execute(
        ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state_manager=state_manager,
            tool_args={"run_id": "schedrun_123", "include_messages": True, "message_limit": 2},
            thread_state=thread_state,
        )
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["run"]["run_id"] == "schedrun_123"
    assert result.data["schedule"]["id"] == "sched_report"
    checkpoint = result.data["checkpoint"]
    assert checkpoint["thread_id"] == "sched_report_abcd1234"
    assert checkpoint["checkpoint_id"]
    assert checkpoint["assistant_response"] == "Report complete"
    assert checkpoint["messages"] == [
        {"role": "human", "content": "Continue"},
        {"role": "ai", "content": "Report complete"},
    ]


@pytest.mark.asyncio
async def test_get_schedule_run_result_returns_checkpoint_note_when_unavailable() -> None:
    state_manager = MagicMock()
    state_manager.get_schedule_runs = AsyncMock(
        return_value=[
            {
                "run_id": "schedrun_123",
                "schedule_id": "cron_alpha",
                "thread_id": "cron_alpha_abcd1234",
                "message": "Ping",
                "status": "completed",
                "result_status": "completed",
                "created_at": "2026-06-19T12:00:00+00:00",
            }
        ]
    )
    state_manager.get_schedule = AsyncMock(return_value={})
    tool = GetScheduleRunResultTool()
    thread_state = await _thread_state_reader("some_other_thread", [AIMessage(content="x")])

    result = await tool.execute(
        ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state_manager=state_manager,
            tool_args={"run_id": "schedrun_123"},
            thread_state=thread_state,
        )
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["checkpoint"] is None
    assert "checkpoint_note" in result.data
