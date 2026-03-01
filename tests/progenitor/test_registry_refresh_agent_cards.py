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

@pytest.mark.asyncio
async def test_refresh_agent_cards_updates_registry(tmp_path) -> None:
    reg_path = tmp_path / "agents.json"
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
async def test_registry_and_mcp_state_are_split(tmp_path) -> None:
    reg_path = tmp_path / "agents.json"
    mcp_path = tmp_path / "mcp.json"
    reg_path.write_text(json.dumps({"agents": []}), encoding="utf-8")
    mcp_path.write_text(
        json.dumps({"tools": [{"name": "search_tool", "kind": "mcp_server"}]}),
        encoding="utf-8",
    )

    registry = ToolRegistry(reg_path)
    await registry.load()

    assert registry.get_agents() == []
    assert registry.get_tools() == [{"name": "search_tool", "kind": "mcp_server"}]
