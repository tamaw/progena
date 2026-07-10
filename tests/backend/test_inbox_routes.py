"""Tests for the inbox message routes (/api/ui/inbox)."""

import asyncio

import pytest
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
        self._ui_routes._ui_stream_subscribers.add(self.queue)
        return self

    def __exit__(self, *exc_info):
        self._ui_routes._ui_stream_subscribers.discard(self.queue)
        return False

    def event_types(self):
        events = []
        while not self.queue.empty():
            event = self.queue.get_nowait()
            events.append(event["envelope"])
        return events


class TestInboxRoutes:
    def test_get_inbox_returns_snapshot_newest_first(self, client, seeded_store):
        with _StreamCapture() as capture:
            response = client.get("/api/ui/inbox")

        assert response.status_code == 200
        data = response.json()
        assert [message["id"] for message in data["messages"]] == ["msg_new", "msg_old"]
        assert data["total"] == 2
        assert data["unread"] == 1
        assert data["messages"][0]["body"] == "Body of msg_new"

        envelopes = capture.event_types()
        assert [envelope.type for envelope in envelopes] == ["inbox.snapshot"]
        assert envelopes[0].payload["unread"] == 1

    def test_get_inbox_empty(self, client, document_store):
        response = client.get("/api/ui/inbox")
        assert response.status_code == 200
        assert response.json() == {"messages": [], "total": 0, "unread": 0}

    def test_mark_read_persists_and_broadcasts(self, client, seeded_store):
        with _StreamCapture() as capture:
            response = client.post("/api/ui/inbox/msg_new/read")

        assert response.status_code == 200
        message = response.json()
        assert message["read"] is True
        assert message["read_at"]

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_new")
        assert stored.payload["read"] is True
        assert stored.payload["read_at"] == message["read_at"]

        types = [envelope.type for envelope in capture.event_types()]
        assert types == ["inbox.message.upserted", "inbox.snapshot"]

    def test_mark_unread_clears_read_at(self, client, seeded_store):
        response = client.post("/api/ui/inbox/msg_old/read", json={"read": False})
        assert response.status_code == 200
        message = response.json()
        assert message["read"] is False
        assert message["read_at"] is None

        stored = seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_old")
        assert stored.payload["read"] is False
        assert stored.payload["read_at"] is None

    def test_mark_read_unknown_message_404s(self, client, document_store):
        response = client.post("/api/ui/inbox/nope/read")
        assert response.status_code == 404

    def test_delete_removes_row_and_broadcasts(self, client, seeded_store):
        with _StreamCapture() as capture:
            response = client.delete("/api/ui/inbox/msg_old")

        assert response.status_code == 204
        assert seeded_store.get_document(PROGENITOR_STATE_PREFIX, "inbox_message:msg_old") is None

        envelopes = capture.event_types()
        types = [envelope.type for envelope in envelopes]
        assert types == ["inbox.message.deleted", "inbox.snapshot"]
        assert envelopes[0].payload["message_id"] == "msg_old"
        assert envelopes[1].payload["total"] == 1

    def test_delete_unknown_message_404s(self, client, document_store):
        response = client.delete("/api/ui/inbox/nope")
        assert response.status_code == 404
