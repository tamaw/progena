"""Builder Copilot agent entry point."""

from __future__ import annotations

import json
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
from fastapi.responses import JSONResponse

from .client import BuilderCopilotClient, ReasonedPlan
from .config import BuilderCopilotConfig, get_config, setup_logging
from .deep_agent import create_builder_copilot_agent
from .tools import ensure_workspace, preflight_copilot_startup

logger = logging.getLogger(__name__)

AGENT_NAME = "Builder Copilot"
AGENT_VERSION = "0.1.0"
DEFAULT_PORT = 8108
STEP_SKILLS = [
    ("plan", "Plan a small repository change before editing."),
    ("build", "Implement changes in a worktree with Copilot and file tools."),
    ("test", "Run focused validation commands before shipping."),
    ("lint", "Run lint/format checks before opening a PR."),
    ("pr", "Commit, push, and create a pull request once validation passes."),
    ("python-search", "Search Python sources quickly before editing."),
]

class BuilderCopilotService:
    """Lazy-initialized wrapper around the DeepAgent graph."""

    def __init__(self) -> None:
        self._graph = None
        self._config: BuilderCopilotConfig | None = None
        self._planner_client: BuilderCopilotClient | None = None
        self._initialized = False
        self._start_time = time.time()

    async def initialize(self) -> None:
        """Initialize config, logging, workspace, and the DeepAgent graph."""
        if self._initialized:
            return

        repo_root = Path(__file__).resolve().parents[3]
        config = get_config(repo_root, repo_root / "config", validate=False)
        setup_logging(config, "builder_copilot")
        await ensure_workspace()

        for path in [config.storage.state_dir, config.storage.log_dir, config.storage.worktree_dir]:
            path.mkdir(parents=True, exist_ok=True)

        preflight = await preflight_copilot_startup(
            config=config,
            project_root=repo_root,
            log_dir=config.storage.log_dir / "builder_copilot",
        )
        if not preflight.get("ready"):
            logger.warning(
                "Builder Copilot ACP preflight failed at startup: %s",
                preflight.get("error") or "unknown error",
            )

        self._graph = create_builder_copilot_agent(
            config=config,
            project_root=repo_root,
            log_dir=config.storage.log_dir / "builder_copilot",
        )
        self._config = config
        self._planner_client = BuilderCopilotClient(config.llm, role="planner")
        self._initialized = True
        logger.info("Builder Copilot initialized")

    async def process(self, request_text: str, context: dict[str, Any]) -> str:
        """Run the DeepAgent for a single request."""
        await self.initialize()
        assert self._graph is not None
        assert self._planner_client is not None

        planning_result = await _build_preflight_plan(self._planner_client, request_text, context)
        prompt = _build_execution_prompt(request_text, context, planning_result)
        result = await self._graph.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            {"configurable": {"thread_id": f"builder_copilot_{uuid.uuid4().hex[:12]}"}},
        )
        return _extract_result_text(result)

    def health_payload(self) -> dict[str, Any]:
        """Return health information."""
        return {
            "status": "healthy",
            "name": AGENT_NAME,
            "version": AGENT_VERSION,
            "uptime_seconds": float(time.time() - self._start_time),
        }


def _extract_result_text(result: dict[str, Any]) -> str:
    """Extract a user-facing text response from the DeepAgent result payload."""
    messages = result.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            content = getattr(message, "content", None)
            if isinstance(message, dict):
                content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict):
                        value = part.get("text") or part.get("value")
                        if isinstance(value, str):
                            text_parts.append(value)
                joined = "".join(text_parts).strip()
                if joined:
                    return joined

    for key in ("output_text", "final_text", "pending_response"):
        candidate = result.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    return "Builder Copilot completed, but no text response was returned."


async def _build_preflight_plan(
    planner_client: BuilderCopilotClient,
    request_text: str,
    context: dict[str, Any],
) -> ReasonedPlan | None:
    try:
        return await planner_client.plan_step(request_text, context)
    except Exception:
        logger.warning("Builder Copilot preflight planning failed; continuing without it", exc_info=True)
        return None


