from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.schedule_results import (
    GetScheduleRunResultTool,
    ListScheduleRunsTool,
)


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

    with patch(
        "app.progenitor.tools.schedule_results._load_checkpoint_snapshot",
        return_value={
            "thread_id": "sched_report_abcd1234",
            "checkpoint_id": "cp_1",
            "checkpoint_ts": "2026-06-19T12:01:00+00:00",
            "assistant_response": "Report complete",
            "messages": [
                {"role": "user", "content": "Run schedule"},
                {"role": "assistant", "content": "Report complete"},
            ],
        },
    ):
        result = await tool.execute(
            ToolContext(
                tool_registry=MagicMock(),
                llm_client=MagicMock(),
                state_manager=state_manager,
                tool_args={"run_id": "schedrun_123", "include_messages": True, "message_limit": 2},
            )
        )

    assert result.success is True
    assert result.data is not None
    assert result.data["run"]["run_id"] == "schedrun_123"
    assert result.data["schedule"]["id"] == "sched_report"
    assert result.data["checkpoint"]["assistant_response"] == "Report complete"
    assert len(result.data["checkpoint"]["messages"]) == 2


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

    with patch(
        "app.progenitor.tools.schedule_results._load_checkpoint_snapshot", return_value=None
    ):
        result = await tool.execute(
            ToolContext(
                tool_registry=MagicMock(),
                llm_client=MagicMock(),
                state_manager=state_manager,
                tool_args={"run_id": "schedrun_123"},
            )
        )

    assert result.success is True
    assert result.data is not None
    assert "checkpoint_note" in result.data
