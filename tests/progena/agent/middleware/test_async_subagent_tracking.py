"""Tests for async-subagent tracking middleware."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain.agents.middleware.types import ToolCallRequest

from src.progena.agent.middleware.async_subagent_tracking import (
    _async_tasks_result_payload,
    _emit_async_subagent_events,
)


class TestAsyncTasksResultPayload:
    def test_counts_interrupted(self):
        tasks = {
            "t1": {"task_id": "t1", "status": "success"},
            "t2": {"task_id": "t2", "status": "interrupted"},
            "t3": {"task_id": "t3", "status": "running"},
        }
        payload = _async_tasks_result_payload(tasks)
        assert payload["interrupted"] == 1
        assert payload["success"] == 1
        assert payload["running"] == 1
        assert payload["total"] == 3


class TestEmitAsyncSubagentEvents:
    def test_emits_pause_for_interrupted_task_with_correlation(self):
        request = MagicMock(spec=ToolCallRequest)
        request.tool_call = {"name": "check_async_task", "args": {}, "id": "tc1"}
        request.runtime = None
        request.state = {}
        tasks = {
            "t1": {"agent_name": "agent1", "status": "interrupted"},
        }
        with patch(
            "src.progena.agent.middleware.async_subagent_tracking.emit_stream_event"
        ) as mock_emit:
            _emit_async_subagent_events(request, "check_async_task", tasks)

        assert mock_emit.call_args.args[0] == {
            "event_type": "async_subagent_pause",
            "content": "agent1",
            "metadata": {
                "subagent_name": "agent1",
                "task_id": "t1",
                "status": "interrupted",
                "tool_call_id": "tc1",
            },
        }

    def test_emits_terminal_results_for_success_error_and_cancelled_tasks(self):
        request = MagicMock(spec=ToolCallRequest)
        request.tool_call = {"name": "list_async_tasks", "args": {}, "id": "tc1"}
        request.runtime = None
        request.state = {}
        tasks = {
            "success": {
                "task_id": "success",
                "agent_name": "agent1",
                "status": "success",
                "summary": "finished",
            },
            "error": {
                "task_id": "error",
                "agent_name": "agent2",
                "status": "error",
                "last_error": "failed",
            },
            "cancelled": {"task_id": "cancelled", "agent_name": "agent3", "status": "cancelled"},
        }
        with patch(
            "src.progena.agent.middleware.async_subagent_tracking.emit_stream_event"
        ) as mock_emit:
            _emit_async_subagent_events(request, "list_async_tasks", tasks)

        emitted = [call.args[0] for call in mock_emit.call_args_list]
        assert [event["event_type"] for event in emitted] == [
            "async_subagent_result",
            "async_subagent_result",
            "async_subagent_result",
        ]
        assert {event["metadata"]["task_id"] for event in emitted} == set(tasks)
        assert {event["metadata"]["tool_call_id"] for event in emitted} == {"tc1"}
        metadata_by_task = {event["metadata"]["task_id"]: event["metadata"] for event in emitted}
        assert metadata_by_task["success"]["result_preview"] == "finished"
        assert metadata_by_task["error"]["error"] == "failed"
