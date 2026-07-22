"""Shared fixtures for progenitor unit tests."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest


class _FakeRuntimeStore:
    """In-memory runtime store for tests.

    Mirrors the LangGraphRuntimeStore interface without persistence.
    """

    def __init__(self) -> None:
        self._documents: dict[str, Any] = {}
        self._logs: dict[str, list[dict[str, Any]]] = {}

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_document(self, name: str, default: Any = None) -> Any:
        if name not in self._documents:
            self._documents[name] = default
        return self._documents[name]

    async def peek_document(self, name: str) -> Any | None:
        return self._documents.get(name)

    async def save_document(self, name: str, payload: Any) -> None:
        self._documents[name] = payload

    async def delete_document(self, name: str) -> None:
        self._documents.pop(name, None)

    async def list_documents(self, key_prefix: str) -> list[tuple[str, Any]]:
        return [(k, v) for k, v in self._documents.items() if k.startswith(key_prefix)]

    async def update_document(self, name: str, updates: dict[str, Any], default: Any) -> None:
        current = await self.get_document(name, default)
        if isinstance(current, dict):
            current.update(updates)
        else:
            self._documents[name] = updates

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
        entry: dict[str, Any] = {
            "timestamp": (
                timestamp.isoformat()
                if isinstance(timestamp, datetime)
                else str(timestamp or datetime.now(UTC).isoformat())
            ),
            "level": level.lower(),
            "source": source,
            "message": message,
            "payload": payload or {},
            "relative_path": relative_path,
        }
        self._logs.setdefault(source, []).append(entry)

    async def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if source:
            rows = list(self._logs.get(source, []))
        else:
            for entries in self._logs.values():
                rows.extend(entries)
        if level:
            rows = [r for r in rows if r.get("level") == level.lower()]
        if search:
            lowered = search.lower()
            rows = [
                r
                for r in rows
                if lowered in str(r.get("message", "")).lower()
                or lowered in str(r.get("payload", {})).lower()
            ]
        rows.sort(key=lambda r: str(r.get("timestamp", "")))
        return rows[-limit:] if limit > 0 else rows


@pytest.fixture(autouse=True)
def patch_checkpointer(monkeypatch):
    """Replace _build_checkpointer with an in-memory saver for all unit tests.

    Unit tests do not need SQLite persistence and must not attempt to create
    filesystem paths like /data/state that are inaccessible in CI.
    """
    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(
        "app.progenitor.agent.agent._build_checkpointer",
        lambda db_path: MemorySaver(),
    )
