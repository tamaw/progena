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
    Artifact,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from .config import get_config, setup_logging
from .llm.mock_client import create_llm_client
from .runtime.background_build import BackgroundBuildRunner
from .runtime.copilot_acp import CopilotACPManager
from .runtime.time_utils import utcnow_isoformat
from .state.manager import StateManager
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)
_start_time = time.time()

# ---------------------------------------------------------------------------
# Module-level singletons — initialised once on first request.
# ---------------------------------------------------------------------------
_config = None
_state_manager: StateManager | None = None
_llm_client: Any = None
_tool_registry: ToolRegistry | None = None
_copilot_acp: CopilotACPManager | None = None
_runner: BackgroundBuildRunner | None = None
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
        rc, _out, err = await _run_git("-C", str(workspace), "checkout", "-B", "main", "origin/master")
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
    setup_logging(config, "builder_agent")
    _config = config

    await _ensure_workspace()

    for p in [config.storage.state_dir, config.storage.log_dir, config.storage.worktree_dir]:
        p.mkdir(parents=True, exist_ok=True)

    _state_manager = StateManager(
        config.storage.state_dir,
        config.storage.log_dir,
        lock_timeout=config.storage.state_lock_timeout_seconds,
    )
    await _state_manager.initialize()

    _llm_client = create_llm_client(config=config.llm, mcp_config=config.mcp)

    _tool_registry = ToolRegistry(config.storage.state_dir / "registry.json")
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


# ---------------------------------------------------------------------------
# A2A AgentExecutor implementation
# ---------------------------------------------------------------------------

class BuilderAgentExecutor(AgentExecutor):
    """Executes build tasks via the inner-loop LangGraph.

    On each ``message/send`` call the executor:
    1. Extracts the natural-language build request from the A2A Message.
    2. Emits a ``working`` status update.
    3. Kicks off the BackgroundBuildRunner (which runs the inner loop graph).
    4. Streams progress as ``TaskStatusUpdateEvent`` messages.
    5. Emits a final ``completed`` or ``failed`` status with an Artifact
       containing the build summary.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:  # noqa: C901
        await _ensure_initialized()
        assert _runner is not None
        assert _llm_client is not None
        assert _tool_registry is not None
        assert _config is not None

        task_id = context.task_id or str(uuid.uuid4())
        ctx_id = context.context_id or str(uuid.uuid4())

        # ------------------------------------------------------------------
        # Extract request text from the A2A Message
        # ------------------------------------------------------------------
        request_text = ""
        if context.message:
            for part in (context.message.parts or []):
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
                        message=_text_message("Empty build request — please describe what to build.", role=Role.agent),
                        timestamp=utcnow_isoformat(),
                    ),
                    final=True,
                )
            )
            return

        # ------------------------------------------------------------------
        # Emit "working" status
        # ------------------------------------------------------------------
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.working,
                    message=_text_message(f"Starting build: {request_text[:120]}", role=Role.agent),
                    timestamp=utcnow_isoformat(),
                ),
                final=False,
            )
        )

        # ------------------------------------------------------------------
        # Decide action via LLM and validate it is a build request
        # ------------------------------------------------------------------
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
        action = str(decision.get("action") or "").lower().strip()
        if action == "execute":
            action = "build_agent"

        if action not in ("build_agent", "build_mcp", "build_progenitor", "upgrade"):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=ctx_id,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_message(
                            f"Request not recognised as a build action (action={action or '(empty)'}). "
                            "Be explicit, e.g. 'build an agent that…'",
                            role=Role.agent,
                        ),
                        timestamp=utcnow_isoformat(),
                    ),
                    final=True,
                )
            )
            return

        # ------------------------------------------------------------------
        # Generate build settings and start the inner-loop run
        # ------------------------------------------------------------------
        build_settings = await decide_mod._generate_build_settings(
            user_input=request_text,
            llm_client=_llm_client,
            llm_action=action,
            decision_data=decision,
        )
        target_capabilities = decide_mod._infer_target_capabilities(request_text)
        task_description = f"{action}: {build_settings.project_name}"

        build_id, ack_message = await _runner.start_build(
            user_input=request_text,
            task_description=task_description,
            target_capabilities=target_capabilities,
            initial_state={},
            build_settings=build_settings,
        )

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.working,
                    message=_text_message(ack_message, role=Role.agent),
                    timestamp=utcnow_isoformat(),
                ),
                final=False,
            )
        )

        # ------------------------------------------------------------------
        # Wait for the build to complete
        # ------------------------------------------------------------------
        bg_task = _runner._current_task
        if bg_task is not None:
            await bg_task

        result = _runner.get_build_status(build_id)

        if result is None:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=ctx_id,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_message("Build completed but no result was recorded.", role=Role.agent),
                        timestamp=utcnow_isoformat(),
                    ),
                    final=True,
                )
            )
            return

        # ------------------------------------------------------------------
        # Emit result artifact + final status
        # ------------------------------------------------------------------
        status_value = getattr(result.status, "value", str(result.status))
        succeeded = status_value == "completed"

        summary_lines = [
            f"**Build ID**: {build_id}",
            f"**Status**: {status_value}",
            f"**Task**: {result.task_description}",
            f"**Iterations**: {result.iterations}",
            f"**Verification score**: {result.verification_score}",
        ]
        if result.output_summary:
            summary_lines.append(f"\n{result.output_summary}")
        if result.error_message:
            summary_lines.append(f"\n⚠️ Error: {result.error_message}")
        summary_text = "\n".join(summary_lines)

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                artifact=Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="build-result",
                    description=f"Build result for {task_description}",
                    parts=[Part(root=TextPart(text=summary_text))],
                ),
                append=False,
            )
        )

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.completed if succeeded else TaskState.failed,
                    message=_text_message(
                        "Build completed successfully." if succeeded else f"Build failed: {result.error_message}",
                        role=Role.agent,
                    ),
                    timestamp=utcnow_isoformat(),
                ),
                final=True,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel a running build task."""
        task_id = context.task_id or ""
        ctx_id = context.context_id or ""

        if _runner is not None:
            # Best-effort: cancel whatever is currently running.
            task = _runner._current_task
            if task is not None and not task.done():
                task.cancel()

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.canceled,
                    message=_text_message("Build task cancelled.", role=Role.agent),
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


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# AgentCard — advertised at /.well-known/agent.json
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
    capabilities=AgentCapabilities(streaming=False, state_transition_history=True),
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


@app.on_event("startup")
async def _startup() -> None:
    port = int(os.environ.get("PORT", "8107"))
    logger.info("Builder Agent starting on port %d", port)
    await _ensure_initialized()
    logger.info("Builder Agent ready — listening on port %d", port)
