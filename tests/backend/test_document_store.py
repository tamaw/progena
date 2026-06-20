from __future__ import annotations

from psycopg_pool import PoolClosed

from app.backend.document_store import PostgresDocumentStore


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
