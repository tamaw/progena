"""
Runtime adapter: bridges ProgenitorLoop with the DeepAgent.

Instead of replacing the entire runtime (risky), this module provides:
- create_deep_agent_for_loop(): Creates a DeepAgent graph compatible with ProgenitorLoop
- The returned graph supports .astream() with the same interface ProgenitorLoop expects

This is the Phase 8 integration point: the DeepAgent becomes the conversational
graph while the background loop and build loop remain unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..config import ProgenitorConfig, get_config
from .loop import ProgenitorLoop

logger = logging.getLogger(__name__)


def create_deep_agent_for_loop(
    config: ProgenitorConfig | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    inner_loop_graph: Any | None = None,
    project_root: str | None = None,
    log_dir: str | Path | None = None,
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
        inner_loop_graph: Compiled inner build loop graph (for build subagent).
        project_root: Project root for filesystem backend.
        log_dir: Directory for audit logs.

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
        inner_loop_graph=inner_loop_graph,
        project_root=project_root,
        log_dir=log_dir,
    )

    logger.info("Created DeepAgent graph for ProgenitorLoop integration")
    return agent


def patch_loop_with_deep_agent(
    loop: ProgenitorLoop,
    config: ProgenitorConfig | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    inner_loop_graph: Any | None = None,
    project_root: str | None = None,
    log_dir: str | Path | None = None,
) -> ProgenitorLoop:
    """Replace a ProgenitorLoop's main graph with a DeepAgent.

    This patches the loop's conversational graph in-place, enabling DeepAgent
    features (middleware, skills, memory, subagents, auto-summarization) while
    keeping the background loop, scheduling, and build infrastructure intact.

    Args:
        loop: Existing ProgenitorLoop instance to patch.
        config: Progenitor configuration.
        tool_registry: Tool/agent registry.
        llm_client: LLM client.
        state_manager: StateManager.
        inner_loop_graph: Inner build loop graph.
        project_root: Project root.
        log_dir: Audit log directory.

    Returns:
        The same loop instance with its graph replaced.
    """
    deep_agent = create_deep_agent_for_loop(
        config=config or loop.config,
        tool_registry=tool_registry,
        llm_client=llm_client or loop.llm_client,
        state_manager=state_manager or loop.state_manager,
        inner_loop_graph=inner_loop_graph,
        project_root=project_root,
        log_dir=log_dir,
    )

    loop.graph = deep_agent
    logger.info("Patched ProgenitorLoop with DeepAgent graph")
    return loop
