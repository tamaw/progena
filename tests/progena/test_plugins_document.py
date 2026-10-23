"""Tests for the plugins state document (plugins.json) validator and persistence."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.progena.agent.filesystem.base import StateDocumentBackend
from src.progena.agent.filesystem.config import create_plugins_state_file
from src.progena.plugins.schema import validate_plugins_document
from src.progena.state.manager import StateManager
from tests.progena.conftest import _FakeRuntimeStore

# ---- validation ------------------------------------------------------------


def test_validate_plugins_document_accepts_valid():
    doc = validate_plugins_document(
        '{"plugins": {"acme-tools/formatter": {"source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"}}}}'
    )
    assert "acme-tools/formatter" in doc["plugins"]
    assert doc["plugins"]["acme-tools/formatter"]["source"]["repo"] == "acme/formatter-plugin"


def test_validate_plugins_document_accepts_valid_with_runtime_fields():
    doc = validate_plugins_document(
        json.dumps(
            {
                "plugins": {
                    "acme-tools/formatter": {
                        "source": {
                            "source": "github",
                            "repo": "acme/formatter-plugin",
                            "ref": "v2.1.0",
                        },
                        "version": "2.1.0",
                        "installed_version": "2.1.0",
                        "installed_digest": "sha256:abc123",
                        "available_version": None,
                        "status": "installed",
                        "status_message": "",
                        "skills": ["quality-review", "lint-fix"],
                        "installed_at": "2026-07-29T10:00:00Z",
                        "last_checked": "2026-07-29T10:00:00Z",
                    }
                }
            }
        )
    )
    assert doc["plugins"]["acme-tools/formatter"]["status"] == "installed"
    assert doc["plugins"]["acme-tools/formatter"]["skills"] == ["quality-review", "lint-fix"]


def test_validate_plugins_document_accepts_agents_field():
    """Regression: reconcile_plugins() always writes an "agents" key on every
    successful install (src/progena/plugins/install.py), even for
    skills-only plugins (as an empty list) -- the schema must accept it or
    every real reconcile's save_plugins_config() call fails post-install."""
    doc = validate_plugins_document(
        json.dumps(
            {
                "plugins": {
                    "acme-tools/pr-summarizer": {
                        "source": {
                            "source": "github",
                            "repo": "acme/pr-summarizer-plugin",
                        },
                        "status": "installed",
                        "skills": [],
                        "agents": ["pr-summarizer"],
                    }
                }
            }
        )
    )
    assert doc["plugins"]["acme-tools/pr-summarizer"]["agents"] == ["pr-summarizer"]


def test_validate_plugins_document_accepts_source_string():
    doc = validate_plugins_document(
        '{"plugins": {"acme-tools/formatter": {"source": "./formatter"}}}'
    )
    assert doc["plugins"]["acme-tools/formatter"]["source"] == "./formatter"


def test_validate_plugins_document_rejects_non_object_top_level():
    with pytest.raises(ValueError, match="top level must be a JSON object"):
        validate_plugins_document('["plugins"]')


def test_validate_plugins_document_rejects_unexpected_top_level_key():
    with pytest.raises(ValueError, match="unexpected top-level key"):
        validate_plugins_document('{"plugins": {}, "extra": {}}')


def test_validate_plugins_document_rejects_malformed_plugin_key():
    with pytest.raises(ValueError, match="plugin key 'bad key' must look like"):
        validate_plugins_document('{"plugins": {"bad key": {"source": "x"}}}')


def test_validate_plugins_document_rejects_missing_source():
    with pytest.raises(
        ValueError,
        match="plugin 'acme-tools/formatter' does not conform to schema: 'source' is a required property",
    ):
        validate_plugins_document('{"plugins": {"acme-tools/formatter": {}}}')


def test_validate_plugins_document_rejects_schema_invalid_field_type():
    with pytest.raises(ValueError, match="schema"):
        validate_plugins_document(
            '{"plugins": {"acme-tools/formatter": {"source": "./x", "status": 123}}}'
        )


# ---- StateManager round-trip -----------------------------------------------


