"""Direct unit tests for src.backend.chat.service.

These exercise the ``session:`` document read model, atomic patch merge,
list ordering, and checkpoint-snapshot rendering.
"""

from __future__ import annotations

import json

from src.backend.chat.service import (
    ChatPatchRequest,
    _extract_progress_log_from_content,
    delete_session_document,
    get_chat_detail,
    list_chat_sessions,
    paginate_chat_messages,
    patch_session_document,
)
from src.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    CheckpointThreadSnapshot,
    InMemoryDocumentStore,
)
from src.progenitor.documents.kinds import SESSION_KEY_PREFIX
from src.progenitor.sessions.titles import ATTACHMENT_CONTEXT_MARKER


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
    store.save_document(PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}", payload)


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


class TestPatchSessionDocument:
    def test_patches_title(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_p", title="Old")

        patch_session_document(store, "thread_p", ChatPatchRequest(title="New"))

        doc = store.get_document(PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_p")
        assert doc.payload["title"] == "New"

    def test_patches_pinned(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_p", pinned=False)

        patch_session_document(store, "thread_p", ChatPatchRequest(pinned=True))

        doc = store.get_document(PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_p")
        assert doc.payload["pinned"] is True

    def test_patches_archived(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_p", archived=False)

        patch_session_document(store, "thread_p", ChatPatchRequest(archived=True))

        doc = store.get_document(PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_p")
        assert doc.payload["archived"] is True

    def test_noop_when_no_fields_set(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_p", title="Old")

        result = patch_session_document(store, "thread_p", ChatPatchRequest())

        assert result["title"] == "Old"

    def test_returns_none_when_session_missing(self):
        store = InMemoryDocumentStore()
        result = patch_session_document(store, "missing", ChatPatchRequest(title="X"))
        assert result is None


class TestDeleteSessionDocument:
    def test_deletes_document(self):
        store = InMemoryDocumentStore()
        _store_session(store, "thread_d")

        assert delete_session_document(store, "thread_d") is True
        assert store.get_document(PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_d") is None

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
