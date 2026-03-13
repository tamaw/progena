"""Builder agent — A2A entry point, graph factory, and system prompt."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from fastapi.responses import JSONResponse
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import SecretStr

from .client import BuilderClient, ReasonedPlan
from .config import BuilderConfig, get_config, setup_logging
from .queue_worker import BuilderTaskQueue, _QueuedTask
from .tools import create_builder_tools, shutdown_builder_tool_runtimes

logger = logging.getLogger(__name__)
AGENT_SLUG = "builder_progenitor"


# ---------------------------------------------------------------------------
# Logging utilities
# ---------------------------------------------------------------------------


def _log_llm_request_response(
    log_dir: Path | None,
    request_type: str,
    model: str,
    duration_ms: float,
    success: bool,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an LLM API request/response to JSONL audit trail.

    Args:
        log_dir: Directory to write logs to. If None, skips file logging.
        request_type: Type of request (e.g., "plan_step", "model_reasoning").
        model: Model name (e.g., "gpt-5-mini").
        duration_ms: Request duration in milliseconds.
        success: Whether the request succeeded.
        error: Error message if failed.
        metadata: Additional context (e.g., retry_count, tokens_used).
    """
    entry = {
        "timestamp": time.time(),
        "request_type": request_type,
        "model": model,
        "duration_ms": round(duration_ms, 2),
        "success": success,
        "error": error,
        "metadata": metadata or {},
    }

    # Always log to Python logger
    status = "OK" if success else "FAILED"
    logger.info(
        "LLM %s: %s (%.1fms) %s",
        request_type,
        model,
        duration_ms,
        status,
    )

    # Write to JSONL file if log_dir is provided
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "llm_requests.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.warning("Failed to write LLM log to %s: %s", log_file, e)

AGENT_NAME = "Builder"
AGENT_VERSION = "0.2.0"
DEFAULT_PORT = 8108
PR_URL_RE = re.compile(r"https://github\.com/\S+/pull/\d+")
COMMITTED_TRUE_RE = re.compile(r'committed\\?":\s*true')
SUCCESS_FALSE_RE = re.compile(r'success\\?":\s*false')

STEP_SKILLS = [
    ("plan", "Plan a repository change before editing."),
    ("build", "Implement changes in a worktree with Copilot."),
    ("validate", "Run pytest, pyright, and ruff to verify code quality."),
    ("pr", "Commit, push, and create a pull request."),
]

SYSTEM_PROMPT = """\
You are Builder, a development agent that implements repository changes.

Workflow order:
1. plan — Use summarize_request then create_worktree.
2. build — Use copilot_build to implement changes in the worktree.
3. validate — Run validate_code (pytest, pyright, ruff). Fix failures and re-validate.
4. pr — commit_changes then create_pull_request.

Rules:
- Create a worktree for every task to isolate changes.
- Use search to inspect the codebase before major edits.
- Use copilot_build for implementation, run_command for manual steps.
- Always run validate_code after changes. It runs pytest, pyright, and ruff.
- If validation fails, fix issues and re-validate before proceeding.
- If commit_changes returns committed=true, you must call create_pull_request before finishing.
- create_pull_request must include title and body arguments and must return a GitHub PR URL.
- Never end the task after commit_changes unless create_pull_request succeeded or no commit was created.
- Periodically clean up old worktrees with cleanup_worktrees.
- Return a concise summary: worktree path, validation results, PR URL.\
"""


# ---------------------------------------------------------------------------
# LangChain model creation
# ---------------------------------------------------------------------------


