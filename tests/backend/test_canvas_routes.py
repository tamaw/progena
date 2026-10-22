"""Canvas route, snapshot, and interest behavior."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from ack_helpers import assert_acknowledged
from fastapi.testclient import TestClient

import src.backend.canvases.routes as canvases_routes
from src.backend.canvases.events import canvas_events
from src.backend.events.documents import TypedDocumentEvent
from src.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    DocumentEvent,
    InMemoryDocumentStore,
    set_default_document_store,
)
from src.progenitor.canvases.contracts import canvas_file_key
from src.progenitor.documents.kinds import DOCUMENT_TYPE_KEY_PREFIX
from src.progenitor.documents.types import DocumentType


@pytest.fixture
def document_store():
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    yield store
    canvas_events.interests.clear()
    set_default_document_store(InMemoryDocumentStore())


@pytest.fixture
def client():
    from src.backend.main import app

    return TestClient(app)


def _seed_types(store: InMemoryDocumentStore) -> None:
    for item in (
        {"id": "canvas", "label": "Canvas", "key_prefix": "canvas:", "schema": "content_document"},
        {"id": "canvas_data", "label": "Canvas Data", "key_prefix": "canvas_data:"},
        {"id": "canvas_status", "label": "Canvas Status", "key_prefix": "canvas_status:"},
        {"id": "preferences", "label": "Preferences", "key": "preferences"},
        {"id": "kanban_card", "label": "Kanban", "key_prefix": "kanban_card:"},
        {"id": "artifact", "label": "Artifact", "key_prefix": "artifact:"},
    ):
        store.save_document(
            PROGENITOR_STATE_PREFIX, f"{DOCUMENT_TYPE_KEY_PREFIX}{item['id']}", item
        )


def _canvas(store: InMemoryDocumentStore, name: str = "demo") -> None:
    manifest = json.dumps(
        {
            "name": name,
            "title": "Demo",
            "description": "A demo",
            "entry": "index.mjs",
            "libraries": [],
        }
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        canvas_file_key(name, "manifest.json"),
        {"content": manifest, "updated_at": "2026-01-01T00:00:00Z"},
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        canvas_file_key(name, "index.mjs"),
        {"content": "export default () => {}", "updated_at": "2026-01-01T00:00:01Z"},
    )


class _Capture:
    def __init__(self, client_id: str | None = None):
        from src.backend.events import stream as events_stream

        self.events_stream = events_stream
        self.queue: asyncio.Queue = asyncio.Queue()
        self.client_id = client_id

    def __enter__(self):
        self.events_stream._ui_stream_subscribers[self.queue] = None
        if self.client_id is not None:
            self.events_stream._ui_stream_clients[self.queue] = self.client_id
        return self

    def __exit__(self, *_exc):
        self.events_stream._ui_stream_subscribers.pop(self.queue, None)
        self.events_stream._ui_stream_clients.pop(self.queue, None)
        return False

    def payloads(self):
        return [item["envelope"].payload for item in list(self.queue._queue)]


def test_in_memory_canvas_revisions_capture_changed_writes_and_delete(document_store):
    key = canvas_file_key("demo", "index.mjs")
    document_store.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "one"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "one"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "two"})
    document_store.delete_document(PROGENITOR_STATE_PREFIX, key)

    revisions = document_store.list_canvas_revisions("demo")
    assert [(item.op, item.content) for item in revisions] == [
        ("delete", None),
        ("write", "two"),
        ("write", "one"),
    ]


def test_routes_reject_unknown_canvas_unknown_type_and_bad_data_key(client, document_store):
    _seed_types(document_store)
    assert (
        client.get("/api/ui/canvases/missing/files", params={"client_id": "tab"}).status_code == 404
    )
    _canvas(document_store)
    assert (
        client.post(
            "/api/ui/canvases/demo/documents",
            json={"client_id": "tab", "type": "missing"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/ui/canvases/demo/data",
            json={"client_id": "tab", "op": "get", "key": "bad/key"},
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/ui/canvases/demo/subscription",
            json={"client_id": "tab", "events": ["missing.updated"]},
        ).status_code
        == 404
    )


def test_documents_page_prefix_and_exact_types(client, document_store):
    _seed_types(document_store)
    _canvas(document_store)
    document_store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:b", {"id": "b"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:a", {"id": "a"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, "preferences", {"theme": "dark"})
    with _Capture("tab") as capture:
        response = client.post(
            "/api/ui/canvases/demo/documents",
            json={"client_id": "tab", "type": "kanban_card", "limit": 1},
        )
        assert_acknowledged(response, ["canvas.documents.loaded"])
        payload = capture.payloads()[-1]
        assert [item["key"] for item in payload["items"]] == ["kanban_card:a"]
        assert payload["cursor"] == "kanban_card:a"
        response = client.post(
            "/api/ui/canvases/demo/documents",
            json={"client_id": "tab", "type": "kanban_card", "limit": 1, "cursor": "kanban_card:a"},
        )
        assert_acknowledged(response, ["canvas.documents.loaded"])
        payload = capture.payloads()[-1]
        assert [item["key"] for item in payload["items"]] == ["kanban_card:b"]
        assert payload["cursor"] is None
        response = client.post(
            "/api/ui/canvases/demo/documents",
            json={"client_id": "tab", "type": "preferences"},
        )
        assert_acknowledged(response, ["canvas.documents.loaded"])
        assert capture.payloads()[-1]["items"] == [
            {"key": "preferences", "id": "preferences", "document": {"theme": "dark"}}
        ]


def test_documents_id_prefix_narrows_a_collection_to_that_type(client, document_store):
    _seed_types(document_store)
    _canvas(document_store)
    for key in ("artifact:sales/q3.csv", "artifact:sales/q4.csv", "artifact:ops/load.csv"):
        document_store.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "a,b\n1,2"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, "artifactx:sales/q1.csv", {})
    with _Capture("tab") as capture:
        response = client.post(
            "/api/ui/canvases/demo/documents",
            json={"client_id": "tab", "type": "artifact", "prefix": "sales/"},
        )
        assert_acknowledged(response, ["canvas.documents.loaded"])
        payload = capture.payloads()[-1]
    assert [item["id"] for item in payload["items"]] == ["sales/q3.csv", "sales/q4.csv"]
    assert payload["cursor"] is None


def test_canvas_data_isolated_by_canvas(client, document_store):
    _seed_types(document_store)
    _canvas(document_store, "demo")
    _canvas(document_store, "other")
    for name, value in (("demo", 1), ("other", 2)):
        response = client.post(
            f"/api/ui/canvases/{name}/data",
            json={"client_id": "tab", "op": "set", "key": "score", "value": value},
        )
        assert_acknowledged(response, ["canvas.data.loaded"])
    with _Capture("tab") as capture:
        client.post(
            "/api/ui/canvases/demo/data",
            json={"client_id": "tab", "op": "get", "key": "score"},
        )
        assert capture.payloads()[-1]["value"] == 1


def test_snapshot_reports_manifest_errors_and_status(document_store):
    document_store.save_document(
        PROGENITOR_STATE_PREFIX,
        canvas_file_key("broken", "index.mjs"),
        {"content": "export default () => {}", "updated_at": "2026-01-01T00:00:00Z"},
    )
    document_store.save_document(
        PROGENITOR_STATE_PREFIX,
        "canvas_status:broken",
        {"state": "error", "revision": "abc", "errors": [{"message": "bad"}], "reported_at": "now"},
    )
    with _Capture() as capture:
        canvases_routes.broadcast_canvases_snapshot()
        item = capture.payloads()[-1]["items"][0]
    assert item["manifest_error"] == "manifest.json is missing."
    assert item["status"]["state"] == "error"


@pytest.mark.asyncio
async def test_canvas_interest_matching_and_disconnect_cleanup(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "src.backend.events.stream.broadcast_ui_event",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    canvas_events.set_interest("one", "demo", ["kanban_card.updated"])
    canvas_events.set_interest("two", "demo", ["preferences.*"])
    typed = TypedDocumentEvent(
        event=DocumentEvent(
            seq=1,
            txid=1,
            prefix=PROGENITOR_STATE_PREFIX,
            key="kanban_card:one",
            op="updated",
            before={"title": "old"},
            after={"title": "new"},
            origin=None,
            created_at=datetime.now(UTC),
        ),
        document_type=DocumentType(id="kanban_card", label="Kanban", key_prefix="kanban_card:"),
        output={
            "event_id": 1,
            "document_type": "kanban_card",
            "key": "kanban_card:one",
            "id": "one",
            "op": "updated",
            "timestamp": "now",
            "changed_fields": ["title"],
        },
    )
    await canvas_events.document_event(typed)
    assert [kwargs["client_id"] for _args, kwargs in sent] == ["one"]
    canvas_events.disconnected("one")
    assert ("one", "demo") not in canvas_events.interests
