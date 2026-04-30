"""Main Progenitor graph factory."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..config import ProgenitorConfig, get_config

logger = logging.getLogger(__name__)


def create_main_graph(
    config: ProgenitorConfig | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    project_root: str | None = None,
    log_dir: str | Path | None = None,
    get_live_state: Callable[[], dict[str, Any] | None] | None = None,
) -> CompiledStateGraph:
    """Create the main Progenitor graph."""
    from ..agent.deep_agent import create_progenitor_agent

    if config is None:
        config = get_config(validate=False)

    graph = create_progenitor_agent(
        config=config,
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
        project_root=project_root,
        log_dir=log_dir,
        get_live_state=get_live_state,
    )

    logger.info("Created main Progenitor graph")
    return graph
