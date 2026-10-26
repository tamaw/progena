"""Global search route tests over the in-memory document store."""

from __future__ import annotations

from typing import Any

import pytest
from ack_helpers import assert_acknowledged
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.backend.events.stream as events_stream
import src.backend.search.routes as routes
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)


@pytest.fixture
def harness(monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        events_stream,
        "broadcast_ui_event",
        lambda event_type, payload, **_kw: events.append((event_type, payload)),
    )
    # Mock state reader for tools/schedules providers
    monkeypatch.setattr(
        routes,
        "get_state_reader",
        lambda: _FakeStateReader(store),
    )
    app = FastAPI()
    app.include_router(routes.router)
    yield TestClient(app, headers={"X-Operation-Id": "test-request"}), store, events
    set_default_document_store(InMemoryDocumentStore())


class _FakeStateReader:
    def __init__(self, store):
        self._store = store

    def get_mcp_config(self):
        doc = self._store.get_document(PROGENA_STATE_PREFIX, "mcp", {})
        return doc.payload if doc is not None else {}

    def get_schedule(self):
        doc = self._store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
        data = doc.payload if doc is not None else {}
        if not isinstance(data, dict):
            return []
        from src.backend.monitoring.state import ScheduleItem

        return [
            ScheduleItem.from_dict({**item, "id": item.get("id") or entry_id})
            for entry_id, item in data.items()
            if isinstance(item, dict)
        ]


def _last(events, event_type):
    return next(payload for kind, payload in reversed(events) if kind == event_type)


