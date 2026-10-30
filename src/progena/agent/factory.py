"""
DeepAgent factory for Progena.

Creates a configured DeepAgent instance that serves as the conversational
layer, using Progena's Azure endpoint for model calls and wrapping
Progena's custom tools.

Uses the LangChain model adapter plus Progena's wrapped tools, middleware,
skills, memory files, and native DeepAgent subagents.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.filesystem import FilesystemMiddleware, FsToolName
from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents.middleware import TodoListMiddleware
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from src.progena.agent.adapters import (
    ProviderChatModel,
    create_deepagent_tools,
    create_dynamic_mcp_tools,
)
from src.progena.agent.filesystem.mounts import create_progena_backend
from src.progena.agent.filesystem.shell import ShellPaths
from src.progena.agent.middleware import (
    AsyncSubagentTrackingMiddleware,
    DirectA2AFastPathMiddleware,
    DynamicMCPToolMiddleware,
    LiveInstructionMiddleware,
    SchemaCompactionMiddleware,
    SecretRedactionMiddleware,
    ToolErrorMiddleware,
    ToolResultPairingMiddleware,
    ToolTimingMiddleware,
)
from src.progena.agent.model_profiles import resolve_model_profile
from src.progena.agent.object_agents.factory import create_object_agent_proxy
from src.progena.agent.object_agents.registry import (
    LiveSubagentMiddleware,
    ObjectAgentRegistry,
    RoutineSubagentRegistry,
)
from src.progena.agent.state import ProgenaAgentState
from src.progena.agent.subagents.descriptors import create_progena_async_subagents
from src.progena.agent.subagents.live import LiveAsyncSubagentMiddleware
from src.progena.async_subagents.tasks import create_agent_background_runner
from src.progena.config import ProgenaConfig, get_config
from src.progena.documents.content import sync_instruction_documents
from src.progena.instructions import GENESIS_INSTRUCTION, render_runtime_instruction
from src.progena.llm.provider_context import (
    ProviderTurn,
    activate_provider_turn,
    current_provider_turn,
)
from src.progena.routines.approvals import approval_lock
from src.progena.routines.controls import RoutineRunController
from src.progena.routines.graph import validate_routine_graph_payload
from src.progena.routines.revisions import (
    NullRoutineRevisionReader,
    PostgresRoutineRevisionReader,
)
from src.progena.routines.subagent import (
    create_routine_executor,
    create_routine_subagent,
    resolve_routine_document,
    resume_routine_thread,
)
from src.progena.threads import parse_routine_thread_id, routine_default_session_id

logger = logging.getLogger(__name__)

_FILESYSTEM_TOOLS: list[FsToolName] = [
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "delete",
    "glob",
    "grep",
    "execute",
]

DEFAULT_RUNTIME_MEMORY_PATH = "/memories/AGENTS.md"
DEFAULT_RUNTIME_MEMORY_CONTENT = """# Progena Runtime Memory

This file stores durable runtime memory for Progena.

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


def _create_javascript_middleware() -> CodeInterpreterMiddleware:
    """Expose read tools, A2A communication and turn-local subagent dispatch."""
    return CodeInterpreterMiddleware(
        tool_name="javascript",
        mode="turn",
        memory_limit=64 * 1024 * 1024,
        timeout=5.0,
        max_result_chars=4000,
        ptc=[
            "ls",
            "read_file",
            "glob",
            "grep",
            "list_agents",
            "a2a_send",
            "a2a_send_async",
            "a2a_status",
            "a2a_list",
        ],
        subagents=True,
    )


def _build_checkpointer(db_path: Path) -> Any:
    """Create an AsyncSqliteSaver checkpointer, falling back to MemorySaver.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        AsyncSqliteSaver if langgraph-checkpoint-sqlite is installed, else MemorySaver.
    """
    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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
    # /memories/agents/<name>/ is private to each object agent (see object_agents.py).
    discovered = sorted(
        f"/memories/{relative_path}"
        for relative_path, _content in rows
        if not relative_path.startswith("agents/")
    )

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


