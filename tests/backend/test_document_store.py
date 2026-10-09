from __future__ import annotations

import asyncio

import psycopg
from psycopg_pool import PoolClosed

from app.backend.document_store import (
    STORE_CHANGE_CHANNEL,
    InMemoryDocumentStore,
    PostgresDocumentStore,
    StoreChangeListener,
    _escape_like,
    store_database_url,
)


class _FakeCursor:
    def __init__(self, row):
        self._row = row
        self.executed: list[tuple[object, object]] = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def execute(self, query, params=None):
        return self._cursor.execute(query, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _ClosedPool:
    def connection(self):
        raise PoolClosed("fake pool closed")


class _HealthyPool:
    def __init__(self, row):
        self.opens = 0
        self._cursor = _FakeCursor(row)

    def open(self):
        self.opens += 1

    def close(self):
        return None

    def connection(self):
        return _FakeConnection(self._cursor)


def test_postgres_document_store_reopens_closed_pool():
    store = PostgresDocumentStore("postgresql://example")
    healthy_pool = _HealthyPool({"value": {"payload": {"ok": True}}, "updated_at": None})
    store._pool = _ClosedPool()
    store._recreate_pool = lambda: setattr(store, "_pool", healthy_pool)

    doc = store.get_document("prefix", "key")

    assert healthy_pool._cursor.executed
    assert doc is not None
    assert doc.payload == {"ok": True}


def test_in_memory_list_documents_filters_by_prefix_and_key_prefix():
    store = InMemoryDocumentStore()
    store.save_document("progenitor.state", "inbox_message:b", {"id": "b"})
    store.save_document("progenitor.state", "inbox_message:a", {"id": "a"})
    store.save_document("progenitor.state", "kanban_card:c", {"id": "c"})
    store.save_document("backend.state", "inbox_message:d", {"id": "d"})

    rows = store.list_documents("progenitor.state", "inbox_message:")

    assert [key for key, _doc in rows] == ["inbox_message:a", "inbox_message:b"]
    assert [doc.payload["id"] for _key, doc in rows] == ["a", "b"]


def test_escape_like_escapes_wildcards():
    assert _escape_like("inbox_message:") == "inbox\\_message:"
    assert _escape_like("100%") == "100\\%"
    assert _escape_like("a\\b") == "a\\\\b"


def test_store_database_url_resolution():
    pg = PostgresDocumentStore("postgresql://example")
    assert store_database_url(pg) == "postgresql://example"
    assert store_database_url(InMemoryDocumentStore()) is None
    assert store_database_url(None) is None


def test_ensure_change_trigger_notifies_state_only():
    store = PostgresDocumentStore("postgresql://example")
    pool = _HealthyPool(None)
    store._pool = pool

    store._ensure_change_trigger()

    emitted = " ".join(str(query) for query, _ in pool._cursor.executed)
    assert "progenitor_notify_store_change" in emitted
    assert "pg_notify" in emitted
    assert "CREATE OR REPLACE TRIGGER progenitor_store_change" in emitted
    # Deletes must notify too - the runtime process has no other way to tell the
    # backend a row disappeared.
    assert "AFTER INSERT OR UPDATE OR DELETE" in emitted
    # State prefixes notify; the high-frequency logs prefix is excluded.
    assert "progenitor.state" in emitted
    assert "backend.state" in emitted
    assert "progenitor.logs" not in emitted


def test_document_store_open_preserves_documents_and_installs_revision_history():
    store = PostgresDocumentStore("postgresql://example")
    pool = _HealthyPool(None)
    store._pool = pool

    store.open()

    assert pool.opens == 1
    statements = [" ".join(str(query).split()) for query, _ in pool._cursor.executed]
    emitted = " ".join(statements)
    assert "CREATE TABLE IF NOT EXISTS store" in emitted
    assert "CREATE TABLE IF NOT EXISTS routine_graph_revisions" in emitted
    assert (
        "CREATE OR REPLACE TRIGGER progenitor_capture_routine_revision AFTER INSERT OR UPDATE ON store"
        in emitted
    )
    assert (
        "CREATE OR REPLACE TRIGGER progenitor_capture_deleted_routine_revision BEFORE DELETE ON store"
        in emitted
    )
    assert "RETURN source_row" in emitted
    assert "IF TG_OP = 'DELETE' THEN source_row := OLD;" in emitted
    assert "graph_doc := source_row.value -> 'payload'" in emitted
    assert (
        "CREATE OR REPLACE TRIGGER progenitor_routine_revisions_immutable BEFORE UPDATE ON routine_graph_revisions"
        in emitted
    )
    # Existing graphs still get immutable snapshots without rewriting the source
    # documents. Subsequent writes continue to capture revisions via the trigger.
    backfill = next(
        statement
        for statement in statements
        if statement.startswith("INSERT INTO routine_graph_revisions")
    )
    assert backfill.startswith("INSERT INTO routine_graph_revisions")
    assert "FROM store" in backfill
    assert "value -> 'payload'" in backfill
    assert "ON CONFLICT (routine_id, revision_id) DO NOTHING" in backfill
    assert "RAISE EXCEPTION 'routine_graph_revisions rows are immutable" in emitted
    assert "CREATE TABLE IF NOT EXISTS policy_revisions" in emitted
    # Setup installs schema/triggers, captures history and seeds the default policy, whether
    # the database is fresh or already contains documents/checkpoints.
    assert all(
        statement.startswith(
            (
                "CREATE TABLE",
                "SELECT pg_advisory_lock",
                "SELECT pg_advisory_unlock",
                "CREATE INDEX",
                "CREATE OR REPLACE FUNCTION",
                "CREATE OR REPLACE TRIGGER",
                "INSERT INTO routine_graph_revisions",
                "INSERT INTO store",
            )
        )
        for statement in statements
    )


class _RowsCursor(_FakeCursor):
    """Like _FakeCursor but fetchall() returns canned rows -- list_routine_runs
    reads its EXISTS(...) result via fetchall(), not fetchone()."""

    def __init__(self, rows):
        super().__init__(None)
        self._rows = rows

    def fetchall(self):
        return self._rows


def test_list_routine_runs_reads_error_channel_not_run_meta():
    """The failed flag comes from checkpoint_writes.__error__, a single-query
    EXISTS alongside the existing GROUP BY -- not a second round trip, and
    not derived from deserializing run_meta (see document_store.py's
    list_routine_runs docstring for why run_meta under-reports failures)."""
    store = PostgresDocumentStore("postgresql://example")
    rows = [
        {
            "thread_id": "routine:alpha:rev-a:run-failed",
            "started_at": "2026-01-01T00:00:00+00:00",
            "last_checkpoint_at": "2026-01-01T00:00:05+00:00",
            "checkpoint_count": 3,
            "failed": True,
        },
        {
            "thread_id": "routine:alpha:rev-a:run-ok",
            "started_at": "2026-01-02T00:00:00+00:00",
            "last_checkpoint_at": "2026-01-02T00:00:05+00:00",
            "checkpoint_count": 5,
            "failed": False,
        },
    ]
    pool = _HealthyPool(None)
    pool._cursor = _RowsCursor(rows)
    store._pool = pool

    summaries, has_more = store.list_routine_runs("alpha", limit=50)

    executed_query = str(pool._cursor.executed[0][0])
    assert "checkpoint_writes" in executed_query
    assert "__error__" in executed_query
    assert has_more is False
    assert [(s.run_id, s.failed) for s in summaries] == [
        ("run-failed", True),
        ("run-ok", False),
    ]


class _FakeNotify:
    def __init__(self, payload: str) -> None:
        self.payload = payload


class _FakeAsyncConn:
    def __init__(self, items: list[_FakeNotify]) -> None:
        self._items = items
        self.executed: list[object] = []
        self.closed = False

    async def execute(self, query: object) -> None:
        self.executed.append(query)

    async def notifies(self):
        for item in self._items:
            yield item

    async def close(self) -> None:
        self.closed = True


class _BlockingAsyncConn:
    def __init__(self) -> None:
        self.closed = False

    async def execute(self, query: object) -> None:
        return None

    async def notifies(self):
        await asyncio.Event().wait()  # block until cancelled
        yield  # pragma: no cover - unreachable

    async def close(self) -> None:
        self.closed = True


async def test_listener_resyncs_on_connect_then_streams_changes(monkeypatch):
    fake = _FakeAsyncConn([_FakeNotify("a"), _FakeNotify("b")])

    async def fake_connect(dsn, autocommit=False):
        return fake

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake_connect)

    resyncs: list[bool] = []
    changes: list[str] = []

    def resync():
        # LISTEN must precede the snapshot read to avoid losing concurrent writes.
        assert any("LISTEN" in str(query) for query in fake.executed)
        resyncs.append(True)

    listener = StoreChangeListener(
        "postgresql://example",
        on_change=changes.append,
        on_resync=resync,
    )

    await listener._listen_once()

    # Resync fires before live notifications start; each NOTIFY maps to on_change.
    assert resyncs == [True]
    assert changes == ["a", "b"]
    assert fake.closed is True
    assert any("LISTEN" in str(q) and STORE_CHANGE_CHANNEL in str(q) for q in fake.executed)


async def test_listener_reconnects_after_connection_error(monkeypatch):
    attempts: list[str] = []
    reconnected = asyncio.Event()

    async def fake_connect(dsn, autocommit=False):
        attempts.append(dsn)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        reconnected.set()
        return _BlockingAsyncConn()

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake_connect)

    listener = StoreChangeListener(
        "postgresql://example",
        on_change=lambda _payload: None,
        reconnect_delay=0.01,
    )
    await listener.start()
    try:
        await asyncio.wait_for(reconnected.wait(), timeout=2)
    finally:
        await listener.stop()

    assert len(attempts) >= 2
