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
