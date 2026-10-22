"""Catalog cleanup and detail reads cannot recreate runtime documents."""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from src.backend.agents.store import AgentStore
from src.backend.storage.documents import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore


@pytest.mark.parametrize("rowcount", [0, 1])
def test_delete_routine_catalog_entry_only_deletes_metadata(rowcount):
    store = object.__new__(AgentStore)
    store._document_store = Mock()
    cursor = Mock(rowcount=rowcount)
    connection = Mock()
    connection.cursor.return_value = nullcontext(cursor)
    store._pool = Mock()
    store._pool.connection.return_value = nullcontext(connection)

    assert store.delete_routine_catalog_entry("example") is bool(rowcount)
    assert store._document_store.mock_calls == []


def test_missing_graph_detail_is_read_only():
    store = object.__new__(AgentStore)
    documents = InMemoryDocumentStore()
    store._document_store = documents
    documents.save_document = Mock(wraps=documents.save_document)
    store.ensure_graph = Mock(side_effect=AssertionError("detail created graph"))

    assert store._graph_payload("deleted", {"status": "Ready"}) is None
    documents.save_document.assert_not_called()
    store.ensure_graph.assert_not_called()
    assert documents.list_documents(PROGENITOR_STATE_PREFIX, "routine:") == []


def _store_with_cursor(cursor):
    store = object.__new__(AgentStore)
    store._document_store = InMemoryDocumentStore()
    connection = Mock()
    connection.cursor.side_effect = lambda: nullcontext(cursor)
    store._pool = Mock()
    store._pool.connection.side_effect = lambda: nullcontext(connection)
    return store


def test_get_agent_returns_none_for_deleted_routine_without_writes():
    cursor = Mock()
    cursor.fetchone.side_effect = [{"id": "deleted", "kind": "routine"}, None]
    store = _store_with_cursor(cursor)
    store._document_store.save_document = Mock()
    assert store.get_agent("deleted") is None
    store._document_store.save_document.assert_not_called()


def test_routine_catalog_ids_include_ghosts_while_visible_list_hides_them():
    cursor = Mock()
    cursor.fetchall.side_effect = [
        [
            {"id": "deleted", "kind": "routine"},
            {"id": "live", "kind": "routine"},
            {"id": "external", "kind": "a2a"},
        ],
        [{"id": "deleted"}, {"id": "live"}],
    ]
    store = _store_with_cursor(cursor)
    store._document_store.save_document(PROGENITOR_STATE_PREFIX, "routine:live", {"nodes": []})
    store._document_store.save_document = Mock()
    assert [agent["id"] for agent in store.list_agents()] == ["live", "external"]
    assert store.list_routine_catalog_ids() == ["deleted", "live"]
    store._document_store.save_document.assert_not_called()
