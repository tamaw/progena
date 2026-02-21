"""
DeepAgent factory for Progenitor.

Creates a configured DeepAgent instance that serves as the conversational
layer, using Progenitor's LiteLLM proxy for model calls and wrapping
Progenitor's custom tools.

Phase 1: Basic agent with LangChain model adapter.
Later phases will add tools, middleware, skills, memory, and subagents.
"""

import logging
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from ..config import ProgenitorConfig, get_config
from .langchain_adapter import create_langchain_model

logger = logging.getLogger(__name__)

# System prompt for the Progenitor deep agent.
# This is prepended to DeepAgent's built-in system prompt which includes
# instructions for planning, filesystem, and subagent tools.
PROGENITOR_SYSTEM_PROMPT = """\
You are Progenitor, a perpetual personal assistant and bootstrap builder.

Your capabilities include:
- Answering questions and having conversations
- Building and managing AI agents (A2A protocol)
- Executing shell commands, git operations, and GitHub CLI
- Scheduling tasks (one-off and recurring)
- Managing MCP (Model Context Protocol) tools
- Self-reflection and continuous improvement

When given complex multi-step tasks, break them down using the planning tools.
For code generation tasks, delegate to the build subagent.
For simple questions, answer directly.

Always be concise and action-oriented.
"""


def create_progenitor_agent(
    config: ProgenitorConfig | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    """Create the Progenitor DeepAgent instance.

    Args:
        config: Progenitor configuration. If None, loads from environment.
        checkpointer: LangGraph checkpointer for conversation persistence.
                      If None, uses in-memory checkpointer (dev mode).

    Returns:
        Compiled DeepAgent graph ready for invoke/stream.
    """
    if config is None:
        config = get_config(validate=False)

    # Create LangChain model routed through LiteLLM
    model = create_langchain_model(config.llm)

    # Default to in-memory checkpointer for development
    if checkpointer is None:
        checkpointer = MemorySaver()

    # Phase 1: Basic agent with model and system prompt.
    # Tools, middleware, skills, memory, and subagents added in later phases.
    agent = create_deep_agent(
        name="progenitor",
        model=model,
        system_prompt=PROGENITOR_SYSTEM_PROMPT,
        tools=[],  # Phase 2: custom tools
        checkpointer=checkpointer,
    )

    logger.info(
        "Created Progenitor DeepAgent: model=%s",
        config.llm.model_default,
    )
    return agent
