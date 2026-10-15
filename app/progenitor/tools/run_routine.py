"""Run a stored routine with its declared, structured input parameters."""

from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from ..routines.subagent import resolve_routine_document
from ..structured import validate_against_schema
from .protocol import BaseSystemTool, ToolContext, ToolResult


class RunRoutineArgs(BaseModel):
    routine_id: str = Field(description="Stored routine id or display name.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Named values required by the routine's input_schema.",
    )
    request: str = Field(
        default="",
        description="Optional natural-language request for routines that read {request}.",
    )


class RunRoutineTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "run_routine"

    @property
    def description(self) -> str:
        return (
            "Run a stored routine and return its output. Read its graph at /routines/<id>.json "
            "first; supply every required input_schema field in parameters. Use this instead "
            "of task() when the routine declares structured inputs."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return RunRoutineArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args or {}
        routine_id = str(args.get("routine_id") or "").strip()
        if not routine_id:
            return ToolResult.fail("routine_id is required")
        if context.state_manager is None or context.routine_runner is None:
            return ToolResult.fail("Routine execution is unavailable")

        resolved = await resolve_routine_document(context.state_manager, routine_id)
        if resolved is None:
            return ToolResult.fail(f"Unknown routine: {routine_id}")
        canonical_id, graph = resolved
        parameters = args.get("parameters") or {}
        if not isinstance(parameters, dict):
            return ToolResult.fail("parameters must be an object")
        input_schema = graph.get("input_schema")
        if input_schema:
            error = validate_against_schema(parameters, input_schema)
            if error is not None:
                return ToolResult.fail(f"Invalid routine parameters: {error}")

        request = str(args.get("request") or "")
        state = {
            "parameters": parameters,
            "messages": [HumanMessage(content=request)] if request else [],
        }
        try:
            result = await context.routine_runner(canonical_id, state)
        except Exception as exc:
            return ToolResult.fail(f"Routine {canonical_id} failed: {exc}")
        return ToolResult.ok(
            {
                "routine_id": canonical_id,
                "thread_id": result.get("_routine_thread_id"),
                "status": "paused" if result.get("_routine_paused") else "completed",
                "output": {
                    key: value
                    for key, value in result.get("data", {}).items()
                    if not key.startswith("_")
                },
            }
        )
