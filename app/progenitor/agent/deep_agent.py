"""
DeepAgent factory for Progenitor.

Creates a configured DeepAgent instance that serves as the conversational
layer, using Progenitor's LiteLLM proxy for model calls and wrapping
Progenitor's custom tools.

Phase 1: Basic agent with LangChain model adapter.
Phase 2: Custom tools wrapped via tools_adapter.
Later phases will add middleware, skills, memory, and subagents.
"""

import logging
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from ..config import ProgenitorConfig, get_config
from .backends import create_progenitor_backend
from .langchain_adapter import create_langchain_model
from .middleware import AuditLoggingMiddleware, MetricsMiddleware, SecretRedactionMiddleware
from .tools_adapter import create_deepagent_tools

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
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    store: Any | None = None,
    project_root: str | None = None,
    log_dir: str | Path | None = None,
    skills_dirs: list[str] | None = None,
) -> CompiledStateGraph:
    """Create the Progenitor DeepAgent instance.

    Args:
        config: Progenitor configuration. If None, loads from environment.
        checkpointer: LangGraph checkpointer for conversation persistence.
                      If None, uses in-memory checkpointer (dev mode).
        tool_registry: Progenitor's tool/agent registry (for tool context).
        llm_client: Progenitor's LLM client (for tools that need AI).
        state_manager: Progenitor's StateManager (for tools that need persistence).
        store: LangGraph BaseStore for cross-thread memory persistence.
               If None, uses InMemoryStore (dev mode).
        project_root: Root directory for filesystem backend. Defaults to cwd.
        log_dir: Directory for JSONL audit logs. If None, logs to Python logger only.
        skills_dirs: Paths to skill directories for progressive disclosure.
                     If None, auto-discovers from prompts/skills/.

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

    # Default to in-memory store for development
    if store is None:
        from langgraph.store.memory import InMemoryStore
        store = InMemoryStore()

    # Create CompositeBackend: LocalShell + StoreBackend + StateBackend
    backend = create_progenitor_backend(config=config, project_root=project_root)

    # Wrap Progenitor's system tools as DeepAgent-compatible tools
    tools = create_deepagent_tools(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
    )

    # Custom middleware stack (appended after DeepAgent's built-in middleware)
    custom_middleware = [
        SecretRedactionMiddleware(),
        AuditLoggingMiddleware(log_dir=log_dir),
        MetricsMiddleware(),
    ]

    # Auto-discover skills from prompts/skills/ if not provided
    if skills_dirs is None:
        root = Path(project_root) if project_root else Path.cwd()
        skills_path = root / "prompts" / "skills"
        if skills_path.is_dir():
            skills_dirs = [str(skills_path)]

    agent = create_deep_agent(
        name="progenitor",
        model=model,
        system_prompt=PROGENITOR_SYSTEM_PROMPT,
        tools=tools,
        backend=backend,
        store=store,
        skills=skills_dirs,
        middleware=custom_middleware,
        checkpointer=checkpointer,
    )

    logger.info(
        "Created Progenitor DeepAgent: model=%s",
        config.llm.model_default,
    )
    return agent
