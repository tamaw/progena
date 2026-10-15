"""
Builder Agent — A2A server entry point.

Implements the A2A protocol (v0.3.0) using the official a2a-sdk.
The agent accepts build requests as A2A Tasks, runs the inner-loop
LangGraph (OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN)
and streams TaskStatusUpdateEvent / TaskArtifactUpdateEvent back to callers.

Transport: JSON-RPC 2.0 over HTTP (FastAPI / Starlette)
Port:       8107  (configurable via PORT env var)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from .config import get_config, setup_logging
from .llm.mock_client import create_llm_client
from .postgres_skills import close_skill_store
from .queue_worker import BuilderTaskQueue
from .runtime.background_build import BackgroundBuildRunner, BuildResult, BuildStatus
from .runtime.copilot_acp import CopilotACPManager
from .runtime.time_utils import utcnow_isoformat
from .state.manager import StateManager
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)
_start_time = time.time()


def _agent_log_name() -> str:
    agent_name = os.getenv("AGENT_NAME", "").strip()
    if not agent_name:
        raise RuntimeError("AGENT_NAME must be set for builder_agent")
    return agent_name


# ---------------------------------------------------------------------------
# Module-level singletons — initialised once on first request.
# ---------------------------------------------------------------------------
_config = None
_state_manager: StateManager | None = None
_llm_client: Any = None
_tool_registry: ToolRegistry | None = None
_copilot_acp: CopilotACPManager | None = None
_runner: BackgroundBuildRunner | None = None
_task_queue: BuilderTaskQueue | None = None
_initialized = False


async def _ensure_workspace() -> None:
    """Initialise the git workspace for Docker deployments.

    When PROGENITOR_PROJECT_ROOT points to a directory that is not yet a git
    repo (i.e. a fresh named volume on first boot) and PROGENITOR_REPO_URL is
    set, clone the repo there so git worktrees become available.

    No-op when running locally (PROGENITOR_PROJECT_ROOT not set) or when the
    workspace is already a valid git repo.
    """
    workspace_env = os.getenv("PROGENITOR_PROJECT_ROOT")
    if not workspace_env:
        return  # Local dev — project root detected via file traversal

    workspace = Path(workspace_env)
    workspace.mkdir(parents=True, exist_ok=True)

    if (workspace / ".git").exists():
        logger.info("Workspace already initialised at %s", workspace)
        return

    repo_url = os.getenv("PROGENITOR_REPO_URL")
    if not repo_url:
        logger.warning(
            "PROGENITOR_PROJECT_ROOT=%s is not a git repo and PROGENITOR_REPO_URL "
            "is not set — git worktrees will be unavailable until the workspace is "
            "populated (mount a pre-cloned volume or set PROGENITOR_REPO_URL).",
            workspace,
        )
        return

    # Note: the workspace directory may already contain runtime dirs (e.g. worktrees/)
    # created by volume init scripts. `git clone <url> <dir>` fails unless the
    # target dir is empty, so we fall back to `git init + fetch + checkout` which
    # works in a non-empty directory.

    async def _run_git(*args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        rc = proc.returncode
        assert rc is not None  # communicate() awaited => process completed
        return int(rc), stdout.decode(errors="replace"), stderr.decode(errors="replace")

    # Fast path: empty dir -> clone
    try:
        is_empty = not any(workspace.iterdir())
    except Exception:
        is_empty = False

    if is_empty:
        logger.info("Cloning %s into %s …", repo_url, workspace)
        rc, _out, err = await _run_git("clone", repo_url, str(workspace))
        if rc != 0:
            logger.error("Failed to clone repo: %s", err[:500])
        else:
            logger.info("Workspace ready at %s", workspace)
        return

    logger.info("Initialising git workspace at %s …", workspace)
    rc, _out, err = await _run_git("-C", str(workspace), "init")
    if rc != 0:
        logger.error("Failed to init git repo: %s", err[:500])
        return

    # Remote add can fail if it already exists (e.g. previous partial init).
    rc, _out, err = await _run_git("-C", str(workspace), "remote", "add", "origin", repo_url)
    if rc != 0:
        await _run_git("-C", str(workspace), "remote", "set-url", "origin", repo_url)

    rc, _out, err = await _run_git("-C", str(workspace), "fetch", "--prune", "origin")
    if rc != 0:
        logger.error("Failed to fetch origin: %s", err[:500])
        return

    # Prefer origin/main, fall back to origin/master if needed.
    rc, _out, err = await _run_git("-C", str(workspace), "checkout", "-B", "main", "origin/main")
    if rc != 0:
        rc, _out, err = await _run_git(
            "-C", str(workspace), "checkout", "-B", "main", "origin/master"
        )
        if rc != 0:
            logger.error("Failed to checkout default branch: %s", err[:500])
            return

    logger.info("Workspace ready at %s", workspace)


async def _ensure_initialized() -> None:
    """Lazy-initialise all builder services (idempotent)."""
    global _config, _state_manager, _llm_client, _tool_registry, _copilot_acp, _runner, _initialized
    if _initialized:
        return

    project_root = Path(__file__).resolve().parent
    config = get_config(project_root, project_root / "config", validate=False)
    setup_logging(config, _agent_log_name())
    _config = config

    await _ensure_workspace()

    for p in [
        config.storage.state_dir,
        config.storage.state_dir.parent / "config",
        config.storage.log_dir,
        config.storage.worktree_dir,
    ]:
        p.mkdir(parents=True, exist_ok=True)

    _state_manager = StateManager(
        config.storage.state_dir,
        config.storage.log_dir,
        lock_timeout=config.storage.state_lock_timeout_seconds,
    )
    await _state_manager.initialize()

    _llm_client = create_llm_client(
        config=config.llm,
        mcp_config=config.mcp,
        instructions_dir=config.storage.instructions_dir,
    )

    _tool_registry = ToolRegistry(config.storage.state_dir.parent / "config" / "agents.json")
    await _tool_registry.load()

    _copilot_acp = CopilotACPManager(
        copilot_cmd=config.copilot.bin_path,
        model=config.copilot.model,
        yolo="--yolo" in config.copilot.default_flags,
        allowed_dirs=config.copilot.acp_allowed_dirs,
        timeout=config.copilot.timeout_seconds,
    )

    _runner = BackgroundBuildRunner(
        state_manager=_state_manager,
        config=config,
        llm_client=_llm_client,
        worktrees_dir=config.storage.worktree_dir,
        copilot_acp_manager=_copilot_acp,
    )

    _initialized = True
    logger.info("Builder Agent initialised")


def _get_or_create_task_queue() -> BuilderTaskQueue:
    """Return the background task queue, creating it on first use."""
    global _task_queue
    if _task_queue is None:
        if _runner is None:
            raise RuntimeError("Builder task queue unavailable: runner is not initialized")
        _task_queue = BuilderTaskQueue(_run_build_request)
    return _task_queue


def _extract_context(message: Message | None) -> dict[str, Any]:
    """Extract metadata/context attached to the incoming A2A message."""
    if not message:
        return {}
    metadata = getattr(message, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


# Human-readable phase text per inner-loop node, keyed to the real node
# names registered in graph.py's create_inner_loop_graph (observe -> think ->
# plan -> build -> merge -> execute -> verify -> publish -> learn).
_NODE_PHASE_MESSAGES: dict[str, str] = {
    "observe": "Observing current repository state...",
    "think": "Reasoning about the request...",
    "plan": "Planning implementation steps...",
    "build": "Invoking GitHub Copilot CLI to write code...",
    "merge": "Merging changes into the integration branch...",
    "execute": "Running tools and checking output...",
    "verify": "Running verification tests...",
    "publish": "Opening pull request...",
    "learn": "Recording lessons learned...",
}


def _push_node_progress_callback(
    task_id: str, context_id: str, event_queue: Any
) -> Callable[[str, Mapping[str, Any]], Awaitable[None]]:
    """Build a BackgroundBuildRunner progress callback that relays each inner-loop
    node transition as a `working` TaskStatusUpdateEvent on the given A2A
    event_queue. Bound to one task_id/event_queue pair per build."""

    async def _push(node_name: str, _build_state: Mapping[str, Any]) -> None:
        phase_text = _NODE_PHASE_MESSAGES.get(node_name, f"Running {node_name}...")
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=False,
                status=TaskStatus(
                    state=TaskState.working,
                    message=_text_message(phase_text, role=Role.agent),
                    timestamp=_utc_now_iso(),
                ),
            )
        )

    return _push


async def _run_build_request(
    task_id: str,
    context_id: str,
    request_text: str,
    request_ctx: dict[str, Any],
    event_queue: Any | None = None,
) -> BuildResult:
    """Execute a builder request and return the recorded build result.

    When `event_queue` is given, registers a per-node progress callback on
    the (single-flight) BackgroundBuildRunner for the duration of this build,
    relaying real inner-loop node transitions (observe/think/plan/... )
    as A2A TaskStatusUpdateEvents -- always cleared afterwards since it's a
    single mutable slot on a long-lived runner, not a per-build registration.
    """
    del request_ctx  # Reserved for future correlation/context expansion.

    await _ensure_initialized()
    assert _runner is not None
    assert _llm_client is not None
    assert _tool_registry is not None

    started_at = datetime.now(UTC)

    import contextlib

    from .runtime import decide as decide_mod  # local import to avoid circular

    tool_context = ""
    with contextlib.suppress(Exception):
        tool_context = _tool_registry.get_tool_context()

    decision = await decide_mod._request_decision_data(
        llm_client=_llm_client,
        user_input=request_text,
        conversation_history=[],
        tool_context=tool_context,
    )
    action = _normalize_build_action(decision.get("action"))

    if action not in ("build_agent", "build_mcp", "build_progenitor", "upgrade"):
        return BuildResult(
            build_id=f"rejected_{task_id}",
            status=BuildStatus.FAILED,
            task_description=request_text,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            error_message=(
                f"Request not recognised as a build action (action={action or '(empty)'}). "
                "Be explicit, e.g. 'build an agent that…'"
            ),
        )

    build_settings = await decide_mod._generate_build_settings(
        user_input=request_text,
        llm_client=_llm_client,
        llm_action=action,
        decision_data=decision,
    )
    target_capabilities = decide_mod._infer_target_capabilities(request_text)
    task_description = f"{action}: {build_settings.project_name}"
    build_id: str | None = None
    if event_queue is not None:
        _runner.set_progress_callback(
            _push_node_progress_callback(task_id, context_id, event_queue)
        )
    try:
        build_id, _ack_message = await _runner.start_build(
            user_input=request_text,
            task_description=task_description,
            target_capabilities=target_capabilities,
            initial_state={
                "a2a_task_id": task_id,
                "a2a_context_id": context_id,
            },
            build_settings=build_settings,
        )

        bg_task = _runner._current_task
        if bg_task is not None:
            await bg_task

        result = _runner.get_build_status(build_id)
        if result is not None:
            return result
    except asyncio.CancelledError:
        # ``start_build`` creates the runner task before it awaits the
        # acknowledgment LLM call. Cancellation in that window leaves
        # ``build_id`` unset, and the surrounding cancellation can also
        # interrupt ``cancel_build`` itself, so still cancel the live runner
        # task directly and await it to completion.
        active_task = _runner._current_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await _runner.cancel_build(build_id)
        if active_task is not None:
            active_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await active_task
        raise
    finally:
        if event_queue is not None:
            _runner.set_progress_callback(None)

    return BuildResult(
        build_id=build_id or f"cancelled_{task_id}",
        status=BuildStatus.FAILED,
        task_description=task_description,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        error_message="Build completed but no result was recorded.",
    )


# ---------------------------------------------------------------------------
# A2A AgentExecutor implementation
# ---------------------------------------------------------------------------


class BuilderAgentExecutor(AgentExecutor):
    """Runs one build per execute() call, awaited to completion.

    execute() must not return until the build is done: the A2A framework
    closes `event_queue` the instant execute() returns
    (DefaultRequestHandler._run_event_stream), so a previous design that
    enqueued work onto a separately-detached background task and returned
    immediately silently dropped every event that task tried to push,
    including the terminal completion event -- tasks/get polling never saw
    anything past the first synchronously-pushed event. The framework
    already provides "don't make a non-blocking caller wait" on its own
    (ResultAggregator.consume_and_break_on_interrupt continues consuming in
    a background task after returning an early response), so execute()
    running the whole build inline is both necessary and sufficient.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        request_ctx = _extract_context(context.message)
        task_id = str(context.task_id or request_ctx.get("a2a_task_id") or uuid.uuid4())
        ctx_id = str(context.context_id or request_ctx.get("a2a_context_id") or uuid.uuid4())
        request_ctx["a2a_task_id"] = task_id
        request_ctx["a2a_context_id"] = ctx_id

        # ------------------------------------------------------------------
        # Extract request text from the A2A Message
        # ------------------------------------------------------------------
        request_text = ""
        if context.message:
            for part in context.message.parts or []:
                root = getattr(part, "root", part)
                if isinstance(root, TextPart):
                    request_text += root.text
        request_text = request_text.strip()

        if not request_text:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=ctx_id,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_message(
                            "Empty build request — please describe what to build.", role=Role.agent
                        ),
                        timestamp=utcnow_isoformat(),
                    ),
                    final=True,
                )
            )
            return

        await _ensure_initialized()
        task_queue = _get_or_create_task_queue()
        logger.info(
            "A2A execute accepted: task_id=%s context_id=%s request=%.120r",
            task_id,
            ctx_id,
            request_text,
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.submitted,
                    message=_text_message(
                        f"Task accepted (id={task_id}): {request_text[:120]}",
                        role=Role.agent,
                    ),
                    timestamp=utcnow_isoformat(),
                ),
                final=False,
            )
        )

        # Awaited: a non-blocking caller already got its fast response above
        # from the framework's own break-on-interrupt continuation, not from
        # execute() returning -- see the class docstring.
        await task_queue.run(task_id, ctx_id, request_text, request_ctx, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel a running build task.

        Only pushes its own terminal event when there was nothing to cancel;
        when a genuinely in-flight build is interrupted, BuilderTaskQueue's
        own CancelledError handling pushes the terminal `canceled` event (it
        also records the JSONL task-log entry, which this method has no way
        to do) -- pushing here too would double-deliver the same event.
        """
        task_id = context.task_id or ""
        ctx_id = context.context_id or ""
        cancelled = False
        if _task_queue is not None and task_id:
            cancelled = _task_queue.cancel(task_id)

        if not cancelled:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=ctx_id,
                    status=TaskStatus(
                        state=TaskState.canceled,
                        message=_text_message("No active task to cancel.", role=Role.agent),
                        timestamp=utcnow_isoformat(),
                    ),
                    final=True,
                )
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_message(text: str, *, role: Role = Role.agent) -> Message:
    return Message(
        message_id=str(uuid.uuid4()),
        role=role,
        parts=[Part(root=TextPart(text=text))],
    )


def _normalize_build_action(raw_action: Any) -> str:
    action = str(raw_action or "").lower().strip()
    if action in {"execute", "answer_directly"}:
        return "build_agent"
    return action


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# AgentCard — advertised at /.well-known/agent-card.json
# ---------------------------------------------------------------------------
_PORT = int(os.environ.get("PORT", "8107"))
_PUBLIC_HOST = os.environ.get("A2A_PUBLIC_HOST", "localhost").strip() or "localhost"

AGENT_CARD = AgentCard(
    name="Builder Agent",
    description=(
        "Self-contained A2A agent that executes the inner build loop "
        "(OBSERVE→THINK→PLAN→BUILD→EXECUTE→VERIFY→LEARN) for agent/MCP/progenitor builds. "
        "Uses GitHub Copilot CLI and git worktrees for concurrent, isolated builds."
    ),
    version="0.2.0",
    url=f"http://{_PUBLIC_HOST}:{_PORT}",
    capabilities=AgentCapabilities(streaming=True, state_transition_history=True),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="build_agent",
            name="Build A2A Agent",
            description="Scaffold and implement a new self-contained A2A agent under app/agents/.",
            tags=["build", "agent", "a2a", "scaffold"],
            examples=["Build an agent that monitors CPU usage and alerts on spikes"],
        ),
        AgentSkill(
            id="build_mcp",
            name="Build MCP Tool",
            description="Create a new FastMCP tool/server under app/mcp/.",
            tags=["build", "mcp", "tool"],
            examples=["Build an MCP tool that queries the GitHub API"],
        ),
        AgentSkill(
            id="build_progenitor",
            name="Improve Progenitor",
            description="Apply changes to the Progenitor agent (app/progenitor/).",
            tags=["build", "progenitor", "self-improve"],
            examples=["Add a new node to the progenitor chat loop"],
        ),
        AgentSkill(
            id="upgrade_repo",
            name="Upgrade Repository",
            description="Apply dependency upgrades, refactoring, or maintenance changes to the repo.",
            tags=["upgrade", "dependencies", "refactor"],
            examples=["Upgrade all Python dependencies to latest compatible versions"],
        ),
    ],
)

# ---------------------------------------------------------------------------
# Build the A2A FastAPI application
# ---------------------------------------------------------------------------
_executor = BuilderAgentExecutor()
_task_store = InMemoryTaskStore()
_request_handler = DefaultRequestHandler(agent_executor=_executor, task_store=_task_store)

_a2a_app = A2AFastAPIApplication(agent_card=AGENT_CARD, http_handler=_request_handler)
app = _a2a_app.build()

# ---------------------------------------------------------------------------
# Extra endpoints: /health (Docker healthcheck) and startup hook
# ---------------------------------------------------------------------------
from fastapi.responses import JSONResponse  # noqa: E402


async def _health(request: Any) -> JSONResponse:
    uptime = time.time() - _start_time
    return JSONResponse(
        {
            "status": "healthy",
            "name": "Builder Agent",
            "version": "0.2.0",
            "uptime_seconds": round(uptime, 1),
            "timestamp": _utc_now_iso(),
        }
    )


# Mount /health on the Starlette app that A2AFastAPIApplication builds.
app.add_route("/health", _health, methods=["GET"])


@asynccontextmanager
async def _lifespan(_app: Any):
    port = int(os.environ.get("PORT", "8107"))
    logger.info("Builder Agent starting on port %d", port)
    await _ensure_initialized()
    _get_or_create_task_queue()
    logger.info("Builder Agent ready — listening on port %d", port)
    try:
        yield
    finally:
        await _shutdown()


async def _shutdown() -> None:
    global _task_queue
    if _task_queue is not None:
        await _task_queue.shutdown()
        _task_queue = None
    await close_skill_store()


app.router.lifespan_context = _lifespan
