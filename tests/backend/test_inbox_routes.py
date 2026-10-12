"""Tests for the inbox message routes (/api/ui/inbox)."""

import asyncio

import pytest
from ack_helpers import assert_acknowledged
from fastapi.testclient import TestClient

from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.main import app


def _message_payload(message_id: str, *, title: str, created_at: str, read: bool = False) -> dict:
    return {
        "id": message_id,
        "title": title,
        "created_at": created_at,
        "read": read,
        "read_at": "2026-07-05T01:00:00+00:00" if read else None,
        "body": f"Body of {message_id}",
    }


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def document_store():
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    yield store
    set_default_document_store(InMemoryDocumentStore())


@pytest.fixture
def seeded_store(document_store):
    for message_id, title, created_at, read in (
        ("msg_old", "Old digest", "2026-07-01T00:00:00+00:00", True),
        ("msg_new", "New digest", "2026-07-05T00:00:00+00:00", False),
    ):
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            f"inbox_message:{message_id}",
            _message_payload(message_id, title=title, created_at=created_at, read=read),
        )
    # A junk non-dict payload row must be tolerated, not crash the snapshot.
    document_store.save_document(PROGENITOR_STATE_PREFIX, "inbox_message:junk", None)
    return document_store


class _StreamCapture:
    """Subscribe to the UI event stream for the duration of a request."""

    def __init__(self):
        from app.backend import ui_routes

        self._ui_routes = ui_routes
        self.queue = asyncio.Queue()

    def __enter__(self):
        self._ui_routes._ui_stream_subscribers[self.queue] = None
        return self

    def __exit__(self, *exc_info):
        self._ui_routes._ui_stream_subscribers.pop(self.queue, None)
        return False

    def event_types(self):
        events = []
        while not self.queue.empty():
            event = self.queue.get_nowait()
            events.append(event["envelope"])
        return events


