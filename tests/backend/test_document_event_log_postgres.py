"""Postgres document event log: reads follow commit order, not ``seq`` order."""

from __future__ import annotations

import time
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    TASK_EVIDENCE_PREFIX,
    DocumentEventTooLargeError,
    PostgresDocumentStore,
)


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


def _wait_until_events_settle(store: PostgresDocumentStore, after) -> None:
    # xmin is cluster-wide: another xdist worker's database can hold it back
    # after this test's own blocker commits. Production dispatch likewise waits
    # for deferred events; absence without deferral must still fail assertions.
    deadline = time.monotonic() + 10
    while store.has_deferred_document_events(after):
        if time.monotonic() >= deadline:
            pytest.fail("Document events remained deferred after releasing the test transaction")
        time.sleep(0.01)


def _settled_events(store: PostgresDocumentStore, after):
    _wait_until_events_settle(store, after)
    return store.read_document_events(after, 1000)


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

    events = [e for e in _settled_events(store, cursor) if tag in e.key]
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

    assert [e.key for e in _settled_events(store, cursor) if tag in e.key] == [
        f"kanban_card:{tag}:kept"
    ]


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

    assert [e.key for e in _settled_events(store, cursor) if tag in e.key] == [
        f"kanban_card:{tag}:waiting"
    ]


def test_postgres_event_reader_hydrates_only_a_byte_bounded_prefix(store):
    tag = uuid.uuid4().hex
    cursor = store.latest_document_event_position()
    store.save_document(PROGENA_STATE_PREFIX, f"kanban_card:{tag}:first", {"body": "x" * 500})
    store.save_document(PROGENA_STATE_PREFIX, f"kanban_card:{tag}:second", {"body": "x" * 500})
    _wait_until_events_settle(store, cursor)

    [first] = store.read_document_events(cursor, 200, max_encoded_bytes=1_000)
    [second] = store.read_document_events(first.position, 200, max_encoded_bytes=1_000)

    assert first.key == f"kanban_card:{tag}:first"
    assert second.key == f"kanban_card:{tag}:second"


def test_postgres_oversized_event_remains_unhydrated_and_unacknowledged(store):
    tag = uuid.uuid4().hex
    cursor = store.latest_document_event_position()
    key = f"kanban_card:{tag}:oversized"
    store.save_document(PROGENA_STATE_PREFIX, key, {"body": "x" * 2_000})
    _wait_until_events_settle(store, cursor)

    with pytest.raises(DocumentEventTooLargeError, match="replay limit") as raised:
        store.read_document_events(cursor, 200, max_encoded_bytes=1_000)

    assert raised.value.key == key
    assert raised.value.position > cursor
    assert store.get_document_event_cursor("test.oversized") is None


def test_postgres_recovery_workflow_run_page_filters_status_before_payload_loading(store):
    tag = f"zz{uuid.uuid4().hex}"
    first_key = f"workflow_run:{tag}:first"
    second_key = f"workflow_run:{tag}:second"
    store.save_document(PROGENA_STATE_PREFIX, first_key, {"status": "queued", "body": "x" * 500})
    store.save_document(PROGENA_STATE_PREFIX, second_key, {"status": "running", "body": "x" * 500})
    store.save_document(
        PROGENA_STATE_PREFIX,
        f"workflow_run:{tag}:terminal",
        {"status": "completed", "body": "x" * 500},
    )

    [(returned_key, returned)] = store.list_recoverable_workflow_runs(
        after_key=f"workflow_run:{tag}:",
        limit=1,
    )
    [(next_key, next_document)] = store.list_recoverable_workflow_runs(
        after_key=returned_key,
        limit=1,
    )

    assert (returned_key, returned.payload["status"]) == (first_key, "queued")
    assert (next_key, next_document.payload["status"]) == (second_key, "running")


def test_postgres_recovery_queue_page_filters_nonempty_arrays_before_loading(store):
    tag = f"zz{uuid.uuid4().hex}"
    first_key = f"workflow_queue:{tag}:first"
    second_key = f"workflow_queue:{tag}:second"
    store.save_document(PROGENA_STATE_PREFIX, f"workflow_queue:{tag}:empty", {"items": []})
    store.save_document(PROGENA_STATE_PREFIX, first_key, {"items": [{"event": "one"}]})
    store.save_document(PROGENA_STATE_PREFIX, second_key, {"items": [{"event": "two"}]})
    store.save_document(PROGENA_STATE_PREFIX, f"workflow_queue:{tag}:invalid", {"items": "no"})

    [returned_key] = store.list_recoverable_workflow_queue_keys(
        after_key=f"workflow_queue:{tag}:",
        limit=1,
    )
    [next_key] = store.list_recoverable_workflow_queue_keys(
        after_key=returned_key,
        limit=1,
    )

    assert returned_key == first_key
    assert next_key == second_key


def test_postgres_task_evidence_namespace_is_not_captured_as_a_document_event(store):
    cursor = store.latest_document_event_position()
    store.save_document(TASK_EVIDENCE_PREFIX, "sha256:abc", {"raw": "x" * 10_000})

    assert _settled_events(store, cursor) == []


def test_mutate_document_is_not_overwritten_by_a_concurrent_merge(store):
    """A plain merge landing while a mutator runs waits for it, rather than
    committing in between and then being overwritten by the stale mutation."""
    import threading

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
