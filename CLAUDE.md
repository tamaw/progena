# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Python environment

```bash
source .venv/bin/activate
export PYTHONPATH="$PWD"
```

### Running locally

Three processes must run concurrently (see `docs/LOCAL_STARTUP.md` for full detail):

```bash
# Progenitor runtime (IPC on :9500)
python -m app.progenitor.main

# Backend API (:8000)
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000

# Frontend (:5173, from app/frontend/)
npm run dev -- --port 5173
```

Postgres is required. Default local config: `postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor`. Set `PROGENITOR_DATABASE_URL` in `.env`. If `/health` hangs, the database connection is the usual cause.

```bash
# Quick health check
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:5173/health
```

### Tests

```bash
pytest tests/ -v                          # all tests
pytest tests/progenitor/ -v               # progenitor only
pytest tests/backend/ -v                  # backend only
pytest tests/agents/builder_agent/ -v     # builder agent only
pytest -m "not slow" tests/               # skip slow tests
```

`asyncio_mode = auto` is set globally; all async tests work without decoration.

### Lint and type checking

```bash
ruff check app/           # lint
ruff format app/          # format
mypy app/                 # type check
bandit -c pyproject.toml -r app/  # security scan
pre-commit run --all-files        # run all checks
```

### Mock LLM mode (no API key needed)

```bash
PROGENITOR_MOCK_LLM=true python -m app.progenitor.main
```

## Architecture

### Three-process stack

```
Browser → Frontend (Vite :5173)
             ↓ proxy /api /v1 /health
         Backend (FastAPI :8000)
             ↓ TCP socket IPC
         Progenitor runtime (:9500)
             ↓
         LangGraph + DeepAgent + Postgres
```

The frontend never talks directly to the runtime. Vite proxies all `/api/`, `/v1/`, and `/health` paths to the backend at `:8000`; the backend forwards conversation requests over IPC.

### Progenitor runtime (`app/progenitor/`)

Perpetual LangGraph agent with two loops:

- **User loop**: `Intake → DeepAgent Chat → Respond → Idle → repeat`
- **Background loop**: `PickDueSchedule → Intake → Chat → Respond → AdvanceSchedule` and `A2ACallback → RecordTaskUpdate → Notify`

Key submodules:
- `graph/` — builds both LangGraph graphs (`progenitor_graph.py`, `heartbeat_graph.py`)
- `nodes/` — factory functions returning async callables; each returns a partial state dict
- `state/models.py` — `ProgenitorState` TypedDict; `ConversationMessage`, `ScheduleEntry`, `A2ATaskRecord`
- `runtime/progenitor.py` — lifecycle (startup, Postgres init, IPC server, loop)
- `runtime/heartbeat.py` — background loop execution
- `ipc/` — TCP socket protocol between runtime and backend (`server.py`, `client.py`, `protocol.py`)
- `tools/` — 23+ LangGraph tools: `call_agent`, `schedule_task`, `mcp`, `shell`, `new_conversation`, etc.
- `agent/` — DeepAgent integration layer (`agent.py`, `middleware.py`, `subagents.py`)

State persists via native LangGraph Postgres checkpoint/store tables. The LangGraph `store` uses namespace `("progenitor", "state")` for runtime documents and `("progenitor", "logs", <name>)` for append-only logs.

### Backend (`app/backend/`)

FastAPI app (`main.py`) that is API-only — it does not serve frontend assets.

- `ipc_client.py` — talks to the runtime over TCP socket
- `ui_routes.py` — `/api/ui/*` routes for frontend status, state views, SSE streams
- `chat_routes.py` / `chat_sessions.py` — chat session management
- `document_store.py` — Postgres-backed document store
- `state_reader.py` — reads `data/state/`, `data/config/`, `logs/` for UI routes

**SSE / realtime pattern**: `GET /api/ui/stream` emits `UiEventEnvelope` frames (`event: ui.event`). Use `broadcast_ui_event()` for new backend-owned state changes. The frontend consumes this through Redux Toolkit listener middleware in `app/frontend/src/store/realtimeListener.js`; reducers in `uiDataSlice.js` apply domain updates. Pages should use selectors, not open their own streams.

**Chat session loading**: `GET /api/ui/chats` returns HTTP 200 and emits `chat.sessions.snapshot` on the stream. `GET /api/ui/chats/{id}?request_id=...` emits `chat.session.upserted` and `chat.session.selected` (matched by `request_id` so multiple tabs don't interfere).

### Frontend (`app/frontend/`)

React + Vite SPA. State management via Redux Toolkit (`app/frontend/src/store/`):
- `store.js` — Redux store
- `realtimeListener.js` — owns the single `EventSource`, deduplicates by event id
- `realtimeSlice.js` — dedup state
- `uiDataSlice.js` — domain state updated by incoming events

Router in `App.jsx` wraps all routes under `AppRuntimeShell`. Pages: ConversationPage, DashboardPage, AgentsPage, WorkspacePage, and more.

### A2A agents (`app/agents/`)

- **Builder Agent** (`builder_agent/`) — code-generation A2A server on port 8107; owns all repo-mutation capabilities (worktrees, Copilot CLI, gh CLI). Skills: `build_agent`, `build_mcp`, `build_progenitor`, `upgrade_repo`. Checkpoints in SQLite per-build.
- **Observe Agent** (`observe_agent/`) — monitoring A2A agent.
- **Builder Progenitor** (`builder_progenitor/`) — ACP-based builder variant.

### Configuration

Runtime config flows from environment variables plus shared runtime state:
- `data/config/agents.json` — registered A2A agents
- shared `mcp` state document — external MCP servers
- scheduled tasks — persisted in the shared Postgres document store (the `schedule`
  document under the `("progenitor", "state")` namespace), not a file

Key env vars: `PROGENITOR_DATABASE_URL`, `PROGENITOR_LLM_PROVIDER`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_DEFAULT`, `CHAT_SERVER_IPC_HOST`, `CHAT_SERVER_IPC_PORT`.

### Node pattern

All LangGraph nodes follow this factory pattern:

```python
def create_node(state_manager, llm_client=None) -> Callable[[ProgenitorState], Awaitable[dict]]:
    async def node(state: ProgenitorState) -> dict:
        return {"key": "value"}  # partial state update
    return node
```

### Repo safety (do not merge PRs)

Builder Agent creates PRs and waits for human approval. Do not merge PRs programmatically. Do not trigger deployments unless explicitly instructed.
