"""Tests for the custom-event envelope and tool lifecycle stream helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.progenitor.agent.middleware import AuditLoggingMiddleware
from app.progenitor.sse.stream_events import (
    ALLOWED_CUSTOM_EVENT_TYPES,
    CUSTOM_EVENT_FALLBACK_TYPE,
    build_a2a_call_progress_event,
    build_reasoning_delta_event,
    build_reasoning_summary_event,
    build_tool_error_event,
    build_tool_result_event,
    build_tool_start_event,
    normalize_custom_event,
)
from app.progenitor.sse.stream_payloads import (
    DEFAULT_STREAM_ARGS_BYTES,
    DEFAULT_STREAM_PREVIEW_BYTES,
)


def test_allowed_event_types_cover_tool_lifecycle():
    for name in ("tool_start", "tool_result", "tool_error"):
        assert name in ALLOWED_CUSTOM_EVENT_TYPES


def test_allowed_event_types_cover_async_subagent_lifecycle():
    for name in (
        "async_subagent_start",
        "async_subagent_update",
        "async_subagent_result",
        "async_subagent_cancel",
    ):
        assert name in ALLOWED_CUSTOM_EVENT_TYPES


def test_allowed_event_types_cover_nested_subagent_tool_lifecycle():
    for name in ("subagent_tool_start", "subagent_tool_result", "subagent_tool_error"):
        assert name in ALLOWED_CUSTOM_EVENT_TYPES


def test_allowed_event_types_cover_a2a_call_progress():
    assert "a2a_call_progress" in ALLOWED_CUSTOM_EVENT_TYPES


def test_normalize_custom_event_passes_known_type_unchanged():
    payload = {
        "event_type": "tool_start",
        "content": "hello",
        "metadata": {"tool_name": "echo"},
    }
    envelope = normalize_custom_event(payload)
    assert envelope == {
        "event_type": "tool_start",
        "content": "hello",
        "metadata": {"tool_name": "echo"},
    }


def test_normalize_custom_event_coerces_unknown_type_to_fallback():
    envelope = normalize_custom_event({"event_type": "totally_made_up", "metadata": {"a": 1}})
    assert envelope is not None
    assert envelope["event_type"] == CUSTOM_EVENT_FALLBACK_TYPE
    assert envelope["metadata"]["original_event_type"] == "totally_made_up"
    assert envelope["metadata"]["a"] == 1


def test_normalize_custom_event_blocks_reserved_types():
    envelope = normalize_custom_event({"event_type": "node_update"})
    assert envelope is not None
    assert envelope["event_type"] == CUSTOM_EVENT_FALLBACK_TYPE


def test_normalize_custom_event_blocks_subagent_message_delta_spoofing():
    # subagent_message_delta is synthesized directly in deepagent_stream.py from
    # nested `messages`-mode chunks, never via get_stream_writer -- it must not
    # be spoofable through the general custom-event channel, same as message_delta.
    envelope = normalize_custom_event(
        {"event_type": "subagent_message_delta", "content": "spoofed"}
    )
    assert envelope is not None
    assert envelope["event_type"] == CUSTOM_EVENT_FALLBACK_TYPE
    assert envelope["metadata"]["original_event_type"] == "subagent_message_delta"


def test_normalize_custom_event_handles_non_dict_payload():
    envelope = normalize_custom_event("not-a-dict")
    assert envelope is not None
    assert envelope["event_type"] == CUSTOM_EVENT_FALLBACK_TYPE
    assert envelope["metadata"]["raw"] == "not-a-dict"


def test_normalize_custom_event_redacts_non_dict_payload_secrets():
    api_key = "sk-" + "b" * 48
    envelope = normalize_custom_event(f"leaked {api_key}")
    assert envelope is not None
    assert api_key not in repr(envelope)
    assert "REDACTED" in repr(envelope)


def test_normalize_custom_event_redacts_dict_keys_and_handles_broken_str():
    api_key = "sk-" + "d" * 48

    class Broken:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    envelope = normalize_custom_event(
        {
            "event_type": "subagent_start",
            "metadata": {
                api_key: "value",
                "broken": Broken(),
            },
        }
    )
    assert envelope is not None
    serialized = repr(envelope)
    assert api_key not in serialized
    assert "REDACTED" in serialized


def test_normalize_custom_event_redacts_tuple_and_object_payloads():
    api_key = "sk-" + "c" * 48

    class SecretHolder:
        def __str__(self) -> str:
            return f"holder {api_key}"

    envelope = normalize_custom_event(
        {
            "event_type": "debug_event",
            "metadata": {
                "tuple_value": (api_key, "safe"),
                "set_value": {api_key},
                "object_value": SecretHolder(),
            },
        }
    )
    assert envelope is not None
    serialized = repr(envelope)
    assert api_key not in serialized
    assert "REDACTED" in serialized


def test_normalize_custom_event_redacts_metadata_secrets():
    api_key = "sk-" + "a" * 48
    envelope = normalize_custom_event(
        {
            "event_type": "subagent_result",
            "content": f"finished with {api_key}",
            "metadata": {
                "summary": f"used key {api_key}",
                "nested": {"token": api_key},
                "items": [api_key, "harmless"],
            },
        }
    )
    assert envelope is not None
    serialized = repr(envelope)
    assert api_key not in serialized
    assert "REDACTED" in serialized


def test_normalize_custom_event_truncates_oversized_metadata():
    huge = "x" * 5000
    envelope = normalize_custom_event(
        {
            "event_type": "debug_event",
            "content": "ok",
            "metadata": {"blob": huge},
        }
    )
    assert envelope is not None
    # truncate_json_safe collapses to a string preview when the encoded size
    # exceeds DEFAULT_STREAM_ARGS_BYTES (2048).
    metadata = envelope["metadata"]
    assert isinstance(metadata, dict)
    rendered = next(iter(metadata.values()))
    assert isinstance(rendered, str)
    assert len(rendered.encode("utf-8")) <= 2048


def test_normalize_custom_event_preserves_correlation_fields_when_metadata_is_oversized():
    """Lifecycle joins must survive truncation of unrelated event metadata."""
    envelope = normalize_custom_event(
        {
            "event_type": "debug_event",
            "content": "progress",
            "metadata": {
                "tool_call_id": "call-correlation-7",
                "subagent_type": "researcher",
                "blob": "x" * 10_000,
            },
        }
    )

    assert envelope is not None
    metadata = envelope["metadata"]
    assert metadata["tool_call_id"] == "call-correlation-7"
    assert metadata["subagent_type"] == "researcher"
    assert "blob" not in metadata
    assert len(str(metadata["value"]).encode("utf-8")) <= DEFAULT_STREAM_ARGS_BYTES


def test_build_tool_start_event_redacts_args():
    api_key = "sk-" + "a" * 48
    event = build_tool_start_event(
        tool_name="search",
        tool_call_id="call_1",
        raw_args={"query": "lookup", "secret": api_key},
    )
    assert event["event_type"] == "tool_start"
    assert event["metadata"]["tool_name"] == "search"
    args_preview = event["metadata"]["args_preview"]
    serialized = str(args_preview)
    assert api_key not in serialized
    assert "REDACTED" in serialized


def test_build_tool_start_event_preserves_subagent_type_when_args_preview_truncates():
    huge_description = "x" * (DEFAULT_STREAM_ARGS_BYTES * 2)
    event = build_tool_start_event(
        tool_name="task",
        tool_call_id="call_1",
        raw_args={"subagent_type": "researcher", "description": huge_description},
    )
    # args_preview collapses to a truncated string once the dict exceeds the
    # byte budget -- subagent_type must survive that collapse in its own field.
    assert isinstance(event["metadata"]["args_preview"], str)
    assert event["metadata"]["subagent_type"] == "researcher"


def test_build_tool_start_event_preserves_bounded_redacted_task_summary_when_args_truncate():
    """Task identity and its readable summary must outlive args-preview truncation."""
    api_key = "sk-" + "z" * 48
    description = f"  Review deployment with {api_key} " + ("x" * (DEFAULT_STREAM_ARGS_BYTES * 2))

    event = build_tool_start_event(
        tool_name="task",
        tool_call_id="call_long-task",
        raw_args={"subagent_type": "researcher", "description": description},
    )

    metadata = event["metadata"]
    assert isinstance(metadata["args_preview"], str)
    assert metadata["subagent_type"] == "researcher"
    task_summary = metadata["task_summary"]
    assert isinstance(task_summary, str)
    assert len(task_summary) <= 140
    assert api_key not in task_summary
    assert "REDACTED" in task_summary


def test_build_tool_start_event_omits_subagent_type_for_non_task_tools():
    event = build_tool_start_event(
        tool_name="search",
        tool_call_id="call_1",
        raw_args={"query": "lookup"},
    )
    assert "subagent_type" not in event["metadata"]


def test_build_tool_result_event_truncates_large_results():
    big = "x" * (DEFAULT_STREAM_PREVIEW_BYTES * 4)
    event = build_tool_result_event("search", "call_1", 123.45, big)
    preview = event["metadata"]["result_preview"]
    assert isinstance(preview, str)
    assert len(preview.encode("utf-8")) <= DEFAULT_STREAM_PREVIEW_BYTES
    assert event["metadata"]["success"] is True
    assert event["metadata"]["duration_ms"] == 123.45


def test_build_tool_result_event_unwraps_command_result():
    # Subagent (`task`) tool calls return a Command(update=...) rather than a
    # plain ToolMessage -- without unwrapping it, the preview falls back to
    # str(Command(...)), a raw Python repr instead of the subagent's own text.
    command = Command(
        update={
            "files": {},
            "messages": [
                ToolMessage(content="HELLO_FROM_SUBAGENT", name="task", tool_call_id="call_1")
            ],
        }
    )
    event = build_tool_result_event("task", "call_1", 42.0, command)
    assert event["metadata"]["result_preview"] == "HELLO_FROM_SUBAGENT"


def test_build_tool_error_event_redacts_and_truncates():
    api_key = "sk-" + "b" * 48
    event = build_tool_error_event(
        tool_name="search",
        tool_call_id="call_1",
        duration_ms=10.0,
        error=f"boom {api_key}",
    )
    err_text = event["metadata"]["error"]
    assert api_key not in err_text
    assert event["metadata"]["success"] is False


def test_build_a2a_call_progress_event_shape():
    event = build_a2a_call_progress_event(
        "call_1", "greetings_agent", "working", "Step 1/3: prep..."
    )
    assert event["event_type"] == "a2a_call_progress"
    assert event["content"] == "Step 1/3: prep..."
    assert event["metadata"] == {
        "tool_call_id": "call_1",
        "agent_name": "greetings_agent",
        "state": "working",
        "message_text": "Step 1/3: prep...",
    }
    assert normalize_custom_event(event)["event_type"] == "a2a_call_progress"


def test_build_a2a_call_progress_event_redacts_and_truncates():
    api_key = "sk-" + "c" * 48
    big = f"{api_key} " + "x" * (DEFAULT_STREAM_PREVIEW_BYTES * 4)
    event = build_a2a_call_progress_event("call_1", "greetings_agent", "working", big)
    assert api_key not in event["content"]
    assert api_key not in event["metadata"]["message_text"]
    assert len(event["content"].encode("utf-8")) <= DEFAULT_STREAM_PREVIEW_BYTES


def test_build_reasoning_delta_event_tracks_text_length():
    event = build_reasoning_delta_event(
        "Checked inputs. ",
        metadata={"stream_metadata": {"langgraph_node": "respond"}},
    )
    assert event["event_type"] == "reasoning_delta"
    assert event["content"] == "Checked inputs. "
    assert event["metadata"]["text_length"] == len("Checked inputs. ")
    assert event["metadata"]["summary"] == "Checked inputs. "


def test_build_reasoning_summary_event_redacts_metadata_and_keeps_tokens():
    api_key = "sk-" + "e" * 48
    event = build_reasoning_summary_event(
        "Checked inputs before answering.",
        metadata={"reasoning_tokens": 17, "debug": api_key},
    )
    assert event["event_type"] == "reasoning_summary"
    assert event["metadata"]["reasoning_tokens"] == 17
    assert event["metadata"]["summary"] == "Checked inputs before answering."
    assert api_key not in repr(event)


@pytest.mark.asyncio
async def test_audit_middleware_emits_tool_start_and_result_events():
    captured: list[dict] = []

    async def handler(req):
        return ToolMessage(content="ok", tool_call_id="call_1")

    middleware = AuditLoggingMiddleware()

    request = type(
        "Req",
        (),
        {"tool_call": {"name": "search", "id": "call_1", "args": {"q": "hi"}}},
    )()

    with patch(
        "app.progenitor.agent.middleware.emit_stream_event",
        side_effect=captured.append,
    ):
        result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert [e["event_type"] for e in captured] == ["tool_start", "tool_result"]
    assert captured[0]["metadata"]["tool_name"] == "search"
    assert captured[1]["metadata"]["success"] is True
    assert captured[1]["metadata"]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_audit_middleware_emits_tool_error_on_exception():
    captured: list[dict] = []

    async def handler(req):
        raise RuntimeError("boom")

    middleware = AuditLoggingMiddleware()
    request = type(
        "Req",
        (),
        {"tool_call": {"name": "search", "id": "call_1", "args": {}}},
    )()

    with (
        patch(
            "app.progenitor.agent.middleware.emit_stream_event",
            side_effect=captured.append,
        ),
        pytest.raises(RuntimeError),
    ):
        await middleware.awrap_tool_call(request, handler)

    assert [e["event_type"] for e in captured] == ["tool_start", "tool_error"]
    assert captured[1]["metadata"]["success"] is False
    assert "boom" in captured[1]["metadata"]["error"]


@pytest.mark.asyncio
async def test_audit_middleware_swallows_writer_failure():
    """Tool execution must succeed even if event emission raises."""

    async def handler(req):
        return ToolMessage(content="ok", tool_call_id="call_1")

    middleware = AuditLoggingMiddleware()
    request = type(
        "Req",
        (),
        {"tool_call": {"name": "search", "id": "call_1", "args": {}}},
    )()

    def boom(_payload):
        raise RuntimeError("writer dead")

    # get_stream_writer returns a writer that raises when invoked; emit_stream_event
    # must swallow the failure so tool execution is unaffected.
    with patch(
        "app.progenitor.sse.stream_events.get_stream_writer",
        return_value=boom,
    ):
        result = await middleware.awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)


def test_build_tool_result_event_omits_artifact_when_result_has_none():
    event = build_tool_result_event("read_file", "call-no-artifact", 4.0, "plain result")

    assert "artifact" not in event["metadata"]
    assert event["metadata"]["result_preview"] == "plain result"


def test_build_tool_result_event_projects_bounded_redacted_artifact():
    api_key = "sk-" + "f" * 48
    event = build_tool_result_event(
        "read_file",
        "call-artifact",
        8.0,
        ToolMessage(
            content="raw result",
            name="read_file",
            tool_call_id="call-artifact",
            artifact={
                "summary": f"{api_key} " + ("x" * (DEFAULT_STREAM_PREVIEW_BYTES * 3)),
                "count": 3,
                "kind": "file",
                "status": "ok",
                "raw": {"secret": api_key, "nested": ["discard me"]},
            },
        ),
    )

    artifact = event["metadata"]["artifact"]
    assert set(artifact) <= {"summary", "count", "kind", "status"}
    assert artifact["count"] == 3
    assert artifact["kind"] == "file"
    assert artifact["status"] == "ok"
    assert api_key not in repr(artifact)
    assert len(artifact["summary"].encode("utf-8")) <= DEFAULT_STREAM_PREVIEW_BYTES


def test_build_tool_result_event_appends_omitted_file_lines_to_truncated_summary():
    summary = "src/main.py: 5 lines"
    content = "line 1\nline 2\nline 3" + ("x" * DEFAULT_STREAM_PREVIEW_BYTES)
    event = build_tool_result_event(
        "read_file",
        "call-truncated-artifact",
        8.0,
        ToolMessage(
            content=content,
            name="read_file",
            tool_call_id="call-truncated-artifact",
            artifact={
                "summary": summary,
                "count": 5,
                "kind": "file",
                "status": "ok",
            },
        ),
    )

    artifact = event["metadata"]["artifact"]
    assert artifact["summary"] == f"{summary} · 2 more lines not shown"


def test_build_tool_result_event_preserves_untruncated_file_summary():
    summary = "src/main.py: 3 lines"
    event = build_tool_result_event(
        "read_file",
        "call-short-artifact",
        8.0,
        ToolMessage(
            content="line 1\nline 2\nline 3",
            name="read_file",
            tool_call_id="call-short-artifact",
            artifact={
                "summary": summary,
                "count": 3,
                "kind": "file",
                "status": "ok",
            },
        ),
    )

    artifact = event["metadata"]["artifact"]
    assert artifact["summary"] == summary
