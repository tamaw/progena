"""Structured routine runs initiated from a conversational tool call."""

import json
from unittest.mock import AsyncMock

from app.progenitor.agent.adapters import create_deepagent_tools
from app.progenitor.policies import ExecutionPolicy
from app.progenitor.routines.revisions import NullRoutineRevisionReader
from app.progenitor.routines.subagent import create_routine_executor
from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.run_routine import RunRoutineTool


def _graph():
    return {
        "id": "routine_answer",
        "name": "Answer routine",
        "description": "Calculate an answer with a structured question.",
        "version": "2.0.0",
        "entry": "calculate",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
        "nodes": [
            {
                "id": "calculate",
                "kind": "eval",
                "label": "Calculate",
                "output_key": "answer",
                "config": {"expression": "40 + 2"},
            }
        ],
        "edges": [],
    }


def _context(manager, runner, **args):
    return ToolContext(
        tool_registry=None,
        llm_client=None,
        state_manager=manager,
        routine_runner=runner,
        tool_args={"routine_id": "routine_answer", **args},
    )


async def test_run_routine_binds_declared_parameter_and_returns_real_output():
    manager = AsyncMock()
    manager.get_routine_graph.return_value = _graph()
    manager.get_execution_policy.return_value = ExecutionPolicy(
        id="default", name="Default"
    ).model_dump(mode="json")
    manager.get_model_profiles_config.return_value = {}
    executor = create_routine_executor(
        routine_id="routine_answer",
        model=AsyncMock(),
        state_manager=manager,
        revision_reader=NullRoutineRevisionReader(),
        agent_registry={},
        routine_registry={},
    )

    async def run(routine_id, state):
        assert routine_id == "routine_answer"
        return await executor.ainvoke(state)

    late_bindings = {}
    tools = create_deepagent_tools(
        state_manager=manager, live_state_provider=lambda: {}, late_bindings=late_bindings
    )
    late_bindings["routine_runner"] = run
    tool = next(tool for tool in tools if tool.name == "run_routine")
    response = await tool.ainvoke(
        {
            "type": "tool_call",
            "name": "run_routine",
            "id": "test-run",
            "args": {
                "routine_id": "routine_answer",
                "parameters": {"question": "What is 40 + 2?"},
            },
        }
    )
    result = json.loads(response.content)
    assert result["status"] == "completed"
    assert result["output"]["question"] == "What is 40 + 2?"
    assert result["output"]["answer"] == 42
    assert "_execution_policy" not in result["output"]
    assert result["thread_id"]


async def test_run_routine_rejects_missing_or_wrong_typed_parameters_before_execution():
    manager = AsyncMock()
    manager.get_routine_graph.return_value = _graph()
    runner = AsyncMock()
    for parameters in ({}, {"question": 42}):
        result = await RunRoutineTool().execute(_context(manager, runner, parameters=parameters))
        assert not result.success
        assert "Invalid routine parameters" in (result.error or "")
    runner.assert_not_awaited()
