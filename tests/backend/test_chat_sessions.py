"""Direct unit tests for app.backend.chat_sessions.

These exercise the metadata-key rename branches, the reusable-draft selection
with multiple candidates, the `_resolve_metadata_key` fallback scan, and the
checkpoint-snapshot cache used to avoid an N+1 Postgres round trip in
`list_chat_sessions` -- none of which had direct coverage before (only
indirect coverage via test_ui_routes.py's TestClient-based tests).
"""

from __future__ import annotations

from app.backend.chat_sessions import (
    ChatCreateRequest,
    ChatDraftRequest,
    ChatPatchRequest,
    ChatSessionSummary,
    _checkpoint_snapshot_for_metadata,
    _resolve_metadata_key,
    acquire_reusable_draft,
    create_chat_metadata,
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
