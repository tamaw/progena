"""Instruction routes over the in-memory document store."""

from __future__ import annotations

from typing import Any

import pytest
from ack_helpers import assert_acknowledged
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.backend.events.stream as events_stream
import app.backend.instructions.routes as routes
from app.backend.events.documents import seed_document_types
from app.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)

GENESIS = "---\ntitle: System\ndescription: Main prompt\n---\nYou are Progenitor.\n"
DISCOVERY = "---\ntitle: Discovery\ndescription: Analyse folders\n---\nAnalyse {folders}.\n"
CARD = "---\ntitle: Card\ninputs:\n  card: kanban_card\n---\nSummarise {card.title}.\n"
EXTERNAL = "---\ntitle: External\ndescription: Configurable prompt\n---\nWork carefully.\n"


@pytest.fixture
def harness(monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    seed_document_types(store)
    for path, content in (
        ("progenitor/genesis.md", GENESIS),
        ("progenitor/discovery.md", DISCOVERY),
        ("external_agent/think.md", EXTERNAL),
        ("workflows/card.md", CARD),
    ):
        store.save_document(PROGENITOR_STATE_PREFIX, f"instruction:{path}", {"content": content})
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        events_stream,
        "broadcast_ui_event",
        lambda event_type, payload, **_kw: events.append((event_type, payload)),
    )
    app = FastAPI()
    app.include_router(routes.router)
    yield TestClient(app, headers={"X-Request-ID": "test-request"}), store, events
    set_default_document_store(InMemoryDocumentStore())


def _last(events, event_type):
    return next(payload for kind, payload in reversed(events) if kind == event_type)


def _stored(store, path):
    return store.get_document(PROGENITOR_STATE_PREFIX, f"instruction:{path}").payload["content"]


def test_list_reports_front_matter_and_editable_instructions(harness):
    client, _, events = harness
    assert_acknowledged(client.get("/api/ui/instructions"), ["instructions.snapshot"])
    listed = {item["path"]: item for item in _last(events, "instructions.snapshot")["instructions"]}
    assert listed["progenitor/discovery.md"]["title"] == "Discovery"
    assert listed["progenitor/discovery.md"]["placeholders"] == ["folders"]
    assert listed["workflows/card.md"]["editable"] is True
    assert listed["progenitor/genesis.md"]["editable"] is True
    assert listed["external_agent/think.md"]["editable"] is True
    assert listed["external_agent/think.md"]["group"] == "external_agent"


def test_read_returns_body_without_front_matter(harness):
    client, _, events = harness
    assert_acknowledged(
        client.get("/api/ui/instructions/content", params={"path": "progenitor/genesis.md"}),
        ["instruction.loaded"],
    )
    loaded = _last(events, "instruction.loaded")
    assert loaded["content"] == GENESIS
    assert loaded["body"] == "You are Progenitor.\n"
    assert client.get("/api/ui/instructions/content", params={"path": "x/y.md"}).status_code == 404


def test_edit_saves_a_runtime_instruction(harness):
    client, store, events = harness
    edited = GENESIS.replace("You are", "You remain").replace("title: System", "title: Updated")
    response = client.put(
        "/api/ui/instructions/content", json={"path": "progenitor/genesis.md", "content": edited}
    )
    assert_acknowledged(response, ["instruction.loaded", "instructions.snapshot"])
    assert _stored(store, "progenitor/genesis.md") == edited
    listed = {item["path"]: item for item in _last(events, "instructions.snapshot")["instructions"]}
    assert listed["progenitor/genesis.md"]["title"] == "Updated"


@pytest.mark.parametrize(
    ("path", "content", "status"),
    [
        ("progenitor/genesis.md", "---\nunknown: 1\n---\nYou are Progenitor.\n", 400),
        ("progenitor/discovery.md", DISCOVERY.replace("{folders}", "the folders"), 400),
        ("progenitor/discovery.md", DISCOVERY.replace("{folders}", "{dirs}"), 400),
        ("progenitor/missing.md", GENESIS, 404),
    ],
    ids=["unknown-key", "placeholder-dropped", "placeholder-renamed", "missing"],
)
def test_rejected_edits_leave_the_instruction_unchanged(harness, path, content, status):
    client, store, _ = harness
    before = {p: _stored(store, p) for p in ("progenitor/genesis.md", "progenitor/discovery.md")}
    response = client.put("/api/ui/instructions/content", json={"path": path, "content": content})
    assert response.status_code == status
    assert {p: _stored(store, p) for p in before} == before


def test_revisions_mark_the_live_one_and_restore_brings_back_old_content(harness):
    client, store, events = harness
    edited = GENESIS.replace("You are", "You remain").replace("title: System", "title: Updated")
    client.put(
        "/api/ui/instructions/content", json={"path": "progenitor/genesis.md", "content": edited}
    )

    assert_acknowledged(
        client.get("/api/ui/instructions/revisions", params={"path": "progenitor/genesis.md"}),
        ["instruction.revisions.loaded"],
    )
    revisions = _last(events, "instruction.revisions.loaded")["revisions"]
    assert len(revisions) == 2
    original = next(rev for rev in revisions if not rev["current"])

    client.get(
        f"/api/ui/instructions/revisions/{original['revision_id']}",
        params={"path": "progenitor/genesis.md"},
    )
    assert _last(events, "instruction.revision.loaded")["content"] == GENESIS

    assert_acknowledged(
        client.post(
            f"/api/ui/instructions/revisions/{original['revision_id']}/restore",
            params={"path": "progenitor/genesis.md"},
        ),
        ["instruction.loaded", "instructions.snapshot"],
    )
    assert _stored(store, "progenitor/genesis.md") == GENESIS
    listed = {item["path"]: item for item in _last(events, "instructions.snapshot")["instructions"]}
    assert listed["progenitor/genesis.md"]["title"] == "System"


def test_unknown_revision_is_404(harness):
    client, _, _ = harness
    response = client.get(
        "/api/ui/instructions/revisions/nope", params={"path": "progenitor/genesis.md"}
    )
    assert response.status_code == 404


def test_workflow_instruction_may_change_its_placeholders(harness):
    client, store, _ = harness
    edited = CARD.replace("{card.title}", "{card.title} for {audience}")
    response = client.put(
        "/api/ui/instructions/content", json={"path": "workflows/card.md", "content": edited}
    )
    assert response.status_code == 202
    assert _stored(store, "workflows/card.md") == edited
