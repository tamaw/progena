"""Direct unit tests for app.backend.chat_sessions.

These exercise the metadata-key rename branches, the reusable-draft selection
with multiple candidates, the `_resolve_metadata_key` fallback scan, and the
checkpoint-snapshot cache used to avoid an N+1 Postgres round trip in
`list_chat_sessions` -- none of which had direct coverage before (only
indirect coverage via test_ui_routes.py's TestClient-based tests).
"""

from __future__ import annotations

import json

from app.backend.chat_sessions import (
    ChatCreateRequest,
    ChatDraftRequest,
    ChatPatchRequest,
    ChatSessionSummary,
    _checkpoint_snapshot_for_metadata,
    _extract_progress_log_from_content,
    _resolve_metadata_key,
    acquire_reusable_draft,
    create_chat_metadata,
    get_chat_detail,
    list_chat_sessions,
    materialize_chat_metadata,
    patch_chat_metadata,
)
from app.backend.document_store import CheckpointThreadSnapshot, InMemoryDocumentStore


class _CountingDocumentStore(InMemoryDocumentStore):
    """Wraps InMemoryDocumentStore to count real per-thread snapshot fetches."""

    def __init__(self) -> None:
        super().__init__()
        self.get_checkpoint_thread_snapshot_calls = 0

    def get_checkpoint_thread_snapshot(
        self, thread_id: str, *, checkpoint_id: str | None = None
    ) -> CheckpointThreadSnapshot | None:
        self.get_checkpoint_thread_snapshot_calls += 1
        return super().get_checkpoint_thread_snapshot(thread_id, checkpoint_id=checkpoint_id)


class TestResolveMetadataKeyFallback:
    def test_falls_back_to_index_scan_when_direct_key_lookup_misses(self):
        store = InMemoryDocumentStore()
        # Metadata persisted under its full "thread_" key, but callers may look
        # it up by the bare session id (no prefix) -- the direct get misses and
        # must fall through to scanning the index for a thread_id match.
        create_chat_metadata(store, ChatCreateRequest(title="Legacy", thread_id="thread_abc"))

        resolved = _resolve_metadata_key(store, "abc")

        assert resolved == "thread_abc"

    def test_returns_none_when_nothing_matches(self):
        store = InMemoryDocumentStore()
        assert _resolve_metadata_key(store, "does-not-exist") is None


class TestMetadataKeyRenames:
    def test_create_chat_metadata_moves_index_entry_on_key_change(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(title="Draft", thread_id="abc"))
        assert "abc" in store.get_document("backend.chat.sessions", "__index__").payload

        # Simulate a record whose own thread_id field has drifted from the
        # storage key it's saved under (e.g. legacy/imported data) -- the
        # fallback scan in _resolve_metadata_key matches it by thread_id, and
        # create_chat_metadata must rename the storage key rather than leave
        # a duplicate record behind under the new thread_id.
        store.save_document(
            "backend.chat.sessions",
            "abc",
            {
                **store.get_document("backend.chat.sessions", "abc").payload,
                "thread_id": "thread_abc",
            },
        )

        create_chat_metadata(store, ChatCreateRequest(title="Draft", thread_id="thread_abc"))

        index = store.get_document("backend.chat.sessions", "__index__").payload
        assert "thread_abc" in index
        assert "abc" not in index

    def test_patch_chat_metadata_renames_key_to_canonical_thread_id(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(title="Draft", thread_id="abc"))

        # A patch that doesn't touch thread_id directly, but the canonical
        # thread_id derived from metadata differs from the storage key --
        # patch must rename the storage key and clean up the old index entry.
        store.save_document(
            "backend.chat.sessions",
            "abc",
            {
                **store.get_document("backend.chat.sessions", "abc").payload,
                "thread_id": "thread_abc",
            },
        )
        result = patch_chat_metadata(store, "abc", ChatPatchRequest(title="Renamed"))

        assert result is not None
        assert result["thread_id"] == "thread_abc"
        index = store.get_document("backend.chat.sessions", "__index__").payload
        assert "thread_abc" in index
        assert "abc" not in index

    def test_materialize_chat_metadata_moves_index_entry_on_key_change(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(title="Draft", thread_id="abc"))
        store.save_document(
            "backend.chat.sessions",
            "abc",
            {
                **store.get_document("backend.chat.sessions", "abc").payload,
                "thread_id": "thread_abc",
            },
        )

        summary = ChatSessionSummary(thread_id="thread_abc", title="Materialized")
        materialize_chat_metadata(store, summary)

        index = store.get_document("backend.chat.sessions", "__index__").payload
        assert "thread_abc" in index
        assert "abc" not in index


