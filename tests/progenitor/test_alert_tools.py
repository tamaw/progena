"""Alert file writes and workflow approval resolution."""

from unittest.mock import AsyncMock

import pytest

from src.progenitor.agent.filesystem.alerts import AlertDirectoryBackend
from src.progenitor.documents.catalog import load_schema
from src.progenitor.llm.structured import validate_against_schema
from src.progenitor.tools.protocol import ToolContext
from src.progenitor.tools.resolve_workflow_approval import ResolveWorkflowApprovalTool


def _context(state_manager, **args):
    return ToolContext(
        tool_registry=None, llm_client=None, state_manager=state_manager, tool_args=args
    )


@pytest.mark.asyncio
async def test_alert_file_write_stamps_and_validates_an_alert():
    manager = AsyncMock()
    manager.list_alerts.return_value = []

    result = await AlertDirectoryBackend(manager).awrite(
        "/build-failed.json",
        '{"title": "Build failed", "message": "Tests failed"}',
    )

    assert result.error is None
    [alert] = manager.save_alert.await_args.args
    assert alert["id"] == "build-failed"
    assert alert["source"] == "progenitor"
    assert alert["timestamp"] > 0
    assert (alert["type"], alert["urgent"], alert["requires_response"], alert["viewed"]) == (
        "info",
        False,
        False,
        False,
    )
    assert validate_against_schema(alert, load_schema("alert")) is None


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
