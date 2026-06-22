"""Tests for the DeepAgent state-document backend (state-as-files)."""

from __future__ import annotations

import copy
import json
from typing import Any

from app.progenitor.agent.state_backend import (
    StateDocumentBackend,
    create_mcp_state_file,
    validate_mcp_document,
)


class _FakeStateManager:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"mcpServers": {}}
        self.saved: list[dict[str, Any]] = []

    async def get_mcp_config(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    async def save_mcp_config(self, payload: dict[str, Any]) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))


def _backend(payload=None) -> tuple[StateDocumentBackend, _FakeStateManager, list[bool]]:
    sm = _FakeStateManager(payload)
    reloads: list[bool] = []

    async def on_change() -> None:
        reloads.append(True)

    backend = StateDocumentBackend([create_mcp_state_file(sm, on_change=on_change)])
    return backend, sm, reloads


# ---- validation ------------------------------------------------------------


def test_validate_mcp_document_accepts_valid():
    doc = validate_mcp_document('{"mcpServers": {"jina": {"url": "https://x/mcp"}}}')
    assert doc["mcpServers"]["jina"]["url"] == "https://x/mcp"


def test_validate_mcp_document_rejects_bad_json():
    try:
        validate_mcp_document("{not json")
    except ValueError as exc:
        assert "JSON" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_validate_mcp_document_rejects_missing_url_and_bad_transport():
    for bad in (
        '{"mcpServers": {"x": {}}}',
        '{"mcpServers": {"x": {"url": "https://x", "transport": "sse"}}}',
        '{"nope": {}}',
    ):
        try:
            validate_mcp_document(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")


# ---- read / ls -------------------------------------------------------------


async def test_aread_returns_mcp_json():
    backend, _sm, _ = _backend({"mcpServers": {"jina": {"url": "https://x/mcp"}}})
    result = await backend.aread("/mcp.json")
    assert result.error is None
    content = result.file_data["content"]
    assert json.loads(content)["mcpServers"]["jina"]["url"] == "https://x/mcp"


async def test_aread_unknown_file_errors():
    backend, _sm, _ = _backend()
    result = await backend.aread("/nope.json")
    assert result.error and "not found" in result.error


async def test_als_lists_state_files():
    backend, _sm, _ = _backend()
    result = await backend.als("/")
    paths = [e["path"] for e in result.entries]
    assert "/mcp.json" in paths


# ---- write -----------------------------------------------------------------


async def test_awrite_persists_and_runs_on_change():
    backend, sm, reloads = _backend({"mcpServers": {}})
    new = json.dumps({"mcpServers": {"jina": {"url": "https://new/mcp", "transport": "http"}}})
    result = await backend.awrite("/mcp.json", new)
    assert result.error is None
    assert result.path == "/mcp.json"
    assert sm.payload["mcpServers"]["jina"]["url"] == "https://new/mcp"
    assert reloads == [True]


async def test_awrite_invalid_does_not_persist_or_reload():
    backend, sm, reloads = _backend({"mcpServers": {"jina": {"url": "https://x/mcp"}}})
    result = await backend.awrite("/mcp.json", '{"mcpServers": {"x": {}}}')  # missing url
    assert result.error and "Invalid" in result.error
    assert sm.saved == []
    assert reloads == []


# ---- edit ------------------------------------------------------------------


async def test_aedit_replaces_and_persists():
    backend, sm, reloads = _backend({"mcpServers": {"jina": {"url": "https://old/mcp"}}})
    result = await backend.aedit("/mcp.json", "https://old/mcp", "https://new/mcp")
    assert result.error is None
    assert result.occurrences == 1
    assert sm.payload["mcpServers"]["jina"]["url"] == "https://new/mcp"
    assert reloads == [True]


async def test_aedit_to_invalid_is_rejected():
    backend, sm, reloads = _backend({"mcpServers": {"jina": {"url": "https://old/mcp"}}})
    # Replace the url value with empty string -> server without url -> invalid.
    result = await backend.aedit("/mcp.json", '"https://old/mcp"', '""')
    assert result.error and "invalid" in result.error.lower()
    assert sm.saved == []
    assert reloads == []
