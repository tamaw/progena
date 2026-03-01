"""Builder Copilot agent entry point."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .client import BuilderCopilotClient, ReasonedPlan
from .config import BuilderCopilotConfig, get_config, setup_logging
from .deep_agent import create_builder_copilot_agent
from .tools import ensure_workspace

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


def build_agent_card_payload(port: int, public_host: str) -> dict[str, Any]:
    """Build the A2A-style agent card payload."""
    return {
        "protocolVersion": "1.0",
        "name": AGENT_NAME,
        "description": (
            "Minimal DeepAgent builder that uses git worktrees, GitHub Copilot, "
            "test/lint commands, and GitHub CLI to produce small pull requests."
        ),
        "url": f"http://{public_host}:{port}/",
        "version": AGENT_VERSION,
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "capabilities": {"streaming": False},
        "skills": [
            {
                "id": skill_id,
                "name": skill_id.replace("-", " ").title(),
                "description": description,
                "tags": ["builder", "copilot", skill_id],
                "examples": [description],
            }
            for skill_id, description in STEP_SKILLS
        ],
    }


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


app = FastAPI()
service = BuilderCopilotService()


@app.get("/.well-known/agent.json")
async def agent_card() -> JSONResponse:
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    public_host = os.environ.get("A2A_PUBLIC_HOST", "localhost").strip() or "localhost"
    return JSONResponse(content=build_agent_card_payload(port, public_host))


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content=service.health_payload())


@app.post("/")
@app.post("/a2a")
async def handle(req: Request) -> JSONResponse:
    try:
        body = await req.json()
    except Exception:
        body = {}

    if isinstance(body, dict) and body.get("jsonrpc") == "2.0" and body.get("method") == "message/send":
        params_raw = body.get("params")
        params: dict[str, Any] = params_raw if isinstance(params_raw, dict) else {}
        message_raw = params.get("message")
        msg: dict[str, Any] = message_raw if isinstance(message_raw, dict) else {}
        parts_raw = msg.get("parts")
        parts: list[Any] = parts_raw if isinstance(parts_raw, list) else []
        request_text = ""
        for part in parts:
            if isinstance(part, dict) and part.get("kind") == "text" and isinstance(part.get("text"), str):
                request_text += part["text"]
        metadata_raw = params.get("metadata")
        context: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        text = await service.process(request_text, context)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "message": {
                        "kind": "message",
                        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                        "role": "agent",
                        "parts": [{"kind": "text", "text": text}],
                    },
                    "metadata": {},
                },
            }
        )

    request_text = body.get("request", "") if isinstance(body, dict) else ""
    context_raw = body.get("context") if isinstance(body, dict) else {}
    context: dict[str, Any] = context_raw if isinstance(context_raw, dict) else {}
    text = await service.process(str(request_text), context)
    return JSONResponse(content={"success": True, "response": text, "error": None, "metadata": {}})
