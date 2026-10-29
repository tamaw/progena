"""LangGraph Store-backed runtime state adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from typing import Any, LiteralString, Protocol

from psycopg.sql import SQL, Composable

STATE_NAMESPACE = ("progena", "state")


class AsyncLangGraphStore(Protocol):
    """Subset of LangGraph BaseStore async methods used by Progena."""

    async def aget(self, namespace: tuple[str, ...], key: str, **kwargs: Any) -> Any | None: ...

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        **kwargs: Any,
    ) -> None: ...

    async def asearch(
        self,
        namespace_prefix: tuple[str, ...],
        /,
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        **kwargs: Any,
    ) -> list[Any]: ...

    async def adelete(self, namespace: tuple[str, ...], key: str, **kwargs: Any) -> None: ...


class LangGraphRuntimeStore:
    """Persist Progena runtime documents in LangGraph's native store."""

    def __init__(self, store: AsyncLangGraphStore) -> None:
        self._store = store
        self._document_locks: dict[str, asyncio.Lock] = {}

    async def open(self) -> None:
        """Match the old store lifecycle; native LangGraph store is already open."""

    async def close(self) -> None:
        """Match the old store lifecycle; native LangGraph persistence owns shutdown."""

    async def get_document(self, name: str, default: Any) -> Any:
        """Return one runtime document, creating it from default when absent."""
        item = await self._store.aget(STATE_NAMESPACE, name)
        if item is None:
            await self.save_document(name, default)
            return default
        value = _item_value(item)
        return value.get("payload", default)

    async def peek_document(self, name: str) -> Any | None:
        """Return one runtime document's payload, or ``None``, without creating it.

        Unlike ``get_document`` this never writes a default row - needed where
        the caller enumerates documents by key prefix (e.g. inbox messages), so
        a speculative read of a missing key must not materialize a junk row.
        """
        item = await self._store.aget(STATE_NAMESPACE, name)
        if item is None:
            return None
        return _item_value(item).get("payload")

    async def save_document(self, name: str, payload: Any) -> None:
        """Persist one runtime document."""
        await self._store.aput(STATE_NAMESPACE, name, {"payload": payload})

    async def merge_document(self, name: str, fields: dict[str, Any]) -> bool:
        """Atomically merge top-level ``fields`` into one runtime document's payload.

        Concurrent writers (e.g. the backend) never lose their own fields to this
        merge. Returns ``True`` when the row existed and was updated.
        """
        key = name
        conn = getattr(self._store, "conn", None)
        if conn is None:
            item = await self._store.aget(STATE_NAMESPACE, key)
            if item is None:
                return False
            payload = _item_value(item).get("payload", {})
            payload.update(fields)
            await self._store.aput(STATE_NAMESPACE, key, {"payload": payload})
            return True
        params = (json.dumps(fields), key)
        sql = """
            UPDATE store
            SET value = jsonb_set(value, '{payload}',
                (value->'payload') || %s::jsonb
            ), updated_at = now()
            WHERE prefix = 'progena.state' AND key = %s
            RETURNING 1
        """
        # A pool hands out connections; a single AsyncConnection's `connection`
        # is a non-callable property.
        if callable(getattr(conn, "connection", None)):
            async with conn.connection() as connection, connection.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
        else:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
        return row is not None

    async def append_document_list(self, name: str, field: str, item: Any) -> bool:
        """Atomically append one value to a list field in a runtime document."""
        key = name
        conn = getattr(self._store, "conn", None)
        if conn is None:
            lock = self._document_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._document_locks[key] = lock
            async with lock:
                current = await self.peek_document(key)
                if not isinstance(current, dict):
                    return False
                payload = dict(current)
                values = payload.get(field)
                payload[field] = [*values, item] if isinstance(values, list) else [item]
                await self.save_document(key, payload)
                return True

        params = (field, field, json.dumps([item]), key)
        sql = """
            UPDATE store
            SET value = jsonb_set(
                value,
                ARRAY['payload', %s],
                COALESCE(value #> ARRAY['payload', %s], '[]'::jsonb) || %s::jsonb
            ), updated_at = now()
            WHERE prefix = 'progena.state' AND key = %s
            RETURNING 1
        """
        return await self._execute_document_mutation(sql, params)

    async def merge_document_object(self, name: str, field: str, fields: dict[str, Any]) -> bool:
        """Atomically merge keys into an object field in a runtime document."""
        key = name
        conn = getattr(self._store, "conn", None)
        if conn is None:
            lock = self._document_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._document_locks[key] = lock
            async with lock:
                current = await self.peek_document(key)
                if not isinstance(current, dict):
                    return False
                payload = dict(current)
                existing = payload.get(field)
                payload[field] = {
                    **(existing if isinstance(existing, dict) else {}),
                    **fields,
                }
                await self.save_document(key, payload)
                return True

        params = (field, field, json.dumps(fields), key)
        sql = """
            UPDATE store
            SET value = jsonb_set(
                value,
                ARRAY['payload', %s],
                COALESCE(value #> ARRAY['payload', %s], '{}'::jsonb) || %s::jsonb
            ), updated_at = now()
            WHERE prefix = 'progena.state' AND key = %s
            RETURNING 1
        """
        return await self._execute_document_mutation(sql, params)

    async def _execute_document_mutation(self, sql: str, params: tuple[Any, ...]) -> bool:
        """Run one SQL mutation against the native store connection."""
        conn = getattr(self._store, "conn", None)
        assert conn is not None
        if callable(getattr(conn, "connection", None)):
            async with conn.connection() as connection, connection.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
        else:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
        return row is not None

    async def delete_document(self, name: str) -> None:
        """Remove one runtime document (no-op if it was never written)."""
        await self._store.adelete(STATE_NAMESPACE, name)

    async def list_documents(
        self,
        key_prefix: str,
        *,
        payload_fields: Iterable[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> list[tuple[str, Any]]:
        """Return state documents matching a literal key prefix.

        ``payload_fields`` projects only named top-level payload fields. ``thread_ids``
        restricts results before native payloads are loaded. The default preserves
        full-document callers.
        """
        fields = tuple(dict.fromkeys(payload_fields)) if payload_fields is not None else None
        if thread_ids == []:
            return []
        if getattr(self._store, "conn", None) is not None:
            return await self._list_documents_native(key_prefix, fields, thread_ids)
        return await self._list_documents_memory(key_prefix, fields, thread_ids)

    async def _list_documents_native(
        self,
        key_prefix: str,
        payload_fields: tuple[str, ...] | None,
        thread_ids: list[str] | None,
    ) -> list[tuple[str, Any]]:
        """Read matching documents directly from LangGraph's Postgres table."""
        projection_sql: LiteralString = "value->'payload'"
        params: list[Any] = [_escape_like_prefix(key_prefix)]
        if payload_fields is not None:
            if payload_fields:
                projection_sql = """
                    (
                        SELECT COALESCE(
                            jsonb_object_agg(field_name, value->'payload'->field_name),
                            '{}'::jsonb
                        )
                        FROM unnest(%s::text[]) AS requested(field_name)
                    )
                """
                params.insert(0, list(payload_fields))
            else:
                projection_sql = "'{}'::jsonb"

        conditions: list[LiteralString] = ["prefix = 'progena.state'", r"key LIKE %s ESCAPE '\'"]
        if getattr(self._store, "_omit_expired", False):
            conditions.append("(expires_at IS NULL OR expires_at > NOW())")
        if thread_ids is not None:
            conditions.append("value->'payload'->>'thread_id' = ANY(%s)")
            params.append(thread_ids)

        sql = SQL("""
            SELECT key, {projection} AS payload
            FROM store
            WHERE {conditions}
            ORDER BY updated_at DESC, key ASC
        """).format(
            projection=SQL(projection_sql),
            conditions=SQL(" AND ").join(SQL(condition) for condition in conditions),
        )
        rows = await self._fetch_native_rows(sql, tuple(params))
        return [
            (
                str(row["key"]) if isinstance(row, Mapping) else str(row[0]),
                row["payload"] if isinstance(row, Mapping) else row[1],
            )
            for row in rows
        ]

    async def _list_documents_memory(
        self,
        key_prefix: str,
        payload_fields: tuple[str, ...] | None,
        thread_ids: list[str] | None,
    ) -> list[tuple[str, Any]]:
        """Page an in-memory store while retaining native filtering semantics."""
        page_size = 500
        offset = 0
        results: list[tuple[str, Any]] = []
        while True:
            items = await self._store.asearch(STATE_NAMESPACE, limit=page_size, offset=offset)
            for item in items:
                key = str(getattr(item, "key", ""))
                payload = _item_value(item).get("payload")
                if not key.startswith(key_prefix):
                    continue
                if thread_ids is not None and (
                    not isinstance(payload, dict) or payload.get("thread_id") not in thread_ids
                ):
                    continue
                if payload_fields is not None:
                    payload = {
                        field: payload.get(field) if isinstance(payload, dict) else None
                        for field in payload_fields
                    }
                results.append((key, payload))
            if len(items) < page_size:
                return results
            offset += page_size

    async def get_document_list_item(
        self,
        name: str,
        field: str,
        value: str,
    ) -> Any | None:
        """Return one object from a document payload array by an exact field value."""
        if getattr(self._store, "conn", None) is None:
            payload = await self.peek_document(name)
            if not isinstance(payload, list):
                return None
            return next(
                (item for item in payload if isinstance(item, dict) and item.get(field) == value),
                None,
            )

        sql = """
            SELECT item
            FROM store
            CROSS JOIN LATERAL jsonb_array_elements(value->'payload') AS item
            WHERE prefix = 'progena.state'
              AND key = %s
              AND item->>%s = %s
            LIMIT 1
        """
        rows = await self._fetch_native_rows(sql, (name, field, value))
        if not rows:
            return None
        row = rows[0]
        return row["item"] if isinstance(row, Mapping) else row[0]

    async def _fetch_native_rows(self, sql: str | Composable, params: tuple[Any, ...]) -> list[Any]:
        """Execute a read against a direct connection or LangGraph connection pool."""
        conn = getattr(self._store, "conn", None)
        assert conn is not None
        if callable(getattr(conn, "connection", None)):
            async with conn.connection() as connection, connection.cursor() as cur:
                await cur.execute(sql, params)
                return await cur.fetchall()
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


def _item_value(item: Any) -> dict[str, Any]:
    value = getattr(item, "value", item)
    return value if isinstance(value, dict) else {}


def _escape_like_prefix(prefix: str) -> str:
    """Build a literal-prefix pattern for PostgreSQL ``LIKE ... ESCAPE '\\'``."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