class TestAcquireReusableDraft:
    def test_reuses_the_most_recently_active_draft_among_several(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(thread_id="draft-older"))
        create_chat_metadata(store, ChatCreateRequest(thread_id="draft-newer"))

        # list_chat_sessions sorts by last_activity descending, so the more
        # recently created draft ("draft-newer") is reused first, and the
        # other reusable draft is left alone.
        detail, created = acquire_reusable_draft(store, ChatDraftRequest())

        assert created is False
        assert detail.thread_id == "draft-newer"

        remaining_drafts = [
            session
            for session in list_chat_sessions(store, include_archived=False)
            if session.thread_id != "draft-newer"
        ]
        assert len(remaining_drafts) == 1
        assert remaining_drafts[0].thread_id == "draft-older"

    def test_falls_through_to_the_next_draft_once_the_first_is_no_longer_empty(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(thread_id="draft-older"))
        create_chat_metadata(store, ChatCreateRequest(thread_id="draft-newer"))
        detail, _ = acquire_reusable_draft(store, ChatDraftRequest())
        assert detail.thread_id == "draft-newer"

        # Simulate the user actually sending a message in the reused draft --
        # it now has a message and stops qualifying as an empty, reusable one.
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="draft-newer",
                checkpoint_id="checkpoint-1",
                checkpoint_ts=None,
                messages=[{"id": "m1", "type": "human", "content": "hello"}],
            )
        )

        detail_2, created_2 = acquire_reusable_draft(store, ChatDraftRequest())

        assert created_2 is False
        assert detail_2.thread_id == "draft-older"

    def test_creates_a_new_draft_when_none_are_reusable(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(title="Has a title", thread_id="thread-a"))

        detail, created = acquire_reusable_draft(store, ChatDraftRequest(thread_id="thread-b"))

        assert created is True
        assert detail.thread_id == "thread-b"


class TestCheckpointSnapshotCache:
    def test_uses_cached_snapshot_instead_of_a_fresh_fetch(self):
        store = _CountingDocumentStore()
        snapshot = CheckpointThreadSnapshot(
            thread_id="thread_cached",
            checkpoint_id="checkpoint-1",
            checkpoint_ts="2026-07-01T00:00:00Z",
            messages=[],
        )
        cached_snapshots = {"thread_cached": snapshot}
        metadata = {"thread_id": "thread_cached", "checkpoint_id": None}

        result = _checkpoint_snapshot_for_metadata(
            store, metadata, cached_snapshots=cached_snapshots
        )

        assert result is snapshot
        assert store.get_checkpoint_thread_snapshot_calls == 0


class TestDisplayMetadataNormalization:
    def test_chat_detail_preserves_optional_assistant_display_metadata(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(thread_id="thread_meta"))
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_meta",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-07-09T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "hi"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "Hello there",
                        "response_metadata": {
                            "display_agent_name": "greetings_agent",
                            "display_model_name": "gpt-5-mini",
                            "display_tool_calls": ["sleep"],
                        },
                    },
                ],
            )
        )

        detail = get_chat_detail(store, "thread_meta")

        assert detail is not None
        assistant = next(message for message in detail.messages if message.role == "assistant")
        assert assistant.display_agent_name == "greetings_agent"
        assert assistant.display_model_name == "gpt-5-mini"
        assert assistant.display_tool_calls == ["sleep"]

    def test_falls_back_to_a_fresh_fetch_when_metadata_pins_a_different_checkpoint(self):
        store = _CountingDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_pinned",
                checkpoint_id="checkpoint-pinned",
                checkpoint_ts="2026-06-01T00:00:00Z",
                messages=[],
            )
        )
        latest_snapshot = CheckpointThreadSnapshot(
            thread_id="thread_pinned",
            checkpoint_id="checkpoint-latest",
            checkpoint_ts="2026-07-01T00:00:00Z",
            messages=[],
        )
        cached_snapshots = {"thread_pinned": latest_snapshot}
        metadata = {"thread_id": "thread_pinned", "checkpoint_id": "checkpoint-pinned"}

        result = _checkpoint_snapshot_for_metadata(
            store, metadata, cached_snapshots=cached_snapshots
        )

        assert result is not None
        assert result.checkpoint_id == "checkpoint-pinned"
        assert store.get_checkpoint_thread_snapshot_calls == 1

    def test_falls_back_to_a_fresh_fetch_on_cache_miss(self):
        store = _CountingDocumentStore()
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_uncached",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-07-01T00:00:00Z",
                messages=[],
            )
        )
        metadata = {"thread_id": "thread_uncached", "checkpoint_id": None}

        result = _checkpoint_snapshot_for_metadata(store, metadata, cached_snapshots={})

        assert result is not None
        assert store.get_checkpoint_thread_snapshot_calls == 1

    def test_tool_call_created_at_survives_from_the_discarded_tool_only_aimessage(self):
        """A tool-call-only AIMessage never becomes a ChatMessage of its own (no
        visible content), so its created_at would otherwise be lost. It must
        still land on the tool_calls attached to the later text-bearing
        AIMessage -- the frontend needs it to sort tool calls against the
        answer by real time instead of guessing an order.
        """
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(thread_id="thread_tool_ts"))
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_tool_ts",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-07-11T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "search for x"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [{"id": "call_1", "name": "search", "args": {"q": "x"}}],
                        "response_metadata": {"created_at": 1783736960.0},
                    },
                    {
                        "id": "t1",
                        "type": "tool",
                        "tool_call_id": "call_1",
                        "content": "result text",
                    },
                    {
                        "id": "a2",
                        "type": "ai",
                        "content": "Here you go",
                        "response_metadata": {"created_at": 1783736979.0},
                    },
                ],
            )
        )

        detail = get_chat_detail(store, "thread_tool_ts")

        assert detail is not None
        assistant = next(message for message in detail.messages if message.role == "assistant")
        assert assistant.tool_calls[0]["created_at"] == "2026-07-11T02:29:20Z"

    def test_human_message_created_at_reads_from_additional_kwargs(self):
        """HumanMessages carry no response_metadata (that's LLM-response-only),
        so the runtime stamps their receipt time onto additional_kwargs
        instead (see DeepAgentIPCStream.stream / chat_node). Without this
        fallback the frontend has no real timestamp and falls back to
        render time for every user message in every chat.
        """
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(thread_id="thread_human_ts"))
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_human_ts",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-07-11T00:00:00Z",
                messages=[
                    {
                        "id": "u1",
                        "type": "human",
                        "content": "hi",
                        "additional_kwargs": {"created_at": 1783736960.0},
                    },
                    {"id": "a1", "type": "ai", "content": "Hello there"},
                ],
            )
        )

        detail = get_chat_detail(store, "thread_human_ts")

        assert detail is not None
        user = next(message for message in detail.messages if message.role == "user")
        assert user.created_at == "2026-07-11T02:29:20Z"