def _build_execution_prompt(
    request_text: str,
    context: dict[str, Any],
    planning_result: ReasonedPlan | None,
) -> str:
    ctx_json = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
    plan_block = ""
    if planning_result is not None:
        step_lines = []
        for item in planning_result.plan.steps:
            commands = ", ".join(item.commands) if item.commands else "none"
            step_lines.append(f"- {item.step}: {item.objective} (commands: {commands})")
        risk_lines = planning_result.plan.risks or ["none"]
        plan_block = (
            "Preflight plan:\n"
            f"Summary: {planning_result.plan.summary or 'No summary'}\n"
            "Steps:\n"
            f"{chr(10).join(step_lines) if step_lines else '- none'}\n"
            "Risks:\n"
            f"{chr(10).join(f'- {risk}' for risk in risk_lines)}\n"
            f"Planning reasoning summary: {planning_result.reasoning or 'No reasoning summary returned.'}\n\n"
        )

    return (
        f"User request:\n{request_text.strip()}\n\n"
        f"Execution context (JSON):\n{ctx_json}\n\n"
        f"{plan_block}"
        "Complete the work using the step skills and return a concise final report."
    )


def _text_message(text: str, *, role: Role = Role.agent) -> Message:
    return Message(
        message_id=str(uuid.uuid4()),
        role=role,
        parts=[Part(root=TextPart(text=text))],
    )


def _extract_request_text(message: Message | None) -> str:
    if message is None:
        return ""
    text_parts: list[str] = []
    for part in (message.parts or []):
        root = getattr(part, "root", part)
        if isinstance(root, TextPart):
            text_parts.append(root.text)
    return "".join(text_parts).strip()


def _extract_request_context(message: Message | None) -> dict[str, Any]:
    if message is None:
        return {}
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, dict):
        return metadata
    return {}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class BuilderCopilotExecutor(AgentExecutor):
    """SDK-backed A2A executor for Builder Copilot."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid.uuid4())
        ctx_id = context.context_id or str(uuid.uuid4())
        request_text = _extract_request_text(context.message)
        request_context = _extract_request_context(context.message)

        if not request_text:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=ctx_id,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_message("Empty request. Describe the repository change to make."),
                        timestamp=_utc_now_iso(),
                    ),
                    final=True,
                )
            )
            return

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.working,
                    message=_text_message(f"Starting Builder Copilot run: {request_text[:120]}"),
                    timestamp=_utc_now_iso(),
                ),
                final=False,
            )
        )

        try:
            response_text = await service.process(request_text, request_context)
        except Exception as exc:
            logger.exception("Builder Copilot execution failed")
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=ctx_id,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_message(f"Builder Copilot failed: {exc}"),
                        timestamp=_utc_now_iso(),
                    ),
                    final=True,
                )
            )
            return

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                artifact=Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="builder-copilot-result",
                    description="Final Builder Copilot response",
                    parts=[Part(root=TextPart(text=response_text))],
                ),
                append=False,
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.completed,
                    message=_text_message("Builder Copilot completed successfully."),
                    timestamp=_utc_now_iso(),
                ),
                final=True,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        ctx_id = context.context_id or ""
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(
                    state=TaskState.canceled,
                    message=_text_message("Builder Copilot task cancelled."),
                    timestamp=_utc_now_iso(),
                ),
                final=True,
            )
        )


service = BuilderCopilotService()
_PORT = int(os.environ.get("PORT", str(DEFAULT_PORT)))
_PUBLIC_HOST = os.environ.get("A2A_PUBLIC_HOST", "localhost").strip() or "localhost"

AGENT_CARD = AgentCard(
    name=AGENT_NAME,
    description=(
        "Minimal DeepAgent builder that uses git worktrees, GitHub Copilot, "
        "test/lint commands, and GitHub CLI to produce small pull requests."
    ),
    version=AGENT_VERSION,
    url=f"http://{_PUBLIC_HOST}:{_PORT}",
    capabilities=AgentCapabilities(streaming=False, state_transition_history=True),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id=skill_id,
            name=skill_id.replace("-", " ").title(),
            description=description,
            tags=["builder", "copilot", skill_id],
            examples=[description],
        )
        for skill_id, description in STEP_SKILLS
    ],
)

_executor = BuilderCopilotExecutor()
_task_store = InMemoryTaskStore()
_request_handler = DefaultRequestHandler(agent_executor=_executor, task_store=_task_store)
_a2a_app = A2AFastAPIApplication(agent_card=AGENT_CARD, http_handler=_request_handler)
app = _a2a_app.build()


async def _health(request: Any) -> JSONResponse:
    del request
    return JSONResponse(content=service.health_payload())


app.add_route("/health", _health, methods=["GET"])


@app.on_event("startup")
async def _startup() -> None:
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    logger.info("Builder Copilot starting on port %d", port)
    await service.initialize()
    logger.info("Builder Copilot ready on port %d", port)