class LoggingChatOpenAI(ChatOpenAI):
    """ChatOpenAI wrapper that logs request/response to JSONL files."""

    def __init__(self, log_dir: Path | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Use object.__setattr__ to bypass Pydantic field validation
        object.__setattr__(self, "log_dir", log_dir)

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        """Invoke with logging."""
        log_dir = object.__getattribute__(self, "log_dir")
        start_time = time.monotonic()
        error = None
        try:
            result = super().invoke(*args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            _log_llm_request_response(
                log_dir,
                "model_invoke",
                self.model,
                duration_ms,
                error is None,
                error,
                {"streaming": self.streaming},
            )

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        """Async invoke with logging."""
        log_dir = object.__getattribute__(self, "log_dir")
        start_time = time.monotonic()
        error = None
        try:
            result = await super().ainvoke(*args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            _log_llm_request_response(
                log_dir,
                "model_ainvoke",
                self.model,
                duration_ms,
                error is None,
                error,
                {"streaming": self.streaming},
            )


def _create_model(config: BuilderConfig, log_dir: Path | None = None) -> ChatOpenAI:
    """Create a ChatOpenAI pointed at Azure Responses API."""
    llm = config.llm
    model_name = llm.get_model_for_role("builder")
    base_url = llm.base_url

    # Strip /responses suffix for LangChain compatibility
    if base_url:
        parsed = urlsplit(base_url)
        path = (parsed.path or "").rstrip("/")
        if path.endswith("/responses"):
            path = path[: -len("/responses")]
        base_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    return LoggingChatOpenAI(
        log_dir=log_dir,
        model=model_name,
        base_url=base_url,
        api_key=SecretStr(llm.api_key or "not-needed"),
        default_headers={"api-key": llm.api_key or ""},
        use_responses_api=True,
        output_version="responses/v1",
        streaming=True,
        timeout=300.0,  # 5 minutes: allow time for complex reasoning
        max_retries=3,  # Retry up to 3 times on transient failures
    )


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def _create_graph(
    config: BuilderConfig,
    project_root: Path,
    log_dir: Path,
) -> CompiledStateGraph:
    """Create the DeepAgent graph with tools and shell backend."""
    model = _create_model(config, log_dir=log_dir)
    backend = LocalShellBackend(
        root_dir=project_root,
        virtual_mode=False,
        inherit_env=True,
        timeout=min(config.copilot.timeout_seconds, 1200),
    )
    tools = create_builder_tools(config=config, project_root=project_root, log_dir=log_dir)

    skills_dir = Path(__file__).resolve().parent / "skills"
    skills: list[str] = []
    if skills_dir.is_dir():
        skills = [skills_dir.relative_to(project_root).as_posix()]

    return create_deep_agent(
        name="builder",
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        skills=skills,
        backend=backend,
        middleware=[],
        checkpointer=MemorySaver(),
    )


# ---------------------------------------------------------------------------
# Builder service
# ---------------------------------------------------------------------------


class BuilderService:
    """Lazy-initialized wrapper around the DeepAgent graph."""

    def __init__(self) -> None:
        self._graph: CompiledStateGraph | None = None
        self._config: BuilderConfig | None = None
        self._planner: BuilderClient | None = None
        self._initialized = False
        self._shutting_down = False
        self._start_time = time.time()
        self._active_tasks: dict[str, asyncio.Task[str]] = {}
        self._task_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._initialized:
            return

        repo_root = Path(__file__).resolve().parents[3]
        config = get_config(repo_root)
        setup_logging(config, AGENT_SLUG)

        for p in (config.storage.state_dir, config.storage.log_dir, config.storage.worktree_dir):
            p.mkdir(parents=True, exist_ok=True)

        log_dir = config.storage.log_dir / AGENT_SLUG
        self._graph = _create_graph(config, repo_root, log_dir)
        self._config = config
        self._planner = BuilderClient(config.llm, role="planner", log_dir=log_dir)
        self._initialized = True
        logger.info("Builder initialized (root=%s)", repo_root)

    async def process(self, request_text: str, context: dict[str, Any]) -> str:
        if self._shutting_down:
            raise RuntimeError("Builder is shutting down")
        await self.initialize()
        assert self._graph is not None
        assert self._planner is not None

        plan = await self._planner.plan_step(request_text, context)
        _assert_plan_contains_pr_step(plan)
        prompt = _build_prompt(request_text, context, plan)
        thread_id = f"builder_{uuid.uuid4().hex[:12]}"
        logger.info("Builder: invoking graph (thread=%s)", thread_id)

        result = await self._graph.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            {"configurable": {"thread_id": thread_id}},
        )
        _assert_result_has_pr_when_committed(result)
        logger.info("Builder: graph complete (thread=%s)", thread_id)
        return _extract_result(result)

    async def run_task(self, task_id: str, request_text: str, context: dict[str, Any]) -> str:
        task = asyncio.create_task(self.process(request_text, context))
        async with self._task_lock:
            self._active_tasks[task_id] = task
        try:
            return await task
        finally:
            async with self._task_lock:
                self._active_tasks.pop(task_id, None)

    async def cancel_task(self, task_id: str) -> bool:
        async with self._task_lock:
            task = self._active_tasks.get(task_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def health(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "name": AGENT_NAME,
            "version": AGENT_VERSION,
            "uptime_seconds": time.time() - self._start_time,
        }

    async def shutdown(self) -> None:
        """Gracefully close shared clients/resources."""
        if not self._initialized:
            return
        self._shutting_down = True

        async with self._task_lock:
            tasks = list(self._active_tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Active builder task failed during shutdown")

        try:
            if self._planner is not None:
                await self._planner.close()
        except Exception:
            logger.exception("Failed closing builder planner client")
        finally:
            self._planner = None

        await shutdown_builder_tool_runtimes()
        self._initialized = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_result(result: dict[str, Any]) -> str:
    """Extract user-facing text from the DeepAgent result."""
    messages = result.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            content = (
                getattr(msg, "content", None)
                or (msg.get("content") if isinstance(msg, dict) else None)
            )
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, str):
                        parts.append(p)
                    elif isinstance(p, dict):
                        parts.append(str(p.get("text") or p.get("value") or ""))
                text = "".join(parts).strip()
                if text:
                    return text

    for key in ("output_text", "final_text"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    return "Builder completed but produced no text response."


def _build_prompt(
    request_text: str,
    context: dict[str, Any],
    plan: ReasonedPlan,
) -> str:
    """Build the execution prompt with the preflight plan."""
    ctx = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
    steps = "\n".join(
        f"- {s.step}: {s.objective}"
        + (f" (commands: {', '.join(s.commands)})" if s.commands else "")
        for s in plan.plan.steps
    ) or "- (none)"
    risks = "\n".join(f"- {r}" for r in (plan.plan.risks or ["none"]))

    return (
        f"User request:\n{request_text.strip()}\n\n"
        f"Execution context:\n{ctx}\n\n"
        f"Plan summary: {plan.plan.summary or 'N/A'}\n"
        f"Steps:\n{steps}\n"
        f"Risks:\n{risks}\n"
        f"Reasoning: {plan.reasoning or 'N/A'}\n\n"
        "Execution requirements:\n"
        "- Follow the workflow order: plan -> build -> validate -> pr.\n"
        "- If commit_changes returns committed=true, you must call create_pull_request(title, body, base).\n"
        "- Do not finish until create_pull_request succeeds and yields a PR URL.\n"
        "- The final report must include the PR URL when changes were committed.\n\n"
        "Execute the plan using the available tools and return a concise final report."
    )


def _assert_plan_contains_pr_step(plan: ReasonedPlan) -> None:
    has_pr = any(step.step.strip().lower() == "pr" for step in plan.plan.steps)
    if not has_pr:
        raise RuntimeError("Builder planning output is invalid: missing required 'pr' step.")


def _iter_tool_payloads(result: dict[str, Any], tool_name: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    messages = result.get("messages")
    if not isinstance(messages, list):
        return payloads

    for message in messages:
        if not isinstance(message, dict) or message.get("tool_name") != tool_name:
            continue
        content = message.get("content")
        if isinstance(content, dict):
            payloads.append(content)
            continue
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                payloads.append(parsed)
    return payloads


def _assert_result_has_pr_when_committed(result: dict[str, Any]) -> None:
    payload = json.dumps(result, default=str)
    payload_lower = payload.lower()

    commit_payloads = _iter_tool_payloads(result, "commit_changes")
    pr_payloads = _iter_tool_payloads(result, "create_pull_request")

    commit_recorded = any(payload.get("committed") is True for payload in commit_payloads)
    if not commit_recorded:
        commit_recorded = "commit_changes" in payload_lower and bool(COMMITTED_TRUE_RE.search(payload_lower))

    has_pr_url = any(
        isinstance(tool_payload.get("url"), str) and bool(PR_URL_RE.search(tool_payload["url"]))
        for tool_payload in pr_payloads
    ) or bool(PR_URL_RE.search(payload))
    pr_failed = any(tool_payload.get("success") is False for tool_payload in pr_payloads)
    if not pr_payloads:
        pr_failed = "create_pull_request" in payload_lower and bool(SUCCESS_FALSE_RE.search(payload_lower))

    if pr_failed and not has_pr_url:
        logger.error(
            "PR creation failed. Result summary: commit_recorded=%s, has_pr_url=%s, pr_payloads=%s",
            commit_recorded,
            has_pr_url,
            pr_payloads,
        )
        logger.error("Full result JSON:\n%s", payload)
        raise RuntimeError("Builder execution failed: create_pull_request returned success=false.")
    if commit_recorded and not has_pr_url:
        raise RuntimeError("Builder committed changes but did not produce a pull request URL.")


def _text_msg(text: str) -> Message:
    return Message(
        message_id=str(uuid.uuid4()),
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
    )


def _extract_request_text(message: Message | None) -> str:
    if not message:
        return ""
    return "".join(
        getattr(getattr(p, "root", p), "text", "")
        for p in (message.parts or [])
        if isinstance(getattr(getattr(p, "root", p), "text", None), str)
    ).strip()


def _extract_context(message: Message | None) -> dict[str, Any]:
    if not message:
        return {}
    meta = getattr(message, "metadata", None)
    return meta if isinstance(meta, dict) else {}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# A2A executor
# ---------------------------------------------------------------------------


class BuilderExecutor(AgentExecutor):
    """A2A AgentExecutor that immediately accepts tasks and runs them in the background.

    HTTP dispatch semantics: ``execute()`` enqueues the work and returns after
    emitting a ``submitted`` status so the HTTP layer can respond with an
    "accepted" response without blocking on execution.  Tasks that run longer
    than 30 s therefore never affect HTTP response times.

    On completion the background worker appends a JSONL record under the
    configured log directory, defaulting to
    ``/app/logs/builder_progenitor/builder_progenitor_tasks.jsonl``
    (see :mod:`queue_worker`).
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid.uuid4())
        ctx_id = context.context_id or str(uuid.uuid4())
        request_text = _extract_request_text(context.message)
        request_ctx = _extract_context(context.message)
        request_ctx.setdefault("a2a_task_id", task_id)
        request_ctx.setdefault("a2a_context_id", ctx_id)

        if not request_text:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id, context_id=ctx_id, final=True,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_msg("Empty request."),
                        timestamp=_utc_now(),
                    ),
                )
            )
            return

        # Enqueue for background execution — do not await the result here.
        queued = _task_queue is not None
        if queued:
            await _task_queue.enqueue(  # type: ignore[union-attr]
                _QueuedTask(
                    task_id=task_id,
                    context_id=ctx_id,
                    request_text=request_text,
                    request_ctx=request_ctx,
                )
            )
        else:
            logger.warning(
                "BuilderExecutor: task queue not initialised, falling back to inline execution"
                " (task_id=%s)",
                task_id,
            )

        # Immediately respond — HTTP connection is released, task runs async.
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id, context_id=ctx_id, final=True,
                status=TaskStatus(
                    state=TaskState.submitted,
                    message=_text_msg(
                        f"Task accepted (id={task_id}): {request_text[:120]}"
                    ),
                    timestamp=_utc_now(),
                ),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        cancelled = False
        if _task_queue is not None and task_id:
            cancelled = await _task_queue.cancel(task_id)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context.context_id or "",
                final=True,
                status=TaskStatus(
                    state=TaskState.canceled,
                    message=_text_msg("Cancelled." if cancelled else "No active task to cancel."),
                    timestamp=_utc_now(),
                ),
            )
        )


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

service = BuilderService()

_PORT = int(os.environ.get("PORT", str(DEFAULT_PORT)))
_HOST = os.environ.get("A2A_PUBLIC_HOST", "localhost").strip() or "localhost"

# Background task queue — initialised in _startup(), used by BuilderExecutor.
_task_queue: BuilderTaskQueue | None = None

AGENT_CARD = AgentCard(
    name=AGENT_NAME,
    description="Development agent: git worktrees, Copilot ACP, validation, pull requests.",
    version=AGENT_VERSION,
    url=f"http://{_HOST}:{_PORT}",
    capabilities=AgentCapabilities(streaming=False, state_transition_history=True),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id=sid,
            name=sid.replace("-", " ").title(),
            description=desc,
            tags=["builder", sid],
            examples=[desc],
        )
        for sid, desc in STEP_SKILLS
    ],
)

_executor = BuilderExecutor()
_task_store = InMemoryTaskStore()
_handler = DefaultRequestHandler(agent_executor=_executor, task_store=_task_store)
_a2a_app = A2AFastAPIApplication(agent_card=AGENT_CARD, http_handler=_handler)
app = _a2a_app.build()


async def _health(request: Any) -> JSONResponse:
    del request
    return JSONResponse(content=service.health())


app.add_route("/health", _health, methods=["GET"])


@asynccontextmanager
async def _lifespan(_app: Any):
    global _task_queue
    logger.info("Builder starting on port %d", _PORT)
    await service.initialize()
    _task_queue = BuilderTaskQueue(service)
    _task_queue.start()
    logger.info("Builder ready")
    try:
        yield
    finally:
        logger.info("Builder shutting down")
        if _task_queue is not None:
            await _task_queue.stop()
            _task_queue = None
        await service.shutdown()


app.router.lifespan_context = _lifespan
