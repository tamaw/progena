"""Module route, snapshot, and interest behavior."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from ack_helpers import assert_acknowledged
from fastapi.testclient import TestClient

from app.backend.document_events import TypedDocumentEvent
from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    DocumentEvent,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.module_events import module_events
from app.backend.ui_routes import broadcast_modules_snapshot
from app.progenitor.document_kinds import DOCUMENT_TYPE_KEY_PREFIX
from app.progenitor.document_types import DocumentType
from app.progenitor.modules import module_file_key


@pytest.fixture
def document_store():
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    yield store
    module_events.interests.clear()
    set_default_document_store(InMemoryDocumentStore())


@pytest.fixture
def client():
    from app.backend.main import app

    return TestClient(app)


def _seed_types(store: InMemoryDocumentStore) -> None:
    for item in (
        {"id": "module", "label": "Module", "key_prefix": "module:", "schema": "content_document"},
        {"id": "module_data", "label": "Module Data", "key_prefix": "module_data:"},
        {"id": "module_status", "label": "Module Status", "key_prefix": "module_status:"},
        {"id": "preferences", "label": "Preferences", "key": "preferences"},
        {"id": "kanban_card", "label": "Kanban", "key_prefix": "kanban_card:"},
    ):
        store.save_document(
            PROGENITOR_STATE_PREFIX, f"{DOCUMENT_TYPE_KEY_PREFIX}{item['id']}", item
        )


def _module(store: InMemoryDocumentStore, name: str = "demo") -> None:
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
        module_file_key(name, "manifest.json"),
        {"content": manifest, "updated_at": "2026-01-01T00:00:00Z"},
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        module_file_key(name, "index.mjs"),
        {"content": "export default () => {}", "updated_at": "2026-01-01T00:00:01Z"},
    )


class _Capture:
    def __init__(self, client_id: str | None = None):
        from app.backend import ui_routes

        self.ui_routes = ui_routes
        self.queue: asyncio.Queue = asyncio.Queue()
        self.client_id = client_id

    def __enter__(self):
        self.ui_routes._ui_stream_subscribers[self.queue] = None
        if self.client_id is not None:
            self.ui_routes._ui_stream_clients[self.queue] = self.client_id
        return self

    def __exit__(self, *_exc):
        self.ui_routes._ui_stream_subscribers.pop(self.queue, None)
        self.ui_routes._ui_stream_clients.pop(self.queue, None)
        return False

    def payloads(self):
        return [item["envelope"].payload for item in list(self.queue._queue)]


def test_in_memory_module_revisions_capture_changed_writes_and_delete(document_store):
    key = module_file_key("demo", "index.mjs")
    document_store.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "one"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "one"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "two"})
    document_store.delete_document(PROGENITOR_STATE_PREFIX, key)

    revisions = document_store.list_module_revisions("demo")
    assert [(item.op, item.content) for item in revisions] == [
        ("delete", None),
        ("write", "two"),
        ("write", "one"),
    ]


def test_module_state_at_reconstructs_files_and_deletes_later_files(document_store):
    one = module_file_key("demo", "one.mjs")
    two = module_file_key("demo", "two.mjs")
    document_store.save_document(PROGENITOR_STATE_PREFIX, one, {"content": "one"})
    at_one = datetime.now(UTC)
    document_store.save_document(PROGENITOR_STATE_PREFIX, two, {"content": "two"})
    document_store.delete_document(PROGENITOR_STATE_PREFIX, one)

    assert document_store.module_state_at("demo", at_one) == {"one.mjs": "one"}


def test_routes_reject_unknown_module_unknown_type_and_bad_data_key(client, document_store):
    _seed_types(document_store)
    assert (
        client.get("/api/ui/modules/missing/files", params={"client_id": "tab"}).status_code == 404
    )
    _module(document_store)
    assert (
        client.post(
            "/api/ui/modules/demo/documents",
            json={"client_id": "tab", "type": "missing"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/ui/modules/demo/data",
            json={"client_id": "tab", "op": "get", "key": "bad/key"},
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/ui/modules/demo/subscription",
            json={"client_id": "tab", "events": ["missing.updated"]},
        ).status_code
        == 404
    )


def test_restore_rewrites_prior_files_and_deletes_later_files(client, document_store):
    _module(document_store)
    captured_at = datetime.now(UTC).isoformat()
    document_store.save_document(
        PROGENITOR_STATE_PREFIX,
        module_file_key("demo", "later.mjs"),
        {"content": "export const later = true", "updated_at": "2026-01-01T00:00:02Z"},
    )
    response = client.post(
        "/api/ui/modules/demo/restore",
        json={"client_id": "tab", "captured_at": captured_at},
    )
    assert_acknowledged(response, ["module.restored"])
    assert (
        document_store.get_document(
            PROGENITOR_STATE_PREFIX, module_file_key("demo", "later.mjs"), None
        )
        is None
    )


def test_documents_page_prefix_and_exact_types(client, document_store):
    _seed_types(document_store)
    _module(document_store)
    document_store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:b", {"id": "b"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:a", {"id": "a"})
    document_store.save_document(PROGENITOR_STATE_PREFIX, "preferences", {"theme": "dark"})
    with _Capture("tab") as capture:
        response = client.post(
            "/api/ui/modules/demo/documents",
            json={"client_id": "tab", "type": "kanban_card", "limit": 1},
        )
        assert_acknowledged(response, ["module.documents.loaded"])
        payload = capture.payloads()[-1]
        assert [item["key"] for item in payload["items"]] == ["kanban_card:a"]
        assert payload["cursor"] == "kanban_card:a"
        response = client.post(
            "/api/ui/modules/demo/documents",
            json={"client_id": "tab", "type": "kanban_card", "limit": 1, "cursor": "kanban_card:a"},
        )
        assert_acknowledged(response, ["module.documents.loaded"])
        payload = capture.payloads()[-1]
        assert [item["key"] for item in payload["items"]] == ["kanban_card:b"]
        assert payload["cursor"] is None
        response = client.post(
            "/api/ui/modules/demo/documents",
            json={"client_id": "tab", "type": "preferences"},
        )
        assert_acknowledged(response, ["module.documents.loaded"])
        assert capture.payloads()[-1]["items"] == [
            {"key": "preferences", "id": "preferences", "document": {"theme": "dark"}}
        ]


def test_module_data_isolated_by_module(client, document_store):
    _seed_types(document_store)
    _module(document_store, "demo")
    _module(document_store, "other")
    for name, value in (("demo", 1), ("other", 2)):
        response = client.post(
            f"/api/ui/modules/{name}/data",
            json={"client_id": "tab", "op": "set", "key": "score", "value": value},
        )
        assert_acknowledged(response, ["module.data.loaded"])
    with _Capture("tab") as capture:
        client.post(
            "/api/ui/modules/demo/data",
            json={"client_id": "tab", "op": "get", "key": "score"},
        )
        assert capture.payloads()[-1]["value"] == 1


def test_snapshot_reports_manifest_errors_and_status(document_store):
    document_store.save_document(
        PROGENITOR_STATE_PREFIX,
        module_file_key("broken", "index.mjs"),
        {"content": "export default () => {}", "updated_at": "2026-01-01T00:00:00Z"},
    )
    document_store.save_document(
        PROGENITOR_STATE_PREFIX,
        "module_status:broken",
        {"state": "error", "revision": "abc", "errors": [{"message": "bad"}], "reported_at": "now"},
    )
    with _Capture() as capture:
        broadcast_modules_snapshot()
        item = capture.payloads()[-1]["items"][0]
    assert item["manifest_error"] == "manifest.json is missing."
    assert item["status"]["state"] == "error"


@pytest.mark.asyncio
async def test_module_interest_matching_and_disconnect_cleanup(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.backend.ui_routes.broadcast_ui_event",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    module_events.set_interest("one", "demo", ["kanban_card.updated"])
    module_events.set_interest("two", "demo", ["preferences.*"])
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
    await module_events.document_event(typed)
    assert [kwargs["client_id"] for _args, kwargs in sent] == ["one"]
    module_events.disconnected("one")
    assert ("one", "demo") not in module_events.interests
