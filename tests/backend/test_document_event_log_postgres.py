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


def test_events_behind_an_open_transaction_are_reported_deferred(store):
    """An older transaction that writes no document event still holds events
    back; its end announces nothing, so the dispatcher must know to re-check."""
    assert DATABASE_URL is not None
    tag = uuid.uuid4().hex
    cursor = store.latest_document_event_position()
    blocker = psycopg.connect(DATABASE_URL)
    try:
        blocker.execute("SELECT pg_current_xact_id()")  # holds the snapshot xmin
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            _insert(conn, f"kanban_card:{tag}:waiting")

        assert _keys(store, cursor, tag) == []
        assert store.has_deferred_document_events(cursor) is True
    finally:
        blocker.rollback()
        blocker.close()

    assert _keys(store, cursor, tag) == [f"kanban_card:{tag}:waiting"]


def test_mutate_document_is_not_overwritten_by_a_concurrent_merge(store):
    """A plain merge landing while a mutator runs waits for it, rather than
    committing in between and then being overwritten by the stale mutation."""
    import threading
    import time

    key = f"kanban_card:{uuid.uuid4().hex}:locked"
    store.save_document(PROGENITOR_STATE_PREFIX, key, {"a": 0, "b": 0})
    merged = threading.Event()

    def concurrent_merge() -> None:
        store.merge_document(PROGENITOR_STATE_PREFIX, key, {"b": 1})
        merged.set()

    def mutator(payload):
        merger = threading.Thread(target=concurrent_merge)
        merger.start()
        time.sleep(0.3)  # the merge would commit here without a row lock
        assert not merged.is_set()
        return {**payload, "a": 1}

    try:
        store.mutate_document(PROGENITOR_STATE_PREFIX, key, mutator)
        assert merged.wait(5)
        assert store.get_document(PROGENITOR_STATE_PREFIX, key).payload == {"a": 1, "b": 1}
    finally:
        store.delete_document(PROGENITOR_STATE_PREFIX, key)
