"""Direct unit tests for src.backend.chat.service.

These exercise the ``session:`` document read model, atomic patch merge,
list ordering, and checkpoint-snapshot rendering.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage, messages_from_dict, messages_to_dict

from src.backend.chat.service import (
    ChatPatchRequest,
    _extract_progress_log_from_content,
    _messages_from_checkpoint,
    _tool_result_preview,
    delete_session_document,
    get_chat_detail,
    list_chat_sessions,
    paginate_chat_messages,
    patch_session_document,
)
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    CheckpointThreadSnapshot,
    InMemoryDocumentStore,
)
from src.progena.documents.kinds import SESSION_KEY_PREFIX
from src.progena.sessions.titles import ATTACHMENT_CONTEXT_MARKER


class _CountingDocumentStore(InMemoryDocumentStore):
    """Wraps InMemoryDocumentStore to count real per-thread snapshot fetches."""

    def __init__(self) -> None:
        super().__init__()
        self.get_checkpoint_thread_snapshot_calls = 0

    def get_checkpoint_thread_snapshot(
        self,
        thread_id: str,
        *,
        checkpoint_id: str | None = None,
        include_summary_occurrences: bool = False,
    ) -> CheckpointThreadSnapshot | None:
        self.get_checkpoint_thread_snapshot_calls += 1
        return super().get_checkpoint_thread_snapshot(
            thread_id,
            checkpoint_id=checkpoint_id,
            include_summary_occurrences=include_summary_occurrences,
        )


def _store_session(store: InMemoryDocumentStore, thread_id: str, **fields) -> None:
    payload = {
        "id": thread_id,
        "thread_id": thread_id,
        "source": "ui",
        "title": "New chat",
        "first_message": "",
        "pinned": False,
        "archived": False,
        "created_at": "2026-01-01T00:00:00Z",
        "last_activity": "2026-01-01T00:00:00Z",
        "content": "",
        **fields,
    }
    store.save_document(PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}", payload)


class TestListChatSessions:
    def test_empty_store_returns_empty_list(self):
        store = InMemoryDocumentStore()
        assert list_chat_sessions(store) == []

    def test_reads_session_documents(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_a", title="Alpha", last_activity="2026-01-02T00:00:00Z")
        _store_session(store, "thread_b", title="Beta", last_activity="2026-01-01T00:00:00Z")

        sessions = list_chat_sessions(store)
        assert [s.thread_id for s in sessions] == ["thread_a", "thread_b"]

    def test_sorts_pinned_first_then_last_activity_desc(self):
        store = InMemoryDocumentStore()
        _store_session(store, "old", title="Old", pinned=True, last_activity="2026-01-01T00:00:00Z")
        _store_session(
            store, "new", title="New", pinned=False, last_activity="2026-01-03T00:00:00Z"
        )
        _store_session(store, "mid", title="Mid", pinned=True, last_activity="2026-01-02T00:00:00Z")

        sessions = list_chat_sessions(store)
        assert [s.thread_id for s in sessions] == ["mid", "old", "new"]

    def test_normalizes_legacy_and_offset_session_timestamps_before_sorting(self):
        store = InMemoryDocumentStore()
        _store_session(
            store,
            "offset_earlier",
            created_at="2026-01-01T00:00:00",
            last_activity="2026-01-02T00:30:00+02:00",
        )
        _store_session(
            store,
            "utc_later",
            last_activity="2026-01-01T23:00:00Z",
        )

        sessions = list_chat_sessions(store)

        assert [session.thread_id for session in sessions] == ["utc_later", "offset_earlier"]
        assert sessions[1].created_at == "2026-01-01T00:00:00Z"
        assert sessions[1].last_activity == "2026-01-01T22:30:00Z"

    def test_filters_archived_by_default(self):
        store = InMemoryDocumentStore()
        _store_session(store, "archived", title="Archived", archived=True)
        _store_session(store, "active", title="Active", archived=False)

        sessions = list_chat_sessions(store)
        assert [s.thread_id for s in sessions] == ["active"]

    def test_include_archived_shows_all(self):
        store = InMemoryDocumentStore()
        _store_session(store, "archived", title="Archived", archived=True)
        _store_session(store, "active", title="Active", archived=False)

        sessions = list_chat_sessions(store, include_archived=True)
        assert {s.thread_id for s in sessions} == {"archived", "active"}

    def test_uses_first_message_for_placeholder_title(self):
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_msg",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "Hello world"},
                ],
            )
        )
        _store_session(store, "thread_msg", title="New chat", first_message="Hello world")

        sessions = list_chat_sessions(store)
        assert len(sessions) == 1
        assert sessions[0].title == "Hello world"

    def test_placeholder_title_redacts_secrets_in_the_first_message(self):
        store = InMemoryDocumentStore()
        secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
        _store_session(store, "thread_key", title="New chat", first_message=f"use {secret} now")

        [session] = list_chat_sessions(store)
        assert secret not in session.title
        assert session.title.startswith("use [")

    def test_counts_messages_from_checkpoint(self):
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_count",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "hi"},
                    {"id": "a1", "type": "ai", "content": "hello"},
                ],
            )
        )
        _store_session(store, "thread_count")

        sessions = list_chat_sessions(store)
        assert sessions[0].message_count == 2

    def test_reads_tool_duration_from_checkpointed_tool_message(self):
        serialized = messages_to_dict(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "c1", "name": "lookup", "args": {}}],
                ),
                ToolMessage(
                    content="result",
                    tool_call_id="c1",
                    additional_kwargs={"duration_ms": 12.5},
                ),
            ]
        )
        checkpoint_messages = messages_from_dict(serialized)
        snapshot = CheckpointThreadSnapshot(
            thread_id="thread_duration",
            checkpoint_id="cp-1",
            checkpoint_ts="2026-01-01T00:00:00Z",
            messages=checkpoint_messages,
        )

        [message] = _messages_from_checkpoint(snapshot)

        assert message.tool_calls[0]["duration_ms"] == 12.5


class TestGetChatDetail:
    def test_returns_none_when_no_session_document(self):
        store = InMemoryDocumentStore()
        assert get_chat_detail(store, "missing") is None

    def test_returns_detail_with_messages(self):
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_d",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "question"},
                    {"id": "a1", "type": "ai", "content": "answer"},
                ],
            )
        )
        _store_session(store, "thread_d", title="My Chat")

        detail = get_chat_detail(store, "thread_d")
        assert detail is not None
        assert detail.thread_id == "thread_d"
        assert detail.title == "My Chat"
        assert [m.role for m in detail.messages] == ["user", "assistant"]

    def test_normalizes_provider_epoch_seconds_and_offset_iso_metadata(self):
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_timestamps",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {
                        "id": "u1",
                        "type": "human",
                        "content": "question",
                        "additional_kwargs": {"created_at": 1767225600},
                    },
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "answer",
                        "response_metadata": {"created_at": "2026-01-01T11:00:00+11:00"},
                    },
                ],
            )
        )
        _store_session(store, "thread_timestamps")

        detail = get_chat_detail(store, "thread_timestamps")

        assert detail is not None
        assert [message.created_at for message in detail.messages] == [
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ]

    def test_context_usage_is_latest_main_agent_call_not_turn_total(self):
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_usage",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "look this up"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Checking.",
                        "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}],
                        "usage_metadata": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                        "response_metadata": {
                            "insights": {
                                "profile_name": "general",
                                "deployment": "first-model",
                                "provider_id": "provider-1",
                            }
                        },
                    },
                    {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "result"},
                    {
                        "id": "a2",
                        "type": "ai",
                        "content": "Here is the result.",
                        "usage_metadata": {
                            "input_tokens": 35,
                            "output_tokens": 8,
                            "total_tokens": 43,
                        },
                        "response_metadata": {
                            "insights": {
                                "profile_name": "general",
                                "deployment": "final-model",
                                "provider_id": "provider-1",
                            }
                        },
                    },
                ],
            )
        )
        _store_session(store, "thread_usage")

        detail = get_chat_detail(store, "thread_usage")

        assert detail is not None
        assert detail.messages[-1].context_usage.model_dump() == {
            "input_tokens": 35,
            "output_tokens": 8,
            "total_tokens": 43,
            "profile_name": "general",
            "deployment": "final-model",
            "provider_id": "provider-1",
            "compaction": None,
        }

    def test_aggregates_root_tool_call_and_final_output_usage(self):
        messages = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_output_breakdown",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "look it up"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Checking.",
                        "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}],
                        "usage_metadata": {"output_tokens": 5},
                        "response_metadata": {"finish_reason": "tool_calls"},
                    },
                    {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "result"},
                    {
                        "id": "a2",
                        "type": "ai",
                        "content": "Here is the result.",
                        "response_metadata": {
                            "finish_reason": "stop",
                            "token_usage": {"completion_tokens": 7},
                        },
                    },
                ],
            )
        )

        answer = messages[-1]

        assert answer.token_count == 12
        assert answer.final_call_token_count == 7
        assert answer.response_status == "completed"
        assert [call["id"] for call in answer.tool_calls] == ["c1"]

    def test_preserves_zero_output_and_marks_missing_output_unknown(self):
        zero_messages = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_zero_output",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "No tokens billed.",
                        "usage_metadata": {"output_tokens": 0},
                        "response_metadata": {"finish_reason": "stop"},
                    },
                ],
            )
        )
        missing_messages = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_missing_output",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Usage was not supplied.",
                    },
                ],
            )
        )

        zero = zero_messages[-1]
        missing = missing_messages[-1]

        assert (zero.token_count, zero.final_call_token_count) == (0, 0)
        assert zero.response_status == "completed"
        assert (missing.token_count, missing.final_call_token_count) == (None, None)
        assert missing.response_status == "unknown"

    def test_retains_empty_provider_failure_with_a_canonical_terminal_row(self):
        [user, failure] = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_content_filter",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "usage_metadata": {"output_tokens": 0},
                        "response_metadata": {"finish_reason": "content_filter"},
                    },
                ],
            )
        )

        assert user.content == "answer"
        assert failure.id == "a1"
        assert failure.content == "Error: Response failed."
        assert failure.raw["content"] == ""
        assert (failure.token_count, failure.final_call_token_count) == (0, 0)
        assert failure.response_status == "failed"

    def test_projects_native_cancelled_rows_without_retired_sentinel_text(self):
        [user, partial, _retry, empty] = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_cancelled_history",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Partial answer. [Cancelled]",
                        "response_metadata": {"status": "cancelled"},
                    },
                    {"id": "u2", "type": "human", "content": "try again"},
                    {
                        "id": "a2",
                        "type": "ai",
                        "content": "[Cancelled]",
                        "response_metadata": {"status": "cancelled"},
                    },
                ],
            )
        )

        assert user.content == "answer"
        assert (
            partial.id,
            partial.content,
            partial.response_status,
            partial.interruption_reason,
        ) == (
            "a1",
            "Partial answer.",
            "cancelled",
            "user_cancelled",
        )
        assert (empty.id, empty.content, empty.response_status, empty.interruption_reason) == (
            "a2",
            "",
            "cancelled",
            "user_cancelled",
        )

    def test_uses_existing_work_as_the_terminal_failure_indicator(self):
        [user, failure] = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_failed_tool_work",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}],
                        "response_metadata": {"status": "failed"},
                    },
                    {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "result"},
                ],
            )
        )

        assert user.content == "answer"
        assert failure.content == ""
        assert failure.response_status == "failed"
        assert [call["id"] for call in failure.tool_calls] == ["c1"]

    def test_retains_trailing_reasoning_only_usage_without_an_answer_boundary(self):
        messages = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_reasoning_tail",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": [
                            {
                                "type": "reasoning",
                                "summary": [{"type": "summary_text", "text": "I need more time."}],
                            }
                        ],
                        "usage_metadata": {"output_tokens": 7},
                    },
                ],
            )
        )

        tail = messages[-1]

        assert tail.content == ""
        assert tail.reasoning_text == "I need more time."
        assert (tail.token_count, tail.final_call_token_count) == (7, None)
        assert tail.response_status == "unknown"

    def test_keeps_known_final_usage_when_provider_status_is_unknown(self):
        messages = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_unknown_status_usage",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Here is the answer.",
                        "usage_metadata": {"output_tokens": 7},
                    },
                ],
            )
        )

        answer = messages[-1]

        assert (answer.token_count, answer.final_call_token_count) == (7, 7)
        assert answer.response_status == "unknown"

    def test_provider_incomplete_status_is_preserved_for_visible_answer(self):
        messages = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_incomplete_status",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Partial answer.",
                        "usage_metadata": {"output_tokens": 7},
                        "response_metadata": {"status": "incomplete"},
                    },
                ],
            )
        )

        answer = messages[-1]

        assert (answer.token_count, answer.final_call_token_count) == (7, 7)
        assert answer.response_status == "interrupted"

    def test_missing_tool_call_usage_makes_answer_group_unknown(self):
        messages = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_partial_usage",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "look it up"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}],
                        "usage_metadata": {"output_tokens": 6},
                    },
                    {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "result"},
                    {
                        "id": "a2",
                        "type": "ai",
                        "content": "Here is the result.",
                        "usage_metadata": {"total_tokens": 99},
                        "response_metadata": {"finish_reason": "stop"},
                    },
                ],
            )
        )

        answer = messages[-1]

        assert answer.token_count is None
        assert answer.final_call_token_count is None
        assert answer.response_status == "completed"

    def test_response_status_uses_lifecycle_events_and_checkpoint_boundaries(self):
        def projected_status(messages, index=-1):
            return _messages_from_checkpoint(
                CheckpointThreadSnapshot(
                    thread_id="thread_status",
                    checkpoint_id="cp-1",
                    checkpoint_ts="2026-01-01T00:00:00Z",
                    messages=messages,
                )
            )[index].response_status

        assert (
            projected_status(
                [
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Done.",
                        "response_metadata": {"finish_reason": "stop"},
                    },
                ]
            )
            == "completed"
        )
        assert (
            projected_status(
                [
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Partial.",
                        "response_metadata": {"status": "cancelled"},
                    },
                ]
            )
            == "cancelled"
        )
        assert (
            projected_status(
                [
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Provider error.",
                        "response_metadata": {"status": "failed"},
                    },
                ]
            )
            == "failed"
        )
        assert (
            projected_status(
                [
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}],
                    },
                ]
            )
            == "unknown"
        )
        assert (
            projected_status(
                [
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": [
                            {
                                "type": "reasoning",
                                "summary": [
                                    {"type": "summary_text", "text": "**Answer**\n\nNot yet."}
                                ],
                            }
                        ],
                        "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}],
                    },
                    {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "result"},
                ]
            )
            == "unknown"
        )

        assert (
            projected_status(
                [
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}],
                    },
                    {"id": "u2", "type": "human", "content": "again"},
                ],
                -2,
            )
            == "interrupted"
        )

        assert (
            projected_status(
                [
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [{"id": "c1", "name": "lookup", "args": {}}],
                    },
                    {
                        "id": "t1",
                        "type": "tool",
                        "tool_call_id": "c1",
                        "content": "lookup failed",
                        "status": "error",
                    },
                ]
            )
            == "unknown"
        )

    def test_reasoning_heading_is_not_an_answer_boundary(self):
        messages = _messages_from_checkpoint(
            CheckpointThreadSnapshot(
                thread_id="thread_reasoning_only",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "answer"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": [
                            {
                                "type": "reasoning",
                                "summary": [
                                    {"type": "summary_text", "text": "**Answer**\n\nNot yet."}
                                ],
                            }
                        ],
                    },
                    {"id": "u2", "type": "human", "content": "again"},
                ],
            )
        )

        assert messages[-2].content == ""
        assert messages[-2].response_status == "interrupted"

    def test_tool_result_preview_preserves_short_results_and_both_long_ends(self):
        short_result = "x" * 400
        long_result = "opening" + "x" * 500 + "\n[Command failed with exit code 3]"

        assert _tool_result_preview(short_result) == short_result
        preview = _tool_result_preview(long_result)
        assert preview.startswith("opening")
        assert "⟨1 KB omitted⟩" in preview
        assert preview.endswith("[Command failed with exit code 3]")

    def test_tool_failure_is_classified_before_history_truncation(self):
        long_failure = "x" * 1000 + "\n[Command failed with exit code 3]"
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_failed_tool",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "run it"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [
                            {"id": "c1", "name": "execute", "args": {}},
                            {"id": "c2", "name": "execute", "args": {}},
                        ],
                    },
                    {
                        "id": "t1",
                        "type": "tool",
                        "name": "execute",
                        "tool_call_id": "c1",
                        "content": long_failure,
                    },
                    {
                        "id": "t2",
                        "type": "tool",
                        "name": "execute",
                        "tool_call_id": "c2",
                        "content": "ok\n[Command succeeded with exit code 0]",
                    },
                    {"id": "a2", "type": "ai", "content": "Done."},
                ],
            )
        )
        _store_session(store, "thread_failed_tool")

        detail = get_chat_detail(store, "thread_failed_tool")

        assert detail is not None
        failed, succeeded = detail.messages[-1].tool_calls
        assert failed["result"].endswith("[Command failed with exit code 3]")
        assert "⟨1 KB omitted⟩" in failed["result"]
        assert failed["failed"] is True
        assert "failed" not in succeeded

    def test_keeps_each_model_calls_reasoning_as_its_own_segment(self):
        def ai(message_id, created_at, reasoning, **extra):
            return {
                "id": message_id,
                "type": "ai",
                "created_at": created_at,
                "content": [
                    {"type": "reasoning", "summary": [{"type": "summary_text", "text": reasoning}]},
                    *extra.pop("text", []),
                ],
                **extra,
            }

        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_r",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "read it"},
                    ai(
                        "a1",
                        "2026-01-01T00:00:01Z",
                        "**Plan**\n\nlist first",
                        tool_calls=[{"id": "c1", "name": "ls", "args": {"path": "/"}}],
                    ),
                    {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "a.json"},
                    ai(
                        "a2",
                        "2026-01-01T00:00:05Z",
                        "**Answer**\n\nsummarise",
                        text=[{"type": "text", "text": "done"}],
                    ),
                ],
            )
        )
        _store_session(store, "thread_r")

        detail = get_chat_detail(store, "thread_r")

        answer = detail.messages[-1]
        assert [(s.text, s.created_at) for s in answer.reasoning_segments] == [
            ("**Plan**\n\nlist first", "2026-01-01T00:00:01Z"),
            ("**Answer**\n\nsummarise", "2026-01-01T00:00:05Z"),
        ]
        assert answer.reasoning_text == "**Plan**\n\nlist first\n\n**Answer**\n\nsummarise"

    def test_text_written_alongside_tool_calls_becomes_first_call_commentary(self):
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_c",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "check it"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "I'll look at the repo first.",
                        "tool_calls": [
                            {"id": "c1", "name": "ls", "args": {"path": "/"}},
                            {"id": "c2", "name": "ls", "args": {"path": "/tmp"}},
                        ],
                    },
                    {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "a"},
                    {"id": "t2", "type": "tool", "tool_call_id": "c2", "content": "b"},
                    {"id": "a2", "type": "ai", "content": "All good."},
                ],
            )
        )
        _store_session(store, "thread_c")

        detail = get_chat_detail(store, "thread_c")

        # The narration is not a separate answer; it travels with its calls.
        assert [(m.role, m.content) for m in detail.messages] == [
            ("user", "check it"),
            ("assistant", "All good."),
        ]
        calls = detail.messages[1].tool_calls
        assert [call.get("commentary") for call in calls] == ["I'll look at the repo first.", None]

    def test_usage_totals_cover_retained_transcript_before_pagination_and_compaction(self):
        store = InMemoryDocumentStore()
        raw_messages = [
            item
            for index in range(51)
            for item in (
                {"id": f"u{index}", "type": "human", "content": f"question {index}"},
                {
                    "id": f"a{index}",
                    "type": "ai",
                    "content": f"answer {index}",
                    "usage_metadata": {"output_tokens": 2, "reasoning_tokens": 1},
                },
            )
        ]
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_usage_totals",
                checkpoint_id="before-compaction",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=raw_messages,
            )
        )
        _store_session(store, "thread_usage_totals")

        before_compaction = get_chat_detail(store, "thread_usage_totals")

        assert before_compaction is not None
        assert before_compaction.usage_totals.model_dump() == {
            "output_tokens": 102,
            "reasoning_tokens": 51,
            "output_coverage": "complete",
            "reasoning_coverage": "complete",
            "assistant_message_count": 51,
            "output_token_message_count": 51,
            "reasoning_token_message_count": 51,
        }
        page, has_more = paginate_chat_messages(before_compaction.messages, limit=50)
        assert len(page) == 50
        assert has_more is True

        # Native compaction preserves the raw root messages even when the
        # browser only renders a page and a summary boundary.
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_usage_totals",
                checkpoint_id="after-compaction",
                checkpoint_ts="2026-01-01T00:01:00Z",
                messages=raw_messages,
            )
        )

        after_compaction = get_chat_detail(store, "thread_usage_totals")

        assert after_compaction is not None
        assert after_compaction.usage_totals == before_compaction.usage_totals

    def test_usage_totals_deduplicate_reemissions_and_mark_missing_provider_counts(self):
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_partial_usage_totals",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "first persisted emission",
                        "usage_metadata": {"output_tokens": 2, "reasoning_tokens": 1},
                    },
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "final persisted emission",
                        "usage_metadata": {"output_tokens": 5},
                    },
                    {
                        "id": "a2",
                        "type": "ai",
                        "content": "output usage unavailable",
                        "usage_metadata": {"reasoning_tokens": 3},
                    },
                ],
            )
        )
        _store_session(store, "thread_partial_usage_totals")

        detail = get_chat_detail(store, "thread_partial_usage_totals")

        assert detail is not None
        assert detail.usage_totals.model_dump() == {
            "output_tokens": 5,
            "reasoning_tokens": 4,
            "output_coverage": "partial",
            "reasoning_coverage": "complete",
            "assistant_message_count": 2,
            "output_token_message_count": 1,
            "reasoning_token_message_count": 2,
        }

    def test_usage_totals_mark_an_unreported_metric_unknown(self):
        store = InMemoryDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_unknown_usage_totals",
                checkpoint_id="cp-1",
                checkpoint_ts="2026-01-01T00:00:00Z",
                messages=[{"id": "a1", "type": "ai", "content": "No provider usage."}],
            )
        )
        _store_session(store, "thread_unknown_usage_totals")

        detail = get_chat_detail(store, "thread_unknown_usage_totals")

        assert detail is not None
        assert detail.usage_totals.model_dump() == {
            "output_tokens": None,
            "reasoning_tokens": None,
            "output_coverage": "unknown",
            "reasoning_coverage": "unknown",
            "assistant_message_count": 1,
            "output_token_message_count": 0,
            "reasoning_token_message_count": 0,
        }


class TestPatchSessionDocument:
    def test_patches_title(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_p", title="Old")

        patch_session_document(store, "thread_p", ChatPatchRequest(title="New"))

        doc = store.get_document(PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_p")
        assert doc.payload["title"] == "New"

    def test_patches_pinned(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_p", pinned=False)

        patch_session_document(store, "thread_p", ChatPatchRequest(pinned=True))

        doc = store.get_document(PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_p")
        assert doc.payload["pinned"] is True

    def test_patches_archived(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_p", archived=False)

        patch_session_document(store, "thread_p", ChatPatchRequest(archived=True))

        doc = store.get_document(PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_p")
        assert doc.payload["archived"] is True

    def test_noop_when_no_fields_set(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_p", title="Old")

        result = patch_session_document(store, "thread_p", ChatPatchRequest())

        assert result is not None
        assert result.payload["title"] == "Old"
        assert result.changed is False

    def test_returns_none_when_session_missing(self):
        store = InMemoryDocumentStore()
        result = patch_session_document(store, "missing", ChatPatchRequest(title="X"))
        assert result is None


class TestDeleteSessionDocument:
    def test_deletes_document(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_d")

        assert delete_session_document(store, "thread_d") is True
        assert store.get_document(PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_d") is None

    def test_returns_false_when_missing(self):
        store = InMemoryDocumentStore()
        assert delete_session_document(store, "missing") is False


class TestPaginateChatMessages:
    def test_empty_list(self):
        assert paginate_chat_messages([]) == ([], False)

    def test_latest_page_by_default(self):
        from src.backend.chat.service import ChatMessage

        messages = [ChatMessage(id=f"m{i}", role="user", content=str(i)) for i in range(60)]
        page, has_more = paginate_chat_messages(messages, limit=50)
        assert len(page) == 50
        assert has_more is True

    def test_before_message_id(self):
        from src.backend.chat.service import ChatMessage

        messages = [ChatMessage(id=f"m{i}", role="user", content=str(i)) for i in range(60)]
        page, has_more = paginate_chat_messages(messages, limit=10, before_message_id="m50")
        assert len(page) == 10
        assert [m.id for m in page] == [f"m{i}" for i in range(40, 50)]
        assert has_more is True

    def test_falls_back_to_oldest_when_id_not_found(self):
        from src.backend.chat.service import ChatMessage

        messages = [ChatMessage(id=f"m{i}", role="user", content=str(i)) for i in range(20)]
        page, has_more = paginate_chat_messages(messages, limit=10, before_message_id="missing")
        assert len(page) == 10
        assert has_more is False


class TestExtractProgressLogFromContent:
    def test_extracts_progress_log_from_tool_message_content(self):
        content = json.dumps(
            {
                "result": "done",
                "progress_log": [
                    {
                        "state": "building",
                        "message_text": "Compiling...",
                        "agent_name": "builder",
                        "timestamp": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        )
        entries = _extract_progress_log_from_content(content)
        assert len(entries) == 1
        assert entries[0]["state"] == "building"
        assert entries[0]["message_text"] == "Compiling..."

    def test_returns_empty_for_non_json(self):
        assert _extract_progress_log_from_content("plain text") == []

    def test_returns_empty_when_no_progress_log_key(self):
        assert _extract_progress_log_from_content('{"result": "ok"}') == []


class TestStripAttachmentContext:
    def test_strips_attachment_context_marker(self):
        text = f"Hello world{ATTACHMENT_CONTEXT_MARKER}some attachment info"
        from src.backend.chat.service import _strip_attachment_context

        assert _strip_attachment_context(text) == "Hello world"

    def test_returns_original_when_no_marker(self):
        from src.backend.chat.service import _strip_attachment_context

        assert _strip_attachment_context("Hello world") == "Hello world"
