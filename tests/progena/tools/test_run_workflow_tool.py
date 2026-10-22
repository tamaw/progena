"""Tests for the Progena run_workflow system tool."""

from unittest.mock import AsyncMock, patch

from src.progena.tools.protocol import ToolContext
from src.progena.tools.run_workflow import RunWorkflowTool


def _context(state_manager, tool_args):
    return ToolContext(
        tool_registry=None,
        llm_client=None,
        state_manager=state_manager,
        tool_args=tool_args,
    )


def _definition(**overrides):
    return {"enabled": True, "paused": False, **overrides}


async def test_execute_missing_workflow_id_fails():
    state_manager = AsyncMock()
    result = await RunWorkflowTool().execute(_context(state_manager, {}))
    assert not result.success
    assert "workflow_id" in (result.error or "")


async def test_execute_unknown_workflow_fails():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = None
    result = await RunWorkflowTool().execute(_context(state_manager, {"workflow_id": "missing"}))
    assert not result.success
    state_manager.request_workflow_run.assert_not_awaited()


async def test_execute_disabled_workflow_fails():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = _definition(enabled=False)
    result = await RunWorkflowTool().execute(_context(state_manager, {"workflow_id": "disabled"}))
    assert not result.success
    assert "disabled" in (result.error or "")


async def test_execute_queues_request_without_active_ancestry():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = _definition()
    with patch("src.progena.tools.run_workflow.get_current_thread_id", return_value=None):
        result = await RunWorkflowTool().execute(
            _context(state_manager, {"workflow_id": "wf_a", "input": {"x": 1}})
        )
    assert result.success
    assert (result.data or {})["queued"] is True
    state_manager.request_workflow_run.assert_awaited_once()
    assert state_manager.request_workflow_run.await_args.kwargs["ancestry"] == []


async def test_execute_resolves_ancestry_from_active_run():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = _definition()
    state_manager.list_workflow_runs.return_value = [
        {"thread_id": "t1", "status": "running", "workflow_id": "wf_a", "ancestry": []}
    ]
    with patch("src.progena.tools.run_workflow.get_current_thread_id", return_value="t1"):
        result = await RunWorkflowTool().execute(_context(state_manager, {"workflow_id": "wf_b"}))
    assert result.success
    assert state_manager.request_workflow_run.await_args.kwargs["ancestry"] == ["wf_a"]


async def test_execute_rejects_cycle():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = _definition()
    state_manager.list_workflow_runs.return_value = [
        {"thread_id": "t1", "status": "running", "workflow_id": "wf_a", "ancestry": []}
    ]
    with patch("src.progena.tools.run_workflow.get_current_thread_id", return_value="t1"):
        result = await RunWorkflowTool().execute(_context(state_manager, {"workflow_id": "wf_a"}))
    assert not result.success
    state_manager.request_workflow_run.assert_not_awaited()


async def test_execute_rejects_max_depth():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = _definition()
    state_manager.list_workflow_runs.return_value = [
        {
            "thread_id": "t1",
            "status": "running",
            "workflow_id": "wf_c",
            "ancestry": ["wf_a", "wf_b"],
        }
    ]
    with patch("src.progena.tools.run_workflow.get_current_thread_id", return_value="t1"):
        result = await RunWorkflowTool().execute(_context(state_manager, {"workflow_id": "wf_d"}))
    assert not result.success
    state_manager.request_workflow_run.assert_not_awaited()
