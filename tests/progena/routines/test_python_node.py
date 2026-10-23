"""Behavior tests for remote sandbox-backed Python routine nodes."""

from __future__ import annotations

from typing import Any

import pytest

from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload


def graph(*nodes: dict[str, Any], edges: list[dict[str, Any]] | None = None):
    return validate_routine_graph_payload(
        {
            "id": "sandbox-python",
            "name": "Sandbox Python",
            "description": "remote Python node tests",
            "version": "2.0.0",
            "entry": nodes[0]["id"],
            "nodes": list(nodes),
            "edges": edges or [],
        }
    )


def python_node(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": "calculate",
        "kind": "python",
        "output_key": "calculation",
        "config": (
            {
                "code": "result = {'answer': inputs['value'] * 2}",
                "input_map": {"value": "payload.value"},
            }
            if config is None
            else config
        ),
    }


@pytest.mark.asyncio
async def test_python_node_binds_inputs_writes_result_and_drives_branch() -> None:
    async def runner(request):
        return {
            "sandbox_id": "sandbox-1",
            "result": {"answer": request.inputs["value"] * 2},
            "stdout": "",
            "stderr": "",
            "output_files": ["/artifacts/report.json"],
            "deleted": True,
        }

    routine = graph(
        python_node(
            {
                "code": "result = {'answer': inputs['value'] * 2}",
                "input_map": {"value": "payload.value"},
                "input_files": {"data/input.csv": "/artifacts/input.csv"},
                "output_files": {"data/report.json": "/artifacts/report.json"},
                "timeout_seconds": 45,
                "response_schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "integer"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            }
        ),
        {
            "id": "gate",
            "kind": "condition",
            "output_key": "gate",
            "config": {"expression": "calculation.answer == 42"},
        },
        {
            "id": "accepted",
            "kind": "eval",
            "output_key": "accepted",
            "config": {"expression": "calculation.answer + 1"},
        },
        {"id": "rejected", "kind": "eval", "output_key": "rejected", "config": {"expression": "0"}},
        edges=[
            {"from": "calculate", "to": "gate", "level": "normal"},
            {
                "from": "gate",
                "level": "route",
                "router": "state_equals",
                "state_key": "gate",
                "branches": {"true": "accepted", "false": "rejected"},
            },
        ],
    )

    result = await compile_routine_graph(
        routine, model=object(), sandbox_python_runner=runner
    ).ainvoke({"data": {"payload": {"value": 21}}})

    assert result["data"]["calculation"] == {"answer": 42}
    assert result["data"]["accepted"] == 43
    assert "rejected" not in result["data"]


@pytest.mark.asyncio
async def test_python_node_propagates_sandbox_failure_without_host_fallback() -> None:
    async def runner(request):
        raise RuntimeError("remote sandbox failed")

    routine = graph(python_node())
    compiled = compile_routine_graph(routine, model=object(), sandbox_python_runner=runner)

    with pytest.raises(RuntimeError, match="remote sandbox failed"):
        await compiled.ainvoke({"data": {"payload": {"value": 21}}})


@pytest.mark.asyncio
async def test_python_node_rejects_result_that_fails_response_schema() -> None:
    async def runner(request):
        return {"result": {"answer": "not-an-integer"}}

    routine = graph(
        python_node(
            {
                "code": "result = {'answer': 'not-an-integer'}",
                "response_schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "integer"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            }
        )
    )
    compiled = compile_routine_graph(routine, model=object(), sandbox_python_runner=runner)

    with pytest.raises(ValueError, match="python node calculate: result failed schema validation"):
        await compiled.ainvoke({"data": {}})


def test_python_node_refuses_an_unavailable_sandbox_executor() -> None:
    with pytest.raises(
        ValueError, match="python node calculate: sandbox Python executor is unavailable"
    ):
        compile_routine_graph(graph(python_node()), model=object())


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"code": "result = 1", "code_file": "/routines/program.py"},
    ],
)
def test_python_node_requires_exactly_one_code_source(config: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="schema validation failed"):
        graph(python_node(config))
