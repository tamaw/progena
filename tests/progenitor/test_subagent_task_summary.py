"""Regression tests for task description propagation into live subagent events."""

from types import SimpleNamespace

from app.progenitor.runtime.streaming.deepagent_stream import DeepAgentSSEStream


class _Graph:
    async def astream(self, *args, **kwargs):
        return
        yield  # pragma: no cover


def _bridge() -> DeepAgentSSEStream:
    config = SimpleNamespace(llm=SimpleNamespace(reasoning_enabled=False))
    return DeepAgentSSEStream(_Graph(), config=config, state={})


def _task_event(event_type: str, metadata: dict) -> dict:
    return {
        "event_type": event_type,
        "content": "task",
        "metadata": {"tool_name": "task", "tool_call_id": "call-summary", **metadata},
    }


def test_live_task_description_is_normalized_and_propagated_to_start_and_result():
    bridge = _bridge()
    start = bridge._handle_custom_stream_event(
        _task_event(
            "tool_start",
            {
                "subagent_type": "researcher",
                "args_preview": {
                    "subagent_type": "researcher",
                    "description": "  Read   the\n README.md\tand summarize its purpose  ",
                },
            },
        )
    )

    assert start[-1]["event_type"] == "subagent_start"
    assert start[-1]["metadata"]["task_summary"] == "Read the README.md and summarize its purpose"

    result = bridge._handle_custom_stream_event(
        _task_event(
            "tool_result",
            {"success": True, "result_preview": "Final answer"},
        )
    )
    assert result[-1]["event_type"] == "subagent_result"
    assert result[-1]["metadata"]["task_summary"] == "Read the README.md and summarize its purpose"


def test_live_task_description_is_bounded_to_140_characters():
    bridge = _bridge()
    long_description = "x" * 200
    expanded = bridge._handle_custom_stream_event(
        _task_event(
            "tool_start",
            {
                "subagent_type": "researcher",
                "args_preview": {"description": long_description},
            },
        )
    )

    summary = expanded[-1]["metadata"]["task_summary"]
    assert summary == ("x" * 139) + "…"
    assert len(summary) == 140


def test_live_task_without_description_keeps_legacy_event_shape():
    bridge = _bridge()
    start = bridge._handle_custom_stream_event(
        _task_event(
            "tool_start",
            {"subagent_type": "researcher", "args_preview": {"subagent_type": "researcher"}},
        )
    )
    assert start[-1]["metadata"] == {
        "subagent_name": "researcher",
        "tool_call_id": "call-summary",
    }

    result = bridge._handle_custom_stream_event(
        _task_event(
            "tool_result",
            {"success": True, "result_preview": "Final answer"},
        )
    )
    assert "task_summary" not in result[-1]["metadata"]