class TestSearchRoute:
    def test_search_returns_202_and_event(self, harness):
        client, _, events = harness
        assert_acknowledged(
            client.get("/api/ui/search?q=hello"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        assert payload["query"] == "hello"
        assert "sections" in payload

    def test_search_rejects_short_query(self, harness):
        client, _, _ = harness
        response = client.get("/api/ui/search?q=ab")
        assert response.status_code == 422

    def test_search_includes_text_kind_results(self, harness):
        client, store, events = harness
        store.save_document(
            PROGENA_STATE_PREFIX,
            "inbox_message:msg-1",
            {"title": "Hello world", "body": "body text"},
        )
        assert_acknowledged(
            client.get("/api/ui/search?q=hello"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        section = next((s for s in payload["sections"] if s["label"] == "Inbox"), None)
        assert section is not None
        assert len(section["results"]) == 1
        assert section["results"][0]["title"] == "Hello world"
        assert section["results"][0]["to"] == "/inbox/msg-1"

    def test_sections_in_fixed_order(self, harness):
        client, store, events = harness
        store.save_document(
            PROGENA_STATE_PREFIX, "inbox_message:m1", {"title": "Alpha", "body": "b"}
        )
        store.save_document(
            PROGENA_STATE_PREFIX, "kanban_card:c1", {"title": "Alpha", "summary": "s"}
        )
        store.save_document(
            PROGENA_STATE_PREFIX, "workflow:w1", {"title": "Alpha", "description": "d"}
        )
        assert_acknowledged(
            client.get("/api/ui/search?q=alpha"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        labels = [s["label"] for s in payload["sections"]]
        assert labels.index("Tasks") < labels.index("Inbox")
        assert labels.index("Inbox") < labels.index("Workflows")

    def test_at_most_five_per_section(self, harness):
        client, store, events = harness
        for i in range(10):
            store.save_document(
                PROGENA_STATE_PREFIX,
                f"inbox_message:m{i}",
                {"title": f"Message {i}", "body": "body"},
            )
        assert_acknowledged(
            client.get("/api/ui/search?q=message"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        inbox_section = next(s for s in payload["sections"] if s["label"] == "Inbox")
        assert len(inbox_section["results"]) <= 5

    def test_failing_provider_isolated(self, harness, monkeypatch):
        client, store, events = harness
        store.save_document(
            PROGENA_STATE_PREFIX, "inbox_message:m1", {"title": "Test title", "body": "b"}
        )
        store.save_document(
            PROGENA_STATE_PREFIX,
            "mcp",
            {"mcpServers": {"jina": {"description": "web search", "url": "http://jina"}}},
        )

        def raise_for_inbox(*args, **kwargs):
            if kwargs.get("key_prefix", "").startswith("inbox"):
                raise RuntimeError("inbox boom")
            return []

        monkeypatch.setattr(store, "search_documents", raise_for_inbox)

        assert_acknowledged(
            client.get("/api/ui/search?q=test"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        sections = {s["label"]: s for s in payload["sections"]}
        assert "Inbox" in sections
        assert "error" in sections["Inbox"]
        # Tools section should still be present without error
        assert "error" not in sections.get("Tools", {})

    def test_tools_provider(self, harness):
        client, store, events = harness
        store.save_document(
            PROGENA_STATE_PREFIX,
            "mcp",
            {"mcpServers": {"jina": {"description": "Web search", "url": "http://jina"}}},
        )
        assert_acknowledged(
            client.get("/api/ui/search?q=jina"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        section = next((s for s in payload["sections"] if s["label"] == "Tools"), None)
        assert section is not None
        assert len(section["results"]) == 1
        assert section["results"][0]["title"] == "jina"
        assert section["results"][0]["to"] == "/tools/mcp%3Ajina"

    def test_schedules_provider(self, harness):
        client, store, events = harness
        store.save_document(
            PROGENA_STATE_PREFIX,
            "schedule",
            {
                "sched_1": {
                    "id": "sched_1",
                    "name": "Daily cleanup",
                    "message": "Run cleanup",
                    "schedule": "0 0 * * *",
                }
            },
        )
        assert_acknowledged(
            client.get("/api/ui/search?q=cleanup"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        section = next((s for s in payload["sections"] if s["label"] == "Schedules"), None)
        assert section is not None
        assert len(section["results"]) == 1
        assert section["results"][0]["title"] == "Daily cleanup"
        assert section["results"][0]["to"] == "/schedules/sched_1"

    def test_agent_links_use_the_manifest_name_id(self, harness):
        # A marketplace agent's directory can differ from its manifest name; the
        # Agents page knows it by the id built from the name.
        client, store, events = harness
        store.save_document(
            PROGENA_STATE_PREFIX,
            "agent:market__plugin/reviewer/SYSTEM.md",
            {"content": "---\nname: Code Reviewer\ndescription: A test agent\n---\n"},
        )
        assert_acknowledged(
            client.get("/api/ui/search?q=agent"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        section = next((s for s in payload["sections"] if s["label"] == "Agents"), None)
        assert section is not None
        assert len(section["results"]) == 1
        assert section["results"][0]["id"] == "object-code-reviewer"
        assert section["results"][0]["to"] == "/agents/object-code-reviewer/details"

    def test_instruction_results_use_front_matter_and_path_links(self, harness):
        client, store, events = harness
        store.save_document(
            PROGENA_STATE_PREFIX,
            "instruction:progena/genesis.md",
            {
                "content": "---\ntitle: Progena system prompt\n"
                "description: Main prompt\n---\nYou are a helpful assistant.\n"
            },
        )
        assert_acknowledged(
            client.get("/api/ui/search?q=assistant"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        section = next((s for s in payload["sections"] if s["label"] == "Instructions"), None)
        assert section is not None
        assert len(section["results"]) == 1
        assert section["results"][0]["title"] == "Progena system prompt"
        assert section["results"][0]["subtitle"] == "Main prompt"
        assert section["results"][0]["to"] == "/instructions/progena/genesis.md"

    def test_empty_sections_omitted(self, harness, monkeypatch):
        client, _, events = harness

        async def mock_knowledge(q, limit=5):
            return {"query": q, "results": []}

        # Mock knowledge provider so it doesn't fail on missing indexer config
        monkeypatch.setattr(routes, "_knowledge_provider", mock_knowledge)
        assert_acknowledged(
            client.get("/api/ui/search?q=nonexistent"),
            ["search.results"],
        )
        payload = _last(events, "search.results")
        # No results and no errors means no sections
        assert payload["sections"] == []
