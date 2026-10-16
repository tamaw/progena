"""Let Progenitor answer a workflow run paused at an approval node."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult


class ResolveWorkflowApprovalArgs(BaseModel):
    workflow_id: str = Field(description="The paused run's workflow id (from its alert).")
    run_id: str = Field(description="The paused run's id (from its alert).")
    approved: bool = Field(description="true to approve, false to reject.")


class ResolveWorkflowApprovalTool(BaseSystemTool):
    """Approve or reject a workflow approval node on the user's behalf."""

    @property
    def name(self) -> str:
        return "resolve_workflow_approval"

    @property
    def description(self) -> str:
        return (
            "Approve or reject a workflow run paused at an approval node, when the user "
            "asks you to. The workflow id and run id are in the pause alert (ls /alerts/). "
            "Approvals marked approver 'human' must be decided by a person in the UI."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return ResolveWorkflowApprovalArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("State manager is not available.")
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        workflow_id = str(args.get("workflow_id") or "").strip()
        run_id = str(args.get("run_id") or "").strip()
        approved = args.get("approved")
        if not workflow_id or not run_id:
            return ToolResult.fail("Missing 'workflow_id' or 'run_id'")
        if not isinstance(approved, bool):
            return ToolResult.fail("'approved' must be true or false")

        run = next(
            (
                r
                for r in await context.state_manager.list_workflow_runs()
                if r.get("run_id") == run_id and r.get("workflow_id") == workflow_id
            ),
            None,
        )
        pending = (run or {}).get("pending_approval") or {}
        if run is None or run.get("status") != "paused" or not pending or "approved" in pending:
            return ToolResult.fail(
                f"Run '{run_id}' of workflow '{workflow_id}' is not waiting on an approval."
            )
        if pending.get("approver") == "human":
            return ToolResult.fail(
                "This approval needs a person's decision: ask the user to approve or reject "
                "it in the Progenitor UI (Workflows page or the alert)."
            )

        request_id = f"wfapprove_{uuid.uuid4().hex[:12]}"
        await context.state_manager.request_workflow_approval(
            request_id=request_id, workflow_id=workflow_id, run_id=run_id, approved=approved
        )
        return ToolResult.ok(
            {
                "success": True,
                "submitted": True,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "decision": "approved" if approved else "rejected",
            }
        )


__all__ = ["ResolveWorkflowApprovalTool"]
