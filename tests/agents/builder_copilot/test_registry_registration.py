"""Tests for Builder Copilot shared registry startup registration."""

from __future__ import annotations

import json

from app.agents.builder_copilot.agent import AGENT_NAME, _register_agent_in_shared_registry


def test_registry_registration_replaces_with_hostname(
    tmp_path,
    monkeypatch,
) -> None:
    registry_path = tmp_path / "config" / "agents.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "agents": [
                    {"name": "Observe Agent", "endpoint": "http://observe:8106"},
                    {"name": AGENT_NAME, "endpoint": "http://old:8108"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOSTNAME", "builder-copilot")
    monkeypatch.delenv("A2A_PUBLIC_HOST", raising=False)

    _register_agent_in_shared_registry(8108, registry_path=registry_path)

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    agents = payload["agents"]
    assert len(agents) == 2
    builder = next(agent for agent in agents if agent["name"] == AGENT_NAME)
    assert builder["endpoint"] == "http://builder-copilot:8108"
    assert builder["agent_card_url"] == "http://builder-copilot:8108/.well-known/agent.json"


def test_registry_registration_prefers_public_host(tmp_path, monkeypatch) -> None:
    registry_path = tmp_path / "config" / "agents.json"
    monkeypatch.setenv("HOSTNAME", "builder-copilot")
    monkeypatch.setenv("A2A_PUBLIC_HOST", "builder-copilot.public")

    _register_agent_in_shared_registry(8108, registry_path=registry_path)

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    builder = next(agent for agent in payload["agents"] if agent["name"] == AGENT_NAME)
    assert builder["endpoint"] == "http://builder-copilot.public:8108"
