"""Tools module - Copilot CLI, git/worktree helpers, MCP client, agent discovery, schedule."""

from .registry import ToolRegistry
from .copilot import CopilotCLI
from .git import GitWorktreeManager
from .mcp import MCPClient
from .agent_discovery import AgentDiscovery, discover_agents
from .schedule import (
    add_scheduled_task,
    remove_scheduled_task,
    list_scheduled_tasks,
    SCHEDULE_TOOL_SPEC,
)

__all__ = [
    "ToolRegistry",
    "CopilotCLI",
    "GitWorktreeManager",
    "MCPClient",
    "AgentDiscovery",
    "discover_agents",
    "add_scheduled_task",
    "remove_scheduled_task",
    "list_scheduled_tasks",
    "SCHEDULE_TOOL_SPEC",
]
