"""
LangGraph Topology for Progenitor Agent - Outer Loop.

Handles user-facing chat interactions only:
  Intake → Chat → Respond → CommitCore → END

Backlog execution (work items, builds) runs exclusively in the background graph
(graph_background_loop.py), driven by the background worker in loop.py.
"""

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .nodes import chat, commit_core, intake, respond
from .state.models import ProgenitorState

logger = logging.getLogger(__name__)

# Backwards-compat exports for tests/older imports.
# Routing lives in app.progenitor.routing, but some callers import from graph_chat_loop.
from .routing import route_after_decide  # noqa: E402,F401


def create_progenitor_graph(
    state_manager,
    llm_client,
    tool_registry,
    config,
    worktrees_dir,
    event_pusher=None,
    copilot_acp_manager=None,
):
    """Create the outer loop LangGraph for Progenitor.

    Flow:  Intake → Chat → Respond → CommitCore → END

    Each cycle processes exactly one user message. The background worker
    (ProgenitorLoop._background_worker_loop) drives all backlog/scheduled work
    via the separate background graph.

    Args:
        state_manager: StateManager instance for persistence.
        llm_client: LLMClient instance for model calls.
        tool_registry: ToolRegistry instance for agent/tool discovery.
        config: ProgenitorConfig instance with all configuration.
        worktrees_dir: Path to worktrees directory (unused; kept for API compat).
        event_pusher: Optional callback to push events to clients via IPC.
        copilot_acp_manager: CopilotACPManager instance for ACP communication.

    Returns:
        Compiled StateGraph implementing the outer loop.
    """
    builder = StateGraph(ProgenitorState)

    builder.add_node("intake", intake.create_node(state_manager))
    builder.add_node("chat", chat.create_node(llm_client, tool_registry, state_manager, config))
    builder.add_node("respond", respond.create_node(state_manager))
    builder.add_node("commit_core", commit_core.create_node(state_manager))

    builder.set_entry_point("intake")
    builder.add_edge("intake", "chat")
    builder.add_edge("chat", "respond")
    builder.add_edge("respond", "commit_core")
    builder.add_edge("commit_core", END)

    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    logger.info("Created outer loop graph")
    return graph
