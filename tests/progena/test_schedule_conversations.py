"""Scheduled turns delivered into a chat conversation."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.progena.orchestration.nodes.intake import cycle_setup_node
from src.progena.schedules.dispatch import (
    SCHEDULED_INPUT_PREFIX,
    emit_scheduled_completion,
    enqueue_due_schedule_entries,
)
from src.progena.utils import utcnow


def _state_manager(*, entry: dict[str, Any], session: dict[str, Any] | None) -> MagicMock:
    sm = MagicMock()
    sm.get_schedule = AsyncMock(return_value={entry["id"]: entry})
    sm.get_session = AsyncMock(return_value=session)
    sm.get_preferences_config = AsyncMock(return_value={})
    sm.update_schedule_entry = AsyncMock(return_value=True)
    sm.upsert_schedule_run = AsyncMock(return_value=None)
    sm.update_schedule_run = AsyncMock(return_value=True)
    sm.log_schedule_event = AsyncMock(return_value=None)
    return sm


def _due_entry(**extra: Any) -> dict[str, Any]:
    due = (utcnow() - timedelta(minutes=10)).replace(microsecond=0).isoformat()
    return {
        "id": "sched_chat",
        "message": "check agent X",
        "cron_expression": "*/5 * * * *",
        "created_at": due,
        "last_run": None,
        **extra,
    }


def _queues() -> MagicMock:
    queues = MagicMock()
    queues.enqueue_input = AsyncMock(return_value=True)
    queues.push_event = AsyncMock()
    return queues


async def test_chat_schedule_runs_as_a_turn_in_its_conversation() -> None:
    sm = _state_manager(entry=_due_entry(conversation_id="conv_a"), session={"archived": False})
    queues = _queues()

    fired = await enqueue_due_schedule_entries(sm, queues, current_time=utcnow())

    assert fired == 1
    content = queues.enqueue_input.await_args.args[0]
    metadata = queues.enqueue_input.await_args.kwargs["metadata"]
    assert content.startswith(SCHEDULED_INPUT_PREFIX)
    assert metadata["thread_id"] == "conv_a"
    assert metadata["conversation_id"] == "conv_a"
    assert metadata["source"] == "schedule"
    run = sm.upsert_schedule_run.await_args.args[0]
    assert run.thread_id == "conv_a"
    assert run.conversation_id == "conv_a"


async def test_standalone_schedule_keeps_its_own_thread() -> None:
    sm = _state_manager(entry=_due_entry(conversation_id=None), session=None)
    queues = _queues()

    await enqueue_due_schedule_entries(sm, queues, current_time=utcnow())

    metadata = queues.enqueue_input.await_args.kwargs["metadata"]
    assert metadata["thread_id"].startswith("sched_chat_")
    assert metadata["conversation_id"] is None
    sm.get_session.assert_not_awaited()


@pytest.mark.parametrize(
    ("session", "reason"),
    [(None, "deleted"), ({"archived": True}, "archived")],
)
async def test_schedule_does_not_wake_deleted_or_archived_chat(
    session: dict[str, Any] | None, reason: str
) -> None:
    sm = _state_manager(entry=_due_entry(conversation_id="conv_a"), session=session)
    queues = _queues()

    fired = await enqueue_due_schedule_entries(sm, queues, current_time=utcnow())

    assert fired == 0
    queues.enqueue_input.assert_not_awaited()
    sm.upsert_schedule_run.assert_not_awaited()
    sm.update_schedule_entry.assert_awaited_once()
    assert sm.update_schedule_entry.await_args.args[1]["paused"] is True
    assert reason in sm.log_schedule_event.await_args.args[0]["message"]


async def test_queued_run_is_skipped_when_chat_is_archived_before_it_starts() -> None:
    sm = _state_manager(entry=_due_entry(), session={"archived": True})
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            state_manager=sm,
            queues=SimpleNamespace(current_input_wait_seconds=0.0, current_input_pending_count=0),
            idle_backoff_seconds=1.0,
        )
    )
    state = {
        "queued_input": {
            "content": f"{SCHEDULED_INPUT_PREFIX}check agent X",
            "metadata": {
                "thread_id": "conv_a",
                "conversation_id": "conv_a",
                "schedule_id": "sched_chat",
                "schedule_run_id": "schedrun_1",
                "source": "schedule",
            },
        },
        "thread_id": "progena_main",
    }

    updates = await cycle_setup_node(state, runtime)  # type: ignore[arg-type]

    assert updates["current_input"] is None
    run_update = sm.update_schedule_run.await_args.args[1]
    assert run_update["status"] == "error"
    assert run_update["result_status"] == "skipped"
    assert sm.update_schedule_entry.await_args.args[1]["last_run_status"] == "skipped"


async def test_completion_event_is_scoped_to_the_destination_chat() -> None:
    sm = _state_manager(entry=_due_entry(), session=None)
    queues = _queues()
    state = {
        "is_scheduled_input": True,
        "scheduled_schedule_id": "sched_chat",
        "scheduled_run_id": "schedrun_1",
        "scheduled_message": "check agent X",
        "last_cycle_response": "still running",
        "current_input_metadata": {"conversation_id": "conv_a", "source": "schedule"},
    }

    await emit_scheduled_completion(state, sm, queues)

    event_type, _content, metadata = queues.push_event.await_args_list[0].args
    assert event_type == "scheduled_response"
    assert metadata["thread_id"] == "conv_a"
    assert metadata["conversation_id"] == "conv_a"
