"""Tests for the marketplaces state document (marketplaces.json) validator and persistence."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.progenitor.agent.state_backend import (
    StateDocumentBackend,
    create_marketplaces_state_file,
    validate_marketplaces_document,
)
from app.progenitor.state.manager import StateManager
from tests.progenitor.conftest import _FakeRuntimeStore

# ---- validation ------------------------------------------------------------


def test_validate_marketplaces_document_accepts_valid():
    doc = validate_marketplaces_document(
        '{"marketplaces": {"acme-tools": {"source": "https://github.com/acme/plugins"}}}'
    )
    assert doc["marketplaces"]["acme-tools"]["source"] == "https://github.com/acme/plugins"


def test_validate_marketplaces_document_accepts_added_at():
    doc = validate_marketplaces_document(
        json.dumps(
            {
                "marketplaces": {
                    "acme-tools": {
                        "source": "acme/plugins@main",
                        "added_at": "2026-07-29T10:00:00Z",
                    }
                }
            }
        )
    )
    assert doc["marketplaces"]["acme-tools"]["added_at"] == "2026-07-29T10:00:00Z"


def test_validate_marketplaces_document_rejects_non_object_top_level():
    with pytest.raises(ValueError, match="top level must be a JSON object"):
        validate_marketplaces_document('["marketplaces"]')


def test_validate_marketplaces_document_rejects_unexpected_top_level_key():
    with pytest.raises(ValueError, match="unexpected top-level key"):
        validate_marketplaces_document('{"marketplaces": {}, "extra": {}}')


def test_validate_marketplaces_document_rejects_bad_name():
    with pytest.raises(ValueError, match="must be kebab-case"):
        validate_marketplaces_document('{"marketplaces": {"Bad Name": {"source": "x"}}}')


def test_validate_marketplaces_document_rejects_missing_source():
    with pytest.raises(ValueError, match="marketplace 'acme-tools' is missing 'source'"):
        validate_marketplaces_document('{"marketplaces": {"acme-tools": {}}}')


def test_validate_marketplaces_document_rejects_schema_invalid_field_type():
    with pytest.raises(ValueError, match="schema"):
        validate_marketplaces_document(
            '{"marketplaces": {"acme-tools": {"source": "x", "added_at": 123}}}'
        )


# ---- StateManager round-trip -----------------------------------------------


@pytest.mark.asyncio
async def test_get_marketplaces_config_reflects_cross_process_store_writes():
    """get_marketplaces_config reads fresh from the store, not from a stale cache."""
    store = _FakeRuntimeStore()
    manager = StateManager(runtime_store=store)
    await manager.initialize()

    assert (await manager.get_marketplaces_config())["marketplaces"] == {}

    # Simulate the backend writing the same shared document directly.
    await store.save_document(
        "marketplaces",
        {"marketplaces": {"acme-tools": {"source": "https://github.com/acme/plugins"}}},
    )

    config = await manager.get_marketplaces_config()
    assert "acme-tools" in config["marketplaces"]
    assert config["marketplaces"]["acme-tools"]["source"] == "https://github.com/acme/plugins"


@pytest.mark.asyncio
async def test_save_marketplaces_config_persists_valid_document():
    store = _FakeRuntimeStore()
    manager = StateManager(runtime_store=store)
    await manager.initialize()

    payload = {"marketplaces": {"acme-tools": {"source": "https://github.com/acme/plugins"}}}
    await manager.save_marketplaces_config(payload)
    assert store._documents["marketplaces"] == payload


@pytest.mark.asyncio
async def test_save_marketplaces_config_rejects_invalid_document():
    store = _FakeRuntimeStore()
    manager = StateManager(runtime_store=store)
    await manager.initialize()

    bad_payload = {"marketplaces": {"Bad Name": {"source": "x"}}}
    with pytest.raises(ValueError):
        await manager.save_marketplaces_config(bad_payload)

    # Document must remain unchanged.
    assert (await manager.get_marketplaces_config())["marketplaces"] == {}


# ---- virtual file backend --------------------------------------------------


class _FakeMarketplacesStateManager:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else {"marketplaces": {}}
        self.saved: list[dict[str, Any]] = []

    async def get_marketplaces_config(self) -> dict[str, Any]:
        return self.payload

    async def save_marketplaces_config(self, payload: dict[str, Any]) -> None:
        self.saved.append(payload)


def _marketplaces_backend(payload=None):
    sm = _FakeMarketplacesStateManager(payload)
    backend = StateDocumentBackend([create_marketplaces_state_file(sm)])
    return backend, sm


@pytest.mark.asyncio
async def test_marketplaces_aread_and_ls():
    backend, _sm = _marketplaces_backend(
        {"marketplaces": {"acme-tools": {"source": "https://github.com/acme/plugins"}}}
    )
    result = await backend.aread("/marketplaces.json")
    assert result.error is None
    assert (
        json.loads(result.file_data["content"])["marketplaces"]["acme-tools"]["source"]
        == "https://github.com/acme/plugins"
    )

    listed = await backend.als("/")
    paths = [entry["path"] for entry in listed.entries]
    assert "/marketplaces.json" in paths


@pytest.mark.asyncio
async def test_marketplaces_awrite_persists_valid_document():
    backend, sm = _marketplaces_backend({"marketplaces": {}})
    new_doc = json.dumps(
        {"marketplaces": {"acme-tools": {"source": "https://github.com/acme/plugins"}}}
    )
    written = await backend.awrite("/marketplaces.json", new_doc)
    assert written.error is None
    assert sm.saved == [
        {"marketplaces": {"acme-tools": {"source": "https://github.com/acme/plugins"}}}
    ]


@pytest.mark.asyncio
async def test_marketplaces_awrite_invalid_is_rejected():
    backend, sm = _marketplaces_backend({"marketplaces": {}})
    bad_doc = json.dumps({"marketplaces": {"Bad Name": {"source": "x"}}})
    written = await backend.awrite("/marketplaces.json", bad_doc)
    assert written.error is not None
    assert sm.saved == []


@pytest.mark.asyncio
async def test_marketplaces_aedit_updates_single_entry():
    backend, sm = _marketplaces_backend(
        {"marketplaces": {"acme-tools": {"source": "acme/plugins@old"}}}
    )
    edited = await backend.aedit(
        "/marketplaces.json",
        old_string='"source": "acme/plugins@old"',
        new_string='"source": "acme/plugins@new"',
    )
    assert edited.error is None
    assert sm.saved == [{"marketplaces": {"acme-tools": {"source": "acme/plugins@new"}}}]


@pytest.mark.asyncio
async def test_marketplaces_aedit_to_invalid_is_rejected():
    backend, sm = _marketplaces_backend(
        {"marketplaces": {"acme-tools": {"source": "acme/plugins@old"}}}
    )
    edited = await backend.aedit(
        "/marketplaces.json",
        old_string='"source": "acme/plugins@old"',
        new_string='"added_at": 123',
    )
    assert edited.error is not None
    assert sm.saved == []
