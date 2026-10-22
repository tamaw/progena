"""LangGraph Store-backed runtime state and log adapter."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from app.progenitor.security.redaction import redact_secrets

STATE_NAMESPACE = ("progenitor", "state")
LOG_NAMESPACE_PREFIX = ("progenitor", "logs")


class AsyncLangGraphStore(Protocol):
    """Subset of LangGraph BaseStore async methods used by Progenitor."""

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
    """Persist Progenitor runtime documents and logs in LangGraph's native store."""

    def __init__(self, store: AsyncLangGraphStore, *, debug_mode: bool = False) -> None:
        self._store = store
        self._document_locks: dict[str, asyncio.Lock] = {}
        self._debug_mode = debug_mode

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
            WHERE prefix = 'progenitor.state' AND key = %s
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
            WHERE prefix = 'progenitor.state' AND key = %s
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
            WHERE prefix = 'progenitor.state' AND key = %s
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
        """Return ``(key, payload)`` for every state document whose key starts with prefix.

        LangGraph's ``asearch`` defaults to a page of 10, so this pages through
        the state namespace until a short page; the namespace holds only dozens
        of rows (logs live under a different namespace prefix).
        """
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

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        """Return one raw item value from any namespace, or ``None``."""
        item = await self._store.aget(namespace, key)
        return _item_value(item) if item is not None else None

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        """Persist one raw item value in any namespace."""
        await self._store.aput(namespace, key, value)

    async def delete_item(self, namespace: tuple[str, ...], key: str) -> None:
        """Remove one item from any namespace (no-op if absent)."""
        await self._store.adelete(namespace, key)

    async def list_items(self, namespace: tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
        """Return ``(key, value)`` for every item in ``namespace``, paging past asearch's default."""
        page_size = 500
        offset = 0
        results: list[tuple[str, dict[str, Any]]] = []
        while True:
            items = await self._store.asearch(namespace, limit=page_size, offset=offset)
            results += [(str(getattr(item, "key", "")), _item_value(item)) for item in items]
            if len(items) < page_size:
                return results
            offset += page_size

    async def write_log(
        self,
        *,
        source: str,
        message: str,
        level: str = "info",
        payload: dict[str, Any] | None = None,
        timestamp: Any = None,
        relative_path: str | None = None,
    ) -> None:
        """Append one structured log entry to the native LangGraph store."""
        timestamp_text = _coerce_timestamp_text(timestamp)
        redacted_payload = _redacted_payload(payload or {})
        value = {
            "timestamp": timestamp_text,
            "level": level.lower(),
            "source": source,
            "message": redact_secrets(message),
            "payload": redacted_payload,
            "relative_path": relative_path,
        }
        key = f"{timestamp_text}_{uuid.uuid4().hex}"
        await self._store.aput((*LOG_NAMESPACE_PREFIX, source), key, value)
        if self._debug_mode:
            print(
                f"[LOG {source}] {timestamp_text} {level}: {message}",
                file=sys.stderr,
                flush=True,
            )


def _item_value(item: Any) -> dict[str, Any]:
    value = getattr(item, "value", item)
    return value if isinstance(value, dict) else {}


def _coerce_timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return timestamp.isoformat()
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    if isinstance(value, str) and value.strip():
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)).isoformat()
        except ValueError:
            return value
    return datetime.now(UTC).isoformat()


def _redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    value = _redact_value(payload)
    return value if isinstance(value, dict) else {"value": value}


def _redact_value(value: Any, *, key_name: str | None = None) -> Any:
    if _is_sensitive_key(key_name) and isinstance(value, str):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(key): _redact_value(item, key_name=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_redact_value(item) for item in value]
    if isinstance(value, bytes):
        return redact_secrets(value.decode("utf-8", errors="replace"))
    if isinstance(value, str):
        return redact_secrets(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact_secrets(str(value))


def _is_sensitive_key(key_name: str | None) -> bool:
    if key_name is None:
        return False
    lowered = key_name.lower().replace("-", "_")
    return any(token in lowered for token in ("api_key", "apikey", "secret", "password", "token"))
