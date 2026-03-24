# Progenitor Agent Specification

This document defines the common contract that every agent in `app/agents/` MUST implement. It is derived from the three existing agents — `observe_agent`, `builder_agent`, and `builder_progenitor` — and serves as the authoritative reference for building new agents.

---

## Table of Contents

1. [Directory Structure](#1-directory-structure)
2. [Identity Contract](#2-identity-contract)
3. [Configuration (`config.py`)](#3-configuration-configpy)
4. [Logging](#4-logging)
5. [HTTP Server and Endpoints](#5-http-server-and-endpoints)
6. [A2A Protocol](#6-a2a-protocol)
7. [Task Queue](#7-task-queue)
8. [LLM Integration](#8-llm-integration)
9. [STATUS Protocol](#9-status-protocol)
10. [Lifecycle Management](#10-lifecycle-management)
11. [Task Logging](#11-task-logging)
12. [Docker](#12-docker)
13. [Security](#13-security)
14. [Environment Variables Reference](#14-environment-variables-reference)

---

## 1. Directory Structure

Every agent lives in its own subdirectory under `app/agents/`. No agent files are placed directly in `app/agents/`.

```
app/agents/{agent_name}/
├── __init__.py            # Required — marks as package, can be empty
├── agent.py               # FastAPI app, agent class, LangGraph graph (REQUIRED)
├── config.py              # AgentConfig dataclass + setup_logging()
├── queue_worker.py        # Async task queue (required if tasks are long-running)
├── Dockerfile             # Production image (build context = repo root)
├── docker-entrypoint.sh   # Entrypoint wrapper script
├── requirements.txt       # Python dependencies
└── README.md              # Usage and env-var documentation
```

**Wrong:** `app/agents/my_agent.py`
**Correct:** `app/agents/my_agent/agent.py`

---

## 2. Identity Contract

Every agent class MUST declare these four class attributes:

```python
class MyAgent:
    AGENT_ID    = "my_agent"                  # snake_case, unique across all agents
    AGENT_NAME  = "My Agent"                  # human-readable display name
    DESCRIPTION = "What this agent does"      # used in agent card and discovery
    CAPABILITIES = ["capability_one",         # used for agent discovery and routing
                    "capability_two"]
```

Agents that expose versioned A2A endpoints also declare a module-level version constant:

```python
AGENT_VERSION = "0.1.0"
```

The `AGENT_ID` value MUST match the `AGENT_NAME` environment variable set in the Dockerfile and docker-compose service definition.

---

## 3. Configuration (`config.py`)

Every agent ships a `config.py` that owns all configuration. Settings are read exclusively from environment variables — never hard-coded.

### Minimal pattern

```python
"""Configuration for {agent_name}."""

import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class AgentConfig:
    # LLM
    azure_base_url: str = ""
    azure_api_key: str = ""
    model: str = "gpt-5-mini"

    # Storage
    log_dir: Path = Path("/app/logs")
    state_dir: Path = Path("/app/data/state")

    # Network
    port: int = 8080

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            azure_base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            azure_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            model=os.getenv("AGENT_MODEL_DEFAULT", "gpt-5-mini"),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", "/app/logs")),
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", "/app/data/state")),
            port=int(os.getenv("PORT", "8080")),
        )


def setup_logging(config: AgentConfig, agent_name: str) -> None:
    """Configure console + rotating file logging for this agent."""
    level = getattr(logging, os.getenv("PROGENITOR_LOG_LEVEL", "info").upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    for h in root.handlers[:]:
        h.close()
        root.removeHandler(h)
    root.setLevel(level)

    # stdout < ERROR, stderr >= ERROR
    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setLevel(level)
    stdout_h.addFilter(_BelowErrorFilter())
    stdout_h.setFormatter(formatter)
    root.addHandler(stdout_h)

    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setLevel(logging.ERROR)
    stderr_h.setFormatter(formatter)
    root.addHandler(stderr_h)

    # Per-agent rotating file: {log_dir}/{agent_name}/{agent_name}.log
    try:
        log_subdir = config.log_dir / agent_name
        log_subdir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_subdir / f"{agent_name}.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except OSError:
        pass

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


class _BelowErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR
```

### Configuration groups (larger agents)

Larger agents (e.g. `builder_agent`, `builder_progenitor`) split configuration into sub-dataclasses and combine them:

```python
@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str | None = None
    model_default: str = "gpt-5-mini"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            model_default=os.getenv("AGENT_MODEL_DEFAULT", "gpt-5-mini"),
        )


@dataclass
class StorageConfig:
    state_dir: Path = Path("/app/data/state")
    log_dir: Path = Path("/app/logs")

    @classmethod
    def from_env(cls) -> "StorageConfig":
        return cls(
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", "/app/data/state")),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", "/app/logs")),
        )


@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(llm=LLMConfig.from_env(), storage=StorageConfig.from_env())
```

---

## 4. Logging

### Required `AGENT_NAME` guard

Every agent requires the `AGENT_NAME` environment variable to derive its log path. Agents MUST call this guard during initialization:

```python
def _require_agent_name() -> str:
    agent_name = os.getenv("AGENT_NAME", "").strip()
    if not agent_name:
        raise RuntimeError("AGENT_NAME must be set")
    return agent_name
```

### Log file path

```
{PROGENITOR_LOG_DIR}/{AGENT_NAME}/{AGENT_NAME}.log
```

The log directory (`PROGENITOR_LOG_DIR`) is bind-mounted from the host in production. Agents MUST create the per-agent subdirectory before writing logs.

### LLM request/response audit log

Every LLM call MUST be logged to a per-agent JSONL file:

```
{PROGENITOR_LOG_DIR}/{AGENT_NAME}/llm_requests.jsonl
```

Each entry is a JSON object with these fields:

```json
{
  "timestamp": 1234567890.123,
  "request_type": "model_invoke",
  "model": "gpt-5-mini",
  "duration_ms": 312.5,
  "success": true,
  "error": null,
  "metadata": {}
}
```

### Log level

The log level is read from `PROGENITOR_LOG_LEVEL` (default: `"info"`). Supported values: `debug`, `info`, `warning`, `error`.

---

## 5. HTTP Server and Endpoints

All agents run a FastAPI application. The server MUST expose these endpoints:

### `GET /health`

Returns agent health status. Used by Docker `HEALTHCHECK` and the Progenitor registry.

```python
@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "healthy",
        "name": agent.AGENT_NAME,
        "version": AGENT_VERSION,
        "uptime_seconds": round(time.time() - _start_time, 1),
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    })
```

Required response fields: `status`, `name`, `version`, `uptime_seconds`, `timestamp`.

### `GET /.well-known/agent.json` and `GET /.well-known/agent-card.json`

Returns the A2A agent card (see [Section 6](#6-a2a-protocol)). Both paths MUST be registered. When using `A2AFastAPIApplication`, this endpoint is registered automatically from the `AgentCard`.

### `POST /`

Main A2A JSON-RPC endpoint. Registered automatically by `A2AFastAPIApplication`.

### `POST /v1/responses` (optional)

An OpenAI-compatible Responses API endpoint for direct LLM-style invocations. Implement this when the agent should be callable by components that speak the Responses API.

```python
@app.post("/v1/responses")
async def responses_api(req: Request) -> JSONResponse:
    body = await req.json()
    # Extract last user message from body["input"]
    ...
    return JSONResponse({
        "id": f"resp-{uuid.uuid4().hex[:8]}",
        "object": "response",
        "created": int(time.time()),
        "model": body.get("model", f"agent/{agent.AGENT_ID}"),
        "output": [{"role": "assistant", "content": [{"type": "output_text", "text": result}]}],
    })
```

---

## 6. A2A Protocol

All agents use the `a2a-sdk` to implement the A2A protocol.

### Agent card

```python
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

_PORT = int(os.environ.get("PORT", "8080"))
_PUBLIC_HOST = os.environ.get("A2A_PUBLIC_HOST", "localhost").strip() or "localhost"

AGENT_CARD = AgentCard(
    name=MyAgent.AGENT_NAME,
    description=MyAgent.DESCRIPTION,
    version=AGENT_VERSION,
    url=f"http://{_PUBLIC_HOST}:{_PORT}",
    capabilities=AgentCapabilities(streaming=False, state_transition_history=True),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="skill_id",
            name="Skill Name",
            description="What this skill does",
            tags=["tag1", "tag2"],
            examples=["Example request"],
        ),
    ],
)
```

### AgentExecutor

```python
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message, Part, Role, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart,
)


class MyExecutor(AgentExecutor):

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid.uuid4())
        ctx_id = context.context_id or str(uuid.uuid4())

        # 1. Extract request text
        request_text = _extract_request_text(context.message)

        # 2. Validate: reject empty requests
        if not request_text:
            await event_queue.enqueue_event(TaskStatusUpdateEvent(
                task_id=task_id, context_id=ctx_id, final=True,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=_text_message("Empty request.", role=Role.agent),
                    timestamp=_utc_now(),
                ),
            ))
            return

        # 3. Enqueue for background execution and respond immediately
        await task_queue.enqueue(_QueuedTask(task_id=task_id, context_id=ctx_id,
                                             request_text=request_text, request_ctx={}))
        await event_queue.enqueue_event(TaskStatusUpdateEvent(
            task_id=task_id, context_id=ctx_id, final=True,
            status=TaskStatus(
                state=TaskState.submitted,
                message=_text_message(f"Task accepted: {request_text[:120]}", role=Role.agent),
                timestamp=_utc_now(),
            ),
        ))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        cancelled = await task_queue.cancel(task_id) if task_id else False
        await event_queue.enqueue_event(TaskStatusUpdateEvent(
            task_id=task_id, context_id=context.context_id or "", final=True,
            status=TaskStatus(
                state=TaskState.canceled,
                message=_text_message("Cancelled." if cancelled else "No active task."),
                timestamp=_utc_now(),
            ),
        ))
```

### App construction

```python
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

_executor = MyExecutor()
_task_store = InMemoryTaskStore()
_handler = DefaultRequestHandler(agent_executor=_executor, task_store=_task_store)
_a2a_app = A2AFastAPIApplication(agent_card=AGENT_CARD, http_handler=_handler)
app = _a2a_app.build()

# Add extra endpoints not covered by A2AFastAPIApplication
app.add_route("/health", _health, methods=["GET"])
```

### Helper functions

These helpers are needed in every executor:

```python
def _text_message(text: str, *, role: Role = Role.agent) -> Message:
    return Message(
        message_id=str(uuid.uuid4()),
        role=role,
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
```

---

## 7. Task Queue

Long-running agents use a background task queue so the HTTP layer can return quickly without blocking on execution.

### Pattern

```python
import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class _QueuedTask:
    task_id: str
    context_id: str
    request_text: str
    request_ctx: dict[str, Any]


class AgentTaskQueue:
    """Serialised fire-and-forget task queue."""

    def __init__(self, worker_fn) -> None:
        self._worker_fn = worker_fn
        self._queue: asyncio.Queue[_QueuedTask | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._active: dict[str, asyncio.Task] = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        await self._queue.put(None)  # sentinel
        if self._task:
            await self._task

    async def enqueue(self, item: _QueuedTask) -> None:
        await self._queue.put(item)

    async def cancel(self, task_id: str) -> bool:
        t = self._active.get(task_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            t = asyncio.create_task(self._worker_fn(item))
            self._active[item.task_id] = t
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Task %s failed", item.task_id)
            finally:
                self._active.pop(item.task_id, None)
```

### Task log path

Completed tasks are appended as JSONL records at:

```
{PROGENITOR_LOG_DIR}/{AGENT_NAME}/{AGENT_NAME}_tasks.jsonl
```

See [Section 11](#11-task-logging) for the record schema.

---

## 8. LLM Integration

### Azure Responses API

All LLM calls go to the Azure OpenAI Responses API. The endpoint and headers are derived from environment variables:

```python
def _responses_endpoint(base_url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    parsed = urlsplit(base_url.rstrip("/"))
    path = (parsed.path or "").rstrip("/")
    if not path.endswith("/responses"):
        path = f"{path}/responses"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _responses_headers() -> dict[str, str]:
    key = (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
    return {"Content-Type": "application/json", "api-key": key}


async def _post_responses(payload: dict[str, Any]) -> dict[str, Any]:
    from typing import cast
    import httpx
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),
        headers=_responses_headers(),
    ) as client:
        r = await client.post(
            _responses_endpoint(os.getenv("AZURE_OPENAI_BASE_URL", "")),
            json=payload,
        )
        r.raise_for_status()
        return cast(dict[str, Any], r.json())
```

### Tool call extraction

The Responses API returns tool calls in the `output` array. Use this extractor:

```python
def _extract_tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    tool_call_types = {"tool_call", "function_call"}
    for item in (data.get("output") or []):
        if isinstance(item, dict) and item.get("type") in tool_call_types:
            calls.append(item)
    if isinstance(data.get("tool_calls"), list):
        calls.extend(c for c in data["tool_calls"] if isinstance(c, dict))
    return calls
```

### Text extraction

```python
def _extract_text(data: dict[str, Any]) -> str:
    t = data.get("output_text")
    if isinstance(t, str) and t.strip():
        return t.strip()
    for item in (data.get("output") or []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in (item.get("content") or []):
            if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                v = part.get("text") or part.get("value") or ""
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return ""
```

### LangGraph tool loop

Agents that run iterative tool calls implement a four-node LangGraph:

```
prepare → llm_call ⇄ run_tools → finalize
```

Key rules:

1. **Deduplication**: track `seen_tool_sigs` (a set of `"{name}:{json_args}"`) to detect and reject repeated identical calls.
2. **Max rounds**: cap tool rounds at `max_tool_rounds` (default 8–12) to prevent runaway loops.
3. **Tool results** are appended to the `input` list as `role=user` messages with the prefix `[TOOL_RESULT tool={name} id={call_id}]`.

```python
def route_after_llm(state) -> str:
    if state.get("tool_calls") and state.get("tool_round", 0) < state.get("max_tool_rounds", 8):
        return "run_tools"
    return "finalize"
```

### Model selection

```python
model = (
    os.getenv("AGENT_MODEL_DEFAULT")
    or "gpt-5-mini"
)
```

---

## 9. STATUS Protocol

Agents MUST emit status lines to **stdout** so the agent manager can track lifecycle events. Print these at the appropriate lifecycle moments:

```
STATUS:starting:Initializing {agent_name}
STATUS:running:Server started on port {port}
STATUS:executing:Processing request
STATUS:waiting:Idle, waiting for requests
STATUS:failed:{error message}
STATUS:stopping:Shutting down
STATUS:stopped:Agent terminated
```

Implementation:

```python
@app.on_event("startup")
async def startup_event() -> None:
    port = int(os.environ.get("PORT", "8080"))
    agent_name = os.environ.get("AGENT_NAME", "agent")
    print(f"STATUS:starting:Initializing {agent_name}", flush=True)
    print(f"STATUS:running:Server started on port {port}", flush=True)
    print("STATUS:waiting:Idle, waiting for requests", flush=True)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    print("STATUS:stopping:Shutting down", flush=True)
    print("STATUS:stopped:Agent terminated", flush=True)
```

For agents using `asynccontextmanager` lifespan instead of `on_event`, emit STATUS lines inside the lifespan function:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(_app):
    agent_name = os.environ.get("AGENT_NAME", "agent")
    port = int(os.environ.get("PORT", "8080"))
    print(f"STATUS:starting:Initializing {agent_name}", flush=True)
    await service.initialize()
    print(f"STATUS:running:Server started on port {port}", flush=True)
    print("STATUS:waiting:Idle, waiting for requests", flush=True)
    try:
        yield
    finally:
        print("STATUS:stopping:Shutting down", flush=True)
        await service.shutdown()
        print("STATUS:stopped:Agent terminated", flush=True)
```

---

## 10. Lifecycle Management

### Initialization

Agents MUST initialize lazily (on first request) or eagerly (in lifespan startup). Either pattern is acceptable; the initialization function MUST be idempotent:

```python
_initialized = False

async def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    # ... create config, setup_logging, create dirs, init state/LLM/tools ...
    _initialized = True
    logger.info("Agent initialized")
```

### Required directories

On startup, create all required directories:

```python
for path in [config.storage.log_dir, config.storage.state_dir]:
    path.mkdir(parents=True, exist_ok=True)
```

### Graceful shutdown

Agents MUST implement graceful shutdown that cancels active tasks:

```python
async def shutdown(self) -> None:
    self._shutting_down = True
    # Cancel all running tasks
    for task in list(self._active_tasks.values()):
        task.cancel()
    for task in list(self._active_tasks.values()):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    # Close open clients / connections
    await self._close_clients()
```

### Startup order

1. Read `AGENT_NAME` env var (raise `RuntimeError` if not set)
2. Load configuration from environment (`AgentConfig.from_env()`)
3. Call `setup_logging(config, agent_name)`
4. Create required filesystem directories
5. Initialize state manager / LLM client / tool registry
6. Start task queue
7. Emit `STATUS:running:...`

---

## 11. Task Logging

Completed tasks are recorded in a JSONL audit log:

**Path:** `{PROGENITOR_LOG_DIR}/{AGENT_NAME}/{AGENT_NAME}_tasks.jsonl`

**Record schema:**

```json
{
  "task_id":          "uuid-string",
  "status":           "completed | failed | cancelled",
  "phase_completed":  "name of last completed phase",
  "build_id":         "optional build identifier",
  "pr_url":           "https://github.com/.../pull/N or null",
  "timestamp_utc":    "2025-01-01T00:00:00Z",
  "error_message":    "error text or null"
}
```

Append atomically using a thread lock:

```python
import json
import threading

_append_lock = threading.Lock()

def append_task_record(record: dict, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with _append_lock:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
```

---

## 12. Docker

### Dockerfile template

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/runner \
    PYTHONPATH=/app \
    AZURE_OPENAI_BASE_URL= \
    AZURE_OPENAI_API_KEY= \
    AGENT_NAME={agent_name} \
    PROGENITOR_LOG_DIR=/app/logs \
    PROGENITOR_STATE_DIR=/app/data/state \
    AGENT_MODEL_DEFAULT= \
    PORT=8080

# Non-root user
RUN addgroup --system agent && adduser --system --ingroup agent runner

# System dependencies
RUN apt-get update && apt-get install -y \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies (cached layer)
COPY app/agents/{agent_name}/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Agent source
COPY app/agents/{agent_name}/ /app/agent/

# Prompts (if the agent loads them)
COPY prompts/ /app/prompts/

# Runtime directories — bind-mount from host in production
RUN mkdir -p /app/data/state /app/logs /app/tmp && \
    chown -R runner:agent /app/data /app/logs /app/tmp

VOLUME ["/app/data", "/app/logs"]

USER runner

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

WORKDIR /app/agent
CMD ["python", "-m", "uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]
```

### docker-compose service entry

```yaml
  {agent_name}:
    build:
      context: .
      dockerfile: app/agents/{agent_name}/Dockerfile
    ports:
      - "{host_port}:8080"
    volumes:
      - /var/lib/progenitor/logs:/app/logs
      - /var/lib/progenitor/data:/app/data
    environment:
      AZURE_OPENAI_BASE_URL: ${AZURE_OPENAI_BASE_URL}
      AZURE_OPENAI_API_KEY: ${AZURE_OPENAI_API_KEY}
      AGENT_NAME: {agent_name}
    restart: unless-stopped
```

### Port assignment

Agents use ports in the range `8100–8199`:

| Agent               | Port |
|---------------------|------|
| `observe_agent`     | 8080 |
| `builder_agent`     | 8107 |
| `builder_progenitor`| 8108 |
| New agents          | 8100–8199 (next available) |

---

## 13. Security

### Path traversal protection

Any tool that reads files from a user-supplied path MUST validate that the resolved path stays within the allowed base directory:

```python
def _resolve_under(base_dir: Path, relative_path: str) -> Path:
    base = base_dir.resolve()
    candidate = (base_dir / relative_path).resolve()
    if not str(candidate).startswith(str(base)):
        raise ValueError("Path escapes base directory")
    return candidate
```

### Empty request validation

Always reject empty requests before doing any work:

```python
if not request_text:
    await event_queue.enqueue_event(TaskStatusUpdateEvent(
        ...,
        status=TaskStatus(state=TaskState.failed, message=_text_message("Empty request.")),
    ))
    return
```

### Secret handling

- Never log API keys or secrets in plain text.
- Use `SecretStr` from Pydantic for fields that hold secrets.
- The `AZURE_OPENAI_API_KEY` value is only ever placed in HTTP headers (not in log lines).

---

## 14. Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `AGENT_NAME` | **Yes** | — | Unique agent identifier (snake_case). Must match the agent's `AGENT_ID`. Used to derive log paths. |
| `AZURE_OPENAI_BASE_URL` | **Yes** | — | Azure OpenAI base URL, e.g. `https://example.openai.azure.com/openai/v1` |
| `AZURE_OPENAI_API_KEY` | **Yes** | — | Azure OpenAI API key |
| `AGENT_MODEL_DEFAULT` | No | `gpt-5-mini` | Default LLM model name |
| `PROGENITOR_LOG_DIR` | No | `/app/logs` | Root log directory. Per-agent logs go in `{PROGENITOR_LOG_DIR}/{AGENT_NAME}/` |
| `PROGENITOR_STATE_DIR` | No | `/app/data/state` | Root state directory |
| `PORT` | No | `8080` | HTTP listen port |
| `A2A_PUBLIC_HOST` | No | `localhost` | Hostname advertised in the agent card URL |
| `PROGENITOR_LOG_LEVEL` | No | `info` | Log level: `debug`, `info`, `warning`, `error` |
| `PROGENITOR_LOG_FORMAT` | No | `text` | Log format: `text` or `json` |

### Additional variables for build-capable agents

| Variable | Default | Description |
|---|---|---|
| `PROGENITOR_REPO_URL` | — | Git repo URL for workspace initialization |
| `PROGENITOR_PROJECT_ROOT` | — | Local path to the cloned repo |
| `PROGENITOR_WORKTREE_DIR` | `/app/worktrees` | Git worktree root |
| `PROGENITOR_WORKTREES_ROOT` | `/app/worktrees` | Alternate worktree root env var |
| `GH_TOKEN` | — | GitHub Personal Access Token for `gh` CLI |
| `COPILOT_GITHUB_TOKEN` | — | Token for GitHub Copilot CLI |
| `GIT_AUTHOR_NAME` | — | Git commit author name |
| `GIT_AUTHOR_EMAIL` | — | Git commit author email |
| `DEFAULT_COPILOT_MODEL` | `gpt-5-mini` | Model for Copilot ACP |
| `OVERRIDE_AGENT_PLANNER_MODEL` | — | Override model for planner role |
| `OVERRIDE_AGENT_BUILDER_MODEL` | — | Override model for builder role |

---

## Quick Implementation Checklist

When building a new agent, verify all of the following:

- [ ] Agent lives in `app/agents/{agent_name}/` (not directly in `app/agents/`)
- [ ] `__init__.py`, `agent.py`, `config.py`, `Dockerfile`, `requirements.txt`, `README.md` all exist
- [ ] Agent class declares `AGENT_ID`, `AGENT_NAME`, `DESCRIPTION`, `CAPABILITIES`
- [ ] `config.py` has `AgentConfig.from_env()` and `setup_logging(config, agent_name)`
- [ ] `_require_agent_name()` is called during initialization
- [ ] Log directory `{PROGENITOR_LOG_DIR}/{AGENT_NAME}/` is created before writing logs
- [ ] `GET /health` returns `status`, `name`, `version`, `uptime_seconds`, `timestamp`
- [ ] `GET /.well-known/agent.json` and `GET /.well-known/agent-card.json` both work
- [ ] `AgentCard` is defined with `name`, `description`, `version`, `url`, `skills`
- [ ] `AgentExecutor.execute()` rejects empty requests with `TaskState.failed`
- [ ] `AgentExecutor.cancel()` is implemented
- [ ] Tasks are handled via an async queue (not blocking the HTTP thread)
- [ ] STATUS protocol lines are emitted to stdout on startup/shutdown/execute
- [ ] Completed tasks are logged to `{PROGENITOR_LOG_DIR}/{AGENT_NAME}/{AGENT_NAME}_tasks.jsonl`
- [ ] Path traversal protection applied to any file-reading tools
- [ ] `Dockerfile` sets `AGENT_NAME` env var and `PROGENITOR_LOG_DIR=/app/logs`
- [ ] `HEALTHCHECK` in `Dockerfile` calls `GET /health`
- [ ] Graceful shutdown cancels active tasks before exiting
- [ ] Agent is registered in `data/config/agents.json` via `rediscover_agents`
