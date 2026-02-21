import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.progenitor.config import ProgenitorConfig, get_config, setup_logging
from app.progenitor.llm.client import LLMClient
from app.progenitor.runtime.background_build import BackgroundBuildRunner
from app.progenitor.runtime.copilot_acp import CopilotACPManager
from app.progenitor.runtime.time_utils import utcnow_isoformat
from app.progenitor.state.manager import StateManager
from app.progenitor.tools.registry import ToolRegistry

app = FastAPI()
_start_time = time.time()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class BuilderAgent:
    AGENT_ID = "builder_agent"
    AGENT_NAME = "Builder Agent"
    DESCRIPTION = "Owns build/upgrade tasks (Copilot/worktrees/PRs) on behalf of progenitor-core."
    CAPABILITIES = [
        "build_agent",
        "build_mcp",
        "build_progenitor",
        "upgrade_repo",
    ]

    def __init__(self) -> None:
        self._initialized = False
        self._config: ProgenitorConfig | None = None
        self._state_manager: StateManager | None = None
        self._llm_client: LLMClient | None = None
        self._tool_registry: ToolRegistry | None = None
        self._copilot_acp: CopilotACPManager | None = None
        self._runner: BackgroundBuildRunner | None = None

    async def initialize(self) -> None:
        if self._initialized:
            return

        project_root = Path(__file__).resolve().parents[3]
        config = get_config(project_root, project_root / "config", validate=False)
        setup_logging(config)
        self._config = config

        for p in [config.storage.state_dir, config.storage.log_dir, config.storage.worktree_dir]:
            p.mkdir(parents=True, exist_ok=True)

        self._state_manager = StateManager(
            config.storage.state_dir,
            config.storage.log_dir,
            lock_timeout=config.storage.state_lock_timeout_seconds,
        )
        await self._state_manager.initialize()

        self._llm_client = LLMClient(config=config.llm, mcp_config=config.mcp)
        self._tool_registry = ToolRegistry(config.storage.state_dir / "registry.json")
        await self._tool_registry.load()

        self._copilot_acp = CopilotACPManager(
            copilot_cmd=config.copilot.bin_path,
            model=config.copilot.model,
            yolo="--yolo" in config.copilot.default_flags,
            allowed_dirs=config.copilot.acp_allowed_dirs,
            timeout=config.copilot.timeout_seconds,
        )

        self._runner = BackgroundBuildRunner(
            state_manager=self._state_manager,
            config=config,
            llm_client=self._llm_client,
            worktrees_dir=config.storage.worktree_dir,
            copilot_acp_manager=self._copilot_acp,
        )

        self._initialized = True

    async def process(self, request: str, _context: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        assert self._runner is not None
        assert self._llm_client is not None

        from app.progenitor.runtime import decide as decide_node

        text = (request or "").strip()
        if not text:
            return {"success": False, "error": "Empty request"}

        tool_context = ""
        if self._tool_registry is not None:
            try:
                tool_context = self._tool_registry.get_tool_context()
            except Exception:
                tool_context = ""

        decision = await decide_node._request_decision_data(
            llm_client=self._llm_client,
            user_input=text,
            conversation_history=[],
            tool_context=tool_context,
        )
        action = str(decision.get("action") or "").lower().strip()
        if action not in ("build_agent", "build_mcp", "build_progenitor"):
            return {
                "success": False,
                "error": f"Request was not recognized as a build action (action={action or '(empty)'}). "
                "Be explicit (e.g. 'build an agent that ...').",
                "decision": decision,
            }

        build_settings = await decide_node._generate_build_settings(
            user_input=text,
            llm_client=self._llm_client,
            llm_action=action,
            decision_data=decision,
        )
        target_capabilities = decide_node._infer_target_capabilities(text)
        task_description = f"{action}: {build_settings.project_name}"

        build_id, ack_message = await self._runner.start_build(
            user_input=text,
            task_description=task_description,
            target_capabilities=target_capabilities,
            initial_state={},
            build_settings=build_settings,
        )

        # Wait for completion (this agent is synchronous by design).
        task = self._runner._current_task  # intentional: internal primitive
        if task is not None:
            await task

        result = self._runner.get_build_status(build_id)
        if not result:
            return {"success": False, "error": "Build completed but no result was recorded", "build_id": build_id}

        payload = {
            "build_id": build_id,
            "status": getattr(result.status, "value", str(result.status)),
            "task_description": result.task_description,
            "ack_message": ack_message,
            "output_summary": result.output_summary,
            "error_message": result.error_message,
            "iterations": result.iterations,
            "verification_score": result.verification_score,
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
        }
        return {"success": payload["status"] == "completed", "result": payload}


agent = BuilderAgent()


@app.on_event("startup")
async def startup_event() -> None:
    port = int(os.environ.get("PORT", "8107"))
    print("STATUS:starting:Initializing Builder Agent")
    await agent.initialize()
    print(f"STATUS:running:Server started on port {port}")
    print("STATUS:waiting:Idle, waiting for requests")


@app.get("/.well-known/agent.json")
async def agent_card() -> JSONResponse:
    port = int(os.environ.get("PORT", "8107"))
    payload = {
        "name": agent.AGENT_NAME,
        "description": agent.DESCRIPTION,
        "version": "0.1.0",
        "protocol": "a2a",
        "capabilities": agent.CAPABILITIES,
        "endpoint": f"http://localhost:{port}",
    }
    return JSONResponse(content=payload)


@app.get("/health")
async def health() -> JSONResponse:
    uptime = time.time() - _start_time
    payload = {
        "status": "healthy",
        "name": agent.AGENT_NAME,
        "version": "0.1.0",
        "uptime_seconds": float(uptime),
        "timestamp": _utc_now_iso(),
    }
    return JSONResponse(content=payload)


@app.post("/")
async def handle(req: Request) -> JSONResponse:
    try:
        body = await req.json()
    except Exception:
        body = {}
    req_str = body.get("request", "") if isinstance(body, dict) else ""
    ctx = body.get("context", {}) if isinstance(body, dict) else {}

    print(f"STATUS:executing:Build request received at {utcnow_isoformat()}")
    try:
        result = await agent.process(req_str, ctx)
        print("STATUS:waiting:Idle, waiting for requests")
        return JSONResponse(content={"success": True, "response": result, "error": None, "metadata": {}})
    except Exception as e:
        print("STATUS:failed:Error processing request")
        err: dict[str, Any] = {
            "success": False,
            "response": None,
            "error": str(e),
            "metadata": {},
        }
        return JSONResponse(content=err, status_code=500)

