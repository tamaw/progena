"""``PostgresDocumentStore.search_documents`` against a fresh Postgres database."""

from __future__ import annotations

import pytest

from src.backend.storage.documents import PROGENA_STATE_PREFIX, PostgresDocumentStore


@pytest.fixture
def store(postgres_url: str):
    store = PostgresDocumentStore(postgres_url)
    store.open()
    yield store
    store.close()


def _keys(rows):
    return [row["key"] for row in rows]


def test_json_fields_match_case_insensitively_with_title_prefix_first_and_limit(store):
    for i in range(7):
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"kanban_card:c{i}",
            {"title": f"Task {i}", "description": "fix the HEALTH check"},
        )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "kanban_card:lead",
        {"title": "Health dashboard", "description": ""},
    )
    store.save_document(PROGENA_STATE_PREFIX, "kanban_card:other", {"title": "Unrelated"})

    rows = store.search_documents(
        PROGENA_STATE_PREFIX, "kanban_card:", ("title", "description"), "json", "health"
    )

    assert len(rows) == 5
    assert rows[0]["key"] == "kanban_card:lead"
    assert "kanban_card:other" not in _keys(rows)


def test_front_matter_search_honours_key_suffix(store):
    manifest = "---\nname: researcher\ndescription: Researches Raft papers\n---\nBody\n"
    store.save_document(
        PROGENA_STATE_PREFIX, "agent:user/researcher/SYSTEM.md", {"content": manifest}
    )
    store.save_document(
        PROGENA_STATE_PREFIX, "agent:user/researcher/notes.md", {"content": "Raft notes"}
    )

    rows = store.search_documents(
        PROGENA_STATE_PREFIX,
        "agent:",
        ("name", "description"),
        "front_matter",
        "raft",
        key_suffix="/SYSTEM.md",
    )

    assert _keys(rows) == ["agent:user/researcher/SYSTEM.md"]


def test_like_wildcards_in_the_query_are_literal(store):
    store.save_document(PROGENA_STATE_PREFIX, "kanban_card:a", {"title": "100% done"})
    store.save_document(PROGENA_STATE_PREFIX, "kanban_card:b", {"title": "1000 items"})

    rows = store.search_documents(PROGENA_STATE_PREFIX, "kanban_card:", ("title",), "json", "100%")

    assert _keys(rows) == ["kanban_card:a"]
