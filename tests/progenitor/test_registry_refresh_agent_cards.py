import json

import pytest

from app.progenitor.registry import ToolRegistry, refresh_agent_cards, refresh_agent_health


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
                "observe_agent": {
                    "kind": "a2a_agent",
                    "description": "Old",
                    "version": "0.1.0",
                    "capabilities": [],
                    "url": "http://example:8080",
                }
            }
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry(reg_path)
    await registry.load()
    summary = await refresh_agent_cards(registry, _FakeA2AClient())
    assert summary["agents_updated"] == 1

    agents = registry.get_agents()
    assert agents[0]["description"] == "Old"
    assert agents[0]["version"] == "0.1.0"
    assert agents[0]["capabilities"] == []
    assert isinstance(agents[0].get("agent_card"), dict)

    await registry.save()
    saved = json.loads(reg_path.read_text(encoding="utf-8"))
    assert "agent_card" not in saved["observe_agent"]


@pytest.mark.asyncio
async def test_runtime_mcp_tools_are_ephemeral(tmp_path) -> None:
    reg_path = tmp_path / "agents.json"
    reg_path.write_text(json.dumps({}), encoding="utf-8")

    registry = ToolRegistry(reg_path)
    await registry.load()
    await registry.register_tool({"name": "search_tool", "kind": "mcp_server"})

    assert registry.get_agents() == []
    tools = registry.get_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "search_tool"
    assert tools[0]["kind"] == "mcp_server"


@pytest.mark.asyncio
async def test_refresh_agent_health_marks_unhealthy(tmp_path) -> None:
    reg_path = tmp_path / "agents.json"
    reg_path.write_text(
        json.dumps({"test_agent": {"url": "http://127.0.0.1:9"}}),
        encoding="utf-8",
    )

    registry = ToolRegistry(reg_path)
    await registry.load()
    summary = await refresh_agent_health(registry, timeout=0.2)

    assert summary["agents_checked"] == 1
    assert summary["unhealthy"] == 1
    assert registry.get_agents()[0]["status"] == "failed"
