"""
Inner Loop Graph for Build Operations.

Implements the build loop per loop.spec.md:
OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN → (repeat until done)
                                                 ↓ (on pass)
                                              PUBLISH → DONE (raise PR)

This graph runs asynchronously from the main loop, managing build_state.json.
The outer loop continues to handle user interactions while builds run in background.
"""

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .routing import (
    route_after_verify,
    should_continue_inner_loop,
)
from .state.models import ProgenitorState

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
    from pathlib import Path

    from .nodes import (
        build,
        execute,
        learn,
        merge,
        observe,
        planner,
        publish,
        think,
        verify_loop,
    )
    from .tools.copilot import CopilotCLI
    from .tools.git import GitWorktreeManager
    from .tools.registry import ToolRegistry

    if copilot_acp_manager is None:
        raise RuntimeError(
            "copilot_acp_manager is required for inner loop graph. "
            "Ensure CopilotACPManager is initialized."
        )

    # Initialize tools needed for build
    copilot_cli = CopilotCLI(
        copilot_cmd=config.copilot.bin_path,
        model=config.copilot.model,
        default_flags=config.copilot.default_flags,
        allowed_dirs=config.copilot.acp_allowed_dirs,
        acp_manager=copilot_acp_manager,
        timeout=config.copilot.timeout_seconds,
        logs_dir=config.storage.log_dir,
    )

    # Tool registry for execute phase
    registry_path = config.storage.state_dir / "registry.json"
    tool_registry = ToolRegistry(registry_path)

    # Get project root (repo root; parent of the `app/` directory).
    project_root = Path(__file__).resolve().parents[2]

    # Git worktree manager for concurrent builds
    # Ensure git operations run against the repo root (not the current working directory).
    git_manager = GitWorktreeManager(worktrees_dir, repo_path=project_root)

    # Create node functions for inner loop
    # Use the new build.create_node from nodes/build.py
    nodes = {
        "observe": observe.create_node(llm_client, state_manager, config),
        "think": think.create_node(llm_client, state_manager),
        "plan": planner.create_node(llm_client, state_manager),
        "build": build.create_node(llm_client, copilot_cli, worktrees_dir, state_manager, git_manager),
        "merge": merge.create_node(llm_client, git_manager, state_manager),
        "execute": execute.create_node(tool_registry, state_manager, project_root),
        "verify": verify_loop.create_node(llm_client, state_manager),
        "publish": publish.create_node(state_manager, worktrees_dir),
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
    # OBSERVE → THINK → PLAN → BUILD → MERGE → EXECUTE → VERIFY → [LEARN or DONE]
    builder.add_edge("observe", "think")
    builder.add_edge("think", "plan")
    builder.add_edge("plan", "build")
    builder.add_edge("build", "merge")
    builder.add_edge("merge", "execute")
    builder.add_edge("execute", "verify")

    # After verify: publish (pass/max-iter), learn (continue), or done (cancel/error)
    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "publish": "publish",
            "learn": "learn",
            "done": END,
        }
    )

    # After publish, we're done
    builder.add_edge("publish", END)

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
