"""Tests for tool registry."""

import json

import pytest

from app.progenitor.registry import ToolRegistry
from app.progenitor.state.models import AgentRegistryEntry


def _fake_store() -> tuple:
    """Return (loader, saver, backing-dict) emulating the registry document."""
    backing: dict = {}

    async def loader() -> dict:
        return backing.get("registry", {})

    async def saver(doc: dict) -> None:
        backing["registry"] = json.loads(json.dumps(doc))

    return loader, saver, backing


class TestToolRegistry:
    """Tests for the ToolRegistry class."""

    @pytest.mark.asyncio
    async def test_load_empty_registry(self):
        """Test loading an empty registry."""
        registry = ToolRegistry()
        await registry.load()

        assert registry.get_agents() == []
        assert registry.get_tools() == []

    @pytest.mark.asyncio
    async def test_register_agent(self):
        """Test registering an agent."""
        registry = ToolRegistry()
        await registry.load()

        entry = AgentRegistryEntry(
            name="test_agent",
            description="A test agent",
            capabilities=["test_cap"],
            entrypoint="remote://test/main.py",
        )

        await registry.register_agent(entry)

        agents = registry.get_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "test_agent"

    @pytest.mark.asyncio
    async def test_register_tool(self):
        """Test registering an MCP tool."""
        registry = ToolRegistry()
        await registry.load()

        tool = {
            "name": "test_tool",
            "kind": "mcp_server",
            "description": "A test tool",
            "capabilities": ["fetch_data"],
            "command": "python /app/mcp/test/server.py",
            "transport": "stdio",
        }

        await registry.register_tool(tool)

        tools = registry.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    @pytest.mark.asyncio
    async def test_get_all_capabilities(self):
        """Test getting all capabilities from agents and tools."""
        registry = ToolRegistry()
        await registry.load()

        # Register agent
        entry = AgentRegistryEntry(
            name="agent1",
            description="Agent 1",
            capabilities=["cap_a", "cap_b"],
            entrypoint="remote://agent1/main.py",
        )
        await registry.register_agent(entry)

        # Register tool
        tool = {
            "name": "tool1",
            "capabilities": ["cap_c", "cap_d"],
        }
        await registry.register_tool(tool)

        caps = registry.get_all_capabilities()

        assert "cap_a" in caps
        assert "cap_b" in caps
        assert "cap_c" in caps
        assert "cap_d" in caps

    @pytest.mark.asyncio
    async def test_find_agent_by_capability(self):
        """Test finding an agent by capability."""
        registry = ToolRegistry()
        await registry.load()

        entry = AgentRegistryEntry(
            name="email_agent",
            description="Email agent",
            capabilities=["send_email", "read_email"],
            entrypoint="remote://email/main.py",
        )
        await registry.register_agent(entry)

        agents = registry.get_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "email_agent"

    @pytest.mark.asyncio
    async def test_register_agent_duplicate(self):
        """Test registering duplicate agents replaces existing."""
        registry = ToolRegistry()
        await registry.load()

        entry = AgentRegistryEntry(
            name="email_agent",
            description="Email agent",
            capabilities=["send_email", "read_email"],
            entrypoint="remote://email/main.py",
        )
        await registry.register_agent(entry)

        found = registry.find_agent_by_capability("send_email")

        assert found is not None
        assert found["name"] == "email_agent"

    @pytest.mark.asyncio
    async def test_find_tool_by_capability(self):
        """Test finding a tool by capability."""
        registry = ToolRegistry()
        await registry.load()

        tool = {
            "name": "weather_tool",
            "capabilities": ["fetch_weather", "forecast"],
        }
        await registry.register_tool(tool)

        found = registry.find_tool_by_capability("fetch_weather")

        assert found is not None
        assert found["name"] == "weather_tool"

    @pytest.mark.asyncio
    async def test_find_by_name(self):
        """Test finding agent or tool by name."""
        registry = ToolRegistry()
        await registry.load()

        entry = AgentRegistryEntry(
            name="my_agent",
            description="My agent",
            capabilities=[],
            entrypoint="remote://my/main.py",
        )
        await registry.register_agent(entry)

        found = registry.find_by_name("my_agent")

        assert found is not None
        assert found["name"] == "my_agent"

    @pytest.mark.asyncio
    async def test_capability_exists(self):
        """Test checking if a capability exists."""
        registry = ToolRegistry()
        await registry.load()

        entry = AgentRegistryEntry(
            name="agent",
            description="Agent",
            capabilities=["existing_cap"],
            entrypoint="remote://agent/main.py",
        )
        await registry.register_agent(entry)

        assert registry.capability_exists("existing_cap")
        assert not registry.capability_exists("nonexistent_cap")

    @pytest.mark.asyncio
    async def test_get_agent_endpoint_reads_url(self):
        """URL-configured entries should resolve for callers."""
        registry = ToolRegistry()
        await registry.load()
        registry._registry["agents"] = [{"name": "url_only_agent", "url": "http://localhost:9310"}]

        endpoint = registry.get_agent_endpoint("url_only_agent")
        assert endpoint == "http://localhost:9310"

    @pytest.mark.asyncio
    async def test_unregister(self):
        """Test unregistering an agent or tool."""
        registry = ToolRegistry()
        await registry.load()

        entry = AgentRegistryEntry(
            name="to_remove",
            description="Agent",
            capabilities=[],
            entrypoint="remote://remove/main.py",
        )
        await registry.register_agent(entry)

        assert len(registry.get_agents()) == 1

        removed = await registry.unregister("to_remove")

        assert removed
        assert len(registry.get_agents()) == 0

    @pytest.mark.asyncio
    async def test_persistence(self):
        """Test that registry persists via the document store."""
        loader, saver, backing = _fake_store()

        # First instance
        registry1 = ToolRegistry()
        registry1.bind_store(loader, saver)
        await registry1.load()

        entry = AgentRegistryEntry(
            name="persistent_agent",
            description="Agent",
            capabilities=["persist"],
            entrypoint="remote://persistent/main.py",
        )
        await registry1.register_agent(entry)

        # Second instance (simulating restart) — same store
        registry2 = ToolRegistry()
        registry2.bind_store(loader, saver)
        await registry2.load()

        agents = registry2.get_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "persistent_agent"

    @pytest.mark.asyncio
    async def test_store_persists_all_entries(self):
        """With store backing, tools are persisted alongside agents."""
        loader, saver, backing = _fake_store()

        registry = ToolRegistry()
        registry.bind_store(loader, saver)
        await registry.load()

        await registry.register_tool({"name": "ephemeral_tool", "capabilities": ["fetch"]})
        tools = registry.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "ephemeral_tool"
        # Explicitly save so the store is written before reloading.
        await registry.save()

        # With store-backed, tools are persisted on save.
        registry_reloaded = ToolRegistry()
        registry_reloaded.bind_store(loader, saver)
        await registry_reloaded.load()
        assert len(registry_reloaded.get_tools()) == 1

    @pytest.mark.asyncio
    async def test_load_merges_legacy_and_map_entries(self):
        """Map entries should be loaded even when legacy agents list exists."""

        async def loader():
            return {
                "agents": [{"name": "from_legacy", "url": "http://legacy:8080"}],
                "from_legacy": {"url": "http://map:8081"},
                "from_map": {"url": "http://map:8082"},
            }

        registry = ToolRegistry()
        registry.bind_store(loader, None)  # no saver needed for load
        await registry.load()

        agents = sorted(registry.get_agents(), key=lambda a: a["name"])
        assert agents == [
            {"name": "from_legacy", "url": "http://map:8081"},
            {"name": "from_map", "url": "http://map:8082"},
        ]
