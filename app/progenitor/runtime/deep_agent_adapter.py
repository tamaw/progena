"""
Runtime adapter: bridges ProgenitorLoop with the DeepAgent.

Instead of replacing the entire runtime (risky), this module provides:
- create_deep_agent_for_loop(): Creates a DeepAgent graph compatible with ProgenitorLoop
- The returned graph supports .astream() with the same interface ProgenitorLoop expects

The DeepAgent is the active conversational graph while ProgenitorLoop handles
queues, scheduling, state persistence, and IPC.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..config import ProgenitorConfig, get_config

logger = logging.getLogger(__name__)


def create_deep_agent_for_loop(
    config: ProgenitorConfig | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    project_root: str | None = None,
    log_dir: str | Path | None = None,
    get_live_state: Callable[[], dict[str, Any] | None] | None = None,
) -> CompiledStateGraph:
    """Create a DeepAgent graph suitable for use as ProgenitorLoop's main graph.

    The returned graph is a compiled LangGraph StateGraph (from create_deep_agent)
    which supports .astream() — the same interface ProgenitorLoop._run_graph_cycle
    already uses. This makes it a drop-in replacement.

    Args:
        config: Progenitor configuration.
        tool_registry: Tool/agent registry for tool context.
        llm_client: LLM client for tools needing AI calls.
        state_manager: StateManager for persistence.
        project_root: Project root for filesystem backend.
        log_dir: Directory for audit logs.
        get_live_state: Optional callback returning ProgenitorLoop's live mutable state.

    Returns:
        CompiledStateGraph that can be assigned to ProgenitorLoop.graph
    """
    from ..agent.deep_agent import create_progenitor_agent

    if config is None:
        config = get_config(validate=False)

    agent = create_progenitor_agent(
        config=config,
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
        project_root=project_root,
        log_dir=log_dir,
        get_live_state=get_live_state,
    )

    logger.info("Created DeepAgent graph for ProgenitorLoop integration")
    return agent
