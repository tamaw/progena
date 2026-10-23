"""TaskStore.peek_kanban_card reads a card without ever writing."""

from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from src.progena.persistence.documents import StateDocuments
from src.progena.persistence.store import STATE_NAMESPACE, LangGraphRuntimeStore
from src.progena.tasks.store import TaskStore


def _store() -> tuple[InMemoryStore, TaskStore]:
    native = InMemoryStore()
    access = StateDocuments(
        store=LangGraphRuntimeStore(native),
        cache={},
        database_url=None,
        read_through=frozenset(),
    )
    return native, TaskStore(access)


def _rows(native: InMemoryStore) -> dict[str, object]:
    return {item.key: item.value for item in native.search(STATE_NAMESPACE, limit=1000)}


@pytest.mark.asyncio
async def test_missing_or_forged_card_id_leaves_storage_unchanged():
    native, tasks = _store()
    await tasks.save_kanban_card("card-1", {"id": "card-1", "title": "Real"})
    before = _rows(native)

    for forged in ("forged-card", "card-1 ", "../card-1", "card-2"):
        assert await tasks.peek_kanban_card(forged) is None

    assert _rows(native) == before


@pytest.mark.asyncio
async def test_get_kanban_card_still_materializes_a_row_on_miss():
    # The reason peek exists: the original accessor writes a default row.
    native, tasks = _store()
    assert await tasks.get_kanban_card("forged-card") is None
    assert "kanban_card:forged-card" in _rows(native)


@pytest.mark.asyncio
async def test_peek_returns_the_stored_card():
    native, tasks = _store()
    card = {"id": "card-1", "title": "Real", "pr_url": "https://github.com/o/r/pull/1"}
    await tasks.save_kanban_card("card-1", card)
    before = _rows(native)
    assert await tasks.peek_kanban_card("card-1") == card
    assert _rows(native) == before


@pytest.mark.asyncio
async def test_peek_ignores_non_object_payloads():
    native, tasks = _store()
    await tasks._access.store.save_document("kanban_card:odd", ["not", "a", "card"])
    assert await tasks.peek_kanban_card("odd") is None
