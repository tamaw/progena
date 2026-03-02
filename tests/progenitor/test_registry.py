"""Tests for tool registry."""

import json
import tempfile
from pathlib import Path

import pytest

from app.progenitor.state.models import AgentRegistryEntry
from app.progenitor.tools.registry import ToolRegistry


class TestToolRegistry:
    """Tests for the ToolRegistry class."""

    @pytest.fixture
    def temp_registry(self):
        """Create isolated temporary registry + mcp files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry_path = root / "agents.json"
            mcp_path = root / "mcp.json"
            registry_path.write_text(json.dumps({"agents": []}), encoding="utf-8")
            mcp_path.write_text(json.dumps({"tools": []}), encoding="utf-8")
            yield registry_path

    @pytest.mark.asyncio
    async def test_load_empty_registry(self, temp_registry):
        """Test loading an empty registry."""
        registry = ToolRegistry(temp_registry)
        await registry.load()

        assert registry.get_agents() == []
        assert registry.get_tools() == []

    @pytest.mark.asyncio
    async def test_register_agent(self, temp_registry):
        """Test registering an agent."""
        registry = ToolRegistry(temp_registry)
        await registry.load()

        entry = AgentRegistryEntry(
            name="test_agent",
            description="A test agent",
            capabilities=["test_cap"],
            entrypoint="/app/agents/test/main.py",
        )

        await registry.register_agent(entry)

        agents = registry.get_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "test_agent"

    @pytest.mark.asyncio
    async def test_register_tool(self, temp_registry):
        """Test registering an MCP tool."""
        registry = ToolRegistry(temp_registry)
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
    async def test_get_all_capabilities(self, temp_registry):
        """Test getting all capabilities from agents and tools."""
        registry = ToolRegistry(temp_registry)
        await registry.load()

        # Register agent
        entry = AgentRegistryEntry(
            name="agent1",
            description="Agent 1",
            capabilities=["cap_a", "cap_b"],
            entrypoint="/app/agents/1/main.py",
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
    async def test_find_agent_by_capability(self, temp_registry):
        """Test finding an agent by capability."""
        registry = ToolRegistry(temp_registry)
        await registry.load()

        entry = AgentRegistryEntry(
            name="email_agent",
            description="Email agent",
            capabilities=["send_email", "read_email"],
            entrypoint="/app/agents/email/main.py",
        )
        await registry.register_agent(entry)

        agents = registry.get_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "email_agent"

    @pytest.mark.asyncio
    async def test_register_agent_duplicate(self, temp_registry):
        """Test registering duplicate agents replaces existing."""
        registry = ToolRegistry(temp_registry)
        await registry.load()

        entry = AgentRegistryEntry(
            name="email_agent",
            description="Email agent",
            capabilities=["send_email", "read_email"],
            entrypoint="/app/agents/email/main.py",
        )
        await registry.register_agent(entry)

        found = registry.find_agent_by_capability("send_email")

        assert found is not None
        assert found["name"] == "email_agent"

    @pytest.mark.asyncio
    async def test_find_tool_by_capability(self, temp_registry):
        """Test finding a tool by capability."""
        registry = ToolRegistry(temp_registry)
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
    async def test_find_by_name(self, temp_registry):
        """Test finding agent or tool by name."""
        registry = ToolRegistry(temp_registry)
        await registry.load()

        entry = AgentRegistryEntry(
            name="my_agent",
            description="My agent",
            capabilities=[],
            entrypoint="/app/agents/my/main.py",
        )
        await registry.register_agent(entry)

        found = registry.find_by_name("my_agent")

        assert found is not None
        assert found["name"] == "my_agent"

    @pytest.mark.asyncio
    async def test_capability_exists(self, temp_registry):
        """Test checking if a capability exists."""
        registry = ToolRegistry(temp_registry)
        await registry.load()

        entry = AgentRegistryEntry(
            name="agent",
            description="Agent",
            capabilities=["existing_cap"],
            entrypoint="/app/agents/agent/main.py",
        )
        await registry.register_agent(entry)

        assert registry.capability_exists("existing_cap")
        assert not registry.capability_exists("nonexistent_cap")

    @pytest.mark.asyncio
    async def test_get_agent_endpoint_reads_url(self, temp_registry):
        """URL-configured entries should resolve for callers."""
        registry = ToolRegistry(temp_registry)
        await registry.load()
        registry._registry["agents"] = [{"name": "url_only_agent", "url": "http://localhost:9310"}]

        endpoint = registry.get_agent_endpoint("url_only_agent")
        assert endpoint == "http://localhost:9310"

    @pytest.mark.asyncio
    async def test_unregister(self, temp_registry):
        """Test unregistering an agent or tool."""
        registry = ToolRegistry(temp_registry)
        await registry.load()

        entry = AgentRegistryEntry(
            name="to_remove",
            description="Agent to remove",
            capabilities=[],
            entrypoint="/app/agents/remove/main.py",
        )
        await registry.register_agent(entry)

        assert len(registry.get_agents()) == 1

        removed = await registry.unregister("to_remove")

        assert removed
        assert len(registry.get_agents()) == 0

    @pytest.mark.asyncio
    async def test_persistence(self, temp_registry):
        """Test that registry persists to disk."""
        # First instance
        registry1 = ToolRegistry(temp_registry)
        await registry1.load()

        entry = AgentRegistryEntry(
            name="persistent_agent",
            description="Agent",
            capabilities=["persist"],
            entrypoint="/app/agents/persist/main.py",
        )
        await registry1.register_agent(entry)

        # Second instance (simulating restart)
        registry2 = ToolRegistry(temp_registry)
        await registry2.load()

        agents = registry2.get_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "persistent_agent"

    @pytest.mark.asyncio
    async def test_load_merges_legacy_and_map_entries(self, temp_registry):
        """Map entries should be loaded even when legacy agents list exists."""
        temp_registry.write_text(
            json.dumps(
                {
                    "agents": [{"name": "from_legacy", "url": "http://legacy:8080"}],
                    "from_legacy": {"url": "http://map:8081"},
                    "from_map": {"url": "http://map:8082"},
                }
            ),
            encoding="utf-8",
        )
        registry = ToolRegistry(temp_registry)
        await registry.load()

        agents = sorted(registry.get_agents(), key=lambda a: a["name"])
        assert agents == [
            {"name": "from_legacy", "url": "http://map:8081"},
            {"name": "from_map", "url": "http://map:8082"},
        ]
