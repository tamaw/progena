"""
Register Node - Updates registry after successful build.

Also tests and activates newly built agents for immediate interaction.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Any, TYPE_CHECKING

from ..state.models import ProgenitorState, AgentRegistryEntry
from ..runtime.time_utils import utcnow_isoformat
from ..runtime.agent_runner import AgentRunner

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)


def create_node(
    tool_registry,
    state_manager,
    config: "ProgenitorConfig" = None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the register node function.

    Args:
        tool_registry: Registry for agents and tools.
        state_manager: State manager for logging decisions.
        config: ProgenitorConfig for project root path.
    """
    # Initialize agent runner for testing newly built agents
    # Derive project root from worktrees_dir (worktrees is a subdirectory)
    if config and hasattr(config, 'storage') and config.storage.worktree_dir:
        project_root = config.storage.worktree_dir.parent
    else:
        project_root = Path.cwd()

    agent_runner = AgentRunner(
        project_root=project_root,
        log_callback=state_manager.log_decision if state_manager else None,
    )

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
            # Register new A2A agent (HTTP-based)
            agent_name = spec.get("name", "unnamed_agent")
            # Use new directory structure: app/agents/<agent_name>/agent.py
            entrypoint = f"app/agents/{agent_name}/agent.py"

            # Allocate a port for the HTTP agent
            port = tool_registry.allocate_port()

            entry = AgentRegistryEntry(
                name=agent_name,
                kind="a2a_agent",
                description=spec.get("description", ""),
                capabilities=spec.get("capabilities", []),
                entrypoint=entrypoint,
                transport="http",  # HTTP by default
                port=port,
                endpoint=f"http://localhost:{port}",
                agent_card_url=f"http://localhost:{port}/.well-known/agent.json",
            )

            await tool_registry.register_agent(entry)

            # Test the newly built agent to ensure it works
            logger.info(f"Testing newly built agent '{agent_name}'...")
            test_result = await agent_runner.test_agent(
                agent_name=agent_name,
                entrypoint=entrypoint,
            )

            agent_test_passed = test_result.success
            agent_test_response = test_result.response
            agent_test_error = test_result.error

            await state_manager.log_decision({
                "event": "agent_tested",
                "agent_name": agent_name,
                "test_passed": agent_test_passed,
                "test_response": str(agent_test_response)[:500] if agent_test_response else None,
                "test_error": agent_test_error,
                "duration_ms": test_result.duration_ms,
                "timestamp": utcnow_isoformat(),
            })

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

        # Build result including agent test info if applicable
        result = {"agents": agents}

        # Include agent runner reference and test results for immediate interaction
        if item_type == "build_agent":
            result["agent_runner"] = agent_runner
            result["new_agent_ready"] = {
                "name": agent_name,
                "entrypoint": entrypoint,
                "test_passed": agent_test_passed,
                "test_response": agent_test_response,
                "test_error": agent_test_error,
            }

        return result

    return register_node