class TestPendingToolCallFlush:
    """A turn can be interrupted (Stop / page reload racing the request /
    dropped connection) right after its tool call's ToolMessage lands but
    before the model's follow-up commentary comes back -- leaving a
    tool-call-only AIMessage with no later text-bearing AIMessage to absorb
    its tool_calls. `_messages_from_checkpoint` must still surface that tool
    call as its own (contentless) assistant message instead of silently
    dropping the entire turn once the reset for the next message fires.
    """

    def test_second_turns_dangling_tool_call_is_not_dropped_by_the_next_human_message(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(thread_id="thread_dangling_mid"))
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_dangling_mid",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-07-12T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "spawn a subagent"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "name": "task",
                                "args": {"subagent_type": "hello-world"},
                            }
                        ],
                        "response_metadata": {"created_at": 1783736960.0},
                    },
                    {
                        "id": "t1",
                        "type": "tool",
                        "tool_call_id": "call_1",
                        "content": "HELLO_FROM_SUBAGENT",
                    },
                    {
                        "id": "a1b",
                        "type": "ai",
                        "content": "Done!",
                        "response_metadata": {"created_at": 1783736965.0},
                    },
                    {"id": "u2", "type": "human", "content": "do it again"},
                    {
                        "id": "a2",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_2",
                                "name": "task",
                                "args": {"subagent_type": "hello-world"},
                            }
                        ],
                        "response_metadata": {"created_at": 1783737000.0},
                    },
                    {
                        "id": "t2",
                        "type": "tool",
                        "tool_call_id": "call_2",
                        "content": "HELLO_FROM_SUBAGENT",
                    },
                    # Turn 2 cut short here -- no trailing AIMessage before reload.
                ],
            )
        )

        detail = get_chat_detail(store, "thread_dangling_mid")

        assert detail is not None
        roles = [message.role for message in detail.messages]
        assert roles == ["user", "assistant", "user", "assistant"]
        second_reply = detail.messages[3]
        assert second_reply.content == ""
        assert [tc["name"] for tc in second_reply.tool_calls] == ["task"]
        assert second_reply.tool_calls[0]["result"] == "HELLO_FROM_SUBAGENT"

    def test_checkpoint_tip_left_as_a_dangling_tool_call_is_flushed_at_the_end(self):
        store = InMemoryDocumentStore()
        create_chat_metadata(store, ChatCreateRequest(thread_id="thread_dangling_tip"))
        store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_dangling_tip",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-07-12T00:00:00Z",
                messages=[
                    {"id": "u1", "type": "human", "content": "spawn a subagent"},
                    {
                        "id": "a1",
                        "type": "ai",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "name": "task",
                                "args": {"subagent_type": "hello-world"},
                            }
                        ],
                        "response_metadata": {"created_at": 1783736960.0},
                    },
                    {
                        "id": "t1",
                        "type": "tool",
                        "tool_call_id": "call_1",
                        "content": "HELLO_FROM_SUBAGENT",
                    },
                    # No trailing AIMessage at all -- checkpoint's tip is the tool result.
                ],
            )
        )

        detail = get_chat_detail(store, "thread_dangling_tip")

        assert detail is not None
        roles = [message.role for message in detail.messages]
        assert roles == ["user", "assistant"]
        reply = detail.messages[1]
        assert reply.content == ""
        assert [tc["name"] for tc in reply.tool_calls] == ["task"]
        assert reply.tool_calls[0]["result"] == "HELLO_FROM_SUBAGENT"


