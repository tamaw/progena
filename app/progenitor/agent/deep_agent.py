"""
DeepAgent factory for Progenitor.

Creates a configured DeepAgent instance that serves as the conversational
layer, using Progenitor's Azure endpoint for model calls and wrapping
Progenitor's custom tools.

Uses the LangChain model adapter plus Progenitor's wrapped tools, middleware,
skills, memory files, and native DeepAgent subagents.
"""

import logging
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from ..config import ProgenitorConfig, get_config
from ..llm.prompts import PromptTemplates, resolve_skills_dir
from .backends import create_progenitor_backend
from .langchain_adapter import create_langchain_model
from .middleware import (
    AuditLoggingMiddleware,
    DynamicMCPToolMiddleware,
    MetricsMiddleware,
    SecretRedactionMiddleware,
)
from .subagents import create_progenitor_subagents
from .tools_adapter import create_deepagent_tools

logger = logging.getLogger(__name__)


def _build_checkpointer(db_path: Path) -> Any:
    """Create an AsyncSqliteSaver checkpointer, falling back to MemorySaver.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        AsyncSqliteSaver if langgraph-checkpoint-sqlite is installed, else MemorySaver.
    """
    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import (
            AsyncSqliteSaver,  # pyright: ignore[reportMissingImports]
        )

        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = aiosqlite.connect(str(db_path))
        return AsyncSqliteSaver(conn=conn)
    except ImportError:
        logger.warning(
            "langgraph-checkpoint-sqlite not installed; using in-memory checkpointer. "
            "Install with: pip install langgraph-checkpoint-sqlite"
        )
        return MemorySaver()


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
    memory_paths: list[str] | None = None,
    inner_loop_graph: Any | None = None,
) -> CompiledStateGraph:
    """Create the Progenitor DeepAgent instance.

    Args:
        config: Progenitor configuration. If None, loads from environment.
        checkpointer: LangGraph checkpointer for conversation persistence.
                      If None, uses in-memory checkpointer (dev mode).
        tool_registry: Progenitor's tool/agent registry (for tool context).
        llm_client: Progenitor's LLM client (for tools that need AI).
        state_manager: Progenitor's StateManager (for tools that need persistence).
        store: Optional LangGraph BaseStore for DeepAgent features that need one.
               If None, uses InMemoryStore (dev mode).
        project_root: Root directory for filesystem backend. Defaults to cwd.
        log_dir: Directory for JSONL audit logs. If None, logs to Python logger only.
        skills_dirs: Paths to skill directories for progressive disclosure.
                     If None, auto-discovers from data/skills/progenitor/.
        memory_paths: Paths to memory files (e.g., AGENTS.md).
                      If None, auto-discovers from project root.
        inner_loop_graph: Compiled LangGraph for the inner build loop.
                          If provided, enables the build subagent.

    Returns:
        Compiled DeepAgent graph ready for invoke/stream.
    """
    if config is None:
        resolved_project_root = Path(project_root) if project_root else None
        config = get_config(project_root=resolved_project_root, validate=False)

    # Create LangChain model routed through Azure endpoint
    model = create_langchain_model(config.llm)

    # Default to SQLite checkpointer for persistence; fall back to MemorySaver
    if checkpointer is None:
        db_path = config.storage.state_dir / "conversations.db"
        checkpointer = _build_checkpointer(db_path)

    # Default to in-memory store for DeepAgent features that require a store
    if store is None:
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()

    # Core-only backend: no project filesystem, no shell.
    backend = create_progenitor_backend(project_root=project_root)

    # Wrap Progenitor's system tools as DeepAgent-compatible tools
    tools = create_deepagent_tools(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
    )

    # Custom middleware stack (appended after DeepAgent's built-in middleware)
    custom_middleware = [
        DynamicMCPToolMiddleware(llm_client=llm_client),
        SecretRedactionMiddleware(),
        AuditLoggingMiddleware(log_dir=log_dir),
        MetricsMiddleware(),
    ]

    prompts_dir = Path(config.storage.prompts_dir).expanduser().resolve()
    if not prompts_dir.is_dir():
        raise FileNotFoundError(
            f"Configured prompts_dir does not exist: {prompts_dir}. "
            "Set PROGENITOR_DATA_DIR (or legacy PROGENITOR_PROMPTS_DIR) or update config.storage.prompts_dir."
        )

    # Auto-discover skills from data/skills/progenitor/ if not provided
    if skills_dirs is None:
        skills_path = resolve_skills_dir("progenitor", prompts_dir=prompts_dir)
        if skills_path.is_dir():
            skills_dirs = [str(skills_path)]

    # Auto-discover memory files if not provided
    if memory_paths is None:
        root = Path(project_root) if project_root else Path.cwd()
        memory_paths = []
        agents_md = root / "AGENTS.md"
        if agents_md.is_file():
            memory_paths.append(str(agents_md))

    # Use the canonical genesis prompt so every conversation starts with it.
    system_prompt = PromptTemplates(prompts_dir=prompts_dir).get_genesis_prompt().strip()
    subagents = create_progenitor_subagents(inner_loop_graph=inner_loop_graph)

    agent = create_deep_agent(
        name="progenitor",
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        backend=backend,
        store=store,
        skills=skills_dirs,
        memory=memory_paths or None,
        subagents=subagents,
        middleware=custom_middleware,
        checkpointer=checkpointer,
    )

    logger.info(
        "Created Progenitor DeepAgent: model=%s subagents=%s",
        config.llm.model_default,
        [subagent["name"] for subagent in subagents],
    )
    return agent
