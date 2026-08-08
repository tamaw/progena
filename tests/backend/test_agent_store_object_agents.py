"""Tests for AgentStore's manifest-backed object-agent create/update/list path.

Constructs AgentStore via object.__new__ to skip the Postgres connection
pool entirely -- list_object_agents/save_user_object_agent/
update_object_agent only ever touch self._document_store (and, for native
agents, the filesystem), never self._pool, so a real database connection is
unnecessary here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backend import agent_store as agent_store_module
from app.backend.agent_store import AgentStore
from app.backend.document_store import InMemoryDocumentStore


def _make_store(monkeypatch: pytest.MonkeyPatch) -> AgentStore:
    # Isolate from the real repo's data/agents/objects/ (e.g. repo-manager).
    monkeypatch.setattr(agent_store_module, "discover_native_object_agents", lambda _root: [])
    store = object.__new__(AgentStore)
    store._document_store = InMemoryDocumentStore()  # noqa: SLF001
    return store


def _manifest_markdown(name: str, description: str, prompt: str, **extra: object) -> str:
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "version: 1.0.0",
        "kind: object",
    ]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append(prompt)
    return "\n".join(lines) + "\n"


def test_save_then_list_round_trips_user_object_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _make_store(monkeypatch)
    content = _manifest_markdown("PR Bot", "Summarizes PRs.", "You summarize PRs.")

    created = store.save_user_object_agent(content)

    assert created["id"] == "object-pr-bot"
    assert created["card"]["source"] == "user"
    records = store.list_object_agents()
    assert [r["id"] for r in records] == ["object-pr-bot"]
    assert records[0]["prompt"] == "You summarize PRs."


def test_update_object_agent_overwrites_prompt_without_renaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _make_store(monkeypatch)
    store.save_user_object_agent(_manifest_markdown("PR Bot", "Summarizes PRs.", "Old prompt."))

    updated = store.update_object_agent(
        "object-pr-bot",
        _manifest_markdown("PR Bot", "Summarizes PRs.", "New prompt."),
    )

    assert updated["prompt"] == "New prompt."
    records = store.list_object_agents()
    assert [r["id"] for r in records] == ["object-pr-bot"]
    assert records[0]["prompt"] == "New prompt."


def test_update_object_agent_moves_document_on_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _make_store(monkeypatch)
    store.save_user_object_agent(_manifest_markdown("PR Bot", "Summarizes PRs.", "Prompt."))

    store.update_object_agent(
        "object-pr-bot",
        _manifest_markdown("Renamed Bot", "Summarizes PRs.", "Prompt."),
    )

    records = store.list_object_agents()
    assert [r["id"] for r in records] == ["object-renamed-bot"]


def test_update_object_agent_rejects_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _make_store(monkeypatch)

    with pytest.raises(LookupError):
        store.update_object_agent(
            "object-does-not-exist",
            _manifest_markdown("Does Not Exist", "n/a", "n/a"),
        )


def test_update_object_agent_rewrites_native_manifest_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _make_store(monkeypatch)
    from app.progenitor.agent.object_agents import ObjectAgentSource

    system_path = tmp_path / "native-agent" / "SYSTEM.md"
    system_path.parent.mkdir(parents=True)
    system_path.write_text(
        _manifest_markdown("Native Agent", "Ships with the repo.", "Old prompt."),
        encoding="utf-8",
    )
    native_manifest = agent_store_module.parse_object_agent_manifest(
        system_path.read_text(encoding="utf-8"), source="native"
    )
    monkeypatch.setattr(
        agent_store_module,
        "discover_native_object_agents",
        lambda _root: [
            ObjectAgentSource(manifest=native_manifest, source="native", native_path=system_path)
        ],
    )

    updated = store.update_object_agent(
        "object-native-agent",
        _manifest_markdown("Native Agent", "Ships with the repo.", "New prompt."),
    )

    assert updated["prompt"] == "New prompt."
    assert "New prompt." in system_path.read_text(encoding="utf-8")


def test_update_object_agent_rejects_renaming_native_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _make_store(monkeypatch)
    from app.progenitor.agent.object_agents import ObjectAgentSource

    system_path = tmp_path / "native-agent" / "SYSTEM.md"
    system_path.parent.mkdir(parents=True)
    system_path.write_text(
        _manifest_markdown("Native Agent", "Ships with the repo.", "Prompt."),
        encoding="utf-8",
    )
    native_manifest = agent_store_module.parse_object_agent_manifest(
        system_path.read_text(encoding="utf-8"), source="native"
    )
    monkeypatch.setattr(
        agent_store_module,
        "discover_native_object_agents",
        lambda _root: [
            ObjectAgentSource(manifest=native_manifest, source="native", native_path=system_path)
        ],
    )

    with pytest.raises(ValueError, match="renaming a native object agent"):
        store.update_object_agent(
            "object-native-agent",
            _manifest_markdown("Renamed Agent", "Ships with the repo.", "Prompt."),
        )


def test_update_object_agent_rejects_rename_colliding_with_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _make_store(monkeypatch)
    store.save_user_object_agent(_manifest_markdown("Agent One", "First.", "Prompt one."))
    store.save_user_object_agent(_manifest_markdown("Agent Two", "Second.", "Prompt two."))

    with pytest.raises(ValueError, match="already in use"):
        store.update_object_agent(
            "object-agent-one",
            _manifest_markdown("Agent Two", "First.", "Prompt one."),
        )
