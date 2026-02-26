import json

import pytest

from app.progenitor.tools.registry import ToolRegistry


class _FakeA2AClient:
    async def get_agent_card(self, _agent_config, timeout: float = 2.0):  # noqa: ARG002
        return {
            "name": "Observe Agent",
            "description": "Card description",
            "version": "9.9.9",
            "protocol": "a2a",
            "capabilities": ["cap_a", "cap_b"],
            "endpoint": "http://example:8080",
        }

    async def list_litellm_agents(self, timeout: float = 5.0):  # noqa: ARG002
        return [
            {
                "agent_name": "observe_agent",
                "agent_id": "agt_observe_1",
                "agent_card_params": {
                    "description": "LiteLLM description",
                    "version": "1.2.3",
                    "skills": [{"id": "observe"}, {"name": "health_checks"}],
                },
            },
            {
                "agent_name": "external_agent",
                "agent_id": "agt_external_1",
                "agent_card_params": {
                    "description": "External from LiteLLM",
                    "version": "0.9.0",
                    "skills": [{"id": "external_cap"}],
                },
            },
        ]


@pytest.mark.asyncio
async def test_refresh_agent_cards_updates_registry(tmp_path) -> None:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "name": "observe_agent",
                        "kind": "a2a_agent",
                        "description": "Old",
                        "version": "0.1.0",
                        "capabilities": [],
                        "endpoint": "http://example:8080",
                    }
                ],
                "tools": [],
            }
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry(reg_path)
    await registry.load()
    summary = await registry.refresh_agent_cards(_FakeA2AClient())
    assert summary["agents_updated"] == 1

    agents = registry.get_agents()
    assert agents[0]["description"] == "Card description"
    assert agents[0]["version"] == "9.9.9"
    assert agents[0]["capabilities"] == ["cap_a", "cap_b"]
    assert isinstance(agents[0].get("agent_card"), dict)


@pytest.mark.asyncio
async def test_sync_agents_from_litellm_upserts_registry(tmp_path) -> None:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "name": "observe_agent",
                        "kind": "a2a_agent",
                        "description": "Old",
                        "version": "0.1.0",
                        "capabilities": [],
                        "entrypoint": "app/agents/observe_agent/agent.py",
                        "transport": "http",
                        "status": "stopped",
                    }
                ],
                "tools": [],
            }
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry(reg_path)
    await registry.load()
    summary = await registry.sync_agents_from_litellm(_FakeA2AClient())

    assert summary["agents_seen"] == 2
    assert summary["agents_added"] == 1
    assert summary["agents_updated"] == 1

    agents = registry.get_agents()
    by_name = {a["name"]: a for a in agents}
    assert by_name["observe_agent"]["litellm_agent_id"] == "agt_observe_1"
    assert by_name["observe_agent"]["description"] == "LiteLLM description"
    assert by_name["observe_agent"]["capabilities"] == ["observe", "health_checks"]
    assert by_name["observe_agent"]["status"] == "running"
    assert by_name["external_agent"]["entrypoint"] == "external:external_agent"
    assert by_name["external_agent"]["status"] == "running"
