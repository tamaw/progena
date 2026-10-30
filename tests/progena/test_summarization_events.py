from __future__ import annotations

import pytest

from src.progena.runtime.streaming.summarization_events import SummarizationCallbackObserver


@pytest.mark.asyncio
async def test_native_summary_callbacks_emit_transient_lifecycle_and_hide_chunks(
    monkeypatch,
) -> None:
    emitted: list[dict] = []
    monkeypatch.setattr(
        "src.progena.runtime.streaming.summarization_events.emit_stream_event",
        emitted.append,
    )
    observer = SummarizationCallbackObserver(
        lambda metadata: {"kind": "conversation", "id": "thread-1"}
    )
    metadata = {
        "lc_source": "summarization",
        "langgraph_checkpoint_ns": "model:root-task",
    }

    await observer.on_chat_model_start({}, [], run_id="summary-run", metadata=metadata)

    assert observer.suppresses_message_stream(metadata)
    assert emitted == [
        {
            "event_type": "conversation_summary_started",
            "metadata": {
                "summary_id": "summary-run",
                "scope": {"kind": "conversation", "id": "thread-1"},
            },
        }
    ]

    await observer.on_llm_end(object(), run_id="summary-run")

    # Buffered summary chunks must stay hidden after the callback ends, while
    # the following main-model call in the same graph node remains visible.
    assert observer.suppresses_message_stream(metadata)
    assert not observer.suppresses_message_stream({"langgraph_checkpoint_ns": "model:root-task"})
    assert emitted[-1] == {
        "event_type": "conversation_summary_finished",
        "metadata": {
            "summary_id": "summary-run",
            "scope": {"kind": "conversation", "id": "thread-1"},
        },
    }


@pytest.mark.asyncio
async def test_summary_retry_error_finishes_only_its_own_transient_activity(monkeypatch) -> None:
    emitted: list[dict] = []
    monkeypatch.setattr(
        "src.progena.runtime.streaming.summarization_events.emit_stream_event",
        emitted.append,
    )
    observer = SummarizationCallbackObserver(
        lambda metadata: {"kind": "subagent", "id": metadata["task_call_id"]}
    )
    metadata = {"lc_source": "summarization", "task_call_id": "call-1"}

    await observer.on_chat_model_start({}, [], run_id="first", metadata=metadata)
    await observer.on_llm_error(RuntimeError("retry"), run_id="first")
    await observer.on_chat_model_start({}, [], run_id="second", metadata=metadata)
    await observer.on_llm_end(object(), run_id="second")

    assert [event["metadata"]["summary_id"] for event in emitted] == [
        "first",
        "first",
        "second",
        "second",
    ]
    assert [event["event_type"] for event in emitted] == [
        "conversation_summary_started",
        "conversation_summary_finished",
        "conversation_summary_started",
        "conversation_summary_finished",
    ]


@pytest.mark.asyncio
async def test_unmatched_nested_summary_is_not_misattributed_to_root(monkeypatch) -> None:
    from src.progena.runtime.streaming.deepagent_stream import DeepAgentSSEStream

    emitted: list[dict] = []
    monkeypatch.setattr(
        "src.progena.runtime.streaming.summarization_events.emit_stream_event",
        emitted.append,
    )
    bridge = DeepAgentSSEStream(object(), config=object())
    observer = SummarizationCallbackObserver(bridge._summary_scope_for_callback)
    metadata = {
        "lc_source": "summarization",
        "langgraph_checkpoint_ns": "tools:unknown-task|model:summary-task",
    }
    await observer.on_chat_model_start({}, [], run_id="unmatched", metadata=metadata)
    await observer.on_llm_end(object(), run_id="unmatched")
    assert emitted == []
    assert observer.suppresses_message_stream(metadata)
