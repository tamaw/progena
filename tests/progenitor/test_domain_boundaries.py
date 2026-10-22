"""Contracts load independently from application assembly and integration code."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _imported_modules(code: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-c", code + "\nimport json, sys; print(json.dumps(sorted(sys.modules)))"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    return set(json.loads(result.stdout.splitlines()[-1]))


@pytest.mark.parametrize(
    "module",
    [
        "workflows.models",
        "schedules.models",
        "registry.models",
        "a2a.models",
        "acp.models",
        "async_subagents.models",
        "sessions.models",
        "settings.model_profiles",
        "documents.schema",
    ],
)
def test_contracts_do_not_import_application_assembly(module):
    loaded = _imported_modules(f"import app.progenitor.{module}")
    forbidden = (
        "app.backend",
        "app.progenitor.agent",
        "app.progenitor.runtime",
        "app.progenitor.orchestration",
        "app.progenitor.tools",
        "app.progenitor.state.manager",
    )
    assert not {name for name in loaded if name.startswith(forbidden)}


def test_instruction_type_resolution_does_not_load_workflows():
    loaded = _imported_modules(
        """
from app.progenitor.documents.types import DocumentType
from app.progenitor.instructions.templates import resolve_type_ref
kind = DocumentType(id="fixture", label="Fixture", key_prefix="fixture:")
schema, definitions = resolve_type_ref("fixture", {"fixture": kind})
assert schema == {"type": "object", "title": "Fixture"}
assert definitions == {}
"""
    )
    assert not {name for name in loaded if name.startswith("app.progenitor.workflows")}


def test_domain_stores_share_cache_and_keep_read_through_documents_live():
    """Composition must preserve cache identity and multi-process coherence."""
    import asyncio

    from app.progenitor.state.manager import StateManager

    class Store:
        def __init__(self):
            self.documents = {"registry": {"agents": []}, "repo_scan": {"revision": 1}}

        async def get_document(self, name, default=None):
            return self.documents.get(name, default)

        async def save_document(self, name, payload):
            self.documents[name] = payload

    async def check():
        store = Store()
        manager = StateManager(store)
        cached = await manager.get_registry()
        store.documents["registry"] = {"agents": [{"name": "external"}]}
        assert await manager.registry.get_registry() == cached
        assert manager.registry._access.cache is manager._state_cache

        assert await manager.get_repo_scan() == {"revision": 1}
        store.documents["repo_scan"] = {"revision": 2}
        assert await manager.repositories.get_repo_scan() == {"revision": 2}
        assert "repo_scan" not in manager._state_cache

        await manager.registry.save_registry({"agents": [{"name": "written"}]})
        assert await manager.get_registry() == store.documents["registry"]

    asyncio.run(check())
