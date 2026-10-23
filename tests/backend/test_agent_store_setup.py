"""Fresh/current catalog setup must not rewrite graphs or purge agent rows."""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from src.backend.agents import store as agent_store
from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore
from src.progena.routines.graph import validate_routine_graph_payload
from src.progena.routines.store import default_routine_graph, routine_graph_key


@pytest.mark.parametrize("existing_graph", [False, True])
def test_catalog_setup_and_graph_seeding_preserve_existing_data(monkeypatch, existing_graph):
    documents = InMemoryDocumentStore()
    graph = default_routine_graph("alpha", name="Existing routine")
    graph["nodes"][0]["config"]["prompt_template"] = "Keep my edited prompt: {request}"
    if existing_graph:
        documents.save_document(PROGENA_STATE_PREFIX, routine_graph_key("alpha"), graph)
    documents.save_document(PROGENA_STATE_PREFIX, "unrelated", {"keep": True})
    save = Mock(wraps=documents.save_document)
    delete = Mock(wraps=documents.delete_document)
    monkeypatch.setattr(documents, "save_document", save)
    monkeypatch.setattr(documents, "delete_document", delete)

    store = object.__new__(agent_store.AgentStore)
    store._document_store = documents
    cursor = Mock()
    connection = Mock()
    connection.cursor.side_effect = lambda: nullcontext(cursor)
    pool = Mock()
    pool.connection.side_effect = lambda: nullcontext(connection)
    store._pool = pool
    monkeypatch.setattr(agent_store, "_default_store", None)
    monkeypatch.setattr(agent_store, "AgentStore", lambda *_args: store)

    configured = agent_store.configure_agent_store(None, "postgresql://example", None, documents)

    assert configured is store
    assert agent_store.get_agent_store() is store
    pool.open.assert_called_once()
    save.assert_not_called()
    delete.assert_not_called()

    store.ensure_graph("alpha")

    persisted = store.get_routine_document("alpha")
    assert persisted == (graph if existing_graph else default_routine_graph("alpha"))
    assert persisted is not None
    validate_routine_graph_payload(persisted)
    if existing_graph:
        save.assert_not_called()
    else:
        save.assert_called_once()
    delete.assert_not_called()
    assert documents.get_document(PROGENA_STATE_PREFIX, "unrelated").payload == {"keep": True}
