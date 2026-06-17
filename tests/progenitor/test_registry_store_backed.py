"""Tests for the database-backed registry store (with file-migration fallback)."""

from __future__ import annotations

import json
from typing import Any

from app.progenitor.registry.store import ToolRegistry
from app.progenitor.state.models import ACPAgentConfig, AgentRegistryEntry


def _fake_store() -> tuple[Any, Any, dict[str, Any]]:
    """Return (loader, saver, backing-dict) emulating the registry document.

    The saver JSON round-trips the payload — like the real LangGraph store — so a
    non-serializable value (e.g. a raw ``datetime``) would raise here.
    """
    backing: dict[str, Any] = {}

    async def loader() -> dict[str, Any]:
        return backing.get("registry", {})

    async def saver(doc: dict[str, Any]) -> None:
        backing["registry"] = json.loads(json.dumps(doc))

    return loader, saver, backing


async def test_store_backed_save_and_reload(tmp_path):
    loader, saver, backing = _fake_store()
    reg = ToolRegistry(tmp_path / "agents.json")
    reg.bind_store(loader, saver)
    await reg.load()
    assert reg.store_backed is True

    await reg.register_agent(
        AgentRegistryEntry(
            name="cronix_copilot",
            kind="acp_agent",
            transport="acp",
            acp=ACPAgentConfig(host="192.168.0.2", port=3000, model="claude-haiku-4.5"),
        )
    )
    # Persisted to the store document, not a file.
    assert not (tmp_path / "agents.json").exists()
    assert [a["name"] for a in backing["registry"]["agents"]] == ["cronix_copilot"]

    # A fresh registry bound to the same store (simulating a restart) sees it.
    reg2 = ToolRegistry(tmp_path / "agents.json")
    reg2.bind_store(loader, saver)
    await reg2.load()
    cfg = reg2.find_by_name("cronix_copilot")
    assert cfg is not None
    assert cfg["acp"]["port"] == 3000


async def test_store_backed_migrates_existing_file_once(tmp_path):
    # A pre-existing agents.json (legacy file shape: {name: cfg}).
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"legacy_agent": {"kind": "a2a_agent", "transport": "http"}}))

    loader, saver, backing = _fake_store()
    reg = ToolRegistry(path)
    reg.bind_store(loader, saver)
    await reg.load()

    # File contents were imported into the store on first (empty-store) load.
    assert reg.find_by_name("legacy_agent") is not None
    assert "legacy_agent" in {a["name"] for a in backing["registry"]["agents"]}


async def test_file_backed_when_unbound(tmp_path):
    path = tmp_path / "agents.json"
    reg = ToolRegistry(path)  # no store bound
    await reg.load()
    assert reg.store_backed is False
    await reg.register_agent(AgentRegistryEntry(name="local", kind="a2a_agent"))
    # Falls back to writing the JSON file.
    assert path.exists()
    assert "local" in json.loads(path.read_text())
