"""Let Progena start a backend-owned workflow run."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from src.progena.llm.structured import validate_against_schema
from src.progena.runs.paths import run_file_path
from src.progena.sessions.context import get_current_thread_id
from src.progena.workflows.constants import MAX_WORKFLOW_INVOKE_DEPTH
from src.progena.workflows.documents import new_workflow_run_id

from .protocol import BaseSystemTool, ToolContext, ToolResult

_ACTIVE_RUN_STATUSES = ("queued", "running", "paused")


class RunWorkflowArgs(BaseModel):
    workflow_id: str = Field(
        description="Id of the workflow definition to run (see ls /workflows/)."
    )
    input: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON payload for the workflow's manual trigger. It must satisfy that "
            "trigger's declared payload schema; property defaults are applied."
        ),
    )


class RunWorkflowTool(BaseSystemTool):
    """Start a durable backend workflow run instead of running it inline."""

    @property
    def name(self) -> str:
        return "run_workflow"

    @property
    def description(self) -> str:
        return (
            "Start a durable run of an existing backend workflow (see "
            "ls /workflows/ for available ids). The backend WorkflowExecutor "
            "runs the nodes on its own execution thread and this call returns "
            "immediately after enqueueing. Provide workflow_id and an optional "
            "JSON input object for its preferred manual trigger. Returns the run's "
            "/runs/workflows/ file path, readable once the run starts. Refuses disabled "
            "or paused workflows, recursive chains deeper than "
            f"{MAX_WORKFLOW_INVOKE_DEPTH} workflows, and workflows already in the "
            "current invocation chain."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return RunWorkflowArgs

    @staticmethod
    def _manual_payload_error(definition: Any, payload: dict[str, Any]) -> str | None:
        """Validate input immediately when the persisted graph has a manual trigger."""

        graph = definition.get("graph") if isinstance(definition, dict) else None
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        if not isinstance(nodes, dict):
            return None
        manual = next(
            (
                node
                for node in nodes.values()
                if isinstance(node, dict) and node.get("kind") == "manual"
            ),
            None,
        )
        if not isinstance(manual, dict):
            return None
        schema = manual.get("output_schema")
        if not isinstance(schema, dict):
            return "manual trigger is missing its payload schema"
        if error := validate_against_schema(payload, schema):
            return f"manual trigger payload failed schema validation: {error}"
        return None

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("State manager is not available.")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        workflow_id = str(args.get("workflow_id") or "").strip()
        if not workflow_id:
            return ToolResult.fail("Missing 'workflow_id'")
        input_payload = args.get("input")
        input_payload = input_payload if isinstance(input_payload, dict) else {}

        definition = await context.state_manager.get_workflow_definition(workflow_id)
        if definition is None:
            return ToolResult.fail(
                f"No workflow found with id '{workflow_id}'. Check ls /workflows/."
            )
        if not definition.get("enabled", True) or definition.get("paused", False):
            return ToolResult.fail(f"Workflow '{workflow_id}' is disabled or paused.")
        if error := self._manual_payload_error(definition, input_payload):
            return ToolResult.fail(f"Workflow '{workflow_id}' {error}.")

        ancestry = await self._resolve_ancestry(context)
        if workflow_id in ancestry:
            return ToolResult.fail(
                f"Refusing to start '{workflow_id}': it is already in this "
                f"invocation chain ({' -> '.join([*ancestry, workflow_id])})."
            )
        if len(ancestry) >= MAX_WORKFLOW_INVOKE_DEPTH:
            return ToolResult.fail(
                f"Refusing to start '{workflow_id}': invocation depth "
                f"{len(ancestry)} already reached the "
                f"{MAX_WORKFLOW_INVOKE_DEPTH}-deep limit (chain: "
                f"{' -> '.join(ancestry)})."
            )

        request_id = f"wfinvoke_{uuid.uuid4().hex[:12]}"
        run_id = new_workflow_run_id()
        await context.state_manager.request_workflow_run(
            request_id=request_id,
            run_id=run_id,
            workflow_id=workflow_id,
            input=input_payload,
            ancestry=ancestry,
        )
        preferences = await context.state_manager.get_preferences_config()
        try:
            tz = ZoneInfo(str(preferences.get("timezone") or "UTC"))
        except (ZoneInfoNotFoundError, ValueError):
            tz = ZoneInfo("UTC")
        path = run_file_path(
            "workflow",
            started_at=datetime.now(UTC),
            title=str(definition.get("title") or workflow_id),
            run_id=run_id,
            tz=tz,
        )
        return ToolResult.ok(
            {
                "success": True,
                "queued": True,
                "workflow_id": workflow_id,
                "request_id": request_id,
                "run_id": run_id,
                # Readable once the run starts; IRC-style log of every step.
                "path": path,
            }
        )

    @staticmethod
    async def _resolve_ancestry(context: ToolContext) -> list[str]:
        """Resolve the active workflow chain from the current execution thread."""

        current_thread_id = get_current_thread_id()
        if not current_thread_id:
            return []
        runs = await context.state_manager.list_workflow_runs()
        for run in runs:
            if run.get("thread_id") != current_thread_id:
                continue
            if run.get("status") not in _ACTIVE_RUN_STATUSES:
                continue
            raw_ancestry = run.get("ancestry")
            ancestry = (
                [str(item) for item in raw_ancestry] if isinstance(raw_ancestry, list) else []
            )
            workflow_id = str(run.get("workflow_id") or "")
            return [*ancestry, workflow_id] if workflow_id else ancestry
        return []


__all__ = ["RunWorkflowTool"]
