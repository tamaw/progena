"""Postgres document event log: reads follow commit order, not ``seq`` order.

Opt-in on a scratch database (``PROGENITOR_TEST_DATABASE_URL``).
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    PostgresDocumentStore,
)

DATABASE_URL = (os.getenv("PROGENITOR_TEST_DATABASE_URL") or "").strip() or None

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="requires PROGENITOR_TEST_DATABASE_URL (a scratch database)"
)


@pytest.fixture
def store():
    assert DATABASE_URL is not None
    store = PostgresDocumentStore(DATABASE_URL)
    store.open()
    yield store
    store.close()


def _insert(conn: psycopg.Connection, key: str) -> None:
    conn.execute(
        "INSERT INTO store (prefix, key, value, created_at, updated_at) "
        "VALUES (%s, %s, %s, now(), now())",
        (PROGENITOR_STATE_PREFIX, key, Jsonb({"payload": {"key": key}})),
    )


def _keys(store: PostgresDocumentStore, after, tag: str) -> list[str]:
    return [e.key for e in store.read_document_events(after, 1000) if tag in e.key]


def test_event_from_a_late_committing_transaction_is_not_skipped(store):
    assert DATABASE_URL is not None
    tag = uuid.uuid4().hex
    cursor = store.latest_document_event_position()
    slow = psycopg.connect(DATABASE_URL)
    try:
        # The slow writer allocates the lower seq first and stays open.
        _insert(slow, f"kanban_card:{tag}:slow")
        with psycopg.connect(DATABASE_URL, autocommit=True) as fast:
            _insert(fast, f"kanban_card:{tag}:fast")

        # Nothing from at or after the open transaction is readable yet, so a
        # consumer can't move its cursor past the slow writer's event.
        assert _keys(store, cursor, tag) == []

        slow.commit()
    finally:
        slow.close()

    events = [e for e in store.read_document_events(cursor, 1000) if tag in e.key]
    assert {e.key for e in events} == {f"kanban_card:{tag}:slow", f"kanban_card:{tag}:fast"}
    positions = [e.position for e in events]
    assert positions == sorted(positions)

    for event in events:
        store.save_document_event_cursor("test.consumer", event.position)
    assert store.get_document_event_cursor("test.consumer") == events[-1].position
    assert _keys(store, events[-1].position, tag) == []


def test_rolled_back_transaction_leaves_no_event_and_does_not_block(store):
    assert DATABASE_URL is not None
    tag = uuid.uuid4().hex
    cursor = store.latest_document_event_position()
    with psycopg.connect(DATABASE_URL) as aborted:
        _insert(aborted, f"kanban_card:{tag}:aborted")
        aborted.rollback()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        _insert(conn, f"kanban_card:{tag}:kept")

    assert _keys(store, cursor, tag) == [f"kanban_card:{tag}:kept"]
