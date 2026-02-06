"""
Inner Loop Graph for Build Operations.

Implements the build loop per loop.spec.md:
OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN → (repeat until done)

This graph runs asynchronously from the main loop, managing build_state.json.
The outer loop continues to handle user interactions while builds run in background.
"""

import logging
from typing import Literal, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state.models import ProgenitorState
from .routing import (
    ensure_ideal_state,
    should_iterate,
    should_continue_inner_loop,
    route_after_verify,
)
from .constants import DEFAULT_MAX_INNER_ITERATIONS

logger = logging.getLogger(__name__)


def create_inner_loop_graph(
    state_manager,
    llm_client,
    config,
    worktrees_dir,
    copilot_acp_manager=None,
):
    """Create the inner loop graph for build operations.

    Args:
        state_manager: StateManager for build_state.json persistence.
        llm_client: LLM client for model calls.
        config: ProgenitorConfig with build settings.
        worktrees_dir: Path to worktrees directory.
        copilot_acp_manager: CopilotACPManager for ACP communication.

    Returns:
        Compiled StateGraph implementing:
        OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN → (loop)
    """
    from .nodes import (
        observe,
        think,
        planner,
        execute,
        learn,
        verify_loop,
        build,
    )
    from .tools.copilot import CopilotCLI
    from .tools.registry import ToolRegistry
    from pathlib import Path

    if copilot_acp_manager is None:
        raise RuntimeError(
            "copilot_acp_manager is required for inner loop graph. "
            "Ensure CopilotACPManager is initialized."
        )

    # Initialize tools needed for build
    copilot_cli = CopilotCLI(
        config_path=config.copilot.config_path,
        acp_manager=copilot_acp_manager,
        timeout=config.copilot.timeout_seconds,
        logs_dir=config.storage.log_dir,
    )

    # Tool registry for execute phase
    registry_path = config.storage.state_dir / "registry.json"
    tool_registry = ToolRegistry(registry_path)

    # Get project root (parent of app directory) for agent discovery
    project_root = Path(__file__).parent.parent.parent

    # Create node functions for inner loop
    # Use the new build.create_node from nodes/build.py
    nodes = {
        "observe": observe.create_node(llm_client, state_manager),
        "think": think.create_node(llm_client, state_manager),
        "plan": planner.create_node(llm_client, state_manager),
        "build": build.create_node(llm_client, copilot_cli, worktrees_dir, state_manager),
        "execute": execute.create_node(tool_registry, state_manager, project_root),
        "verify": verify_loop.create_node(llm_client, state_manager),
        "learn": learn.create_node(llm_client, state_manager),
    }

    # Build the graph
    builder = StateGraph(ProgenitorState)

    # Add all nodes
    for name, node_fn in nodes.items():
        builder.add_node(name, node_fn)

    # Set entry point
    builder.set_entry_point("observe")

    # Define edges for the inner loop
    # OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → [LEARN or DONE]
    builder.add_edge("observe", "think")
    builder.add_edge("think", "plan")
    builder.add_edge("plan", "build")
    builder.add_edge("build", "execute")
    builder.add_edge("execute", "verify")

    # After verify, either learn (continue) or done (exit)
    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "learn": "learn",
            "done": END,
        }
    )

    # After learn, check if we should continue
    builder.add_conditional_edges(
        "learn",
        should_continue_inner_loop,
        {
            "observe": "observe",
            "done": END,
        }
    )

    # Compile with checkpointing
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    logger.info("Inner loop graph created with nodes: %s", list(nodes.keys()))
    return graph
