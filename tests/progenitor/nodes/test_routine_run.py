"""Tests for routine_run_node."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.progenitor.orchestration.nodes.routine_run import routine_run_node
from src.progenitor.orchestration.state import ProgenitorState


class TestRoutineRunResume:
    async def test_missing_metadata_routes_to_failed(self):
        state: ProgenitorState = {
            "messages": [],
            "current_input_metadata": {
                "kind": "routine_run",
                "operation": "resume",
                "routine_id": "test-routine",
                # missing thread_id, answer, interrupt_id, request_id
            },
        }
        runtime = MagicMock()
        runtime.context.state_manager = AsyncMock()
        runtime.context.routine_resumer = AsyncMock()

        result = await routine_run_node(state, runtime)
        assert "cycle_error" in result
        assert "missing required field" in result["cycle_error"]
        runtime.context.state_manager.save_routine_run_event.assert_awaited_once()
        runtime.context.routine_resumer.assert_not_awaited()

    async def test_resume_with_all_metadata_succeeds(self):
        state: ProgenitorState = {
            "messages": [],
            "current_input_metadata": {
                "kind": "routine_run",
                "operation": "resume",
                "routine_id": "test-routine",
                "thread_id": "routine:test-routine:abc",
                "answer": "yes",
                "interrupt_id": "i1",
                "request_id": "r1",
            },
        }
        runtime = MagicMock()
        runtime.context.state_manager = AsyncMock()
        runtime.context.routine_resumer = AsyncMock()

        result = await routine_run_node(state, runtime)
        assert result == {}
        runtime.context.routine_resumer.assert_awaited_once_with(
            "routine:test-routine:abc",
            "yes",
            interrupt_id="i1",
            request_id="r1",
        )
