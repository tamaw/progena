# Agent Structure Specification

## Directory Structure (CRITICAL — MUST FOLLOW EXACTLY)

**DO NOT create files directly in `app/agents/`. ALL agent files MUST go in a subdirectory.**

```
app/agents/{agent_name}/
├── __init__.py        # Required (marks as package, can be empty)
├── agent.py           # FastAPI app + agent class + LangGraph (REQUIRED)
├── config.py          # AgentConfig dataclass + setup_logging()
├── Dockerfile         # Production image (build context = repo root)
├── requirements.txt   # Python dependencies
└── README.md          # Usage and env-var documentation
```

WRONG: `app/agents/{agent_name}_agent.py` or `app/agents/{agent_name}.py`
CORRECT: `app/agents/{agent_name}/agent.py`

---

## config.py — Configuration and Logging

Every agent gets a `config.py` with an env-backed dataclass and a `setup_logging()` function.
All settings come from environment variables — never hard-code URLs, keys, or paths.

```python
"""Configuration for {agent_name}."""

import logging
import logging.handlers
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentConfig:
    azure_base_url: str = "https://example.openai.azure.com/openai/v1"
    azure_api_key: str = ""
    model: str = "gpt-5.1-codex-max"
    data_dir: Path = Path("/app/data")   # bind-mounted from host
    log_dir: Path = Path("/app/logs")    # bind-mounted from host
    port: int = 8080

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            azure_base_url=os.getenv("PROGENITOR_AZURE_BASE_URL", "").rstrip("/"),
            azure_api_key=os.getenv("PROGENITOR_AZURE_API_KEY", ""),
            model=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.1-codex-max"),
            data_dir=Path(os.getenv("AGENT_DATA_DIR", "/app/data")),
            log_dir=Path(os.getenv("AGENT_LOG_DIR", "/app/logs")),
            port=int(os.getenv("PORT", "8080")),
        )


def setup_logging(config: AgentConfig, agent_name: str) -> None:
    """Stdout + rotating file → {log_dir}/{agent_name}/{agent_name}.log (10 MB × 5)."""
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    try:
        log_subdir = config.log_dir / agent_name
        log_subdir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_subdir / f"{agent_name}.log",
            maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        fh.setFormatter(formatter)
        logging.root.addHandler(fh)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Could not create log file for %s: %s", agent_name, exc)
    logging.getLogger("httpx").setLevel(logging.WARNING)
```

---

## agent.py — Full Implementation

### Module-level setup

```python
import json, logging, sqlite3, time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, TypedDict, cast

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langgraph.graph import END, StateGraph

# Absolute imports — works because PYTHONPATH=/app/agent in Dockerfile
from config import AgentConfig, setup_logging

logger = logging.getLogger(__name__)
_config = AgentConfig.from_env()
setup_logging(_config, "{agent_name}")
```

### SQLite state persistence

State persists to `data/{agent_name}/state.db` across container restarts.
Use `check_same_thread=False` — LangGraph calls SQLite from multiple async contexts.

```python
def _init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT NOT NULL,
            request  TEXT NOT NULL,
            response TEXT
        )
    """)
    conn.commit()
    return conn

_db: sqlite3.Connection | None = None

def _get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = _init_db(_config.data_dir / "{agent_name}" / "state.db")
    return _db
```

### Agent class

```python
class MyAgent:
    AGENT_ID = "{agent_name}"           # snake_case, unique across all agents
    AGENT_NAME = "My Agent"             # human-readable
    DESCRIPTION = "What this agent does"
    CAPABILITIES = ["capability_1"]     # used for agent discovery

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._graph = self._build_graph()
```

### Tools

Define tools as OpenAI function-calling objects. Dispatch in `_execute_tool`.