class TestInboxRoutes:
    def test_get_inbox_returns_request_correlated_snapshot(self, client, seeded_store):
        """The accepted client ID resolves on the inbox snapshot event."""
        request_id = "e6579b7c-366a-4d61-a71b-1b4fb08dcd91"
        with _StreamCapture() as capture:
            response = client.get(
                "/api/ui/inbox",
                headers={"X-Request-ID": request_id},
            )

        assert_acknowledged(
            response,
            ["inbox.snapshot"],
            request_id,
            check_request_id_header=True,
        )
        envelopes = capture.event_types()
        assert [envelope.type for envelope in envelopes] == ["inbox.snapshot"]
        snapshot = envelopes[0]
        assert snapshot.payload["request_id"] == request_id
        assert len(snapshot.payload["messages"]) == 2
        assert snapshot.payload["total"] == 2
        assert snapshot.payload["unread"] == 1
        assert snapshot.payload["messages"][0]["id"] == "msg_new"
        assert snapshot.payload["messages"][1]["id"] == "msg_old"
        assert snapshot.payload["messages"][0]["body"] == "Body of msg_new"

    def test_get_inbox_empty(self, client, document_store):
        response = client.get("/api/ui/inbox")
        assert_acknowledged(response, ["inbox.snapshot"])

    def test_mark_read_persists_and_broadcasts(self, client, seeded_store):
        with _StreamCapture() as capture:
            response = client.post("/api/ui/inbox/msg_new/read")

        assert_acknowledged(response, ["inbox.snapshot"])

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_new")
        assert stored.payload["read"] is True
        assert stored.payload["read_at"]

        types = [envelope.type for envelope in capture.event_types()]
        assert types == ["inbox.message.upserted", "inbox.snapshot"]

    def test_mark_unread_clears_read_at(self, client, seeded_store):
        response = client.post("/api/ui/inbox/msg_old/read", json={"read": False})
        assert_acknowledged(response, ["inbox.snapshot"])

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_old")
        assert stored.payload["read"] is False
        assert stored.payload["read_at"] is None

    def test_mark_read_unknown_message_404s(self, client, document_store):
        response = client.post("/api/ui/inbox/nope/read")
        assert response.status_code == 404

    def test_decide_approved_persists_and_broadcasts(self, client, seeded_store):
        with _StreamCapture() as capture:
            response = client.post("/api/ui/inbox/msg_new/decision", json={"decision": "approved"})

        assert_acknowledged(response, ["inbox.snapshot"])

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_new")
        assert stored.payload["decision"] == "approved"
        assert stored.payload["read"] is True
        assert stored.payload["read_at"]

        types = [envelope.type for envelope in capture.event_types()]
        assert types == ["inbox.message.upserted", "inbox.snapshot"]

    def test_decide_rejected_persists(self, client, seeded_store):
        response = client.post("/api/ui/inbox/msg_old/decision", json={"decision": "rejected"})
        assert_acknowledged(response, ["inbox.snapshot"])

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_old")
        assert stored.payload["decision"] == "rejected"

    def test_decide_rejects_invalid_value(self, client, seeded_store):
        response = client.post("/api/ui/inbox/msg_new/decision", json={"decision": "maybe"})
        assert response.status_code == 422

    def test_decide_unknown_message_404s(self, client, document_store):
        response = client.post("/api/ui/inbox/nope/decision", json={"decision": "approved"})
        assert response.status_code == 404

    def test_snapshot_reflects_requires_approval_and_task_id(self, client, document_store):
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "inbox_message:msg_plan",
            {
                **_message_payload(
                    "msg_plan", title="Plan", created_at="2026-07-06T00:00:00+00:00"
                ),
                "requires_approval": True,
                "task_id": "card_1",
            },
        )
        with _StreamCapture() as capture:
            response = client.get("/api/ui/inbox")

        assert_acknowledged(response, ["inbox.snapshot"])
        snapshot = capture.event_types()[0]
        message = snapshot.payload["messages"][0]
        assert message["requires_approval"] is True
        assert message["task_id"] == "card_1"
        assert message["decision"] is None

    def test_archive_persists_and_broadcasts(self, client, seeded_store):
        with _StreamCapture() as capture:
            response = client.post("/api/ui/inbox/msg_new/archive")

        assert_acknowledged(response, ["inbox.snapshot"])

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_new")
        assert stored.payload["archived"] is True

        types = [envelope.type for envelope in capture.event_types()]
        assert types == ["inbox.message.upserted", "inbox.snapshot"]

    def test_unarchive_clears_flag(self, client, seeded_store):
        client.post("/api/ui/inbox/msg_new/archive")
        response = client.post("/api/ui/inbox/msg_new/archive", json={"archived": False})
        assert_acknowledged(response, ["inbox.snapshot"])

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_new")
        assert stored.payload["archived"] is False

    def test_archive_unknown_message_404s(self, client, document_store):
        response = client.post("/api/ui/inbox/nope/archive")
        assert response.status_code == 404

    def test_delete_removes_row_and_broadcasts(self, client, seeded_store):
        with _StreamCapture() as capture:
            response = client.delete("/api/ui/inbox/msg_old")

        assert_acknowledged(response, ["inbox.snapshot"])
        assert seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_old") is None

        envelopes = capture.event_types()
        types = [envelope.type for envelope in envelopes]
        assert types == ["inbox.message.deleted", "inbox.snapshot"]
        assert envelopes[0].payload["message_id"] == "msg_old"
        assert envelopes[1].payload["total"] == 1

    def test_patch_section_replaces_range_and_broadcasts(self, client, seeded_store):
        with _StreamCapture() as capture:
            response = client.patch(
                "/api/ui/inbox/msg_new/section",
                json={
                    "expected_body": "Body of msg_new",
                    "start": 5,
                    "end": 7,
                    "replacement": "is",
                },
            )

        assert_acknowledged(response, ["inbox.snapshot"])

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_new")
        assert stored.payload["body"] == "Body is msg_new"

        types = [envelope.type for envelope in capture.event_types()]
        assert types == ["inbox.message.upserted", "inbox.snapshot"]

    def test_patch_section_with_astral_characters(self, client, document_store):
        # U+1F600 😀 occupies two UTF-16 code units; U+10437 𐐷 also occupies two.
        body = "Hello 😀 𐐷 world"
        payload = _message_payload("emoji", title="Emoji", created_at="2026-07-06T00:00:00+00:00")
        payload["body"] = body
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "inbox_message:emoji",
            payload,
        )
        # Replace "😀 " (start=6, end=9) with ""
        response = client.patch(
            "/api/ui/inbox/emoji/section",
            json={"expected_body": body, "start": 6, "end": 9, "replacement": ""},
        )
        assert_acknowledged(response, ["inbox.snapshot"])

        stored = document_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:emoji")
        assert stored.payload["body"] == "Hello 𐐷 world"

    def test_patch_section_stale_body_returns_409(self, client, seeded_store):
        response = client.patch(
            "/api/ui/inbox/msg_new/section",
            json={
                "expected_body": "stale body",
                "start": 0,
                "end": 5,
                "replacement": "fresh",
            },
        )
        assert response.status_code == 409

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_new")
        assert stored.payload["body"] == "Body of msg_new"

    def test_patch_section_preserves_concurrent_metadata(self, client, seeded_store):
        # Simulate concurrent metadata change by mutating the store directly.
        seeded_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "inbox_message:msg_new",
            {
                **_message_payload(
                    "msg_new", title="New digest", created_at="2026-07-05T00:00:00+00:00"
                ),
                "read": True,
                "read_at": "2026-07-05T01:00:00+00:00",
            },
        )
        response = client.patch(
            "/api/ui/inbox/msg_new/section",
            json={
                "expected_body": "Body of msg_new",
                "start": 0,
                "end": 4,
                "replacement": "Text",
            },
        )
        assert_acknowledged(response, ["inbox.snapshot"])

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_new")
        assert stored.payload["body"] == "Text of msg_new"
        assert stored.payload["read"] is True
        assert stored.payload["read_at"] == "2026-07-05T01:00:00+00:00"

    def test_patch_section_invalid_range_returns_400(self, client, seeded_store):
        for bad in (
            {"expected_body": "Body of msg_new", "start": -1, "end": 3, "replacement": ""},
            {"expected_body": "Body of msg_new", "start": 3, "end": 2, "replacement": ""},
            {"expected_body": "Body of msg_new", "start": 0, "end": 100, "replacement": ""},
        ):
            response = client.patch("/api/ui/inbox/msg_new/section", json=bad)
            assert response.status_code == 400, bad

    def test_patch_section_splits_surrogate_pair_returns_400(self, client, document_store):
        body = "a𐐷b"  # 𐐷 is U+10437, two UTF-16 code units
        payload = _message_payload("surrogate", title="S", created_at="2026-07-06T00:00:00+00:00")
        payload["body"] = body
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "inbox_message:surrogate",
            payload,
        )
        # start=2 splits the surrogate pair (indices 0='a', 1-2='𐐷', 3='b')
        response = client.patch(
            "/api/ui/inbox/surrogate/section",
            json={"expected_body": body, "start": 2, "end": 3, "replacement": ""},
        )
        assert response.status_code == 400

    def test_patch_section_unknown_message_404s(self, client, document_store):
        response = client.patch(
            "/api/ui/inbox/nope/section",
            json={"expected_body": "x", "start": 0, "end": 1, "replacement": "y"},
        )
        assert response.status_code == 404

    def test_delete_unknown_message_404s(self, client, document_store):
        response = client.delete("/api/ui/inbox/nope")
        assert response.status_code == 404