class TestExtractProgressLogFromContent:
    """agent_name must survive this extraction -- it's what lets a reloaded
    chat repopulate the A2A card's header (see the matching agent_name param
    on extract_progress_log in app/progenitor/a2a/payloads.py). Regression
    coverage for a bug where this function's own {state, message_text}
    whitelist silently dropped agent_name even though every upstream layer
    already carried it."""

    def test_preserves_agent_name_alongside_state_and_message_text(self):
        content = json.dumps(
            {
                "progress_log": [
                    {
                        "state": "submitted",
                        "message_text": "Accepted: hi",
                        "agent_name": "greetings_agent",
                    },
                    {
                        "state": "working",
                        "message_text": "Working...",
                        "agent_name": "greetings_agent",
                    },
                ],
            }
        )
        assert _extract_progress_log_from_content(content) == [
            {
                "state": "submitted",
                "message_text": "Accepted: hi",
                "agent_name": "greetings_agent",
                "timestamp": "",
            },
            {
                "state": "working",
                "message_text": "Working...",
                "agent_name": "greetings_agent",
                "timestamp": "",
            },
        ]

    def test_defaults_agent_name_to_empty_string_when_absent(self):
        """Older persisted data (predating this field) must still parse cleanly."""
        content = json.dumps(
            {
                "progress_log": [{"state": "submitted", "message_text": "Accepted: hi"}],
            }
        )
        assert _extract_progress_log_from_content(content) == [
            {
                "state": "submitted",
                "message_text": "Accepted: hi",
                "agent_name": "",
                "timestamp": "",
            },
        ]

    def test_preserves_timestamp_alongside_state_and_message_text(self):
        """timestamp must survive this extraction too -- it's what lets a
        reloaded chat show each A2A sub-entry's own real time instead of the
        parent tool call's single shared timestamp (see extract_progress_log
        in app/progenitor/a2a/payloads.py)."""
        content = json.dumps(
            {
                "progress_log": [
                    {
                        "state": "submitted",
                        "message_text": "Accepted: hi",
                        "agent_name": "greetings_agent",
                        "timestamp": "2026-07-11T02:31:02+00:00",
                    },
                ],
            }
        )
        assert _extract_progress_log_from_content(content) == [
            {
                "state": "submitted",
                "message_text": "Accepted: hi",
                "agent_name": "greetings_agent",
                "timestamp": "2026-07-11T02:31:02+00:00",
            },
        ]

    def test_skips_json_parse_for_content_with_no_progress_log_key(self, monkeypatch):
        """A cheap substring pre-filter must reject non-call_agent tool
        results (the overwhelming majority) before paying for json.loads --
        regression coverage against that pre-filter being too eager and
        skipping content that actually has a progress_log."""
        import app.backend.chat_sessions as chat_sessions_module

        def _fail_if_called(*_args, **_kwargs):
            raise AssertionError("json.loads should not run when 'progress_log' key is absent")

        monkeypatch.setattr(chat_sessions_module.json, "loads", _fail_if_called)
        assert _extract_progress_log_from_content('{"result": "no progress log here"}') == []

    def test_returns_empty_list_on_recursion_error_instead_of_crashing(self, monkeypatch):
        """json.loads raises RecursionError (not ValueError) on sufficiently
        deeply nested JSON -- a malformed/adversarial persisted `task` payload
        alongside progress_log must not crash chat-session hydration for the
        whole conversation, only drop this one optional progress log."""
        import app.backend.chat_sessions as chat_sessions_module

        def _raise_recursion_error(*_args, **_kwargs):
            raise RecursionError("maximum recursion depth exceeded")

        monkeypatch.setattr(chat_sessions_module.json, "loads", _raise_recursion_error)
        content = '{"progress_log": [{"state": "submitted"}]}'
        assert _extract_progress_log_from_content(content) == []
