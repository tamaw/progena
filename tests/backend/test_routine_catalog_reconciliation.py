"""Exercise NOTIFY -> StateReader -> catalog reconciliation without a database."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.backend.agents.reconciliation as agents_reconciliation
import src.backend.agents.store as agents_store
import src.backend.events.stream as events_stream
import src.backend.monitoring.status as monitoring_status
import src.backend.storage.documents as storage_documents
from src.backend.agents.routes import create_agent_profile_router
from src.backend.monitoring.state import StateReader
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    CheckpointThreadSnapshot,
    InMemoryDocumentStore,
    StoreChangeListener,
)


class Catalog:
    def __init__(self, documents):
        self.documents = documents
        self.rows = {
            "gone": {"id": "gone", "kind": "routine"},
            "other": {"id": "other", "kind": "routine"},
            "object": {"id": "object", "kind": "object"},
        }
        for row in self.rows.values():
            row["name"] = row["id"]
        self.deleted = []

    def list_agents(self):
        return [
            row
            for row in self.rows.values()
            if row["kind"] != "routine" or self.get_routine_document(row["id"]) is not None
        ]

    def list_routine_catalog_ids(self):
        return sorted(row["id"] for row in self.rows.values() if row["kind"] == "routine")

    def list_object_agents(self):
        return []

    def list_routine_document_ids(self):
        return [
            key.removeprefix("routine:")
            for key, _ in self.documents.list_documents(PROGENA_STATE_PREFIX, "routine:")
        ]

    def get_routine_document(self, agent_id):
        document = self.documents.get_document(PROGENA_STATE_PREFIX, f"routine:{agent_id}")
        return document.payload if document else None

    def delete_routine_catalog_entry(self, agent_id):
        row = self.rows.get(agent_id)
        if not row or row["kind"] != "routine":
            return False
        self.deleted.append(agent_id)
        del self.rows[agent_id]
        return True

    def ensure_routine_agent(self, agent_id, graph):
        created = agent_id not in self.rows
        self.rows[agent_id] = {"id": agent_id, "kind": "routine"}
        return created

    def get_agent(self, agent_id):
        row = self.rows.get(agent_id)
        graph = self.get_routine_document(agent_id)
        return {**row, "graph": graph} if row and graph else None


def test_deleted_event_is_accepted_by_canonical_transport(monkeypatch):
    queue = asyncio.Queue()
    monkeypatch.setattr(events_stream, "_ui_stream_subscribers", {queue: None})
    monkeypatch.setattr(events_stream, "_ui_event_replay", [])
    envelope = events_stream.broadcast_ui_event(
        "agent.deleted", {"agent_id": "gone"}, request_id="delete-request"
    )
    assert envelope.type == "agent.deleted"
    assert envelope.payload == {"agent_id": "gone", "request_id": "delete-request"}
    assert events_stream._ui_event_replay == [envelope]
    assert queue.get_nowait() == {"type": "ui.event", "envelope": envelope}


@pytest.fixture
def harness(monkeypatch):
    documents = InMemoryDocumentStore()
    graph = {"id": "gone", "entry": None, "nodes": [], "edges": []}
    documents.save_document(PROGENA_STATE_PREFIX, "routine:gone", graph)
    documents.save_document(PROGENA_STATE_PREFIX, "routine:other", {**graph, "id": "other"})
    documents.upsert_routine_revision("gone", "last", graph, captured_at="2026-10-03")
    documents.upsert_checkpoint_thread_snapshot(
        CheckpointThreadSnapshot(
            thread_id="routine:gone:last:run",
            checkpoint_id="cp",
            checkpoint_ts="2026-10-03",
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    catalog = Catalog(documents)
    events = []
    roster = AsyncMock(wraps=monitoring_status._broadcast_agent_profiles_safe)
    monkeypatch.setattr(agents_store, "get_agent_store", lambda: catalog)
    monkeypatch.setattr(storage_documents, "get_default_document_store", lambda: documents)
    monkeypatch.setattr(
        events_stream,
        "broadcast_ui_event",
        lambda kind, payload, **kwargs: events.append((kind, payload)),
    )
    monkeypatch.setattr(monitoring_status, "_broadcast_agent_profiles_safe", roster)
    reader = StateReader(document_store=documents)
    # Routine-only changes do not affect the coarse state diff.
    snapshot = reader.get_snapshot()
    reader._last_snapshot = snapshot
    monkeypatch.setattr(reader, "get_snapshot", lambda: snapshot)
    reader.add_watcher(agents_reconciliation._queue_routine_agent_reconciliation)
    return documents, catalog, events, roster, reader


async def drain_reconciliation():
    tasks = list(agents_reconciliation._active_routine_agent_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def test_delete_notification_removes_only_catalog_and_keeps_history(harness, monkeypatch):
    documents, catalog, events, roster, reader = harness
    documents.delete_document(PROGENA_STATE_PREFIX, "routine:gone")
    payload = json.dumps(
        {"prefix": PROGENA_STATE_PREFIX, "key": "routine:gone", "action": "deleted"}
    )

    class Connection:
        async def execute(self, query):
            assert "LISTEN" in str(query)

        async def notifies(self):
            yield type("Notify", (), {"payload": payload})()

        async def close(self):
            pass

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", AsyncMock(return_value=Connection()))
    listener = StoreChangeListener("postgresql://example", on_change=reader._on_store_change)
    await listener._listen_once()
    await drain_reconciliation()

    assert catalog.deleted == ["gone"]
    assert set(catalog.rows) == {"other", "object"}
    assert events[0] == ("agent.deleted", {"agent_id": "gone"})
    assert events[1][0] == "agents.snapshot"
    assert {agent["id"] for agent in events[1][1]["agents"]} == {"other", "object"}
    roster.assert_awaited_once()
    assert documents.get_document(PROGENA_STATE_PREFIX, "routine:gone") is None
    assert documents.get_routine_revision("gone", "last")["id"] == "gone"
    assert [r.revision_id for r in documents.list_routine_revisions("gone")] == ["last"]
    runs, _ = documents.list_routine_runs("gone")
    assert [run.thread_id for run in runs] == ["routine:gone:last:run"]

    reader._on_store_change(payload)
    await drain_reconciliation()
    assert catalog.deleted == ["gone"]
    assert len(events) == 2
    roster.assert_awaited_once()


async def test_reconnect_removes_missing_document_with_unchanged_snapshot(harness):
    documents, catalog, events, roster, reader = harness
    documents.delete_document(PROGENA_STATE_PREFIX, "routine:gone")
    assert "gone" not in {row["id"] for row in catalog.list_agents()}
    assert "gone" in catalog.list_routine_catalog_ids()
    reader._emit_if_changed()  # listener reconnect callback
    await drain_reconciliation()
    assert catalog.deleted == ["gone"]
    assert ("agent.deleted", {"agent_id": "gone"}) in events
    assert "other" in catalog.rows and "object" in catalog.rows
    roster.assert_awaited_once()


async def test_delayed_delete_notification_uses_current_live_document(harness):
    documents, catalog, events, roster, reader = harness
    documents.delete_document(PROGENA_STATE_PREFIX, "routine:gone")
    documents.save_document(
        PROGENA_STATE_PREFIX, "routine:gone", {"entry": None, "nodes": [], "edges": []}
    )
    reader._on_store_change(json.dumps({"key": "routine:gone", "action": "deleted"}))
    await drain_reconciliation()
    assert catalog.deleted == []
    assert events[0][0] == "agent.graph.updated"
    roster.assert_not_awaited()


async def test_unrelated_notification_does_not_remove_missing_routine(harness):
    documents, catalog, events, roster, reader = harness
    documents.delete_document(PROGENA_STATE_PREFIX, "routine:gone")
    reader._on_store_change(json.dumps({"key": "workflow:other", "action": "deleted"}))
    await drain_reconciliation()
    assert catalog.deleted == []
    assert events == []
    roster.assert_not_awaited()


def test_deleted_routine_history_routes_remain_available(harness, monkeypatch):
    documents, catalog, events, _roster, _reader = harness
    documents.delete_document(PROGENA_STATE_PREFIX, "routine:gone")
    catalog.delete_routine_catalog_entry("gone")
    app = FastAPI()
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: catalog,
            get_document_store=lambda: documents,
            broadcast_ui_event=lambda kind, payload, **kwargs: events.append((kind, payload)),
        ),
        prefix="/api/ui",
    )
    client = TestClient(app, headers={"X-Request-ID": "deleted-routine-history"})
    assert client.get("/api/ui/agent-profiles/gone").status_code == 404
    for path, event_type in (
        ("revisions", "agent.routine.revisions.loaded"),
        ("revisions/last", "agent.routine.revision.loaded"),
        ("runs", "agent.routine.runs.loaded"),
    ):
        response = client.get(f"/api/ui/agent-profiles/gone/{path}")
        assert response.status_code == 202
        assert events[-1][0] == event_type
    assert events[0][1]["revisions"][0]["revision_id"] == "last"
    assert events[1][1]["graph"]["id"] == "gone"
    assert events[2][1]["runs"][0]["thread_id"] == "routine:gone:last:run"

    # Saved checkpoint state remains queryable without the live catalog row.
    # Replace the saver connection so this exercises the route without Postgres.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    monkeypatch.setattr(documents, "database_url", "postgresql://example", raising=False)
    saver = AsyncMock()
    saver.aget_tuple.return_value = SimpleNamespace(
        checkpoint={
            "channel_values": {"run_meta": {"status": "completed"}, "data": {"result": "done"}}
        },
        pending_writes=[],
    )
    connection = AsyncMock()
    connection.__aenter__.return_value = saver

    def open_saver(_url):
        return connection

    monkeypatch.setattr(AsyncPostgresSaver, "from_conn_string", open_saver)
    response = client.get(
        "/api/ui/agent-profiles/gone/run-state",
        params={"thread_id": "routine:gone:last:run"},
    )
    assert response.status_code == 202
    assert events[-1][0] == "agent.routine.run_state.loaded"
    assert events[-1][1]["found"] is True
    assert events[-1][1]["data"] == {"result": "done"}
    saver.aget_tuple.assert_awaited_once_with(
        {"configurable": {"thread_id": "routine:gone:last:run"}}
    )


def test_catalog_hides_deleted_document_before_notification_reconciles(harness, monkeypatch):
    documents, catalog, events, _roster, _reader = harness
    documents.delete_document(PROGENA_STATE_PREFIX, "routine:gone")
    # Older store adapters can return surviving metadata with graph=None.
    original_get_agent = catalog.get_agent
    monkeypatch.setattr(
        catalog,
        "get_agent",
        lambda agent_id: (
            {**catalog.rows[agent_id], "graph": None}
            if agent_id == "gone"
            else original_get_agent(agent_id)
        ),
    )
    app = FastAPI()
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: catalog,
            get_document_store=lambda: documents,
            broadcast_ui_event=lambda kind, payload, **kwargs: events.append((kind, payload)),
        ),
        prefix="/api/ui",
    )
    client = TestClient(app, headers={"X-Request-ID": "pending-deletion"})
    assert client.get("/api/ui/agent-profiles/gone").status_code == 404
    assert (
        client.patch(
            "/api/ui/agent-profiles/gone/graph", json={"nodes": [], "edges": []}
        ).status_code
        == 404
    )
    assert client.post("/api/ui/agent-profiles/gone/run", json={}).status_code == 404
    assert client.get("/api/ui/agent-profiles").status_code == 202
    assert events[-1][0] == "agents.snapshot"
    assert {agent["id"] for agent in events[-1][1]["agents"]} == {"other", "object"}
    assert "gone" in catalog.rows  # reads never mutate the catalog
    assert catalog.deleted == []
    assert documents.get_document(PROGENA_STATE_PREFIX, "routine:gone") is None