@pytest.mark.asyncio
async def test_get_plugins_config_reflects_cross_process_store_writes():
    """get_plugins_config reads fresh from the store, not from a stale cache."""
    store = _FakeRuntimeStore()
    manager = StateManager(runtime_store=store)
    await manager.initialize()

    # Startup seeds an empty config and caches it.
    assert (await manager.get_plugins_config())["plugins"] == {}

    # Simulate the backend writing the same shared document directly.
    await store.save_document(
        "plugins",
        {
            "plugins": {
                "acme-tools/formatter": {
                    "source": {
                        "source": "github",
                        "repo": "acme/formatter-plugin",
                        "ref": "v2.1.0",
                    },
                    "version": "2.1.0",
                }
            }
        },
    )

    config = await manager.get_plugins_config()
    assert "acme-tools/formatter" in config["plugins"]
    assert config["plugins"]["acme-tools/formatter"]["version"] == "2.1.0"


@pytest.mark.asyncio
async def test_save_plugins_config_persists_valid_document():
    store = _FakeRuntimeStore()
    manager = StateManager(runtime_store=store)
    await manager.initialize()

    payload = {
        "plugins": {
            "acme-tools/formatter": {
                "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
                "version": "2.1.0",
            }
        }
    }
    await manager.save_plugins_config(payload)
    assert store._documents["plugins"] == payload


@pytest.mark.asyncio
async def test_save_plugins_config_rejects_invalid_document():
    store = _FakeRuntimeStore()
    manager = StateManager(runtime_store=store)
    await manager.initialize()

    bad_payload = {"plugins": {"bad key": {}}}
    with pytest.raises(ValueError):
        await manager.save_plugins_config(bad_payload)

    # Document must remain unchanged.
    assert (await manager.get_plugins_config())["plugins"] == {}


# ---- virtual file backend --------------------------------------------------


class _FakePluginsStateManager:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else {"plugins": {}}
        self.saved: list[dict[str, Any]] = []

    async def get_plugins_config(self) -> dict[str, Any]:
        return self.payload

    async def save_plugins_config(self, payload: dict[str, Any]) -> None:
        self.saved.append(payload)


def _plugins_backend(payload=None):
    sm = _FakePluginsStateManager(payload)
    backend = StateDocumentBackend([create_plugins_state_file(sm)])
    return backend, sm


@pytest.mark.asyncio
async def test_plugins_aread_and_ls():
    backend, _sm = _plugins_backend({"plugins": {"acme-tools/formatter": {"source": "./x"}}})
    result = await backend.aread("/plugins.json")
    assert result.error is None
    assert (
        json.loads(result.file_data["content"])["plugins"]["acme-tools/formatter"]["source"]
        == "./x"
    )

    listed = await backend.als("/")
    paths = [entry["path"] for entry in listed.entries]
    assert "/plugins.json" in paths


@pytest.mark.asyncio
async def test_plugins_awrite_persists_valid_document():
    backend, sm = _plugins_backend({"plugins": {}})
    new_doc = json.dumps({"plugins": {"acme-tools/formatter": {"source": "./x"}}})
    written = await backend.awrite("/plugins.json", new_doc)
    assert written.error is None
    assert sm.saved == [{"plugins": {"acme-tools/formatter": {"source": "./x"}}}]


@pytest.mark.asyncio
async def test_plugins_awrite_invalid_is_rejected():
    backend, sm = _plugins_backend({"plugins": {}})
    bad_doc = json.dumps({"plugins": {"bad key": {}}})
    written = await backend.awrite("/plugins.json", bad_doc)
    assert written.error is not None
    assert sm.saved == []


@pytest.mark.asyncio
async def test_plugins_aedit_updates_single_entry():
    backend, sm = _plugins_backend({"plugins": {"acme-tools/formatter": {"source": "./old"}}})
    edited = await backend.aedit(
        "/plugins.json",
        old_string='"source": "./old"',
        new_string='"source": "./new"',
    )
    assert edited.error is None
    assert sm.saved == [{"plugins": {"acme-tools/formatter": {"source": "./new"}}}]


@pytest.mark.asyncio
async def test_plugins_aedit_to_invalid_is_rejected():
    backend, sm = _plugins_backend({"plugins": {"acme-tools/formatter": {"source": "./old"}}})
    edited = await backend.aedit(
        "/plugins.json",
        old_string='"source": "./old"',
        new_string='"source": 123',
    )
    assert edited.error is not None
    assert sm.saved == []