```python
    def _tool_defs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "my_tool",
                    "description": "Does something useful.",
                    "parameters": {
                        "type": "object",
                        "properties": {"input": {"type": "string"}},
                        "required": ["input"],
                    },
                },
            },
        ]

    def _execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "my_tool":
            return {"result": args.get("input", "")}
        return {"error": f"Unknown tool: {name}"}
```

### Azure /responses helpers

All LLM calls go directly to Azure at `PROGENITOR_AZURE_BASE_URL/responses`.

```python
    def _responses_endpoint(self) -> str:
        return f"{self.config.azure_base_url.rstrip('/')}/responses"

    def _responses_headers(self) -> dict[str, str]:
        key = self.config.azure_api_key
        h: dict[str, str] = {"Content-Type": "application/json"}
        if key:
            h["api-key"] = key
        return h

    async def _post_responses(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0),
                                     headers=self._responses_headers()) as c:
            r = await c.post(self._responses_endpoint(), json=payload)
            r.raise_for_status()
            return cast(dict[str, Any], r.json())

    @staticmethod
    def _extract_tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        if isinstance(data.get("output"), list):
            calls += [i for i in data["output"]
                      if isinstance(i, dict)
                      and i.get("type") in {"tool_call", "function_call"}]
        if isinstance(data.get("tool_calls"), list):
            calls += [c for c in data["tool_calls"] if isinstance(c, dict)]
        return calls

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        t = data.get("output_text")
        if isinstance(t, str) and t.strip():
            return t.strip()
        for item in (data.get("output") or []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in (item.get("content") or []):
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    v = part.get("text") or part.get("value") or ""
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        return ""
```

### LangGraph tool loop

Standard four-node pattern: `prepare → llm_call ⇄ run_tools → finalize`

```python
    class _State(TypedDict, total=False):
        request: str
        context: dict[str, Any]
        payload: dict[str, Any]
        tool_round: int
        max_tool_rounds: int
        tool_calls: list[dict[str, Any]]
        seen_sigs: set[str]
        last_data: dict[str, Any]
        final_text: str

    def _build_graph(self) -> Any:
        async def prepare(state: MyAgent._State) -> dict[str, Any]:
            return {
                "payload": {
                    "model": self.config.model,
                    "input": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user",   "content": (state.get("request") or "").strip()},
                    ],
                    "tools": self._tool_defs(),
                    "tool_choice": "auto",
                    "max_output_tokens": 2048,
                },
                "tool_round": 0,
                "max_tool_rounds": 8,
            }

        async def llm_call(state: MyAgent._State) -> dict[str, Any]:
            data = await self._post_responses(state["payload"])
            return {"last_data": data, "tool_calls": self._extract_tool_calls(data)}

        async def run_tools(state: MyAgent._State) -> dict[str, Any]:
            seen: set[str] = state.get("seen_sigs") or set()
            msgs: list[dict[str, Any]] = state["payload"]["input"]
            for call in (state.get("tool_calls") or []):
                if not isinstance(call, dict):
                    continue
                call_id = call.get("call_id") or call.get("id") or ""
                name = str(call.get("name") or call.get("function", {}).get("name") or "")
                raw = (call.get("arguments") or call.get("input")
                       or call.get("function", {}).get("arguments") or {})
                args: dict[str, Any] = (
                    json.loads(raw) if isinstance(raw, str)
                    else (raw if isinstance(raw, dict) else {})
                )
                sig = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
                result = ({"error": f"Repeated: {name}"} if sig in seen
                          else self._execute_tool(name, args))
                seen.add(sig)
                msgs.append({"role": "user",
                              "content": f"[TOOL_RESULT tool={name} id={call_id}] "
                                         f"{json.dumps(result, default=str)}"})
            state["payload"]["input"] = msgs
            return {"payload": state["payload"],
                    "tool_round": int(state.get("tool_round", 0)) + 1,
                    "seen_sigs": seen}

        async def finalize(state: MyAgent._State) -> dict[str, Any]:
            return {"final_text": self._extract_text(state.get("last_data") or {}) or "No response."}

        def route(state: MyAgent._State) -> str:
            return ("run_tools"
                    if (state.get("tool_calls") or [])
                       and int(state.get("tool_round", 0)) < int(state.get("max_tool_rounds", 0))
                    else "finalize")

        g: StateGraph = StateGraph(MyAgent._State)
        g.add_node("prepare",   prepare)
        g.add_node("llm_call",  llm_call)
        g.add_node("run_tools", run_tools)
        g.add_node("finalize",  finalize)
        g.set_entry_point("prepare")
        g.add_edge("prepare", "llm_call")
        g.add_conditional_edges("llm_call", route,
                                {"run_tools": "run_tools", "finalize": "finalize"})
        g.add_edge("run_tools", "llm_call")
        g.add_edge("finalize", END)
        return g.compile()

    async def process(self, request: str, context: dict[str, Any]) -> str:
        result: dict[str, Any] = await self._graph.ainvoke(
            {"request": request, "context": context},
            {"configurable": {"thread_id": f"req_{int(time.time() * 1000)}"}},
        )
        text = str(result.get("final_text") or "No response.")
        # Persist to SQLite
        try:
            db = _get_db()
            db.execute("INSERT INTO requests (ts, request, response) VALUES (?, ?, ?)",
                       (datetime.now(UTC).isoformat(), request[:4096], text[:4096]))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB write failed: %s", exc)
        return text
```

