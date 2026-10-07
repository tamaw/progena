from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.progenitor.nodes.routine_run import routine_run_node


@pytest.mark.asyncio
async def test_routine_run_uses_postgres_backed_runner_not_registry():
    runner = AsyncMock(return_value={"messages": [AIMessage(content="fresh result")]})
    # A stale registry must not affect deterministic execution.  The runner
    # represents the runtime's per-invocation Postgres document resolver.
    context = SimpleNamespace(
        agent_graph=SimpleNamespace(routine_registry={}),
        routine_runner=runner,
    )

    await routine_run_node(
        {"current_input_metadata": {"routine_id": "new-after-start"}},
        Runtime(context=context),
    )

    runner.assert_awaited_once()
    assert runner.await_args.args[0] == "new-after-start"


@pytest.mark.asyncio
async def test_queued_run_deleted_before_execution_still_publishes_failure():
    writer = AsyncMock()
    context = SimpleNamespace(
        routine_runner=AsyncMock(side_effect=KeyError("routine was deleted")),
        state_manager=SimpleNamespace(save_routine_run_event=writer),
    )
    result = await routine_run_node(
        {
            "current_input_metadata": {
                "routine_id": "gone",
                "request_id": "attempt",
                "session_id": "session",
            }
        },
        Runtime(context=context),
    )
    assert "deleted" in result["cycle_error"]
    writer.assert_awaited_once()
    assert writer.await_args.args[0]["status"] == "failed"
    assert writer.await_args.args[0]["request_id"] == "attempt"


@pytest.mark.asyncio
async def test_queued_approval_resumes_exact_interrupt_without_agent_turn():
    resume = AsyncMock(return_value={"status": "completed"})
    runner = AsyncMock()
    await routine_run_node(
        {
            "current_input_metadata": {
                "kind": "routine_run",
                "operation": "resume",
                "routine_id": "approval",
                "thread_id": "routine:approval:rev:session",
                "interrupt_id": "pending",
                "request_id": "answer-request",
                "answer": "decline",
            }
        },
        Runtime(context=SimpleNamespace(routine_resumer=resume, routine_runner=runner)),
    )
    resume.assert_awaited_once_with(
        "routine:approval:rev:session",
        "decline",
        interrupt_id="pending",
        request_id="answer-request",
    )
    runner.assert_not_awaited()
