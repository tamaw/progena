"""Postgres document event log: reads follow commit order, not ``seq`` order."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from src.backend.storage.documents import PROGENA_STATE_PREFIX, PostgresDocumentStore


@pytest.fixture
def store(postgres_url: str):
    store = PostgresDocumentStore(postgres_url)
    store.open()
    yield store
    store.close()


def _insert(conn: psycopg.Connection, key: str) -> None:
    conn.execute(
        "INSERT INTO store (prefix, key, value, created_at, updated_at) "
        "VALUES (%s, %s, %s, now(), now())",
        (PROGENA_STATE_PREFIX, key, Jsonb({"payload": {"key": key}})),
    )


def _keys(store: PostgresDocumentStore, after, tag: str) -> list[str]:
    return [e.key for e in store.read_document_events(after, 1000) if tag in e.key]


def test_event_from_a_late_committing_transaction_is_not_skipped(store, postgres_url: str):
    tag = uuid.uuid4().hex
    cursor = store.latest_document_event_position()
    slow = psycopg.connect(postgres_url)
    try:
        # The slow writer allocates the lower seq first and stays open.
        _insert(slow, f"kanban_card:{tag}:slow")
        with psycopg.connect(postgres_url, autocommit=True) as fast:
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


def test_rolled_back_transaction_leaves_no_event_and_does_not_block(store, postgres_url: str):
    tag = uuid.uuid4().hex
    cursor = store.latest_document_event_position()
    with psycopg.connect(postgres_url) as aborted:
        _insert(aborted, f"kanban_card:{tag}:aborted")
        aborted.rollback()
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        _insert(conn, f"kanban_card:{tag}:kept")

    assert _keys(store, cursor, tag) == [f"kanban_card:{tag}:kept"]


def test_events_behind_an_open_transaction_are_reported_deferred(store, postgres_url: str):
    """An older transaction that writes no document event still holds events
    back; its end announces nothing, so the dispatcher must know to re-check."""
    tag = uuid.uuid4().hex
    cursor = store.latest_document_event_position()
    blocker = psycopg.connect(postgres_url)
    try:
        blocker.execute("SELECT pg_current_xact_id()")  # holds the snapshot xmin
        with psycopg.connect(postgres_url, autocommit=True) as conn:
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
    store.save_document(PROGENA_STATE_PREFIX, key, {"a": 0, "b": 0})
    merged = threading.Event()
    merge_errors: list[Exception] = []
    merger: threading.Thread | None = None

    def concurrent_merge() -> None:
        try:
            store.merge_document(PROGENA_STATE_PREFIX, key, {"b": 1})
        except Exception as exc:
            merge_errors.append(exc)
        finally:
            merged.set()

    def mutator(payload):
        nonlocal merger
        merger = threading.Thread(target=concurrent_merge)
        merger.start()
        time.sleep(0.3)  # the merge would commit here without a row lock
        assert not merged.is_set()
        return {**payload, "a": 1}

    try:
        store.mutate_document(PROGENA_STATE_PREFIX, key, mutator)
        assert merger is not None
        merger.join(timeout=5)
        assert not merger.is_alive()
        if merge_errors:
            raise merge_errors[0]
        assert merged.is_set()
        assert store.get_document(PROGENA_STATE_PREFIX, key).payload == {"a": 1, "b": 1}
    finally:
        if merger is not None:
            merger.join(timeout=5)
            assert not merger.is_alive()
        store.delete_document(PROGENA_STATE_PREFIX, key)
