"""
Inner Loop Graph for Build Operations.

Implements the build loop per loop.spec.md:
OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN → (repeat until done)
                                                 ↓ (on pass)
                                              PUBLISH → DONE (raise PR)

Each node transition is checkpointed automatically by LangGraph.
In production an AsyncSqliteSaver writes checkpoints to disk so that an
interrupted build can be inspected or resumed.  In tests / when no DB path
is supplied we fall back to MemorySaver.
"""
import logging
from pathlib import Path
from typing import Any, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .routing import (
    route_after_verify,
    should_continue_inner_loop,
)
from .runtime.hooks import HookContext, create_default_hook_manager, extract_build_id
from .state.models import ProgenitorState

logger = logging.getLogger(__name__)


def create_inner_loop_graph(
    state_manager,
    llm_client,
    config,
    worktrees_dir,
    copilot_acp_manager=None,
    hook_manager=None,
    checkpoint_db_path: Path | str | None = None,
) -> Any:
    """Create the inner loop graph for build operations.

    Each node in the graph (observe, think, plan, build, execute, verify,
    learn) is persisted as a LangGraph checkpoint after it completes.  This
    allows per-iteration recovery and post-build inspection.

    Args:
        state_manager: StateManager for build_state.json persistence.
        llm_client: LLM client for model calls.
        config: Config with build settings.
        worktrees_dir: Path to worktrees directory.
        copilot_acp_manager: CopilotACPManager for ACP communication.
        checkpoint_db_path: Optional path to SQLite DB file for persistent
            checkpoints.  Defaults to ``<state_dir>/build_checkpoints.db``
            when not provided and the config has a storage.state_dir.
            Pass ``False`` to explicitly disable persistence (MemorySaver).

    Returns:
        Compiled StateGraph implementing:
        OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN → (loop)
    """
    if copilot_acp_manager is None:
        raise RuntimeError(
            "copilot_acp_manager is required for inner loop graph. "
            "Ensure CopilotACPManager is initialized."
        )

    from .nodes import (
        build,
        execute,
        learn,
        merge,
        observe,
        plan,
        publish,
        think,
        verify,
    )
    from .tools.copilot import CopilotCLI
    from .tools.git import GitWorktreeManager
    from .tools.registry import ToolRegistry

    # Initialize tools needed for build
    copilot_cli = CopilotCLI(
        copilot_cmd=config.copilot.bin_path,
        model=config.copilot.model,
        default_flags=config.copilot.default_flags,
        allowed_dirs=config.copilot.acp_allowed_dirs,
        acp_manager=copilot_acp_manager,
        timeout=config.copilot.timeout_seconds,
        logs_dir=config.storage.log_dir,
    )

    registry_path = config.storage.state_dir.parent / "config" / "agents.json"
    tool_registry = ToolRegistry(registry_path)

    # Prefer an explicit env override (required in Docker where the full repo is
    # not present in the image).  Fall back to file-relative traversal for local dev:
    # graph.py lives at app/agents/builder_agent/graph.py → parents[3] = repo root.
    import os as _os  # local import to avoid polluting module namespace
    _project_root_env = _os.getenv("PROGENITOR_PROJECT_ROOT")
    if _project_root_env:
        project_root = Path(_project_root_env)
    else:
        candidate = Path(__file__).resolve().parents[3]
        project_root = candidate if (candidate / ".git").exists() else Path.cwd()
    git_manager = GitWorktreeManager(worktrees_dir, repo_path=project_root)

    hook_registrations = getattr(getattr(config, "runtime", None), "hook_registrations", None)
    hook_manager = hook_manager or create_default_hook_manager(
        state_manager,
        registrations=hook_registrations,
    )

    def _wrap_node(name: str, node_fn: Any) -> Any:
        async def _wrapped(state: ProgenitorState) -> ProgenitorState:
            build_id = extract_build_id(state)
            await hook_manager.emit(
                f"before_{name}",
                HookContext(
                    event=f"before_{name}",
                    build_id=build_id,
                    node_name=name,
                    state=state if isinstance(state, dict) else None,
                    metadata={"phase": name},
                ),
            )
            try:
                result = cast(ProgenitorState, await node_fn(state))
            except Exception as exc:
                await hook_manager.emit(
                    "node_error",
                    HookContext(
                        event="node_error",
                        build_id=build_id,
                        node_name=name,
                        state=state if isinstance(state, dict) else None,
                        metadata={"phase": name, "error_type": type(exc).__name__},
                        error=str(exc),
                    ),
                )
                raise

            await hook_manager.emit(
                f"after_{name}",
                HookContext(
                    event=f"after_{name}",
                    build_id=build_id,
                    node_name=name,
                    state=state if isinstance(state, dict) else None,
                    result=result if isinstance(result, dict) else None,
                    metadata={"phase": name},
                ),
            )
            return result

        return _wrapped

    nodes = {
        "observe": _wrap_node("observe", observe.create_node(llm_client, state_manager, config)),
        "think": _wrap_node("think", think.create_node(llm_client, state_manager)),
        "plan": _wrap_node("plan", plan.create_node(llm_client, state_manager)),
        "build": _wrap_node("build", build.create_node(llm_client, copilot_cli, worktrees_dir, state_manager, git_manager)),
        "merge": _wrap_node("merge", merge.create_node(llm_client, git_manager, state_manager)),
        "execute": _wrap_node("execute", execute.create_node(tool_registry, state_manager, project_root)),
        "verify": _wrap_node("verify", verify.create_node(llm_client, state_manager)),
        "publish": _wrap_node("publish", publish.create_node(state_manager, worktrees_dir)),
        "learn": _wrap_node("learn", learn.create_node(llm_client, state_manager)),
    }

    builder = StateGraph(ProgenitorState)
    for name, node_fn in nodes.items():
        builder.add_node(name, node_fn)  # type: ignore[call-overload]

    builder.set_entry_point("observe")

    # OBSERVE → THINK → PLAN → BUILD → MERGE → EXECUTE → VERIFY → [LEARN or DONE]
    builder.add_edge("observe", "think")
    builder.add_edge("think", "plan")
    builder.add_edge("plan", "build")
    builder.add_edge("build", "merge")
    builder.add_edge("merge", "execute")
    builder.add_edge("execute", "verify")

    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {"publish": "publish", "learn": "learn", "done": END},
    )
    builder.add_edge("publish", END)
    builder.add_conditional_edges(
        "learn",
        should_continue_inner_loop,
        {"observe": "observe", "done": END},
    )

    # ------------------------------------------------------------------
    # Checkpointer: prefer SQLite for durable per-iteration snapshots
    # ------------------------------------------------------------------
    checkpointer = _build_checkpointer(checkpoint_db_path, config)
    graph = builder.compile(checkpointer=checkpointer)

    logger.info(
        "Inner loop graph created with nodes: %s (checkpointer=%s)",
        list(nodes.keys()),
        type(checkpointer).__name__,
    )
    return graph


