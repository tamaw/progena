"""Knowledge page routes over the in-memory document store."""

from __future__ import annotations

from typing import Any

import pytest
from ack_helpers import assert_acknowledged
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.backend.knowledge_routes as routes
from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)

PAGE_A = "---\ntype: Concept\ntitle: Retry policy\ndescription: How retries work\n---\nRetry three times.\n"


@pytest.fixture
def harness(monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    store.save_document(
        PROGENITOR_STATE_PREFIX, "knowledge", {"bases": [{"id": "eng", "name": "Engineering"}]}
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX, "knowledge:eng/concepts/retry.md", {"content": PAGE_A}
    )
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/log.md", {"content": "## log\n"})
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        routes,
        "broadcast_ui_event",
        lambda event_type, payload, **_kw: events.append((event_type, payload)),
    )
    monkeypatch.setattr(
        routes,
        "broadcast_knowledge_snapshot",
        lambda bases=None, **_kw: events.append(("knowledge.snapshot", {"bases": bases})),
    )
    monkeypatch.setattr(routes, "snapshot_bases", routes.load_bases)
    app = FastAPI()
    app.include_router(routes.router)
    yield TestClient(app, headers={"X-Request-ID": "test-request"}), store, events
    set_default_document_store(InMemoryDocumentStore())


def _last(events, event_type):
    return next(payload for kind, payload in reversed(events) if kind == event_type)


def test_pages_snapshot_lists_concepts_and_reserved_files(harness):
    client, _, events = harness
    assert_acknowledged(client.get("/api/ui/knowledge/eng/pages"), ["knowledge.pages.snapshot"])
    payload = _last(events, "knowledge.pages.snapshot")
    assert payload["base_id"] == "eng"
    assert [(p["path"], p["kind"]) for p in payload["pages"]] == [
        ("concepts/retry.md", "concept"),
        ("log.md", "log"),
    ]
    assert payload["pages"][0]["title"] == "Retry policy"


def test_unknown_base_is_404(harness):
    client, _, _ = harness
    assert client.get("/api/ui/knowledge/nope/pages").status_code == 404
    assert client.get("/api/ui/knowledge/nope/page", params={"path": "a.md"}).status_code == 404


def test_read_page_and_generated_index(harness):
    client, _, events = harness
    assert_acknowledged(
        client.get("/api/ui/knowledge/eng/page", params={"path": "concepts/retry.md"}),
        ["knowledge.page.loaded"],
    )
    assert _last(events, "knowledge.page.loaded")["content"] == PAGE_A
    client.get("/api/ui/knowledge/eng/page", params={"path": "index.md"})
    assert "- [concepts/](concepts/index.md)" in _last(events, "knowledge.page.loaded")["content"]


def test_delete_soft_deletes_concepts_only(harness):
    client, store, _ = harness
    assert client.delete("/api/ui/knowledge/eng/page", params={"path": "log.md"}).status_code == 400
    response = client.delete("/api/ui/knowledge/eng/page", params={"path": "concepts/retry.md"})
    assert_acknowledged(response, ["knowledge.pages.snapshot", "knowledge.snapshot"])
    payload = store.get_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/concepts/retry.md").payload
    assert payload["deleted_at"] and payload["content"] == PAGE_A
    assert (
        client.get("/api/ui/knowledge/eng/page", params={"path": "concepts/retry.md"}).status_code
        == 404
    )


def test_rename_moves_page_and_rejects_existing_target(harness):
    client, store, _ = harness
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/other.md", {"content": PAGE_A})
    body = {"path": "concepts/retry.md", "new_path": "other.md"}
    assert client.post("/api/ui/knowledge/eng/page/rename", json=body).status_code == 409
    body["new_path"] = "concepts/retries.md"
    assert_acknowledged(
        client.post("/api/ui/knowledge/eng/page/rename", json=body), ["knowledge.pages.snapshot"]
    )
    assert (
        store.get_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/concepts/retries.md").payload[
            "content"
        ]
        == PAGE_A
    )
    assert store.get_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/concepts/retry.md").payload[
        "deleted_at"
    ]


def test_revisions_list_read_and_restore(harness):
    client, store, events = harness
    store.save_document(
        PROGENITOR_STATE_PREFIX, "knowledge:eng/concepts/retry.md", {"content": PAGE_A + "More.\n"}
    )
    params = {"path": "concepts/retry.md"}
    assert_acknowledged(
        client.get("/api/ui/knowledge/eng/page/revisions", params=params),
        ["knowledge.page.revisions.loaded"],
    )
    revisions = _last(events, "knowledge.page.revisions.loaded")["revisions"]
    assert len(revisions) == 2
    original = next(
        r["revision_id"]
        for r in revisions
        if store.get_knowledge_page_revision("eng", "concepts/retry.md", r["revision_id"]) == PAGE_A
    )
    client.get(f"/api/ui/knowledge/eng/page/revisions/{original}", params=params)
    assert _last(events, "knowledge.page.revision.loaded")["content"] == PAGE_A
    response = client.post(
        f"/api/ui/knowledge/eng/page/revisions/{original}/restore", params=params
    )
    assert_acknowledged(response, ["knowledge.page.loaded", "knowledge.pages.snapshot"])
    assert (
        store.get_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/concepts/retry.md").payload[
            "content"
        ]
        == PAGE_A
    )
    assert (
        client.post(
            "/api/ui/knowledge/eng/page/revisions/missing/restore", params=params
        ).status_code
        == 404
    )


def test_new_base_id_avoids_orphaned_pages(harness, monkeypatch):
    client, store, _ = harness
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:research/a.md", {"content": PAGE_A})
    monkeypatch.setattr(routes, "_database_url", lambda: None)
    monkeypatch.setattr(routes.psycopg, "connect", lambda _url: _NullConnection())
    response = client.put(
        "/api/ui/knowledge",
        json={"bases": [{"id": "eng", "name": "Engineering"}, {"name": "Research"}]},
    )
    assert response.status_code == 202
    assert [b["id"] for b in routes.load_bases()] == ["eng", "research-2"]


class _NullConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False
