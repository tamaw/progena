"""Observe agent A2A entry point and monitoring service."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
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
from fastapi.responses import JSONResponse
from langgraph.graph import END, StateGraph

from .config import ObserveConfig, get_config, setup_logging
from .prompts import load_prompt
from .queue_worker import ObserveTaskQueue, _QueuedTask

logger = logging.getLogger(__name__)

AGENT_ID = "observe_agent"
AGENT_NAME = "Observe Agent"
AGENT_VERSION = "0.2.0"
DEFAULT_PORT = 8080
CAPABILITIES = [
    "progenitor_health_report",
    "diagnose_last_error",
    "tail_state_and_logs",
]


class ObserveGraphState(TypedDict, total=False):
    request: str
    context: dict[str, Any]
    payload: dict[str, Any]
    tool_round: int
    max_tool_rounds: int
    last_data: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    seen_tool_sigs: set[str]
    final_text: str


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _runtime_agent_name() -> str:
    return os.getenv("AGENT_NAME", AGENT_ID).strip() or AGENT_ID


def _safe_read_text(path: Path, max_bytes: int = 512 * 1024) -> str:
    """Read up to *max_bytes* from the end of *path*."""
    try:
        if not path.exists() or not path.is_file():
            return ""
        size = path.stat().st_size
        if size <= max_bytes:
            return path.read_text(encoding="utf-8", errors="replace")
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            data = handle.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _safe_read_json(path: Path) -> Any:
    try:
        if not path.exists() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl_tail(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    text = _safe_read_text(path, max_bytes=512 * 1024)
    if not text:
        return []
    entries: list[dict[str, Any]] = []
    for line in text.splitlines()[-max(limit * 3, 200) :]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries[-limit:]


def _build_responses_endpoint(base_url: str) -> str:
    raw = (base_url or "").strip()
    if not raw:
        raise RuntimeError("AZURE_OPENAI_BASE_URL must be set for observe_agent")

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    endpoint_path = path if path.endswith("/responses") else f"{path}/responses"
    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


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
        getattr(getattr(part, "root", part), "text", "")
        for part in (message.parts or [])
        if isinstance(getattr(getattr(part, "root", part), "text", None), str)
    ).strip()


def _extract_context(message: Message | None) -> dict[str, Any]:
    if not message:
        return {}
    meta = getattr(message, "metadata", None)
    return meta if isinstance(meta, dict) else {}


class ObserveAgent:
    """Monitoring service for mounted Progenitor state and logs."""

    AGENT_ID = AGENT_ID
    AGENT_NAME = AGENT_NAME
    DESCRIPTION = (
        "Monitors Progenitor by reading mounted state/logs and producing diagnostic insights."
    )
    CAPABILITIES = CAPABILITIES

    def __init__(
        self,
        config: ObserveConfig | None = None,
        *,
        state_dir: Path | None = None,
        logs_dir: Path | None = None,
        source_logs_dir: Path | None = None,
    ) -> None:
        self.config = config or ObserveConfig.load()
        self.runtime_agent_name = _runtime_agent_name()
        self.state_dir = state_dir or self.config.storage.state_dir
        self.source_logs_dir = source_logs_dir or logs_dir or self.config.storage.log_dir
        self.agent_log_dir = self.config.storage.log_dir / self.runtime_agent_name
        self._system_prompt = load_prompt(
            "system.md",
            prompts_dir=self.config.storage.prompts_dir,
        ).strip()
        self._graph = self._build_graph()

    async def initialize(self) -> None:
        self.agent_log_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Observe agent initialised state_dir=%s source_logs_dir=%s agent_log_dir=%s",
            self.state_dir,
            self.source_logs_dir,
            self.agent_log_dir,
        )

    async def shutdown(self) -> None:
        logger.info("Observe agent shutdown complete")

    def health(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "name": self.AGENT_NAME,
            "version": AGENT_VERSION,
            "timestamp": _utc_now_iso(),
            "state_dir": str(self.state_dir),
            "source_logs_dir": str(self.source_logs_dir),
        }

    def _resolve_under(self, base_dir: Path, relative_path: str) -> Path:
        base = base_dir.resolve()
        candidate = (base_dir / relative_path).resolve()
        if str(candidate).startswith(str(base)):
            return candidate
        raise ValueError("Path escapes base directory")

    @staticmethod
    def _list_files(base_dir: Path, limit: int = 200) -> list[str]:
        if not base_dir.exists():
            return []
        results = [
            str(path.relative_to(base_dir)) for path in base_dir.rglob("*") if path.is_file()
        ]
        return sorted(results)[:limit]

    def tool_list_state_files(self) -> dict[str, Any]:
        try:
            return {"state_dir": str(self.state_dir), "files": self._list_files(self.state_dir)}
        except Exception as exc:
            return {"error": str(exc)}

    def tool_list_log_files(self) -> dict[str, Any]:
        try:
            return {
                "logs_dir": str(self.source_logs_dir),
                "files": self._list_files(self.source_logs_dir),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def tool_read_state_json(self, filename: str) -> dict[str, Any]:
        try:
            path = self._resolve_under(self.state_dir, filename)
            return {"path": str(path), "json": _safe_read_json(path)}
        except Exception as exc:
            return {"error": str(exc)}

    def tool_read_state_text_tail(self, filename: str, max_bytes: int = 131072) -> dict[str, Any]:
        try:
            path = self._resolve_under(self.state_dir, filename)
            return {
                "path": str(path),
                "text": _safe_read_text(path, max_bytes=max(1024, int(max_bytes))),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def tool_read_log_jsonl_tail(self, filename: str, limit: int = 50) -> dict[str, Any]:
        try:
            path = self._resolve_under(self.source_logs_dir, filename)
            return {
                "path": str(path),
                "entries": _read_jsonl_tail(path, limit=max(1, min(int(limit), 500))),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def tool_read_log_text_tail(self, filename: str, max_bytes: int = 262144) -> dict[str, Any]:
        try:
            path = self._resolve_under(self.source_logs_dir, filename)
            return {
                "path": str(path),
                "text": _safe_read_text(path, max_bytes=max(1024, int(max_bytes))),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _tool_defs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_state_files",
                    "description": "List files under the mounted state directory.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_log_files",
                    "description": "List files under the mounted logs directory. Paths may include nested directories.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_state_json",
                    "description": "Read a JSON file from the mounted state directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {"filename": {"type": "string"}},
                        "required": ["filename"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_state_text_tail",
                    "description": "Read the tail of a state file as text.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "max_bytes": {"type": "integer", "default": 131072},
                        },
                        "required": ["filename"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_log_jsonl_tail",
                    "description": "Read the tail of a JSONL log file from the mounted logs directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "limit": {"type": "integer", "default": 50},
                        },
                        "required": ["filename"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_log_text_tail",
                    "description": "Read the tail of a log file from the mounted logs directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "max_bytes": {"type": "integer", "default": 262144},
                        },
                        "required": ["filename"],
                    },
                },
            },
        ]

    def _execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "list_state_files":
            return self.tool_list_state_files()
        if name == "list_log_files":
            return self.tool_list_log_files()
        if name == "read_state_json":
            return self.tool_read_state_json(filename=str(args.get("filename") or ""))
        if name == "read_state_text_tail":
            return self.tool_read_state_text_tail(
                filename=str(args.get("filename") or ""),
                max_bytes=int(args.get("max_bytes") or 131072),
            )
        if name == "read_log_jsonl_tail":
            return self.tool_read_log_jsonl_tail(
                filename=str(args.get("filename") or ""),
                limit=int(args.get("limit") or 50),
            )
        if name == "read_log_text_tail":
            return self.tool_read_log_text_tail(
                filename=str(args.get("filename") or ""),
                max_bytes=int(args.get("max_bytes") or 262144),
            )
        return {"error": f"Unknown tool: {name}"}

    def _responses_headers(self) -> dict[str, str]:
        api_key = (self.config.llm.api_key or "").strip()
        if not api_key:
            raise RuntimeError("AZURE_OPENAI_API_KEY must be set for observe_agent")
        return {"Content-Type": "application/json", "api-key": api_key}

    def _responses_endpoint(self) -> str:
        return _build_responses_endpoint(self.config.llm.base_url)

    def _build_graph(self):
        async def prepare(state: ObserveGraphState) -> dict[str, Any]:
            request = (state.get("request") or "").strip() or (
                "Summarize current Progenitor health and explain any recent errors."
            )
            context = state.get("context") or {}
            input_messages: list[dict[str, Any]] = [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": request},
            ]
            if context:
                input_messages.append(
                    {"role": "user", "content": f"Context keys: {sorted(context.keys())}"}
                )

            payload: dict[str, Any] = {
                "model": self.config.llm.get_model(),
                "input": input_messages,
                "tools": self._tool_defs(),
                "tool_choice": "auto",
                "max_output_tokens": 2048,
            }
            return {
                "payload": payload,
                "tool_round": 0,
                "max_tool_rounds": max(1, min(self.config.max_tool_rounds, 30)),
            }

        async def llm_call(state: ObserveGraphState) -> dict[str, Any]:
            data = await self._post_responses(state.get("payload") or {})
            return {"last_data": data, "tool_calls": self._extract_tool_calls(data)}

        async def run_tools(state: ObserveGraphState) -> dict[str, Any]:
            tool_calls = state.get("tool_calls") or []
            if not tool_calls:
                return {}

            input_messages = (state.get("payload") or {}).get("input") or []
            seen = state.get("seen_tool_sigs")
            if not isinstance(seen, set):
                seen = set()
                state["seen_tool_sigs"] = seen

            tool_result_messages: list[dict[str, Any]] = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                call_id = call.get("call_id") or call.get("id") or call.get("tool_call_id") or ""
                name = (
                    call.get("name")
                    or call.get("tool_name")
                    or call.get("function", {}).get("name")
                    or ""
                )
                raw_args = (
                    call.get("arguments")
                    or call.get("input")
                    or call.get("params")
                    or call.get("function", {}).get("arguments")
                    or {}
                )
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {"raw": raw_args}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {"raw": raw_args}

                signature = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
                if signature in seen:
                    result = {"error": f"Repeated tool call detected: {name}"}
                else:
                    seen.add(signature)
                    result = self._execute_tool(str(name), args)

                tool_result_messages.append(
                    {
                        "role": "user",
                        "content": f"[TOOL_RESULT tool={name} id={call_id}] {json.dumps(result, default=str)}",
                    }
                )

            input_messages.extend(tool_result_messages)
            current_payload = state.get("payload") or {}
            current_payload["input"] = input_messages
            next_round = int(state.get("tool_round", 0)) + 1
            return {"payload": current_payload, "tool_round": next_round}

        async def finalize(state: ObserveGraphState) -> dict[str, Any]:
            text = self._extract_text(state.get("last_data") or {})
            if not text:
                text = "No response text returned from responses API."
            return {"final_text": text}

        def route_after_llm(state: ObserveGraphState) -> str:
            tool_calls = state.get("tool_calls") or []
            if tool_calls and int(state.get("tool_round", 0)) < int(
                state.get("max_tool_rounds", 0)
            ):
                return "run_tools"
            return "finalize"

        graph = StateGraph(ObserveGraphState)
        graph.add_node("prepare", prepare)
        graph.add_node("llm_call", llm_call)
        graph.add_node("run_tools", run_tools)
        graph.add_node("finalize", finalize)
        graph.set_entry_point("prepare")
        graph.add_edge("prepare", "llm_call")
        graph.add_conditional_edges(
            "llm_call",
            route_after_llm,
            {"run_tools": "run_tools", "finalize": "finalize"},
        )
        graph.add_edge("run_tools", "llm_call")
        graph.add_edge("finalize", END)
        return graph.compile()

    async def _post_responses(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = self._responses_endpoint()
        start = time.monotonic()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0), headers=self._responses_headers()
        ) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            data = cast(dict[str, Any], response.json())
        logger.info(
            "Observe responses call succeeded model=%s duration_ms=%.1f",
            payload.get("model"),
            (time.monotonic() - start) * 1000,
        )
        return data

    @staticmethod
    def _extract_tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        tool_call_types = {"tool_call", "function_call"}
        for item in data.get("output", []) if isinstance(data.get("output"), list) else []:
            if isinstance(item, dict) and item.get("type") in tool_call_types:
                tool_calls.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in tool_call_types:
                            tool_calls.append(part)
        if isinstance(data.get("tool_calls"), list):
            tool_calls.extend([call for call in data["tool_calls"] if isinstance(call, dict)])
        return tool_calls

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        for item in data.get("output", []) if isinstance(data.get("output"), list) else []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                        candidate = part.get("text") or part.get("value") or ""
                        if isinstance(candidate, str) and candidate.strip():
                            return candidate.strip()
        return ""

    async def process(self, request: str, context: dict[str, Any]) -> str:
        req = (
            request or ""
        ).strip() or "Summarize current Progenitor health and explain any recent errors."
        initial_state: ObserveGraphState = {
            "request": req,
            "context": context if isinstance(context, dict) else {},
        }
        result = await self._graph.ainvoke(
            initial_state,
            {"configurable": {"thread_id": f"observe_{int(time.time())}"}},
        )
        text = str(result.get("final_text") or "").strip()
        return text or "No response text returned from Observe graph."


class ObserveExecutor(AgentExecutor):
    """A2A executor that immediately accepts tasks and runs them in the background."""

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
                    task_id=task_id,
                    context_id=ctx_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_msg("Empty request."),
                        timestamp=_utc_now_iso(),
                    ),
                )
            )
            return

        if _task_queue is None:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=ctx_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_text_msg("Observe task queue is not initialised."),
                        timestamp=_utc_now_iso(),
                    ),
                )
            )
            return

        await _task_queue.enqueue(
            _QueuedTask(
                task_id=task_id,
                context_id=ctx_id,
                request_text=request_text,
                request_ctx=request_ctx,
                event_queue=event_queue,
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                final=False,
                status=TaskStatus(
                    state=TaskState.submitted,
                    message=_text_msg(f"Task accepted (id={task_id}): {request_text[:120]}"),
                    timestamp=_utc_now_iso(),
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
                    timestamp=_utc_now_iso(),
                ),
            )
        )


agent = ObserveAgent()
_PORT = int(os.environ.get("PORT", str(DEFAULT_PORT)))
_HOST = os.environ.get("A2A_PUBLIC_HOST", "localhost").strip() or "localhost"
_task_queue: ObserveTaskQueue | None = None

AGENT_CARD = AgentCard(
    name=AGENT_NAME,
    description=agent.DESCRIPTION,
    version=AGENT_VERSION,
    url=f"http://{_HOST}:{_PORT}",
    capabilities=AgentCapabilities(streaming=False, state_transition_history=True),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id=capability,
            name=capability.replace("_", " ").title(),
            description=f"Observe capability: {capability}",
            tags=["observe", "diagnostics"],
            examples=[f"Run {capability}"],
        )
        for capability in CAPABILITIES
    ],
)

_executor = ObserveExecutor()
_task_store = InMemoryTaskStore()
_handler = DefaultRequestHandler(agent_executor=_executor, task_store=_task_store)
_a2a_app = A2AFastAPIApplication(agent_card=AGENT_CARD, http_handler=_handler)
app = _a2a_app.build()


def _agent_card_payload() -> dict[str, Any]:
    return cast(dict[str, Any], AGENT_CARD.model_dump(mode="json", exclude_none=True))


async def agent_card() -> JSONResponse:
    return JSONResponse(content=_agent_card_payload())


async def _health(request: Any) -> JSONResponse:
    del request
    return JSONResponse(content=agent.health())


app.add_route("/health", _health, methods=["GET"])


@asynccontextmanager
async def _lifespan(_app: Any):
    global _task_queue
    runtime_config = get_config()
    setup_logging(runtime_config, _runtime_agent_name())
    logger.info("Observe agent starting on port %d", _PORT)
    await agent.initialize()
    _task_queue = ObserveTaskQueue(agent)
    _task_queue.start()
    logger.info("Observe agent ready")
    try:
        yield
    finally:
        logger.info("Observe agent shutting down")
        if _task_queue is not None:
            await _task_queue.stop()
            _task_queue = None
        await agent.shutdown()


app.router.lifespan_context = _lifespan
