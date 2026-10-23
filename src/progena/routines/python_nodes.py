"""Remote sandbox Python node builder for declarative routine graphs."""

from __future__ import annotations

from typing import Any

from src.progena.llm.structured import validate_against_schema
from src.progena.sandboxes.execution import SandboxPythonRequest, SandboxPythonRunner

from .graph import RoutineNode
from .state import RoutineState, _node_config, _resolve_inputs, _resolve_response_schema


def _build_python_node(
    node: RoutineNode,
    *,
    sandbox_python_runner: SandboxPythonRunner | None,
    defs: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Build a node that delegates all Python execution to the sandbox boundary."""
    if sandbox_python_runner is None:
        raise ValueError(f"python node {node.id}: sandbox Python executor is unavailable")

    config = _node_config(node)
    output_key = node.output_key or node.id
    input_map = config.get("input_map") or {}
    response_schema = _resolve_response_schema(node.id, "python", config, defs)

    async def run(state: RoutineState) -> dict[str, Any]:
        request = SandboxPythonRequest(
            code=config.get("code"),
            code_file=config.get("code_file"),
            inputs=_resolve_inputs(node.id, input_map, state.get("data", {})),
            input_files=config.get("input_files") or {},
            output_files=config.get("output_files") or {},
            timeout_seconds=config.get("timeout_seconds"),
        )
        envelope = await sandbox_python_runner(request)
        if not isinstance(envelope, dict) or "result" not in envelope:
            raise ValueError(f"python node {node.id}: sandbox executor returned no result")
        result = envelope["result"]
        if response_schema is not None:
            error = validate_against_schema(result, response_schema)
            if error is not None:
                raise ValueError(f"python node {node.id}: result failed schema validation: {error}")
        return {"data": {output_key: result}}

    return run
