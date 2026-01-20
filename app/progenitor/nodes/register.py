"""
Register Node - Updates registry after successful build.
"""

from datetime import datetime
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState, AgentRegistryEntry
from ..runtime.time_utils import utcnow_isoformat


def create_node(tool_registry, state_manager) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the register node function."""

    async def register_node(state: ProgenitorState) -> ProgenitorState:
        """Register new/updated agents and tools."""
        current_item = state.get("current_work_item")
        validation_result = state.get("last_validation_result") or {}

        if not current_item:
            return {}

        # Only register if validation passed
        if not validation_result.get("success", False):
            return {}

        # Get item details
        if isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
            result_data = current_item.get("result", {})
            worktree_path = current_item.get("worktree_path")
        else:
            item_type = current_item.item_type.value if hasattr(current_item.item_type, "value") else current_item.item_type
            result_data = current_item.result or {}
            worktree_path = current_item.worktree_path

        spec = result_data.get("spec", {})

        if item_type == "build_agent":
            # Register new A2A agent
            entry = AgentRegistryEntry(
                name=spec.get("name", "unnamed_agent"),
                kind="a2a_agent",
                description=spec.get("description", ""),
                capabilities=spec.get("capabilities", []),
                entrypoint=worktree_path + "/main.py" if worktree_path else "",
                transport="stdio",
            )

            await tool_registry.register_agent(entry)

            await state_manager.log_decision({
                "event": "agent_registered",
                "agent_name": entry.name,
                "capabilities": entry.capabilities,
                "timestamp": utcnow_isoformat(),
            })

        elif item_type == "build_mcp":
            # Register new MCP tool
            tool_entry = {
                "name": spec.get("name", "unnamed_tool"),
                "kind": "mcp_server",
                "description": spec.get("description", ""),
                "capabilities": spec.get("capabilities", []),
                "command": f"python {worktree_path}/server.py" if worktree_path else "",
                "transport": "stdio",
                "created_at": utcnow_isoformat(),
            }

            await tool_registry.register_tool(tool_entry)

            await state_manager.log_decision({
                "event": "tool_registered",
                "tool_name": tool_entry["name"],
                "capabilities": tool_entry["capabilities"],
                "timestamp": utcnow_isoformat(),
            })

        elif item_type == "upgrade":
            # Update existing registration
            target_name = spec.get("name")
            if target_name:
                existing = tool_registry.find_by_name(target_name)
                if existing:
                    # Merge capabilities
                    new_caps = list(set(
                        existing.get("capabilities", []) +
                        spec.get("capabilities", [])
                    ))
                    existing["capabilities"] = new_caps
                    existing["updated_at"] = utcnow_isoformat()

                    if existing.get("kind") == "a2a_agent":
                        entry = AgentRegistryEntry(**existing)
                        await tool_registry.register_agent(entry)
                    else:
                        await tool_registry.register_tool(existing)

        # Update agents list in state
        agents = tool_registry.get_agents()

        return {"agents": agents}

    return register_node
