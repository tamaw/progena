"""
DeepAgent factory for Progenitor.

Creates a configured DeepAgent instance that serves as the conversational
layer, using Progenitor's Azure endpoint for model calls and wrapping
Progenitor's custom tools.

Uses the LangChain model adapter plus Progenitor's wrapped tools, middleware,
skills, memory files, and native DeepAgent subagents.
"""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from ..config import ProgenitorConfig, get_config
from ..llm.prompts import PromptTemplates, resolve_skills_dir
from .backends import create_progenitor_backend, runtime_memory_namespace
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

DEFAULT_RUNTIME_MEMORY_PATH = "/memories/AGENTS.md"
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


def _build_store(db_path: Path) -> Any:
    """Create a persistent SQLite store, falling back to in-memory store."""
    try:
        from langgraph.store.sqlite import SqliteStore

        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        store = SqliteStore(conn)
        store.setup()
        return store
    except ImportError:
        logger.warning(
            "langgraph store sqlite backend not installed; using in-memory store for /memories"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to initialize SQLite store at %s; using in-memory store: %s",
            db_path,
            exc,
        )

    from langgraph.store.memory import InMemoryStore

    return InMemoryStore()


def _runtime_memory_store_namespace() -> tuple[str, ...]:
    """Resolve the namespace used for persisted runtime memory documents."""
    return runtime_memory_namespace(None)


def _store_file_data(content: str, *, created_at: str | None = None) -> dict[str, Any]:
    """Serialize markdown content using StoreBackend's file representation."""
    now = datetime.now(UTC).isoformat()
    return {
        "content": content.split("\n"),
        "created_at": created_at or now,
        "modified_at": now,
    }


def _list_store_memory_paths(store: Any) -> list[str]:
    """Discover markdown memory files persisted in the LangGraph store."""
    namespace = _runtime_memory_store_namespace()
    memory_paths: list[str] = []
    offset = 0
    limit = 100

    while True:
        items = store.search(namespace, limit=limit, offset=offset)
        if not items:
            break

        for item in items:
            key = getattr(item, "key", None)
            if isinstance(key, str) and key.endswith(".md"):
                memory_paths.append(f"/memories/{key.lstrip('/')}")

        if len(items) < limit:
            break
        offset += len(items)

    return sorted(set(memory_paths))


def _resolve_runtime_memory_paths(
    data_dir: Path,
    explicit_memory_paths: list[str] | None,
    store: Any | None,
) -> list[str] | None:
    """Resolve runtime memory files to backend-visible /memories paths."""
    if explicit_memory_paths is not None:
        return explicit_memory_paths or None

    if store is not None:
        discovered = _list_store_memory_paths(store)
    else:
        memories_dir = data_dir / "memories"
        discovered = []
        if memories_dir.is_dir():
            for memory_file in sorted(memories_dir.rglob("*.md")):
                if not memory_file.is_file():
                    continue
                rel_path = memory_file.relative_to(memories_dir).as_posix()
                discovered.append(f"/memories/{rel_path}")

    memory_paths: list[str] = [DEFAULT_RUNTIME_MEMORY_PATH]
    for memory_path in discovered:
        if memory_path != DEFAULT_RUNTIME_MEMORY_PATH:
            memory_paths.append(memory_path)
    return memory_paths


def _migrate_disk_memories_to_store(data_dir: Path, store: Any) -> None:
    """Copy legacy disk-backed /memories files into the persistent store."""
    memories_dir = data_dir / "memories"
    if not memories_dir.is_dir():
        return

    namespace = _runtime_memory_store_namespace()
    uploads: list[tuple[str, bytes]] = []

    for file_path in sorted(memories_dir.rglob("*")):
        if not file_path.is_file():
            continue

        store_path = f"/{file_path.relative_to(memories_dir).as_posix()}"
        if store.get(namespace, store_path) is None:
            uploads.append((store_path, file_path.read_bytes()))

    if uploads:
        for store_path, content in uploads:
            store.put(
                namespace,
                store_path,
                _store_file_data(content.decode("utf-8")),
            )


def _ensure_default_runtime_memory(data_dir: Path, store: Any | None) -> None:
    """Ensure the canonical runtime memory file exists."""
    if store is not None:
        namespace = _runtime_memory_store_namespace()
        if store.get(namespace, "/AGENTS.md") is None:
            store.put(
                namespace,
                "/AGENTS.md",
                _store_file_data(DEFAULT_RUNTIME_MEMORY_CONTENT),
            )
        return

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
               If None, uses a persistent SQLite store when available.
        project_root: Root directory for filesystem backend. Defaults to cwd.
        log_dir: Directory for JSONL audit logs. If None, logs to Python logger only.
        skills_dirs: Paths to skill directories for progressive disclosure.
                     If None, auto-discovers from data/skills/progenitor/.
        memory_paths: Backend-visible AGENTS.md-style prompt-context files loaded
                      via Deep Agents' `memory=` middleware. If None, Progenitor
                      loads runtime memory from `/memories/*.md`.
        inner_loop_graph: Compiled LangGraph for the inner build loop.
                          If provided, enables the build subagent.

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
    log_root = (
        Path(getattr(config.storage, "log_dir", resolved_project_root / "logs"))
        .expanduser()
        .resolve()
    )
    prompts_dir = (
        Path(getattr(config.storage, "prompts_dir", data_dir / "prompts")).expanduser().resolve()
    )

    # Create LangChain model routed through Azure endpoint
    model = create_langchain_model(config.llm)

    # Default to SQLite checkpointer for persistence; fall back to MemorySaver
    if checkpointer is None:
        db_path = state_dir / "conversations.db"
        checkpointer = _build_checkpointer(db_path)

    # Default to a persistent SQLite store for /memories and other DeepAgent
    # features that rely on LangGraph store semantics.
    if store is None:
        store = _build_store(state_dir / "store.db")

    # Core-only backend: no project filesystem, no shell.
    backend = create_progenitor_backend(
        project_root=project_root,
        data_dir=data_dir,
        log_dir=log_root,
        prompts_dir=prompts_dir,
        store=store,
    )

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

    if store is not None:
        _migrate_disk_memories_to_store(data_dir, store)
        _ensure_default_runtime_memory(data_dir, store)
    elif memory_paths is None:
        _ensure_default_runtime_memory(data_dir, store)
    memory_paths = _resolve_runtime_memory_paths(data_dir, memory_paths, store)

    # Use the canonical genesis prompt so every conversation starts with it.
    system_prompt = PromptTemplates(prompts_dir=prompts_dir).get_genesis_prompt().strip()
    subagents = create_progenitor_subagents(inner_loop_graph=inner_loop_graph)

    # Deep Agents' `memory=` parameter injects runtime memory from persisted
    # /memories documents using backend-visible paths.
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
