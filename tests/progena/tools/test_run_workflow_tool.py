"""Tests for the Progena run_workflow system tool."""

import re
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


def _manual_definition(**overrides):
    return _definition(
        graph={
            "nodes": {
                "manual": {
                    "kind": "manual",
                    "output_schema": {
                        "type": "object",
                        "required": ["run_id"],
                        "properties": {
                            "run_id": {"type": "string"},
                            "round_count": {"type": "integer", "default": 3},
                        },
                    },
                }
            }
        },
        **overrides,
    )


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


async def test_execute_rejects_invalid_manual_payload_before_enqueueing():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = _manual_definition()

    result = await RunWorkflowTool().execute(
        _context(state_manager, {"workflow_id": "manual", "input": {"run_id": 7}})
    )

    assert not result.success
    assert "manual trigger payload failed schema validation" in (result.error or "")
    state_manager.request_workflow_run.assert_not_awaited()


async def test_execute_applies_manual_defaults_before_validating_and_enqueueing():
    state_manager = AsyncMock()
    definition = _manual_definition()
    definition["graph"]["nodes"]["manual"]["output_schema"] = {
        "type": "object",
        "required": ["n"],
        "properties": {"n": {"type": "integer", "default": 3}},
    }
    state_manager.get_workflow_definition.return_value = definition
    state_manager.get_preferences_config.return_value = {}

    with patch("src.progena.tools.run_workflow.get_current_thread_id", return_value=None):
        result = await RunWorkflowTool().execute(_context(state_manager, {"workflow_id": "manual"}))

    assert result.success
    state_manager.request_workflow_run.assert_awaited_once()
    assert state_manager.request_workflow_run.await_args.kwargs["input"] == {"n": 3}


async def test_execute_queues_manual_trigger_payload_without_active_ancestry():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = _manual_definition()
    state_manager.get_preferences_config.return_value = {"timezone": "Australia/Melbourne"}
    with patch("src.progena.tools.run_workflow.get_current_thread_id", return_value=None):
        result = await RunWorkflowTool().execute(
            _context(state_manager, {"workflow_id": "wf_a", "input": {"run_id": "round-a"}})
        )
    assert result.success
    assert (result.data or {})["queued"] is True
    state_manager.request_workflow_run.assert_awaited_once()
    assert state_manager.request_workflow_run.await_args.kwargs["input"] == {
        "run_id": "round-a",
        "round_count": 3,
    }
    assert state_manager.request_workflow_run.await_args.kwargs["ancestry"] == []
    result_data = result.data or {}
    assert re.fullmatch(r"workflow_run_[0-9a-f]{12}", result_data["run_id"])
    assert state_manager.request_workflow_run.await_args.kwargs["run_id"] == result_data["run_id"]
    # The returned path is the run's /runs/ file: dated, titled and ending in its id.
    assert re.fullmatch(
        rf"/runs/workflows/\d{{4}}-\d{{2}}-\d{{2}}_[a-z0-9-]+_{result_data['run_id']}\.md",
        result_data["path"],
    )


async def test_execute_resolves_ancestry_from_active_run():
    state_manager = AsyncMock()
    state_manager.get_workflow_definition.return_value = _definition()
    state_manager.get_preferences_config.return_value = {}
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
