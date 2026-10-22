"""Tests for the register_acp_agent / register_a2a_agent / unregister tools."""

from __future__ import annotations

from typing import Any

from app.progenitor.acp.agents import parse_acp_connection
from app.progenitor.registry.models import AgentRegistryEntry
from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.register_a2a_agent import RegisterA2AAgentTool
from app.progenitor.tools.register_acp_agent import RegisterACPAgentTool
from app.progenitor.tools.unregister_agent import UnregisterAgentTool


class _FakeRegistry:
    def __init__(self) -> None:
        self.entries: list[AgentRegistryEntry] = []

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        for e in self.entries:
            if e.name == name:
                return e.model_dump()
        return None

    async def register_agent(self, entry: AgentRegistryEntry) -> None:
        self.entries = [e for e in self.entries if e.name != entry.name]
        self.entries.append(entry)

    async def unregister(self, name: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.name != name]
        return len(self.entries) < before


def test_parse_acp_connection_tcp_stdio_and_bare():
    tcp = parse_acp_connection("tcp://192.0.2.10:3000?model=claude-haiku-4.5&cwd=/tmp")
    assert tcp == {
        "model": "claude-haiku-4.5",
        "cwd": "/tmp",
        "host": "192.0.2.10",
        "port": 3000,
        "profile": "copilot",
    }
    assert parse_acp_connection("stdio://copilot") == {"command": "copilot", "profile": "copilot"}
    assert parse_acp_connection("copilot") == {"command": "copilot", "profile": "copilot"}
    assert parse_acp_connection("/opt/claude-acp")["profile"] is None


async def test_register_acp_agent_via_connection_string():
    registry = _FakeRegistry()
    tool = RegisterACPAgentTool()
    context = ToolContext(
        tool_registry=registry,
        llm_client=None,
        tool_args={
            "name": "remote_copilot",
            "connection": "tcp://192.0.2.10:3000?model=claude-haiku-4.5&cwd=/tmp",
        },
    )
    result = await tool.execute(context)
    assert result.success is True
    assert result.data["mode"] == "tcp"
    entry = registry.entries[0]
    assert entry.transport == "acp"
    assert entry.acp is not None
    assert (entry.acp.host, entry.acp.port, entry.acp.model) == (
        "192.0.2.10",
        3000,
        "claude-haiku-4.5",
    )


async def test_register_acp_agent_writes_entry():
    registry = _FakeRegistry()
    tool = RegisterACPAgentTool()
    context = ToolContext(
        tool_registry=registry,
        llm_client=None,
        tool_args={
            "name": "my_acp_agent",
            "command": "copilot",
            "profile": "copilot",
            "model": "gpt-5-mini",
            "capabilities": ["code_generation"],
        },
    )
    result = await tool.execute(context)
    assert result.success is True
    assert len(registry.entries) == 1
    entry = registry.entries[0]
    assert entry.kind == "acp_agent"
    assert entry.transport == "acp"
    assert entry.acp is not None
    assert entry.acp.command == "copilot"
    assert entry.acp.model == "gpt-5-mini"


async def test_register_acp_agent_derives_profile_from_command():
    """A non-copilot command without an explicit profile shouldn't inherit copilot flags."""
    registry = _FakeRegistry()
    tool = RegisterACPAgentTool()
    context = ToolContext(
        tool_registry=registry,
        llm_client=None,
        tool_args={"name": "claude_acp", "command": "claude-acp"},
    )
    result = await tool.execute(context)
    assert result.success is True
    entry = registry.entries[0]
    assert entry.acp is not None
    assert entry.acp.command == "claude-acp"
    assert entry.acp.profile is None


async def test_register_acp_agent_rejects_invalid_timeout():
    registry = _FakeRegistry()
    tool = RegisterACPAgentTool()
    context = ToolContext(
        tool_registry=registry,
        llm_client=None,
        tool_args={"name": "x", "command": "copilot", "timeout_seconds": "soon"},
    )
    result = await tool.execute(context)
    assert result.success is False
    assert "timeout" in (result.error or "").lower()
    assert registry.entries == []


async def test_register_acp_agent_requires_name():
    tool = RegisterACPAgentTool()
    context = ToolContext(tool_registry=_FakeRegistry(), llm_client=None, tool_args={})
    result = await tool.execute(context)
    assert result.success is False


async def test_register_a2a_agent_writes_entry_and_validates_url():
    registry = _FakeRegistry()
    tool = RegisterA2AAgentTool()

    def ctx(args):
        return ToolContext(tool_registry=registry, llm_client=None, tool_args=args)

    ok = await tool.execute(
        ctx(
            {"name": "remote_helper", "url": "http://192.168.0.5:8200/", "capabilities": ["search"]}
        )
    )
    assert ok.success is True
    entry = registry.entries[0]
    assert entry.kind == "a2a_agent"
    assert entry.transport == "http"
    assert entry.url == "http://192.168.0.5:8200"
    assert entry.port == 8200
    assert entry.agent_card_url == "http://192.168.0.5:8200/.well-known/agent-card.json"

    bad = await tool.execute(ctx({"name": "x", "url": "not-a-url"}))
    assert bad.success is False


async def test_unregister_agent_removes_and_reports_missing():
    registry = _FakeRegistry()
    registry.entries.append(AgentRegistryEntry(name="remote_copilot", kind="acp_agent"))

    def ctx(args):
        return ToolContext(tool_registry=registry, llm_client=None, tool_args=args)

    removed = await UnregisterAgentTool().execute(ctx({"name": "remote_copilot"}))
    assert removed.success is True
    assert registry.entries == []

    missing = await UnregisterAgentTool().execute(ctx({"name": "remote_copilot"}))
    assert missing.success is False
