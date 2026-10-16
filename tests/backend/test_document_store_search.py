"""``PostgresDocumentStore.search_documents`` against a real Postgres (isolated schema)."""

from __future__ import annotations

import os
import uuid
from urllib.parse import quote

import psycopg
import pytest

from app.backend.document_store import PROGENITOR_STATE_PREFIX, PostgresDocumentStore

DB_URL = (os.getenv("PROGENITOR_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
pytestmark = pytest.mark.skipif(not DB_URL, reason="requires PROGENITOR_DATABASE_URL")


@pytest.fixture
def store():
    schema = f"pytest_search_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    separator = "&" if "?" in DB_URL else "?"
    store = PostgresDocumentStore(
        f"{DB_URL}{separator}options={quote(f'-c search_path={schema},public')}"
    )
    store.open()
    yield store
    store.close()
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _keys(rows):
    return [row["key"] for row in rows]


def test_json_fields_match_case_insensitively_with_title_prefix_first_and_limit(store):
    for i in range(7):
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            f"kanban_card:c{i}",
            {"title": f"Task {i}", "description": "fix the HEALTH check"},
        )
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "kanban_card:lead",
        {"title": "Health dashboard", "description": ""},
    )
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:other", {"title": "Unrelated"})

    rows = store.search_documents(
        PROGENITOR_STATE_PREFIX, "kanban_card:", ("title", "description"), "json", "health"
    )

    assert len(rows) == 5
    assert rows[0]["key"] == "kanban_card:lead"
    assert "kanban_card:other" not in _keys(rows)


def test_front_matter_search_honours_key_suffix(store):
    manifest = "---\nname: researcher\ndescription: Researches Raft papers\n---\nBody\n"
    store.save_document(
        PROGENITOR_STATE_PREFIX, "agent:user/researcher/SYSTEM.md", {"content": manifest}
    )
    store.save_document(
        PROGENITOR_STATE_PREFIX, "agent:user/researcher/notes.md", {"content": "Raft notes"}
    )

    rows = store.search_documents(
        PROGENITOR_STATE_PREFIX,
        "agent:",
        ("name", "description"),
        "front_matter",
        "raft",
        key_suffix="/SYSTEM.md",
    )

    assert _keys(rows) == ["agent:user/researcher/SYSTEM.md"]


def test_like_wildcards_in_the_query_are_literal(store):
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:a", {"title": "100% done"})
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_card:b", {"title": "1000 items"})

    rows = store.search_documents(
        PROGENITOR_STATE_PREFIX, "kanban_card:", ("title",), "json", "100%"
    )

    assert _keys(rows) == ["kanban_card:a"]
