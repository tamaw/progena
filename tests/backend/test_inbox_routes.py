"""Inbox list/detail routes and private detail-event regression coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from ack_helpers import assert_acknowledged
from fastapi.testclient import TestClient

from src.backend.events import stream as events_stream
from src.backend.events.documents import TypedDocumentEvent
from src.backend.inbox import routes as inbox_routes
from src.backend.inbox.events import inbox_message_events
from src.backend.main import app
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    DocumentEvent,
    InMemoryDocumentStore,
    set_default_document_store,
)


def _message_payload(
    message_id: str,
    *,
    title: str,
    created_at: str,
    read: bool = False,
    archived: bool = False,
    approval: bool = False,
    body: str | None = None,
) -> dict:
    return {
        "id": message_id,
        "title": title,
        "created_at": created_at,
        "read": read,
        "read_at": "2026-07-05T01:00:00+00:00" if read else None,
        "body": body if body is not None else f"Body of {message_id}",
        "requires_approval": approval,
        "archived": archived,
    }


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def document_store():
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    events_stream._ui_stream_topics.clear()
    yield store
    inbox_message_events.interests.clear()
    events_stream._ui_stream_topics.clear()
    set_default_document_store(InMemoryDocumentStore())


@pytest.fixture
def seeded_store(document_store):
    for message_id, title, created_at, read in (
        ("msg_old", "Old digest", "2026-07-01T00:00:00+00:00", True),
        ("msg_new", "New digest", "2026-07-05T00:00:00+00:00", False),
    ):
        document_store.save_document(
            PROGENA_STATE_PREFIX,
            f"inbox_message:{message_id}",
            _message_payload(message_id, title=title, created_at=created_at, read=read),
        )
    return document_store


class _StreamCapture:
    def __init__(self, *client_ids: str):
        self.queues = {client_id: asyncio.Queue() for client_id in client_ids}

    def __enter__(self):
        for client_id, queue in self.queues.items():
            events_stream._ui_stream_subscribers[queue] = None
            events_stream._ui_stream_clients[queue] = client_id
        return self

    def __exit__(self, *exc_info):
        for queue in self.queues.values():
            events_stream._ui_stream_subscribers.pop(queue, None)
            events_stream._ui_stream_clients.pop(queue, None)
        return False

    def events(self, client_id: str):
        events = []
        queue = self.queues[client_id]
        while not queue.empty():
            events.append(queue.get_nowait()["envelope"])
        return events


def test_publish_counts_includes_server_pending_approval_count(document_store):
    document_store.save_document(
        PROGENA_STATE_PREFIX,
        "inbox_message:approval",
        _message_payload(
            "approval",
            title="Approval",
            created_at="2026-07-06T00:00:00+00:00",
            approval=True,
        ),
    )
    events: list[dict] = []
    inbox_routes._publish_counts(
        document_store,
        lambda _event_type, payload, **_kwargs: events.append(payload),
    )
    assert events == [{"unread": 1, "pending_approvals": 1}]


class TestInboxRoutes:
    def test_list_is_targeted_paged_and_never_includes_bodies(self, client, seeded_store):
        with _StreamCapture("tab-a", "tab-b") as capture:
            response = client.get(
                "/api/ui/inbox?client_id=tab-a&offset=0&limit=1",
                headers={"X-Operation-Id": "list-request"},
            )
            envelopes = capture.events("tab-a")
            assert capture.events("tab-b") == []

        assert_acknowledged(response, ["inbox.list.loaded"], "list-request")
        assert [event.type for event in envelopes] == ["inbox.list.loaded"]
        payload = envelopes[0].payload
        assert payload["total"] == 2
        assert payload["offset"] == 0
        assert [row["id"] for row in payload["rows"]] == ["msg_new"]
        assert "body" not in payload["rows"][0]
        assert payload["rows"][0]["preview"] == "Body of msg_new"

    def test_list_filters_searches_body_and_orders_by_name(self, seeded_store):
        seeded_store.save_document(
            PROGENA_STATE_PREFIX,
            "inbox_message:pending",
            _message_payload(
                "pending",
                title="Alpha",
                created_at="2026-07-06T00:00:00+00:00",
                approval=True,
                body="needle in body",
            ),
        )
        seeded_store.save_document(
            PROGENA_STATE_PREFIX,
            "inbox_message:archived",
            _message_payload(
                "archived",
                title="Archived",
                created_at="2026-07-07T00:00:00+00:00",
                archived=True,
            ),
        )
        body_search = inbox_routes.query_inbox_list(
            seeded_store,
            inbox_routes.InboxListView(q="needle", filter="all", sort="recent"),
            offset=0,
            limit=50,
        )
        pending = inbox_routes.query_inbox_list(
            seeded_store,
            inbox_routes.InboxListView(filter="pending", sort="name"),
            offset=0,
            limit=50,
        )
        archived = inbox_routes.query_inbox_list(
            seeded_store,
            inbox_routes.InboxListView(filter="archived", sort="recent"),
            offset=0,
            limit=50,
        )
        assert [row.id for row in body_search.rows] == ["pending"]
        assert [row.id for row in pending.rows] == ["pending"]
        assert [row.id for row in archived.rows] == ["archived"]

    def test_select_replaces_interest_and_external_change_keeps_body_private(
        self, client, seeded_store
    ):
        with _StreamCapture("tab-a", "tab-b") as capture:
            first = client.get("/api/ui/inbox/msg_new?client_id=tab-a")
            second = client.get("/api/ui/inbox/msg_old?client_id=tab-a")
            inbox_message_events.subscribe("tab-b", "msg_new")
            changed = inbox_routes._normalize_message(
                "inbox_message:msg_new",
                {
                    **_message_payload(
                        "msg_new", title="New digest", created_at="2026-07-05T00:00:00+00:00"
                    ),
                    "body": "secret body",
                },
            )
            assert changed is not None
            inbox_routes.publish_inbox_message_change(seeded_store, changed)
            tab_a = capture.events("tab-a")
            tab_b = capture.events("tab-b")

        assert_acknowledged(first, ["inbox.message.selected"])
        assert_acknowledged(second, ["inbox.message.selected"])
        assert [event.type for event in tab_a] == [
            "inbox.message.selected",
            "inbox.message.selected",
            "inbox.message.upserted",
            "inbox.counts.updated",
        ]
        # tab-b holds only a selected-message interest, not the Inbox page topic.
        assert [event.type for event in tab_b] == [
            "inbox.counts.updated",
            "inbox.message.updated",
        ]
        assert tab_b[-1].payload["message"]["body"] == "secret body"
        assert "body" not in tab_a[-2].payload["message"]

    def test_mutation_uses_atomic_merge_and_acknowledges_row_event(self, client, seeded_store):
        events_stream.subscribe_topic("tab-a", inbox_routes.INBOX_TOPIC)
        with _StreamCapture("tab-a") as capture:
            response = client.post("/api/ui/inbox/msg_new/read?client_id=tab-a")
            events = capture.events("tab-a")

        assert_acknowledged(response, ["inbox.message.upserted"])
        stored = seeded_store.get_document(PROGENA_STATE_PREFIX, "inbox_message:msg_new")
        assert stored is not None
        assert stored.payload["read"] is True
        assert stored.payload["body"] == "Body of msg_new"
        assert [event.type for event in events] == [
            "inbox.message.upserted",
            "inbox.counts.updated",
        ]

    def test_subscription_release_and_unknown_message(self, client, document_store):
        with _StreamCapture("tab-a"):
            response = client.put(
                "/api/ui/inbox/subscription",
                json={"client_id": "tab-a", "message_id": None},
            )
            missing = client.get("/api/ui/inbox/nope?client_id=tab-a")

        assert_acknowledged(response, ["inbox.counts.updated"])
        assert missing.status_code == 404

    def test_subscription_renews_interest_with_fresh_selected_detail(self, client, seeded_store):
        with _StreamCapture("tab-a") as capture:
            response = client.put(
                "/api/ui/inbox/subscription",
                json={"client_id": "tab-a", "message_id": "msg_new"},
            )
            events = capture.events("tab-a")

        assert_acknowledged(response, ["inbox.message.selected"])
        assert [event.type for event in events] == ["inbox.message.selected"]
        assert events[0].payload["message"]["body"] == "Body of msg_new"
        assert inbox_message_events.interests["tab-a"].message_id == "msg_new"

    def test_pages_cover_every_row_once_for_both_sorts(self, seeded_store):
        for index, (title, created) in enumerate(
            [
                ("beta", "2026-07-06T00:00:00+00:00"),
                ("Alpha", "2026-07-06T00:00:00+00:00"),
                ("alpha", "2026-07-03T00:00:00+00:00"),
            ]
        ):
            seeded_store.save_document(
                PROGENA_STATE_PREFIX,
                f"inbox_message:tie{index}",
                _message_payload(f"tie{index}", title=title, created_at=created),
            )

        def pages(sort: str, limit: int) -> tuple[list[str], int]:
            view = inbox_routes.InboxListView(sort=sort)
            total = inbox_routes.query_inbox_list(seeded_store, view, offset=0, limit=0).total
            ids = [
                row.id
                for offset in range(0, total, limit)
                for row in inbox_routes.query_inbox_list(
                    seeded_store, view, offset=offset, limit=limit
                ).rows
            ]
            return ids, total

        for sort in ("recent", "name"):
            by_two, total = pages(sort, 2)
            by_five, _ = pages(sort, 5)
            assert total == 5
            assert by_two == by_five
            assert len(set(by_two)) == total

        past_end = inbox_routes.query_inbox_list(
            seeded_store, inbox_routes.InboxListView(), offset=100, limit=50
        )
        assert past_end.rows == [] and past_end.total == 5

    def test_row_events_reach_only_tabs_on_the_inbox_page(self, client, seeded_store):
        with _StreamCapture("inbox-tab", "chat-tab") as capture:
            client.get("/api/ui/inbox?client_id=inbox-tab&offset=0&limit=1")
            capture.events("inbox-tab")
            client.post("/api/ui/inbox/msg_new/read?client_id=inbox-tab")
            inbox_tab = capture.events("inbox-tab")
            chat_tab = capture.events("chat-tab")
            client.delete("/api/ui/inbox/msg_old?client_id=inbox-tab")
            inbox_after_delete = capture.events("inbox-tab")
            chat_after_delete = capture.events("chat-tab")

        assert [e.type for e in inbox_tab] == ["inbox.message.upserted", "inbox.counts.updated"]
        # The nav badge is app chrome: every tab keeps receiving it.
        assert [e.type for e in chat_tab] == ["inbox.counts.updated"]
        assert [e.type for e in inbox_after_delete] == [
            "inbox.message.deleted",
            "inbox.counts.updated",
        ]
        assert [e.type for e in chat_after_delete] == ["inbox.counts.updated"]

    def test_leaving_the_page_stops_row_events(self, client, seeded_store):
        with _StreamCapture("tab-a", "writer") as capture:
            client.get("/api/ui/inbox?client_id=tab-a&offset=0&limit=1")
            left = client.put(
                "/api/ui/inbox/subscription",
                json={"client_id": "tab-a", "message_id": None, "watching": False},
            )
            capture.events("tab-a")
            client.post("/api/ui/inbox/msg_new/read?client_id=writer")
            events = capture.events("tab-a")

        assert_acknowledged(left, ["inbox.counts.updated"])
        assert not events_stream.has_topic("tab-a", inbox_routes.INBOX_TOPIC)
        assert [e.type for e in events] == ["inbox.counts.updated"]

    def test_clearing_selection_keeps_the_page_topic(self, client, seeded_store):
        with _StreamCapture("tab-a"):
            client.get("/api/ui/inbox/msg_new?client_id=tab-a")
            client.put(
                "/api/ui/inbox/subscription", json={"client_id": "tab-a", "message_id": None}
            )

        assert events_stream.has_topic("tab-a", inbox_routes.INBOX_TOPIC)


def test_disconnected_inbox_interest_is_removed():
    inbox_message_events.subscribe("tab", "message")
    inbox_message_events.disconnected("tab")
    assert inbox_message_events.interests == {}


def test_external_document_events_publish_slim_rows_counts_and_delete(document_store):
    payload = _message_payload(
        "external", title="External", created_at="2026-07-08T00:00:00+00:00", body="private"
    )
    document_store.save_document(PROGENA_STATE_PREFIX, "inbox_message:external", payload)
    created = DocumentEvent(
        seq=1,
        txid=1,
        prefix=PROGENA_STATE_PREFIX,
        key="inbox_message:external",
        op="created",
        before=None,
        after=payload,
        origin=None,
        created_at=datetime.now(UTC),
    )
    deleted = DocumentEvent(
        seq=2,
        txid=2,
        prefix=PROGENA_STATE_PREFIX,
        key="inbox_message:external",
        op="deleted",
        before=payload,
        after=None,
        origin=None,
        created_at=datetime.now(UTC),
    )
    events_stream.subscribe_topic("tab", inbox_routes.INBOX_TOPIC)
    with _StreamCapture("tab") as capture:
        asyncio.run(
            inbox_routes.publish_inbox_document_event(TypedDocumentEvent(created, None, None))
        )
        document_store.delete_document(PROGENA_STATE_PREFIX, "inbox_message:external")
        asyncio.run(
            inbox_routes.publish_inbox_document_event(TypedDocumentEvent(deleted, None, None))
        )
        events = capture.events("tab")

    assert [event.type for event in events] == [
        "inbox.message.upserted",
        "inbox.counts.updated",
        "inbox.message.deleted",
        "inbox.counts.updated",
    ]
    assert "body" not in events[0].payload["message"]
