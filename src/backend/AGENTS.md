# Backend — agent guide

Read root `AGENTS.md` for environment setup, shared validation and repository
safety. This component is the FastAPI gateway; its ASGI entry point is
`src.backend.main:app`. It serves APIs, while Vite/Nginx serves the frontend.

## Ownership

```text
backend/
├── main.py              # Application, middleware and router assembly
├── config.py            # Backend environment settings
├── http/                # Request IDs, acknowledgements, middleware and UI router
├── events/              # Envelopes, canonical SSE transport and document dispatch
├── storage/             # Shared Postgres document store and document-type routes
├── lifecycle/           # Startup, shutdown, process clients/state and watchers
├── chat/                # Chat routes, sessions, response execution and content streams
├── attachments/         # Upload processing and blob storage
├── agents/              # Agent catalogue and routine reconciliation
├── instructions/        # Instruction routes/service
├── knowledge/           # Knowledge pages, catalogue and indexing
├── plugins/             # Plugins and marketplaces
├── repositories/        # Declared repository operations
├── canvases/            # Canvas routes and connection-scoped interests
├── workflows/           # Execution, cron, ACP and routine event handling
├── tasks/ / schedules/  # Task/Kanban and schedule/widget APIs
├── settings/            # Preferences, providers, profiles, policies and MCP
├── insights/            # Checkpoint-backed usage accounting
├── inbox/ / alerts/ / search/
├── monitoring/          # State reader, status/metrics and logs
└── integrations/        # Runtime clients and Telegram adapters
```

Put endpoints in their feature's `routes.py`; register routers in `http/router.py`
or `main.py`. Keep persistence and execution in feature services/stores. Shared
HTTP/event/storage code must not import application assembly. Importing
`src.backend` itself must not construct the FastAPI app.

Process clients, session management and shutdown state live in `lifecycle/state.py`;
startup orchestration lives in `lifecycle/startup.py`. Keep blocking database work
off the event loop.

## Browser request and event contract

`docs/BACKEND.md` is authoritative, including exceptions. Preserve these roles:

- `GET /api/ui/stream`: one app-lifetime canonical feed, emitting `event: ui.event`
  with `UiEventEnvelope` payloads. It owns retained state and chat lifecycle.
- Finite application requests: persist/dispatch, enqueue causal canonical events,
  then return `202` with exactly `{request_id, event_types}`. Preserve the caller's
  request ID; authoritative application data arrives through events.
- `POST /api/ui/responses`: acknowledge generation. Its streaming content is
  delivered on `GET /api/ui/responses/{request_id}/stream`, containing that request's
  `chat.response.delta` and `chat.response.event` frames. Lifecycle events remain
  on the canonical stream.

Use `events.stream.broadcast_ui_event()` after persistence succeeds. Envelopes
live in `events/contracts.py`; acknowledgements live in `http/contracts.py`.
Preserve bounded replay, `Last-Event-ID`, and replay-before-`connection.ready`
ordering. Keep connection-scoped interests and response content out of global
replay where the contract requires isolation.

Chat list requests emit `chat.sessions.snapshot`. Detail requests emit
`chat.session.upserted` and `chat.session.selected` with the matching request ID;
selection in one tab must not change another tab's selected chat. Frontend pages
consume feature state through Redux rather than opening additional app-lifetime
streams.

## Running and validation

From the repository root, in the activated venv:

```bash
python -m uvicorn src.backend.main:app --host 0.0.0.0 --port 8000 --reload
pytest tests/backend/
ruff check src/backend tests/backend
pyright --pythonpath .venv/bin/python src/
```

The backend's `/healthz` route is an explicit data-free liveness exception,
returning empty HTTP 200. The runtime's `/health` is on port 9501; do not confuse
these probes with browser application-data routes. CORS comes from
`src/backend/config.py`, and Vite's API proxy comes from `src/frontend/vite.config.js`.
Generated API docs/schema are available at `/docs` and `/openapi.json`; there is
no checked-in `openapi_spec.json` to maintain.

Backend tests use shared root Postgres fixtures and local conftest mocks/cleanup.
Reuse `tests/backend/ack_helpers.py` for acknowledgement assertions and check the
causal event/persistence behavior separately. Keep source moves and protocol
changes covered at their owning layer. Follow root merge/deployment rules.
