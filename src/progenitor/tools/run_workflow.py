"""Let Progenitor start a backend-owned workflow run."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from src.progenitor.sessions.context import get_current_thread_id
from src.progenitor.workflows.constants import MAX_WORKFLOW_INVOKE_DEPTH

from .protocol import BaseSystemTool, ToolContext, ToolResult

_ACTIVE_RUN_STATUSES = ("queued", "running", "paused")


class RunWorkflowArgs(BaseModel):
    workflow_id: str = Field(
        description="Id of the workflow definition to run (see ls /workflows/)."
    )
    input: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON object passed as the triggering event payload.",
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
            "JSON input object. Refuses disabled or paused workflows, recursive "
            f"chains deeper than {MAX_WORKFLOW_INVOKE_DEPTH} workflows, and "
            "workflows already in the current invocation chain."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return RunWorkflowArgs

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
        await context.state_manager.request_workflow_run(
            request_id=request_id,
            workflow_id=workflow_id,
            input=input_payload,
            ancestry=ancestry,
        )
        return ToolResult.ok(
            {
                "success": True,
                "queued": True,
                "workflow_id": workflow_id,
                "request_id": request_id,
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