### FastAPI app MUST support A2A JSON-RPC + Responses API

Agents MUST be callable via:

1. A2A protocol (JSON-RPC 2.0 over HTTP) using the official `a2a-sdk` (preferred).
   - Progenitor calls agents with method `message/send`.
   - The server must accept JSON-RPC at POST `/` (the default for `A2AFastAPIApplication`).
2. OpenAI Responses API for direct runtime calls:
   - POST `/v1/responses`

Minimum pattern (use this, do not invent your own protocol):

```python
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities, AgentCard, AgentSkill,
    Message, Part, Role, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_agent = MyAgent(_config)
_start_time = time.time()

def _text_message(text: str, *, role: Role = Role.agent) -> Message:
    return Message(message_id=str(uuid.uuid4()), role=role, parts=[Part(root=TextPart(text=text))])

class MyExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        request_text = ""
        if context.message:
            for part in (context.message.parts or []):
                root = getattr(part, "root", part)
                if isinstance(root, TextPart):
                    request_text += root.text
        request_text = request_text.strip()
        text = await _agent.process(request_text, context.metadata or {})
        await event_queue.enqueue_event(TaskStatusUpdateEvent(
            task_id=context.task_id or str(uuid.uuid4()),
            context_id=context.context_id or str(uuid.uuid4()),
            status=TaskStatus(
                state=TaskState.completed,
                message=_text_message(text, role=Role.agent),
                timestamp=datetime.now(UTC).isoformat(),
            ),
            final=True,
        ))

AGENT_CARD = AgentCard(
    name=_agent.AGENT_NAME,
    description=_agent.DESCRIPTION,
    version="0.1.0",
    url=f"http://localhost:{_config.port}",
    capabilities=AgentCapabilities(streaming=False, state_transition_history=False),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[AgentSkill(id=_agent.AGENT_ID, name=_agent.AGENT_NAME, description=_agent.DESCRIPTION)],
)

_executor = MyExecutor()
_task_store = InMemoryTaskStore()
_handler = DefaultRequestHandler(agent_executor=_executor, task_store=_task_store)
_a2a_app = A2AFastAPIApplication(agent_card=AGENT_CARD, http_handler=_handler)
app: FastAPI = _a2a_app.build()

@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "healthy", "uptime_seconds": time.time() - _start_time})

@app.post("/v1/responses")
async def openai_responses(req: Request) -> JSONResponse:
    body = await req.json()
    # Extract last user input and answer via _agent.process(...)
    ...
```

---

## STATUS Protocol