def _build_checkpointer(
    checkpoint_db_path: Path | str | None | bool,
    config: Any,
) -> Any:
    """Return the appropriate LangGraph checkpointer.

    Preference order:
    1. Explicit ``checkpoint_db_path`` argument.
    2. ``<config.storage.state_dir>/build_checkpoints.db`` when config has state_dir.
    3. In-memory MemorySaver as fallback (also used when path is ``False``).

    The inner-loop graph is executed asynchronously, so we prefer
    AsyncSqliteSaver (requires aiosqlite). If unavailable, fall back to
    in-memory checkpoints.
    """
    if checkpoint_db_path is False:
        # Caller explicitly opted out of persistence.
        return MemorySaver()

    if checkpoint_db_path is None:
        try:
            checkpoint_db_path = config.storage.state_dir / "build_checkpoints.db"
        except Exception:
            return MemorySaver()

    db_path = Path(str(checkpoint_db_path))
    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import (  # pyright: ignore[reportMissingImports]
            AsyncSqliteSaver,
        )

        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = aiosqlite.connect(str(db_path))
        checkpointer = AsyncSqliteSaver(conn=conn)
        logger.info("Build checkpoints will be stored at: %s", db_path)
        return checkpointer
    except ImportError:
        logger.warning(
            "AsyncSqliteSaver unavailable; falling back to MemorySaver. "
            "Install with: pip install aiosqlite langgraph-checkpoint-sqlite"
        )
        return MemorySaver()
    except Exception as exc:
        logger.warning("Could not open checkpoint DB %s: %s — using MemorySaver", db_path, exc)
        return MemorySaver()
