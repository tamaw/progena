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
    def test_emits_result_for_interrupted(self):
        request = MagicMock(spec=ToolCallRequest)
        request.tool_call = {"name": "check_async_task", "args": {}, "id": "tc1"}
        request.runtime = None
        request.state = {}
        tasks = {
            "t1": {"task_id": "t1", "agent_name": "agent1", "status": "interrupted"},
        }
        with patch(
            "src.progena.agent.middleware.async_subagent_tracking.emit_stream_event"
        ) as mock_emit:
            _emit_async_subagent_events(request, "check_async_task", tasks)
        # _emit_async_subagent_events uses positional args (a dict), not kwargs
        result_calls = [
            c
            for c in mock_emit.call_args_list
            if c.args and c.args[0].get("event_type") == "async_subagent_result"
        ]
        assert len(result_calls) == 1
        assert result_calls[0].args[0]["metadata"]["status"] == "interrupted"
