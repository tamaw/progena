"""LangGraph Store-backed runtime state and log adapter."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from .models import redact_secrets

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

    def __init__(self, store: AsyncLangGraphStore) -> None:
        self._store = store

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

    async def save_document(self, name: str, payload: Any) -> None:
        """Persist one runtime document."""
        await self._store.aput(STATE_NAMESPACE, name, {"payload": payload})

    async def delete_document(self, name: str) -> None:
        """Remove one runtime document (no-op if it was never written)."""
        await self._store.adelete(STATE_NAMESPACE, name)

    async def update_document(self, name: str, updates: dict[str, Any], default: Any) -> None:
        """Merge updates into one dict document."""
        current = await self.get_document(name, default)
        if not isinstance(current, dict):
            current = dict(default) if isinstance(default, dict) else {}
        current.update(updates)
        await self.save_document(name, current)

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

    async def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Query structured log entries from native LangGraph store namespaces."""
        namespace = (*LOG_NAMESPACE_PREFIX, source) if source else LOG_NAMESPACE_PREFIX
        filter_value = {"level": level.lower()} if level else None
        requested_limit = max(limit, 1) if limit > 0 else 1000
        if search:
            requested_limit = max(requested_limit, 1000)
        items = await self._store.asearch(
            namespace,
            filter=filter_value,
            limit=requested_limit,
        )
        rows = [dict(_item_value(item)) for item in items]
        if search:
            lowered_search = search.lower()
            rows = [
                row
                for row in rows
                if lowered_search in str(row.get("message", "")).lower()
                or lowered_search in str(row.get("payload", {})).lower()
            ]
        rows.sort(key=lambda row: str(row.get("timestamp", "")))
        return rows[-limit:] if limit > 0 else rows


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
