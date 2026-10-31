"""Tests for the backend knowledge service (document load/save/snapshot)."""

from __future__ import annotations

import pytest

from src.backend.knowledge import service as svc
from src.backend.storage.documents import InMemoryDocumentStore, set_default_document_store


@pytest.fixture(autouse=True)
def _in_memory_store():
    set_default_document_store(InMemoryDocumentStore())
    yield
    set_default_document_store(InMemoryDocumentStore())


def test_load_defaults_to_empty() -> None:
    assert svc.load_bases() == []


def test_save_then_load_roundtrip() -> None:
    svc.save_bases([{"id": "k1", "name": "Alpha", "sources": []}])
    bases = svc.load_bases()
    assert len(bases) == 1
    assert bases[0]["id"] == "k1"
    assert bases[0]["name"] == "Alpha"


def test_snapshot_merges_index_fields_without_database() -> None:
    # The in-memory store has no Postgres DSN, so index stats default to
    # "indexing" / zero, and page_count is zero because there are no pages.
    svc.save_bases([{"id": "k1", "name": "Alpha", "sources": []}])
    snapshot = svc.snapshot_bases()
    assert snapshot[0]["page_count"] == 0
    assert snapshot[0]["index_status"] == "unknown"
    assert snapshot[0]["index_pages"] == 0
    assert snapshot[0]["indexed_pages"] == 0
    assert snapshot[0]["index_error"] == ""
