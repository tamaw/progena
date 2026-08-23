"""
DeepAgent factory for Progenitor.

Creates a configured DeepAgent instance that serves as the conversational
layer, using Progenitor's Azure endpoint for model calls and wrapping
Progenitor's custom tools.

Uses the LangChain model adapter plus Progenitor's wrapped tools, middleware,
skills, memory files, and native DeepAgent subagents.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.subagents import CompiledSubAgent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from ..config import ProgenitorConfig, get_config
from ..content_documents import ensure_content_documents_seeded
from ..routines.graph import validate_routine_graph_payload
from ..routines.revisions import (
    NullRoutineRevisionReader,
    PostgresRoutineRevisionReader,
)
from ..routines.subagent import create_routine_subagent
from .adapters import (
    create_deepagent_tools,
    create_dynamic_mcp_tools,
    create_langchain_model,
)
from .backends import create_progenitor_backend
from .middleware import (
    AsyncSubagentTrackingMiddleware,
    AuditLoggingMiddleware,
    DirectA2AFastPathMiddleware,
    DynamicMCPToolMiddleware,
    SchemaCompactionMiddleware,
    SecretRedactionMiddleware,
)
from .object_agents import create_object_agent_proxy, discover_object_agents
from .shell_backend import ShellPaths
from .state import ProgenitorAgentState
from .subagents import create_progenitor_async_subagents

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


async def _resolve_runtime_memory_paths(
    state_manager: Any,
    explicit_memory_paths: list[str] | None,
) -> list[str] | None:
    """Resolve runtime memory documents to backend-visible /memories paths."""
    if explicit_memory_paths is not None:
        return explicit_memory_paths or None

    rows = await state_manager.list_content_documents("memory")
    discovered = sorted(f"/memories/{relative_path}" for relative_path, _content in rows)

    memory_paths: list[str] = [DEFAULT_RUNTIME_MEMORY_PATH]
    for memory_path in discovered:
        if memory_path != DEFAULT_RUNTIME_MEMORY_PATH:
            memory_paths.append(memory_path)
    return memory_paths


async def _ensure_default_runtime_memory(state_manager: Any) -> None:
    """Ensure the canonical runtime memory document exists."""
    existing = await state_manager.get_content_document("memory", "AGENTS.md")
    if existing is not None:
        return
    await state_manager.save_content_document("memory", "AGENTS.md", DEFAULT_RUNTIME_MEMORY_CONTENT)


async def create_progenitor_agent(
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
    live_state_provider: Callable[[], Any] | None = None,
) -> CompiledStateGraph:
    """Create the Progenitor DeepAgent instance.

    Args:
        config: Progenitor configuration. If None, loads from environment.
        checkpointer: LangGraph checkpointer for conversation persistence.
                      If None, uses in-memory checkpointer (dev mode).
        tool_registry: Progenitor's tool/agent registry (for tool context).
        llm_client: Progenitor's LLM client (for tools that need AI).
        state_manager: Progenitor's StateManager. Required: prompts and runtime
                       memories are Postgres-backed content documents (see
                       ``content_documents.py``), installed marketplace-plugin
                       skills are Postgres-backed too, and the genesis system
                       prompt is loaded from there.
        store: Optional LangGraph BaseStore for DeepAgent features that need one.
               If None, no store is configured by default.
        project_root: Root directory for filesystem backend. Defaults to cwd.
        log_sink: Optional async sink for Postgres audit logs.
        skills_dirs: Paths to skill directories for progressive disclosure.
                     If None, auto-discovers: ``/skills`` (disk-backed) for
                     progenitor's own native skills when present, plus one
                     ``/skills/<marketplace>__<plugin>`` (Postgres-backed)
                     source per installed marketplace plugin.
        memory_paths: Backend-visible AGENTS.md-style prompt-context files loaded
                      via Deep Agents' `memory=` middleware. If None, Progenitor
                      loads runtime memory from the Postgres-backed `/memories/*`
                      content documents.
        live_state_provider: Callable returning the running loop's live state
                             dict for tool execution; falls back to the
                             state manager snapshot when None.
    Returns:
        Compiled DeepAgent graph ready for invoke/stream.
    """
    if state_manager is None:
        raise ValueError(
            "create_progenitor_agent requires state_manager: prompts and runtime "
            "memories are Postgres-backed content documents with no filesystem "
            "fallback, and installed marketplace-plugin skills are Postgres-backed too."
        )

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
    # Not a runtime mount -- only the one-time Postgres seed source (see
    # content_documents.ensure_content_documents_seeded), baked into the
    # container image via `COPY data/prompts/``.
    prompts_seed_dir = (
        Path(getattr(config.storage, "prompts_dir", data_dir / "prompts")).expanduser().resolve()
    )
    # A real runtime mount (/skills/, see backends.py) -- progenitor's own
    # skills are static, baked into the image, and never seeded into Postgres.
    skills_dir = data_dir / "skills"
    worktree_dir = (
        Path(getattr(config.storage, "worktree_dir", resolved_project_root / "data" / "repos"))
        .expanduser()
        .resolve()
    )

    # Create LangChain model routed through Azure endpoint
    model = create_langchain_model(config.llm)

    # Default to SQLite checkpointer for persistence; fall back to MemorySaver
    if checkpointer is None:
        db_path = state_dir / "conversations.db"
        checkpointer = _build_checkpointer(db_path)

    # Discover installed marketplace-plugin skill segments (see
    # plugins/install.py) up front so the backend can mount one Postgres-backed
    # /skills/<segment>/ route per plugin. The skills_dirs auto-discovery below
    # reuses these same rows -- only one query.
    skill_rows = await state_manager.list_content_documents("skill")
    agent_rows = await state_manager.list_content_documents("agent")
    plugin_segments = sorted(
        {
            rel.split("/", 1)[0]
            for rel, _content in skill_rows
            if "/" in rel and "__" in rel.split("/", 1)[0]
        }
    )
    plugin_agent_segments = sorted(
        {
            rel.split("/", 1)[0]
            for rel, _content in agent_rows
            if "/" in rel and "__" in rel.split("/", 1)[0]
        }
    )

    # Writes to /config/mcp.json persist via the StateManager; the runtime's store
    # listener re-discovers MCP tools when the mcp document changes (the same path
    # used for UI-driven server/tool toggles), so no per-backend write-hook is
    # needed. The managing-mcp skill documents this edit flow for the agent.
    database_url = getattr(getattr(config, "database", None), "url", None) or None
    backend = create_progenitor_backend(
        project_root=project_root,
        skills_dir=skills_dir,
        agents_dir=data_dir / "agents",
        worktree_dir=worktree_dir,
        state_manager=state_manager,
        database_url=database_url,
        plugin_skill_segments=plugin_segments,
        plugin_agent_segments=plugin_agent_segments,
    )

    # Wrap Progenitor's system tools as DeepAgent-compatible tools
    tools = create_deepagent_tools(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
        live_state_provider=live_state_provider,
    )

    custom_middleware = [
        DirectA2AFastPathMiddleware(tool_registry=tool_registry),
        DynamicMCPToolMiddleware(mcp_tools_provider=lambda: create_dynamic_mcp_tools(llm_client)),
        SchemaCompactionMiddleware(),
        SecretRedactionMiddleware(),
        AuditLoggingMiddleware(log_sink=log_sink),
    ]

    # One-time bootstrap: populate Postgres from the image's baked-in prompt
    # files if the store is empty (fresh database). No-op afterward. Skills
    # are never seeded -- progenitor's own are a disk mount (see backends.py)
    # and marketplace-plugin skills are written directly by plugins/install.py.
    await ensure_content_documents_seeded(state_manager, "prompt", prompts_seed_dir)

    # Auto-discover skills through the DeepAgent backend mount. Progenitor's
    # own skills are flat under /skills/<skill-name>/SKILL.md (disk-backed, no
    # segment prefix), so /skills itself is a source whenever that directory
    # has any skills. Installed marketplace plugins are namespaced
    # /skills/<marketplace>__<plugin>/<skill-name>/SKILL.md (Postgres-backed,
    # see plugins/install.py) and each becomes its own additional source.
    # /skills is always first (the agent's own management skills are never
    # shadowed by a third-party plugin); plugin sources are sorted after.
    # Name conflicts across sources are allowed, not rejected -- SkillsMiddleware
    # dedupes by frontmatter `name` with silent last-source-wins, matching
    # every other Agent Skills host; source order only controls which source
    # wins a collision, deterministically.
    if skills_dirs is None:
        has_native_skills = skills_dir.is_dir() and any(skills_dir.glob("*/SKILL.md"))
        ordered = (["/skills"] if has_native_skills else []) + [
            f"/skills/{segment}" for segment in plugin_segments
        ]
        skills_dirs = ordered or None

    if memory_paths is None:
        await _ensure_default_runtime_memory(state_manager)
    memory_paths = await _resolve_runtime_memory_paths(state_manager, memory_paths)

    # Use the canonical genesis prompt so every conversation starts with it.
    genesis_prompt = await state_manager.get_content_document("prompt", "progenitor/genesis.md")
    if genesis_prompt is None:
        raise RuntimeError(
            "Missing 'progenitor/genesis.md' prompt document in Postgres after seeding. "
            f"Checked seed source: {prompts_seed_dir / 'progenitor' / 'genesis.md'}"
        )
    system_prompt = genesis_prompt.strip()
    runtime_config = getattr(config, "runtime", None)
    http_host = getattr(runtime_config, "http_host", "127.0.0.1")
    if http_host in {"0.0.0.0", "::"}:  # nosec B104 - normalize wildcard binds to loopback for local callbacks
        http_host = "127.0.0.1"
    http_port = int(getattr(runtime_config, "http_port", 9501))
    http_base_url = (
        getattr(runtime_config, "http_public_base_url", None) or f"http://{http_host}:{http_port}"
    )
    async_subagents = create_progenitor_async_subagents(http_base_url)
    revision_reader = (
        PostgresRoutineRevisionReader(database_url) if database_url else NullRoutineRevisionReader()
    )

    # Object agents and routine graphs share one lazy runnable registry. This
    # lets graph `agent` nodes invoke either kind, while object agents receive
    # the same set through their own `task` middleware. Every proxy is installed
    # before any invocation compiles a graph, so declaration order cannot make a
    # valid forward reference unavailable.
    object_sources = await discover_object_agents(
        state_manager,
        native_root=data_dir / "agents" / "objects",
    )
    agent_specs_by_name: dict[str, CompiledSubAgent] = {}
    agent_registry: dict[str, Any] = {}
    reserved_names = {spec["name"] for spec in async_subagents}

    def delegated_agent_specs(caller: str) -> list[CompiledSubAgent]:
        return [spec for name, spec in agent_specs_by_name.items() if name != caller]

    object_subagents: list[CompiledSubAgent] = []
    for source in object_sources:
        if source.name in reserved_names:
            logger.warning(
                "Skipping object agent %r: name collides with a native agent",
                source.name,
            )
            continue
        object_spec = create_object_agent_proxy(
            source,
            state_manager=state_manager,
            model=model,
            tools=tools,
            backend=backend,
            checkpointer=checkpointer,
            agent_specs=delegated_agent_specs,
            log_sink=log_sink,
            worktree_dir=worktree_dir,
            shell_paths=ShellPaths(
                shim_bin=Path(config.storage.shim_bin_dir),
                agent_home=Path(config.storage.shell_home_dir),
                timeout_seconds=config.runtime.shell_timeout_seconds,
            ),
        )
        object_subagents.append(object_spec)
        agent_specs_by_name[source.name] = object_spec
        agent_registry[source.name] = object_spec["runnable"]
        reserved_names.add(source.name)

    # A routine's graph is still revision-pinned. Its in-graph `agent` nodes
    # resolve against agent_registry (object agents only); `routine` nodes
    # resolve against routine_registry (other routines) below.
    routine_registry: dict[str, Any] = {}
    routine_subagents: list[CompiledSubAgent] = []
    for graph_doc in await state_manager.list_routine_graphs():
        routine_id = str(graph_doc.get("id") or "").strip()
        if not routine_id:
            logger.warning("Skipping routine graph document with no id: %r", graph_doc)
            continue
        if routine_id in reserved_names:
            logger.warning("Skipping routine %r: name collides with an existing agent", routine_id)
            continue
        try:
            validate_routine_graph_payload(graph_doc)
        except (ValueError, KeyError) as exc:
            logger.warning("Skipping routine %r: invalid graph document: %s", routine_id, exc)
            continue
        description = str(graph_doc.get("description") or "").strip() or (
            f"Runs the {graph_doc.get('name') or routine_id} routine graph."
        )
        routine_spec = create_routine_subagent(
            routine_id=routine_id,
            description=description,
            model=model,
            state_manager=state_manager,
            revision_reader=revision_reader,
            agent_registry=agent_registry,
            routine_registry=routine_registry,
            tool_registry=tool_registry,
            checkpointer=checkpointer,
        )
        routine_subagents.append(routine_spec)
        agent_specs_by_name[routine_id] = routine_spec
        routine_registry[routine_id] = routine_spec["runnable"]
        reserved_names.add(routine_id)

    all_subagents: list[AsyncSubAgent | CompiledSubAgent] = [
        *async_subagents,
        *object_subagents,
        *routine_subagents,
    ]

    # Deep Agents' `memory=` parameter injects runtime memory from the
    # Postgres-backed memory documents (see backends.py's /memories/ mount).
    custom_middleware.insert(
        4,
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
        subagents=all_subagents,
        middleware=custom_middleware,
        checkpointer=checkpointer,
        state_schema=ProgenitorAgentState,
    )
    agent.routine_registry = routine_registry  # type: ignore[attr-defined]

    logger.info(
        "Created Progenitor DeepAgent: model=%s agents=%s object_agents=%d graph_agents=%d",
        config.llm.model_default,
        [subagent["name"] for subagent in all_subagents],
        len(object_subagents),
        len(routine_subagents),
    )
    return agent
