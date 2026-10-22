"""Primary Progena conversational graph construction.

Flow overview:

        caller
            |
            v
        +---------------------------+
        | create_conversation_graph |
        +---------------------------+
            |
            | resolve config if needed
            v
        +---------------------------+
        | create_progena_agent   |
        +---------------------------+
            |
            v
        +---------------------------+
        | compiled DeepAgent graph  |
        +---------------------------+

In practice, this module does not build routing edges itself.
It delegates the conversational graph to the agent factory and returns it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from src.progena.agent.factory import create_progena_agent
from src.progena.config import ProgenaConfig, get_config

logger = logging.getLogger(__name__)


async def create_conversation_graph(
    config: ProgenaConfig | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    project_root: str | None = None,
    log_sink: Any | None = None,
    live_state_provider: Callable[[], Any] | None = None,
) -> CompiledStateGraph:
    """Create the primary user-facing graph.

    The conversational graph is intentionally the native DeepAgent graph. Runtime
    orchestration wraps it for queueing and streaming, while DeepAgent remains
    the single source of truth for chat behavior, tools, memory, and subagents.
    """
    if config is None:
        config = get_config(validate=False)
    graph = await create_progena_agent(
        config=config,
        checkpointer=checkpointer,
        store=store,
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
        project_root=project_root,
        log_sink=log_sink,
        live_state_provider=live_state_provider,
    )
    logger.info("Created Progena conversation graph")
    return graph
