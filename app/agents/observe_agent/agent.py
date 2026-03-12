"""Observe agent (A2A).

Receives a user request, uses the same /responses flow as Progenitor,
invokes local tools to read mounted state/logs, and returns plain-text insights.
"""

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langgraph.graph import END, StateGraph


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_read_text(path: Path, max_bytes: int = 512 * 1024) -> str:
    """Read up to max_bytes from end of file (best-effort) without holding locks."""
    try:
        if not path.exists() or not path.is_file():
            return ""
        size = path.stat().st_size
        if size <= max_bytes:
            return path.read_text(encoding="utf-8", errors="replace")
        with path.open("rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _build_responses_endpoint(base_url: str) -> str:
    """Build a valid /responses endpoint from either base URL or full endpoint URL."""
    raw = (base_url or "").strip()
    if not raw:
        return "/responses"

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    endpoint_path = path if path.endswith("/responses") else f"{path}/responses"

    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


def _safe_read_json(path: Path) -> Any:
    try:
        if not path.exists() or not path.is_file():
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
        return json.loads(content)
    except Exception:
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
            if isinstance(obj, dict):
                entries.append(obj)
        except Exception:
            continue
    return entries[-limit:]


class ObserveAgent:
    AGENT_ID = "observe_agent"
    AGENT_NAME = "Observe Agent"
    DESCRIPTION = "Monitors Progenitor by reading mounted state/logs and generating diagnostic insights."
    CAPABILITIES = [
        "progenitor_health_report",
        "diagnose_last_error",
        "tail_state_and_logs",
    ]

    def __init__(
        self,
        state_dir: Path | None = None,
        logs_dir: Path | None = None,
    ):
        self.state_dir = state_dir or Path(os.getenv("OBSERVE_STATE_DIR", "/app/data/state"))
        self.logs_dir = logs_dir or Path(os.getenv("OBSERVE_LOGS_DIR", "/app/logs"))
        self._graph = self._build_graph()

    # -------------------------------------------------------------------------
    # Tooling (local, read-only)
    # -------------------------------------------------------------------------
    def _resolve_under(self, base_dir: Path, relative_path: str) -> Path:
        base = base_dir.resolve()
        candidate = (base_dir / relative_path).resolve()
        if str(candidate).startswith(str(base)):
            return candidate
        raise ValueError("Path escapes base directory")

    def tool_list_state_files(self) -> dict[str, Any]:
        try:
            files = sorted([p.name for p in self.state_dir.iterdir() if p.is_file()]) if self.state_dir.exists() else []
            return {"state_dir": str(self.state_dir), "files": files}
        except Exception as e:
            return {"error": str(e)}

    def tool_list_log_files(self) -> dict[str, Any]:
        try:
            files = sorted([p.name for p in self.logs_dir.iterdir() if p.is_file()]) if self.logs_dir.exists() else []
            return {"logs_dir": str(self.logs_dir), "files": files}
        except Exception as e:
            return {"error": str(e)}

    def tool_read_state_json(self, filename: str) -> dict[str, Any]:
        try:
            path = self._resolve_under(self.state_dir, filename)
            data = _safe_read_json(path)
            return {"path": str(path), "json": data}
        except Exception as e:
            return {"error": str(e)}

    def tool_read_state_text_tail(self, filename: str, max_bytes: int = 131072) -> dict[str, Any]:
        try:
            path = self._resolve_under(self.state_dir, filename)
            text = _safe_read_text(path, max_bytes=max(1024, int(max_bytes)))
            return {"path": str(path), "text": text}
        except Exception as e:
            return {"error": str(e)}

    def tool_read_log_jsonl_tail(self, filename: str, limit: int = 50) -> dict[str, Any]:
        try:
            path = self._resolve_under(self.logs_dir, filename)
            entries = _read_jsonl_tail(path, limit=max(1, min(int(limit), 500)))
            return {"path": str(path), "entries": entries}
        except Exception as e:
            return {"error": str(e)}

    def tool_read_log_text_tail(self, filename: str, max_bytes: int = 262144) -> dict[str, Any]:
        try:
            path = self._resolve_under(self.logs_dir, filename)
            text = _safe_read_text(path, max_bytes=max(1024, int(max_bytes)))
            return {"path": str(path), "text": text}
        except Exception as e:
            return {"error": str(e)}

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
                    "description": "List files under the mounted logs directory.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_state_json",
                    "description": "Read a JSON file from the mounted state directory (e.g. health.json, current_state.json).",
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
                    "description": "Read the tail of a state file as text (for malformed JSON or debugging).",
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
                    "description": "Read the tail of a JSONL log file as parsed JSON objects (e.g. decisions.jsonl).",
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
                    "description": "Read the tail of a log file as text (e.g. progenitor.client.stderr.log).",
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

    # -------------------------------------------------------------------------
    # /responses (tool loop)
    # -------------------------------------------------------------------------
    def _responses_endpoint(self) -> str:
        return _build_responses_endpoint(os.getenv("AZURE_OPENAI_BASE_URL", ""))

    def _responses_headers(self) -> dict[str, str]:
        key = (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
        return {"Content-Type": "application/json", "api-key": key}

    def _build_graph(self):
        """LangGraph tool-loop: prepare -> LLM -> tools -> LLM -> finalize.

        This is intentionally a small graph so we can extend it later with nodes like:
        - prefetch (read common files upfront)
        - classify (route to different diagnostics strategies)
        - memory (persist last-seen snapshots)
        """

        class ObserveGraphState(TypedDict, total=False):
            request: str
            context: dict[str, Any]
            model: str
            system_prompt: str
            payload: dict[str, Any]
            tool_round: int
            max_tool_rounds: int
            last_data: dict[str, Any]
            tool_calls: list[dict[str, Any]]
            seen_tool_sigs: set[str]
            final_text: str

        async def prepare(state: ObserveGraphState) -> dict[str, Any]:
            request = (state.get("request") or "").strip() or "Summarize current Progenitor health and explain any recent errors."
            context = state.get("context") or {}
            model = (state.get("model") or "").strip() or os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.1-codex-max").strip() or "gpt-5.1-codex-max"
            system_prompt = state.get("system_prompt")
            if not system_prompt:
                system_prompt = (
                    "You are Observe Agent, a monitoring assistant for the Progenitor runtime.\n"
                    "You have read-only tool access to Progenitor state/log mounts.\n"
                    "Use tools as needed to answer the user's question.\n"
                    "Return plain text only. Do not output JSON.\n"
                    "If you cite log/state evidence, quote small relevant snippets.\n"
                )

            input_messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request},
            ]
            if context:
                input_messages.append({"role": "user", "content": f"Context keys: {sorted(context.keys())}"})

            payload: dict[str, Any] = {
                "model": model,
                "input": input_messages,
                "tools": self._tool_defs(),
                "tool_choice": "auto",
                "max_output_tokens": 2048,
            }
            return {"payload": payload, "tool_round": 0, "max_tool_rounds": int(state.get("max_tool_rounds") or 12)}

        async def llm_call(state: ObserveGraphState) -> dict[str, Any]:
            payload = state.get("payload") or {}
            data = await self._post_responses(payload)
            tool_calls = self._extract_tool_calls(data)
            return {"last_data": data, "tool_calls": tool_calls}

        async def run_tools(state: ObserveGraphState) -> dict[str, Any]:
            tool_calls = state.get("tool_calls") or []
            if not isinstance(tool_calls, list) or not tool_calls:
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
                name = call.get("name") or call.get("tool_name") or call.get("function", {}).get("name") or ""
                raw_args = call.get("arguments") or call.get("input") or call.get("params") or call.get("function", {}).get("arguments") or {}
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {"raw": raw_args}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {"raw": raw_args}

                sig = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
                if sig in seen:
                    result = {"error": f"Repeated tool call detected: {name}"}
                else:
                    seen.add(sig)
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
            state["tool_round"] = int(state.get("tool_round", 0)) + 1
            return {"payload": current_payload, "tool_round": state["tool_round"]}

        async def finalize(state: ObserveGraphState) -> dict[str, Any]:
            data = state.get("last_data") or {}
            text = self._extract_text(data)
            if not text:
                text = "No response text returned from responses API."
            return {"final_text": text}

        def route_after_llm(state: ObserveGraphState) -> str:
            tool_calls = state.get("tool_calls") or []
            if tool_calls and int(state.get("tool_round", 0)) < int(state.get("max_tool_rounds", 0)):
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), headers=self._responses_headers()) as client:
            resp = await client.post(self._responses_endpoint(), json=payload)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

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
            tool_calls.extend([c for c in data["tool_calls"] if isinstance(c, dict)])

        return tool_calls

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        text = data.get("output_text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        # Fallback: walk output messages
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

    async def _chat_with_tools(self, *, request: str, context: dict[str, Any]) -> str:
        max_rounds = int(os.getenv("OBSERVE_MAX_TOOL_ROUNDS", "12"))
        max_rounds = max(1, min(max_rounds, 30))
        init_state = {"request": request, "context": context, "max_tool_rounds": max_rounds}
        result = await self._graph.ainvoke(init_state, {"configurable": {"thread_id": f"observe_{int(time.time())}"}})  # pyright: ignore[reportArgumentType]
        return str(result.get("final_text") or "").strip() or "No response text returned from Observe graph."

    async def process(self, request: str, context: dict[str, Any]) -> str:
        req = (request or "").strip() or "Summarize current Progenitor health and explain any recent errors."
        return await self._chat_with_tools(request=req, context=context if isinstance(context, dict) else {})


app = FastAPI()
agent = ObserveAgent()
_start_time = time.time()


@app.on_event("startup")
async def startup_event() -> None:
    port = int(os.environ.get("PORT", "8080"))
    print("STATUS:starting:Initializing Observe Agent", flush=True)
    print(f"STATUS:running:Server started on port {port}", flush=True)
    print("STATUS:waiting:Idle, waiting for requests", flush=True)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    print("STATUS:stopping:Shutting down", flush=True)
    print("STATUS:stopped:Agent terminated", flush=True)


@app.get("/.well-known/agent.json")
async def agent_card() -> JSONResponse:
    port = int(os.environ.get("PORT", "8080"))
    public_host = os.environ.get("A2A_PUBLIC_HOST", "localhost").strip() or "localhost"
    skills = [
        {
            "id": capability,
            "name": capability.replace("_", " ").title(),
            "description": f"Observe capability: {capability}",
            "tags": ["observe", "diagnostics"],
            "examples": [f"Run {capability}"],
        }
        for capability in agent.CAPABILITIES
    ]
    payload = {
        "protocolVersion": "1.0",
        "name": agent.AGENT_NAME,
        "description": agent.DESCRIPTION,
        "url": f"http://{public_host}:{port}/",
        "version": "0.1.0",
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "capabilities": {"streaming": True},
        "skills": skills,
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

    # A2A JSON-RPC: support message/send so Progenitor can call us without fallbacks.
    if isinstance(body, dict) and body.get("jsonrpc") == "2.0" and body.get("method") == "message/send":
        params_raw = body.get("params")
        params: dict[str, Any] = params_raw if isinstance(params_raw, dict) else {}
        message_raw = params.get("message")
        msg: dict[str, Any] = message_raw if isinstance(message_raw, dict) else {}
        parts_raw = msg.get("parts")
        parts: list[Any] = parts_raw if isinstance(parts_raw, list) else []
        req_str = ""
        for part in parts:
            if isinstance(part, dict) and part.get("kind") == "text" and isinstance(part.get("text"), str):
                req_str += part["text"]
        req_str = req_str.strip()
        metadata_raw = params.get("metadata")
        ctx: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}

        print("STATUS:executing:Calling responses API with tool access", flush=True)
        try:
            text = await agent.process(req_str, ctx)
            response = {
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
            print("STATUS:waiting:Idle, waiting for requests", flush=True)
            return JSONResponse(content=response)
        except Exception as e:
            print(f"STATUS:failed:{e}", flush=True)
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "error": {"code": -32000, "message": str(e)},
                },
                status_code=500,
            )

    req_str = body.get("request", "") if isinstance(body, dict) else ""
    context_raw = body.get("context") if isinstance(body, dict) else {}
    request_context: dict[str, Any] = context_raw if isinstance(context_raw, dict) else {}

    print("STATUS:executing:Calling responses API with tool access", flush=True)
    try:
        text = await agent.process(req_str, request_context)
        response = {
            "success": True,
            "response": text,
            "error": None,
            "metadata": {},
        }
        print("STATUS:waiting:Idle, waiting for requests", flush=True)
        return JSONResponse(content=response)
    except Exception as e:
        print(f"STATUS:failed:{e}", flush=True)
        err: dict[str, Any] = {
            "success": False,
            "response": None,
            "error": "Observe agent failed to generate report",
            "metadata": {"detail": str(e)},
        }
        return JSONResponse(content=err, status_code=500)


@app.post("/v1/responses")
async def responses_api(req: Request) -> JSONResponse:
    """Responses API endpoint."""
    try:
        body = await req.json()
    except Exception:
        body = {}

    request_input = (body or {}).get("input")
    request_text = ""
    if isinstance(request_input, str):
        request_text = request_input.strip()
    elif isinstance(request_input, list):
        for item in reversed(request_input):
            if isinstance(item, dict) and str(item.get("role") or "") == "user":
                request_text = str(item.get("content") or "").strip()
                if request_text:
                    break
    content = await agent.process(request_text, {}) if request_text else "Empty request."
    payload = {
        "id": f"resp-{uuid.uuid4().hex[:8]}",
        "object": "response",
        "created": int(time.time()),
        "model": str((body or {}).get("model") or "agent/observe_agent"),
        "output": [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
            }
        ],
        "usage": {
            "prompt_tokens": len(request_text.split()),
            "completion_tokens": len(content.split()),
            "total_tokens": len(request_text.split()) + len(content.split()),
        },
    }
    return JSONResponse(payload)