Agents MUST emit status lines to stdout for the agent manager:

```
STATUS:starting:Initializing {agent_name}
STATUS:running:Server started on port {port}
STATUS:executing:Processing request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:{error message}
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

---

## requirements.txt

Baseline for any LLM-backed agent:

```
a2a-sdk[http-server]>=0.3.24
fastapi
uvicorn[standard]
httpx
langgraph==1.0.9
aiosqlite
```

Add agent-specific packages below these.

---

## Dockerfile

Build context is the **repo root** (so prompts and shared assets are available).

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for layer caching
COPY app/agents/{agent_name}/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Agent source
COPY app/agents/{agent_name}/ /app/agent/

# Prompts available at /app/prompts/ if the agent loads them
COPY prompts/ /app/prompts/

# Runtime dirs — ALWAYS bind-mount these from the host.
# Never use named Docker volumes for state or logs.
RUN mkdir -p /app/data /app/logs

# Absolute imports in agent.py resolve from /app/agent/
ENV PYTHONPATH=/app/agent

# Override in docker-compose as needed
ENV AGENT_DATA_DIR=/app/data
ENV AGENT_LOG_DIR=/app/logs
ENV PROGENITOR_MODEL_DEFAULT=gpt-5.1-codex-max

EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:{port}/health')" || exit 1

WORKDIR /app/agent
CMD ["python", "-m", "uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "{port}"]
```

### docker-compose service entry

```yaml
  {agent_name}:
    build:
      context: .
      dockerfile: app/agents/{agent_name}/Dockerfile
    ports:
      - "{host_port}:{port}"
    volumes:
      - ./data:/app/data        # state persists on host
      - ./logs:/app/logs        # logs visible on host
    environment:
      PROGENITOR_AZURE_BASE_URL: ${PROGENITOR_AZURE_BASE_URL}
      PROGENITOR_AZURE_API_KEY: ${PROGENITOR_AZURE_API_KEY}
    restart: unless-stopped
```

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `PROGENITOR_AZURE_BASE_URL` | _(required)_ | Azure OpenAI base URL |
| `PROGENITOR_AZURE_API_KEY` | _(required)_ | Azure OpenAI API key |
| `PROGENITOR_MODEL_DEFAULT` | `gpt-5.1-codex-max` | LLM model name |
| `AGENT_DATA_DIR` | `/app/data` | Root for SQLite state files |
| `AGENT_LOG_DIR` | `/app/logs` | Root for rotating log files |
| `PORT` | `8080` | HTTP listen port |

---

## When to Use DeepAgent Instead

Use this **raw LangGraph** pattern when the agent is purpose-built and focused.

Use the **DeepAgent harness** (`langchain-deepagent`) when the agent needs:
- A perpetual outer loop (always running, not request/response)
- Persistent conversation sessions with SQLite checkpointing
- Skills, middleware, or sub-agent delegation
- Full parity with how progenitor or builder_agent orchestrate tasks

For DeepAgent-based agents, use `app/progenitor/agent/deep_agent.py` and
`app/agents/builder_agent/` as reference implementations.

---

## Testing

Create tests in `tests/agents/{agent_name}/`:

```python
"""Tests for {agent_name}."""
import pytest
import httpx

AGENT_URL = "http://localhost:{port}"

@pytest.mark.asyncio
async def test_health():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{AGENT_URL}/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"

@pytest.mark.asyncio
async def test_agent_card():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{AGENT_URL}/.well-known/agent.json")
    assert r.status_code == 200
    data = r.json()
    assert "name" in data and "capabilities" in data and data["protocol"] == "a2a"

@pytest.mark.asyncio
async def test_invoke():
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{AGENT_URL}/", json={"request": "Hello", "context": {}})
    assert r.status_code == 200
    assert r.json()["success"] is True
```

---

## Registration

After building, run the `rediscover_agents` system tool to register the agent in `registry.json`.
