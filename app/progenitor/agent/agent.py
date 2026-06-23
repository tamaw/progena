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
from ..prompts import load_prompt
from .adapters import (
    DynamicMCPToolMiddleware,
    create_deepagent_tools,
    create_dynamic_mcp_tools,
    create_langchain_model,
)
from .backends import create_progenitor_backend
from .middleware import (
    AsyncSubagentTrackingMiddleware,
    AuditLoggingMiddleware,
    MetricsMiddleware,
    SecretRedactionMiddleware,
)
from .subagents import (
    create_progenitor_async_subagents,
    create_progenitor_subagents,
)

logger = logging.getLogger(__name__)

DEFAULT_RUNTIME_MEMORY_PATH = "/data/memories/AGENTS.md"
DEFAULT_RUNTIME_MEMORY_CONTENT = """# Progenitor Runtime Memory

This file stores durable runtime memory for Progenitor.

## What Belongs Here
- Stable user preferences and working style corrections
- Long-lived tool usage details and identifiers
- Reusable patterns learned from prior conversations

## What Does Not Belong Here
- Temporary status updates
- One-off task requests
- Secrets, API keys, passwords, or access tokens

## Editing Guidance
- Prefer updating this file instead of creating new memory files
- Keep entries concise and durable
- Remove stale or superseded guidance when it no longer applies
"""


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


def _resolve_runtime_memory_paths(
    data_dir: Path,
    explicit_memory_paths: list[str] | None,
) -> list[str] | None:
    """Resolve runtime memory files to backend-visible /data/memories paths."""
    if explicit_memory_paths is not None:
        return explicit_memory_paths or None

    memories_dir = data_dir / "memories"
    discovered: list[str] = []
    if memories_dir.is_dir():
        for memory_file in sorted(memories_dir.rglob("*.md")):
            if not memory_file.is_file():
                continue
            rel_path = memory_file.relative_to(memories_dir).as_posix()
            discovered.append(f"/data/memories/{rel_path}")

    memory_paths: list[str] = [DEFAULT_RUNTIME_MEMORY_PATH]
    for memory_path in discovered:
        if memory_path != DEFAULT_RUNTIME_MEMORY_PATH:
            memory_paths.append(memory_path)
    return memory_paths


def _ensure_default_runtime_memory(data_dir: Path) -> None:
    """Ensure the canonical runtime memory file exists."""
    memory_file = data_dir / "memories" / "AGENTS.md"
    if memory_file.exists():
        return
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text(DEFAULT_RUNTIME_MEMORY_CONTENT, encoding="utf-8")


def create_progenitor_agent(
    config: ProgenitorConfig | None = None,
    checkpointer: Any | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    store: Any | None = None,
    project_root: str | None = None,
    log_sink: Any | None = None,
    skills_dirs: list[str] | None = None,
    memory_paths: list[str] | None = None,
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
               If None, no store is configured by default.
        project_root: Root directory for filesystem backend. Defaults to cwd.
        log_sink: Optional async sink for Postgres audit logs.
        skills_dirs: Paths to skill directories for progressive disclosure.
                     If None, auto-discovers from data/skills/progenitor/.
        memory_paths: Backend-visible AGENTS.md-style prompt-context files loaded
                      via Deep Agents' `memory=` middleware. If None, Progenitor
                      loads runtime memory from `/data/memories/*.md`.
    Returns:
        Compiled DeepAgent graph ready for invoke/stream.
    """
    if config is None:
        resolved_project_root = Path(project_root) if project_root else None
        config = get_config(project_root=resolved_project_root, validate=False)

    resolved_project_root = (
        Path(project_root).expanduser().resolve() if project_root else Path.cwd()
    )
    data_dir = (
        Path(getattr(config.storage, "data_dir", resolved_project_root / "data"))
        .expanduser()
        .resolve()
    )
    state_dir = (
        Path(getattr(config.storage, "state_dir", data_dir / "state")).expanduser().resolve()
    )
    prompts_dir = (
        Path(getattr(config.storage, "prompts_dir", data_dir / "prompts")).expanduser().resolve()
    )
    worktree_dir = (
        Path(getattr(config.storage, "worktree_dir", resolved_project_root / "worktrees"))
        .expanduser()
        .resolve()
    )

    # Create LangChain model routed through Azure endpoint
    model = create_langchain_model(config.llm)

    # Default to SQLite checkpointer for persistence; fall back to MemorySaver
    if checkpointer is None:
        db_path = state_dir / "conversations.db"
        checkpointer = _build_checkpointer(db_path)

    # Writes to /state/mcp.json persist via the StateManager; the runtime's store
    # listener re-discovers MCP tools when the mcp document changes (the same path
    # used for UI-driven server/tool toggles), so no per-backend write-hook is
    # needed. The managing-mcp skill documents this edit flow for the agent.
    backend = create_progenitor_backend(
        project_root=project_root,
        data_dir=data_dir,
        prompts_dir=prompts_dir,
        worktree_dir=worktree_dir,
        state_manager=state_manager,
    )

    # Wrap Progenitor's system tools as DeepAgent-compatible tools
    tools = create_deepagent_tools(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
    )

    # Custom middleware stack (appended after DeepAgent's built-in middleware)
    custom_middleware = [
        DynamicMCPToolMiddleware(mcp_tools_provider=lambda: create_dynamic_mcp_tools(llm_client)),
        SecretRedactionMiddleware(),
        AuditLoggingMiddleware(log_sink=log_sink),
        MetricsMiddleware(),
    ]

    if not prompts_dir.is_dir():
        raise FileNotFoundError(
            f"Configured prompts_dir does not exist: {prompts_dir}. "
            "Set PROGENITOR_DATA_DIR (or legacy PROGENITOR_PROMPTS_DIR) or update config.storage.prompts_dir."
        )

    # Auto-discover skills through the DeepAgent backend mount.
    if skills_dirs is None:
        skills_path = data_dir / "skills" / "progenitor"
        if skills_path.is_dir():
            skills_dirs = ["/data/skills/progenitor"]

    if memory_paths is None:
        _ensure_default_runtime_memory(data_dir)
    memory_paths = _resolve_runtime_memory_paths(data_dir, memory_paths)

    # Use the canonical genesis prompt so every conversation starts with it.
    system_prompt = load_prompt("progenitor/genesis.md", prompts_dir=prompts_dir).strip()
    runtime_config = getattr(config, "runtime", None)
    http_host = getattr(runtime_config, "http_host", "127.0.0.1")
    if http_host in {"0.0.0.0", "::"}:  # nosec B104 - normalize wildcard binds to loopback for local callbacks
        http_host = "127.0.0.1"
    http_port = int(getattr(runtime_config, "http_port", 9501))
    http_base_url = (
        getattr(runtime_config, "http_public_base_url", None) or f"http://{http_host}:{http_port}"
    )
    subagents = create_progenitor_subagents(http_base_url)
    async_subagents = create_progenitor_async_subagents(http_base_url)

    # Deep Agents' `memory=` parameter injects runtime memory from the
    # durable filesystem-backed memories directory.
    custom_middleware.insert(
        3,
        AsyncSubagentTrackingMiddleware(
            state_manager=state_manager,
            async_subagents=async_subagents,
        ),
    )
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
