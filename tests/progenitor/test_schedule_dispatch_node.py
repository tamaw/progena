"""Tests for schedule dispatch/finalize nodes."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.nodes.schedule_dispatch import create_schedule_finalize_node


@pytest.mark.asyncio
async def test_schedule_finalize_clears_work_flags_when_entry_missing():
    state_manager = MagicMock()
    state_manager.get_schedule = AsyncMock(return_value=[])
    node = create_schedule_finalize_node("UTC", state_manager)

    result = await node(
        {
            "bg_schedule_id": "missing",
            "bg_had_work": True,
            "bg_work_type": "scheduled",
        }
    )

    assert result == {
        "bg_schedule_id": None,
        "bg_had_work": False,
        "bg_work_type": None,
    }


@pytest.mark.asyncio
async def test_schedule_finalize_uses_pending_response_for_scheduled_result():
    state_manager = MagicMock()
    state_manager.get_schedule = AsyncMock(
        return_value=[
            {
                "id": "sched-1",
                "message": "Say hello",
                "scheduled_at": "2026-01-15T10:00:00+00:00",
                "cron_expression": "0 10 15 1 *",
            }
        ]
    )
    state_manager.remove_schedule_entry = AsyncMock(return_value=True)
    event_pusher = AsyncMock()
    node = create_schedule_finalize_node("UTC", state_manager, event_pusher=event_pusher)

    result = await node(
        {
            "bg_schedule_id": "sched-1",
            "pending_response": "Hello from schedule",
        }
    )

    assert result == {"bg_schedule_id": None}
    event_pusher.assert_any_await(
        "scheduled_response",
        "Hello from schedule",
        {
            "schedule_id": "sched-1",
            "schedule_message": "Say hello",
            "is_recurring": False,
            "status": "completed",
        },
    )
