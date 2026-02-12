# Backend API

The backend is a FastAPI service that exposes OpenAI-compatible APIs and bridges requests to the Progenitor runtime.

## Runtime Topology

- Frontend (`app/frontend`) runs separately and proxies API calls to backend (`:8000`)
- Backend communicates with Progenitor primarily through IPC (socket)
- Progenitor hosts the perpetual loop and IPC server

## Main Files

- `app/backend/main.py` - FastAPI app and API routes
- `app/backend/config.py` - Environment-based server config
- `app/backend/ipc_client.py` - IPC client used by backend
- `app/backend/client.py` - Legacy subprocess client
- `app/progenitor/ipc/` - IPC protocol and server implementation

## Local Development

Always activate virtual environment first:

```powershell
& .\.venv\Scripts\Activate.ps1
```

Start Progenitor runtime:

```bash
python -m app.progenitor.main
```

Start backend API:

```bash
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

## Core Endpoints

- `GET /health` - Service health summary
- `GET /ready` - Readiness check
- `GET /live` - Liveness check
- `GET /v1/models` - OpenAI-compatible model listing
- `POST /v1/responses` - Primary response API
- `POST /chat` - Simplified chat endpoint
- `POST /v1/attachments` - Upload and register attachments
- `GET /v1/attachments/{attachment_id}` - Attachment metadata/status
- `GET /v1/session` / `POST /v1/session` - Session retrieval/update
- `GET /v1/conversation` - Retrieve conversation history

Additional UI-specific routes are mounted under `/api/ui/*`.

## Key Environment Variables

- `CHAT_SERVER_HOST` (default `0.0.0.0`)
- `CHAT_SERVER_PORT` (default `8000`)
- `CHAT_SERVER_PROGENITOR_TIMEOUT` (default `300`)
- `CHAT_SERVER_IPC_HOST` (default `127.0.0.1`)
- `CHAT_SERVER_IPC_PORT` (default `9500`)
- `CHAT_SERVER_MODEL_NAME` (default `progenitor-v1`)
- `CHAT_SERVER_RATE_LIMIT_ENABLED` (default `true`)
- `CHAT_SERVER_STREAM_ENABLED` (default `true`)

## Notes

- `POST /v1/responses` is the preferred OpenAI-style endpoint for this project.
- Legacy subprocess communication remains available for compatibility but IPC is the primary path.