async def create_progena_agent(
    config: ProgenaConfig | None = None,
    checkpointer: Any | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    store: Any | None = None,
    project_root: str | None = None,
    skills_dirs: list[str] | None = None,
    memory_paths: list[str] | None = None,
    live_state_provider: Callable[[], Any] | None = None,
) -> CompiledStateGraph:
    """Create the Progena DeepAgent instance.

    Args:
        config: Progena configuration. If None, loads from environment.
        checkpointer: LangGraph checkpointer for conversation persistence.
                      If None, uses in-memory checkpointer (dev mode).
        tool_registry: Progena's tool/agent registry (for tool context).
        llm_client: Progena's LLM client (for tools that need AI).
        state_manager: Progena's StateManager. Required: instructions and
                       runtime memories are Postgres-backed content documents
                       (see ``content_documents.py``), installed marketplace-plugin
                       skills are Postgres-backed too, and the genesis system
                       instruction is loaded from there.
        store: Optional LangGraph BaseStore for DeepAgent features that need one.
               If None, no store is configured by default.
        project_root: Root directory for filesystem backend. Defaults to cwd.
        skills_dirs: Paths to skill directories for progressive disclosure.
                     If None, auto-discovers: ``/skills`` (disk-backed) for
                     progena's own native skills when present, plus one
                     ``/skills/<marketplace>__<plugin>`` (Postgres-backed)
                     source per installed marketplace plugin.
        memory_paths: Backend-visible AGENTS.md-style prompt-context files loaded
                      via Deep Agents' `memory=` middleware. If None, Progena
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
            "create_progena_agent requires state_manager: instructions and runtime "
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
    # Not a runtime mount -- the source of the stored instructions, synced on
    # every boot (see content_documents.sync_instruction_documents) and baked
    # into the container image via ``COPY data/instructions/``.
    instructions_dir = (
        Path(getattr(config.storage, "instructions_dir", data_dir / "instructions"))
        .expanduser()
        .resolve()
    )
    # A real runtime mount (/skills/, see backends.py) -- progena's own
    # skills are static, baked into the image, and never seeded into Postgres.
    skills_dir = data_dir / "skills"
    worktree_dir = (
        Path(getattr(config.storage, "worktree_dir", resolved_project_root / "data" / "repos"))
        .expanduser()
        .resolve()
    )
    configured_shared_dir = getattr(config.storage, "shared_dir", None)
    shared_dir = Path(configured_shared_dir).expanduser() if configured_shared_dir else None

    # Provider resolution is deliberately lazy so the runtime can boot before
    # Settings has a connection. Each invocation uses its turn snapshot.
    model = ProviderChatModel(template=config.llm)

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
    backend = create_progena_backend(
        project_root=project_root,
        skills_dir=skills_dir,
        agents_dir=data_dir / "agents" / "objects",
        worktree_dir=worktree_dir,
        shared_dir=shared_dir,
        state_manager=state_manager,
        database_url=database_url,
        checkpointer=checkpointer,
        plugin_skill_segments=plugin_segments,
        plugin_agent_segments=plugin_agent_segments,
    )

    # Wrap Progena's system tools as DeepAgent-compatible tools. The
    # resume_routine tool needs model/registries/checkpointer, and
    # schedule_results needs the compiled agent itself; neither exists yet at
    # this point in construction (built below) -- this box is populated once
    # they do; create_deepagent_tools reads it at call time, not at wrap time.
    late_bindings: dict[str, Any] = {}
    tools = create_deepagent_tools(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
        live_state_provider=live_state_provider,
        late_bindings=late_bindings,
    )

    custom_middleware = [
        # The backend applies MOUNT_ACCESS to `delete`: single files on
        # read-write-delete mounts only, never directories.
        FilesystemMiddleware(backend=backend, tools=_FILESYSTEM_TOOLS),
        TodoListMiddleware(),
        DirectA2AFastPathMiddleware(tool_registry=tool_registry),
        DynamicMCPToolMiddleware(mcp_tools_provider=lambda: create_dynamic_mcp_tools(llm_client)),
        SchemaCompactionMiddleware(),
        SecretRedactionMiddleware(),
        ToolTimingMiddleware(),
        _create_javascript_middleware(),
    ]

    # Bring the stored instructions in line with the image's data/instructions/.
    # Skills are never stored this way -- progena's own are a disk mount (see
    # backends.py) and marketplace-plugin skills are written by plugins/install.py.
    await sync_instruction_documents(state_manager, instructions_dir)

    # Auto-discover skills through the DeepAgent backend mount. Progena's
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

    # Every conversation starts with the genesis instruction; edits made on the
    # Instructions page apply on the next model call (LiveInstructionMiddleware).
    genesis = await state_manager.get_content_document("instruction", GENESIS_INSTRUCTION)
    if genesis is None:
        raise RuntimeError(
            f"Missing '{GENESIS_INSTRUCTION}' instruction in Postgres after syncing "
            f"{instructions_dir}"
        )
    system_prompt = (await render_runtime_instruction(state_manager, genesis)).strip()
    custom_middleware.append(
        LiveInstructionMiddleware(state_manager, GENESIS_INSTRUCTION, system_prompt)
    )
    runtime_config = getattr(config, "runtime", None)
    http_host = getattr(runtime_config, "http_host", "127.0.0.1")
    if http_host in {"0.0.0.0", "::"}:  # nosec B104 - normalize wildcard binds to loopback for local callbacks
        http_host = "127.0.0.1"
    http_port = int(getattr(runtime_config, "http_port", 9501))
    http_base_url = (
        getattr(runtime_config, "http_public_base_url", None) or f"http://{http_host}:{http_port}"
    )
    async_subagents = create_progena_async_subagents(http_base_url)
    revision_reader = (
        PostgresRoutineRevisionReader(database_url) if database_url else NullRoutineRevisionReader()
    )

    # Object agents use a registry because they are conversational subagents.
    # Routine execution is different: routine documents are authoritative in
    # Postgres, so deterministic runs resolve and compile a fresh document for
    # each invocation rather than consulting an in-memory routine registry.
    reserved_names = {spec["name"] for spec in async_subagents}

    class _LiveTools:
        """Static tools plus the MCP tools loaded right now, re-read on every iteration.

        Object agents compile per invocation, so a manifest can name an MCP
        tool (e.g. ``jina_search_web``) added after startup.
        """

        def __iter__(self):
            yield from tools
            yield from create_dynamic_mcp_tools(llm_client)

    live_tools = _LiveTools()

    def object_proxy(source):
        return create_object_agent_proxy(
            source,
            state_manager=state_manager,
            model=model,
            tools=live_tools,
            backend=backend,
            checkpointer=checkpointer,
            worktree_dir=worktree_dir,
            shell_paths=ShellPaths(
                shim_bin=Path(config.storage.shim_bin_dir),
                agent_home=Path(config.storage.shell_home_dir),
                timeout_seconds=config.runtime.shell_timeout_seconds,
                workspace_root=Path(config.storage.shell_workspace_dir),
            ),
        )

    live_objects = ObjectAgentRegistry(
        state_manager=state_manager,
        proxy_factory=object_proxy,
        reserved_names=reserved_names,
        native_root=data_dir / "agents" / "objects",
    )
    await live_objects.refresh()
    live_async = LiveAsyncSubagentMiddleware(
        live_objects, base_url=http_base_url, static_subagents=async_subagents
    )
    background_runner = create_agent_background_runner(state_manager, http_base_url)
    routine_controls = RoutineRunController(state_manager)

    async def profile_resolver(name):
        return await resolve_model_profile(state_manager, name)

    object_agent_registry = live_objects.callables
    reserved_names.update(object_agent_registry)

    async def _run_routine(
        routine_id_or_name: str,
        state: dict[str, Any],
        runtime: Any | None = None,
    ) -> dict[str, Any]:
        """Resolve, compile, run, and discard one routine invocation.

        This is used by both the heartbeat's deterministic Run action and
        nested ``routine`` nodes.  The conversational subagent descriptors
        below are still built at startup for DeepAgent task discovery, but
        execution never looks up a compiled routine from a process registry.
        """
        resume_thread_id = str(state.get("_routine_resume_thread_id") or "").strip() or None
        if resume_thread_id:
            routine_id, _revision_id, _session_id = parse_routine_thread_id(resume_thread_id)
            if routine_id != routine_id_or_name:
                resolved = await resolve_routine_document(state_manager, routine_id_or_name)
                if resolved is None or resolved[0] != routine_id:
                    raise ValueError("resume thread does not belong to the requested routine")
        else:
            resolved = await resolve_routine_document(state_manager, routine_id_or_name)
            if resolved is None:
                raise KeyError(f"unknown routine '{routine_id_or_name}'")
            routine_id, _graph_doc = resolved
        executor = create_routine_executor(
            routine_id=routine_id,
            model=model,
            state_manager=state_manager,
            revision_reader=revision_reader,
            agent_registry=object_agent_registry,
            # Kept empty for backwards-compatible factory callers; all
            # routine-to-routine calls use routine_runner below.
            routine_registry={},
            routine_runner=_run_routine,
            agent_background_runner=background_runner,
            model_profile_resolver=profile_resolver,
            tool_registry=tool_registry,
            filesystem_backend=backend,
            checkpointer=checkpointer,
            routine_controls=routine_controls,
            context=dict(getattr(runtime, "context", None) or {}),
        )

        async def invoke() -> dict[str, Any]:
            resume = state.get("_routine_resume")
            if resume:
                # Share the UI/chat approval lock so answering a child directly
                # cannot race with answering its approval through the parent.
                async with approval_lock(resume["thread_id"], database_url, wait=True):
                    return await executor.ainvoke(state)
            return await executor.ainvoke(state)

        if current_provider_turn() is not None:
            return await invoke()
        session_id = str(state.get("thread_id") or routine_default_session_id(routine_id))
        turn = await ProviderTurn.create(config.llm, session_id=session_id, resolve_default=False)
        with activate_provider_turn(turn):
            return await invoke()

    def _routine_subagent(graph_doc: dict[str, Any]) -> CompiledSubAgent | None:
        routine_id = str(graph_doc.get("id") or "").strip()
        if not routine_id:
            logger.warning("Skipping routine graph document with no id: %r", graph_doc)
            return None
        if routine_id in reserved_names:
            logger.warning("Skipping routine %r: name collides with an existing agent", routine_id)
            return None
        try:
            validate_routine_graph_payload(graph_doc)
        except (ValueError, KeyError) as exc:
            logger.warning("Skipping routine %r: invalid graph document: %s", routine_id, exc)
            return None
        input_schema = graph_doc.get("input_schema")
        description = str(graph_doc.get("description") or "").strip() or (
            f"Runs the {graph_doc.get('name') or routine_id} routine graph."
        )
        if input_schema:
            description += (
                " Invoke with task(subagent_type="
                f"{routine_id!r}, description=<JSON object matching this input schema>). "
                f"Input schema: {json.dumps(input_schema, separators=(',', ':'))}"
            )
        return create_routine_subagent(
            routine_id=routine_id,
            description=description,
            model=model,
            state_manager=state_manager,
            revision_reader=revision_reader,
            agent_registry=object_agent_registry,
            routine_registry={},
            routine_runner=_run_routine,
            agent_background_runner=background_runner,
            model_profile_resolver=profile_resolver,
            tool_registry=tool_registry,
            filesystem_backend=backend,
            checkpointer=checkpointer,
            routine_controls=routine_controls,
        )

    # Rebuilt whenever a routine document changes (see runtime/progena.py),
    # so a routine Progena just wrote is runnable without a restart.
    live_routines = RoutineSubagentRegistry(state_manager=state_manager, build=_routine_subagent)
    await live_routines.refresh()

    async def _resume_routine(thread_id: str, answer: Any, **kwargs: Any) -> dict[str, Any]:
        return await resume_routine_thread(
            thread_id=thread_id,
            answer=answer,
            database_url=database_url,
            model=model,
            state_manager=state_manager,
            revision_reader=revision_reader,
            agent_registry=object_agent_registry,
            routine_registry={},
            routine_runner=_run_routine,
            agent_background_runner=background_runner,
            model_profile_resolver=profile_resolver,
            tool_registry=tool_registry,
            filesystem_backend=backend,
            checkpointer=checkpointer,
            routine_controls=routine_controls,
            **kwargs,
        )

    # Progena's resume_routine tool answers as the agent, so human-only
    # steps refuse it; the UI path (agent.routine_resumer) answers as a person.
    async def _resume_routine_as_agent(thread_id: str, answer: Any) -> dict[str, Any]:
        return await _resume_routine(thread_id, answer, answered_by="agent")

    late_bindings["resume_routine"] = _resume_routine_as_agent

    all_subagents: list[AsyncSubAgent | CompiledSubAgent] = [
        *async_subagents,
        *live_objects.descriptors,
        *live_routines.descriptors,
    ]
    custom_middleware.append(
        LiveSubagentMiddleware(
            live_objects,
            routines=live_routines,
            backend=backend,
            state_schema=ProgenaAgentState,
        )
    )

    custom_middleware.append(live_async)
    # Innermost of ours: sees the final message list just before the model,
    # after summarization and every other request rewrite.
    custom_middleware.append(ToolResultPairingMiddleware())

    # Deep Agents' `memory=` parameter injects runtime memory from the
    # Postgres-backed memory documents (see backends.py's /memories/ mount).
    custom_middleware.insert(
        4,
        AsyncSubagentTrackingMiddleware(
            state_manager=state_manager,
            async_subagents=async_subagents,
            specs_provider=live_async.descriptors,
        ),
    )
    # Outermost of ours: a failing tool (e.g. a subagent that cannot be built)
    # becomes an error result for the model instead of aborting the run.
    custom_middleware.insert(0, ToolErrorMiddleware())
    agent = create_deep_agent(
        name="progena",
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        backend=backend,
        store=store,
        skills=skills_dirs,
        memory=memory_paths or None,
        subagents=[],
        middleware=custom_middleware,
        checkpointer=checkpointer,
        state_schema=ProgenaAgentState,
    )
    # The heartbeat uses this callable for deterministic UI-triggered runs.
    # It is intentionally a resolver/runner, not a routine registry.
    agent.routine_runner = _run_routine  # type: ignore[attr-defined]
    agent.routine_controls = routine_controls  # type: ignore[attr-defined]
    agent.routine_resumer = _resume_routine  # type: ignore[attr-defined]

    agent.object_agent_registry = live_objects  # type: ignore[attr-defined]
    agent.routine_subagent_registry = live_routines  # type: ignore[attr-defined]

    async def _thread_state(thread_id: str) -> Any:
        return await agent.aget_state({"configurable": {"thread_id": thread_id}})

    late_bindings["thread_state"] = _thread_state

    logger.info(
        "Created Progena DeepAgent: model=%s agents=%s object_agents=%d graph_agents=%d",
        config.llm.model_default,
        [subagent["name"] for subagent in all_subagents],
        len(live_objects.descriptors),
        len(live_routines.descriptors),
    )
    return agent
