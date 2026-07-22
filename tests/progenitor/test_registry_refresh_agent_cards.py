import json

import pytest

from app.progenitor.registry import ToolRegistry, refresh_agent_cards, refresh_agent_health
from app.progenitor.state.models import AgentRegistryEntry


def _fake_store() -> tuple:
    """Return (loader, saver, backing-dict) emulating the registry document."""
    backing: dict = {}

    async def loader() -> dict:
        return backing.get("registry", {})

    async def saver(doc: dict) -> None:
        backing["registry"] = json.loads(json.dumps(doc))

    return loader, saver, backing


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
async def test_refresh_agent_cards_updates_registry() -> None:
    loader, saver, backing = _fake_store()

    registry = ToolRegistry()
    registry.bind_store(loader, saver)
    await registry.load()

    # Register agent with initial metadata
    entry = AgentRegistryEntry(
        name="observe_agent",
        description="Old",
        version="0.1.0",
        capabilities=[],
        url="http://example:8080",
        kind="a2a_agent",
    )
    await registry.register_agent(entry)

    summary = await refresh_agent_cards(registry, _FakeA2AClient())
    assert summary["agents_updated"] == 1

    agents = registry.get_agents()
    assert agents[0]["description"] == "Old"
    assert agents[0]["version"] == "0.1.0"
    assert agents[0]["capabilities"] == []
    assert isinstance(agents[0].get("agent_card"), dict)

    # With store-backed, agent_card is persisted (not stripped like file-backed).
    await registry.save()
    saved = backing["registry"]
    assert "agent_card" in saved["agents"][0]


@pytest.mark.asyncio
async def test_runtime_mcp_tools_are_ephemeral() -> None:
    registry = ToolRegistry()
    await registry.load()
    await registry.register_tool({"name": "search_tool", "kind": "mcp_server"})

    assert registry.get_agents() == []
    tools = registry.get_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "search_tool"
    assert tools[0]["kind"] == "mcp_server"


@pytest.mark.asyncio
async def test_refresh_agent_health_marks_unhealthy() -> None:
    registry = ToolRegistry()
    await registry.load()

    entry = AgentRegistryEntry(
        name="test_agent",
        url="http://127.0.0.1:9",
        kind="a2a_agent",
        capabilities=[],
    )
    await registry.register_agent(entry)

    summary = await refresh_agent_health(registry, timeout=0.2)

    assert summary["agents_checked"] == 1
    assert summary["unhealthy"] == 1
    assert registry.get_agents()[0]["status"] == "failed"
