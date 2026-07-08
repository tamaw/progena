# Backend API

## What is running

The backend is the FastAPI app in `app/backend/main.py`.

Current behavior:
- API-only service: it does **not** serve frontend assets.
- Uses the HTTP client in `app/backend/ipc_client.py` (`IPCProgenitorClient`; name kept for import stability) to talk to a separately running Progenitor runtime over HTTP + SSE.
- Mounts UI-specific routes from `app/backend/ui_routes.py` under `/api/ui`.
- Initializes session management, SSE status watching, and optional Telegram integration during lifespan startup.

`app/backend/client.py` still exists, but `app/backend/main.py` currently wires up the HTTP client path, not the legacy subprocess client.

## Runtime dependency

The backend expects Progenitor's runtime HTTP server to be reachable:
- `PROGENITOR_RUNTIME_URL` (default `http://127.0.0.1:9501`)

The backend does not start Progenitor for you. In local development, start the runtime separately.

## Active endpoint surface

### Health and operations
- `GET /health` - simple health status (`healthy` or `draining`)
- `GET /health/detailed` - uptime, client status, rate limiter, shutdown info, memory, config summary
- `GET /ready` - readiness probe; fails if backend is shutting down or client setup did not complete
- `GET /live` - liveness probe
- `GET /health/features` - feature-manager status (sessions, attachments, conversation, shutdown)

`/ready` is a backend-local check; it does not perform a live handshake to Progenitor.

### Chat API
- `GET /v1/models` - advertises the configured backend model name
- `POST /v1/responses` - primary OpenAI-style endpoint used by the frontend
  - request shape: `{ "model": "...", "input": <string|message[]>, "stream": <bool> }`
  - streaming returns Server-Sent Events with `response.output_text.delta` and `response.completed`
- `POST /chat` - simple non-OpenAI helper endpoint for manual testing

The backend does **not** currently implement `/v1/chat/completions`.

### Attachments
- `POST /v1/attachments`
- `GET /v1/attachments/{attachment_id}`

Uploads are parsed through `app/backend/attachments.py`. Extracted attachment content is cached in memory for the running backend process; the configured `data/attachments/` directory is created, but this code path does not currently persist uploads there.

### Session and settings
- `GET /v1/session`
- `POST /v1/session`
- `PATCH /v1/session/preferences`
- `GET /v1/session/settings/export`
- `POST /v1/session/settings/import`

Sessions use the `progenitor_session` cookie and are persisted by `SessionManager` in `data/sessions/sessions.json`.

### Conversation history
- `GET /v1/conversation`
- `GET /v1/conversation/{message_id}`
- `DELETE /v1/conversation`

Conversation history is maintained in backend memory; it is not restored from disk on startup.

### UI routes (`/api/ui/*`)

The frontend relies on additional routes in `app/backend/ui_routes.py`, including:
- status snapshot and SSE streams: `/api/ui/status/snapshot`, `/api/ui/status`, `/api/ui/events`
- state views: `/api/ui/loops`, `/api/ui/agents`, `/api/ui/backlog`, `/api/ui/schedule`, `/api/ui/workspace`
- UI config/content: `/api/ui/widgets`, `/api/ui/widget-schemas`, `/api/ui/prompts*`
- logs and exports: `/api/ui/logs*`
- Telegram status/testing: `/api/ui/telegram/*`

These routes read repo/runtime state from file-backed runtime artifacts plus
shared documents via `app/backend/state_reader.py`.

## Important environment variables

Backend-specific:
- `CHAT_SERVER_HOST`
- `CHAT_SERVER_PORT`
- `CHAT_SERVER_PROGENITOR_TIMEOUT`
- `CHAT_SERVER_PROGENITOR_STOP_TIMEOUT`
- `PROGENITOR_RUNTIME_URL`
- `CHAT_SERVER_MODEL_NAME`
- `CHAT_SERVER_RATE_LIMIT_ENABLED`
- `CHAT_SERVER_RATE_LIMIT_PER_MINUTE`
- `CHAT_SERVER_RATE_LIMIT_PER_HOUR`

Optional Telegram integration:
- `TELEGRAM_ENABLED`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_ALLOWED_USERS`
- `TELEGRAM_POLLING_ENABLED`

The backend also depends on the Progenitor runtime's LLM configuration being valid because requests are forwarded to that runtime.

## Local development

```bash
source .venv/bin/activate
export AGENT_NAME=progenitor
python -m app.progenitor.main
```

In another shell:

```bash
source .venv/bin/activate
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

The frontend dev server proxies `/api`, `/v1`, and health endpoints to this backend.
