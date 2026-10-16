"""notify_user writes alert documents; resolve_workflow_approval respects the approver."""

from unittest.mock import AsyncMock

import pytest

from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.resolve_workflow_approval import ResolveWorkflowApprovalTool
from app.progenitor.tools.user_communication import NotifyUserTool


def _context(state_manager, **args):
    return ToolContext(
        tool_registry=None, llm_client=None, state_manager=state_manager, tool_args=args
    )


@pytest.mark.asyncio
async def test_notify_user_saves_an_alert_document():
    manager = AsyncMock()
    result = await NotifyUserTool().execute(
        _context(manager, title="Build", body="Tests failed", level="error")
    )

    assert result.success
    [alert] = manager.save_alert.await_args.args
    assert alert["id"] == result.data["alert_id"]
    assert (alert["title"], alert["message"], alert["type"]) == ("Build", "Tests failed", "error")
    assert alert["urgent"] is True and alert["viewed"] is False
    assert alert["source"] == "notify_user"


def _manager(pending):
    manager = AsyncMock()
    manager.list_workflow_runs.return_value = [
        {"run_id": "run-1", "workflow_id": "wf", "status": "paused", "pending_approval": pending}
    ]
    return manager


@pytest.mark.asyncio
async def test_approval_tool_submits_for_any_approver():
    manager = _manager({"node_id": "gate", "approver": "any"})
    result = await ResolveWorkflowApprovalTool().execute(
        _context(manager, workflow_id="wf", run_id="run-1", approved=True)
    )

    assert result.success and result.data["decision"] == "approved"
    request = manager.request_workflow_approval.await_args.kwargs
    assert (request["workflow_id"], request["run_id"], request["approved"]) == ("wf", "run-1", True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending", "run_id", "message"),
    [
        ({"node_id": "gate", "approver": "human"}, "run-1", "person"),
        ({"node_id": "gate", "approver": "any", "approved": True}, "run-1", "not waiting"),
        ({"node_id": "gate", "approver": "any"}, "other-run", "not waiting"),
    ],
)
async def test_approval_tool_refuses(pending, run_id, message):
    manager = _manager(pending)
    result = await ResolveWorkflowApprovalTool().execute(
        _context(manager, workflow_id="wf", run_id=run_id, approved=True)
    )

    assert not result.success and message in result.error
    manager.request_workflow_approval.assert_not_awaited()
