"""LangGraph Store-backed runtime state adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

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

    async def list_documents(self, key_prefix: str) -> list[tuple[str, Any]]:
        """Return every state document whose key starts with ``key_prefix``."""
        page_size = 500
        offset = 0
        results: list[tuple[str, Any]] = []
        while True:
            items = await self._store.asearch(STATE_NAMESPACE, limit=page_size, offset=offset)
            for item in items:
                key = str(getattr(item, "key", ""))
                if key.startswith(key_prefix):
                    results.append((key, _item_value(item).get("payload")))
            if len(items) < page_size:
                return results
            offset += page_size


def _item_value(item: Any) -> dict[str, Any]:
    value = getattr(item, "value", item)
    return value if isinstance(value, dict) else {}
