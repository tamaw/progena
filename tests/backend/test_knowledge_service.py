"""Tests for the backend knowledge service (document load/save/snapshot)."""

from __future__ import annotations

import pytest

from app.backend import knowledge_service as svc
from app.backend.document_store import InMemoryDocumentStore, set_default_document_store


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


def test_snapshot_merges_default_stats_without_database() -> None:
    # The in-memory store has no Postgres DSN, so SQL stats are skipped and each
    # base reports zero documents / no collections.
    svc.save_bases([{"id": "k1", "name": "Alpha", "sources": []}])
    snapshot = svc.snapshot_bases()
    assert snapshot[0]["documents"] == 0
    assert snapshot[0]["collections"] == []
