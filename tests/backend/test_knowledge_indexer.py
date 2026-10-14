"""Backend knowledge indexer against Postgres (isolated schema) with a fake embedder."""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import quote

import psycopg
import pytest

from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.knowledge_indexer import KnowledgeIndexer
from app.progenitor.knowledge.indexer_config import INDEX_STATUS_DOC_KEY

DB_URL = (os.getenv("PROGENITOR_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
pytestmark = pytest.mark.skipif(not DB_URL, reason="requires PROGENITOR_DATABASE_URL")

PROFILES = {"embeddings": {"provider": "azure_openai", "deployment": "text-embedding-3-small"}}


def _page(title: str, body: str) -> dict[str, str]:
    return {"content": f"---\ntype: Concept\ntitle: {title}\n---\n{body}\n"}


class FakeEmbedder:
    def __init__(self, dims: int, calls: list[list[str]], fail: bool = False) -> None:
        self.dims, self.calls, self.fail = dims, calls, fail

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding service down")
        self.calls.append(texts)
        return [[1.0] + [0.0] * (self.dims - 1) for _ in texts]


@pytest.fixture
def harness():
    schema = f"pytest_kidx_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    separator = "&" if "?" in DB_URL else "?"
    url = f"{DB_URL}{separator}options={quote(f'-c search_path={schema},public')}"
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    store.save_document(
        PROGENITOR_STATE_PREFIX, "knowledge", {"bases": [{"id": "eng"}, {"id": "ops"}]}
    )
    store.save_document(PROGENITOR_STATE_PREFIX, "profile", PROFILES)
    store.save_document(
        PROGENITOR_STATE_PREFIX, "indexer", {"profile": "embeddings", "dimensions": 8}
    )
    state: dict[str, Any] = {"events": [], "dispatched": [], "calls": [], "fail": False}
    indexer = KnowledgeIndexer(
        database_url=url,
        embedder_factory=lambda target: FakeEmbedder(
            target.dimensions, state["calls"], state["fail"]
        ),
        publish=lambda event_type, payload: state["events"].append((event_type, payload)),
        dispatch=_collect(state["dispatched"]),
    )
    yield indexer, store, state, url
    set_default_document_store(InMemoryDocumentStore())
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _collect(sink: list):
    async def dispatch(event_type: str, payload: dict[str, Any]) -> None:
        sink.append((event_type, payload))

    return dispatch


def _record(store, base_id: str) -> dict[str, Any] | None:
    doc = store.get_document(PROGENITOR_STATE_PREFIX, INDEX_STATUS_DOC_KEY)
    return doc.payload["bases"].get(base_id) if doc else None


def _chunk_paths(url: str, base_id: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT DISTINCT path FROM knowledge_chunks WHERE base_id = %s", (base_id,)
        ).fetchall()
    return {row[0] for row in rows}


async def test_new_pages_are_indexed_with_lifecycle_events(harness):
    indexer, store, state, url = harness
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/a.md", _page("A", "Alpha."))
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/log.md", {"content": "log"})
    await indexer.reconcile("eng")
    assert [kind for kind, _ in state["events"]] == [
        "knowledge.index.started",
        "knowledge.index.progress",
        "knowledge.index.completed",
    ]
    record = _record(store, "eng")
    assert record["status"] == "indexed" and record["pages"] == 1 and record["chunks"] == 1
    assert state["dispatched"] == [("knowledge.index.completed", {"key": "eng", **record})]
    assert _chunk_paths(url, "eng") == {"a.md"}
    # Nothing changed: no work, no events.
    state["events"].clear()
    await indexer.reconcile("eng")
    assert state["events"] == []


async def test_only_changed_pages_are_reembedded_and_removed_or_deprecated_pages_dropped(harness):
    indexer, store, state, url = harness
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/a.md", _page("A", "Alpha."))
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/b.md", _page("B", "Beta."))
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/c.md", _page("C", "Gamma."))
    await indexer.reconcile("eng")
    state["calls"].clear()
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/a.md", _page("A", "Alpha 2."))
    store.delete_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/b.md")
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "knowledge:eng/c.md",
        {"content": "---\ntype: Concept\ntitle: C\nstatus: deprecated\n---\nGamma.\n"},
    )
    await indexer.reconcile("eng")
    assert len(state["calls"]) == 1 and "Alpha 2." in state["calls"][0][0]
    assert _chunk_paths(url, "eng") == {"a.md"}
    assert _record(store, "eng")["pages"] == 1


async def test_removed_base_loses_chunks_and_status(harness):
    indexer, store, state, url = harness
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:ops/x.md", _page("X", "Ops."))
    await indexer.reconcile_all()
    assert _chunk_paths(url, "ops") == {"x.md"}
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge", {"bases": [{"id": "eng"}]})
    await indexer.reconcile_all()
    assert _chunk_paths(url, "ops") == set()
    assert _record(store, "ops") is None


async def test_embedding_failure_reports_once_and_keeps_existing_chunks(harness):
    indexer, store, state, url = harness
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/a.md", _page("A", "Alpha."))
    await indexer.reconcile("eng")
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/a.md", _page("A", "Alpha 2."))
    state["fail"] = True
    state["events"].clear()
    await indexer.reconcile("eng")
    record = _record(store, "eng")
    assert record["status"] == "failed" and "embedding service down" in record["error"]
    assert record["pending"] == ["a.md"]
    assert state["events"][-1][0] == "knowledge.index.failed"
    assert state["dispatched"][-1][0] == "knowledge.index.failed"
    assert _chunk_paths(url, "eng") == {"a.md"}  # previous version stays searchable


async def test_unconfigured_profile_fails_every_base_without_event_storms(harness):
    indexer, store, state, _ = harness
    store.save_document(PROGENITOR_STATE_PREFIX, "indexer", {"profile": "", "dimensions": 8})
    await indexer.reconcile_all()
    await indexer.reconcile_all()
    assert _record(store, "eng")["error"].startswith('Set "profile" in /config/indexer.json')
    failed = [payload["base_id"] for kind, payload in state["events"]]
    assert sorted(failed) == ["eng", "ops"]


async def test_dimension_change_rebuilds_the_index(harness):
    indexer, store, state, url = harness
    store.save_document(PROGENITOR_STATE_PREFIX, "knowledge:eng/a.md", _page("A", "Alpha."))
    await indexer.reconcile("eng")
    store.save_document(
        PROGENITOR_STATE_PREFIX, "indexer", {"profile": "embeddings", "dimensions": 4}
    )
    state["calls"].clear()
    await indexer.reconcile_all()
    assert len(state["calls"]) == 1
    with psycopg.connect(url) as conn:
        dims = conn.execute(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = to_regclass('knowledge_chunks') AND attname = 'embedding'"
        ).fetchone()
    assert dims[0] == 4
    status = store.get_document(PROGENITOR_STATE_PREFIX, INDEX_STATUS_DOC_KEY).payload
    assert status["embedding"]["dimensions"] == 4
    assert status["bases"]["eng"]["status"] == "indexed"
