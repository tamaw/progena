# Backend - GitHub Copilot Agent

## FastAPI Backend Agent

OpenAI-compatible API server providing responses and streaming endpoints.

### Agent Capabilities
- FastAPI application development
- OpenAI-compatible API implementation
- SSE (Server-Sent Events) streaming
- IPC via socket-based client/server
- CORS configuration
- Health checks and monitoring

### Architecture
```
backend/
├── main.py              # FastAPI app and endpoints
├── models.py            # Request/response models
├── sse_client.py        # HTTP + SSE client for Progenitor runtime
└── client.py            # Legacy subprocess client
```

- `POST /api/ui/responses` - Browser generation acknowledgement endpoint
- `GET /api/ui/stream` - Canonical browser event stream
- `GET /healthz` - Docker-only liveness probe (explicit event-stream exception; empty 200 response)
- `GET /health` - Legacy application health route (absent)
- `GET /ready` - Legacy readiness route (absent)
- `GET /live` - Legacy liveness route (absent)

### Development
```bash
# Activate venv first
& .\.venv\Scripts\Activate.ps1

# Run directly
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 --reload

# Or use VS Code task: "Start Backend (dev)"
```

### Testing
```bash
pytest tests/backend/ -v
```

### Integration Points
- **Progenitor Loop**: Communicates via IPC socket protocol
- **Frontend**: CORS enabled for `http://localhost:5173`
- **LLM**: Proxies to configured LLM provider (Azure OpenAI, OpenAI, etc.)

### Key Patterns
- Async/await throughout
- Pydantic models for validation
- Streaming responses via SSE
- `/healthz` is an explicit Docker/container liveness exception to the event-stream-only rule; it returns an empty 200 and no application data

### React UI Event Stream

The canonical frontend event feed is `GET /api/ui/stream`. It emits named SSE frames as `event: ui.event`, where each `data` payload is a `UiEventEnvelope`:

```json
{
  "id": "ui-42",
  "seq": 42,
  "type": "chat.sessions.snapshot",
  "scope": "chat",
  "session_id": null,
  "payload": {},
  "timestamp": "2026-01-01T00:00:00Z"
}
```

Use `broadcast_ui_event()` for new backend-owned UI changes after persistence succeeds. The stream keeps a bounded in-memory replay buffer and honors `Last-Event-ID`; replayed events are sent before the fresh `connection.ready` event so reconnects apply in order.

Frontend React code consumes this through Redux Toolkit listener middleware in `app/frontend/src/store/realtimeListener.js`. That listener owns the single `EventSource`, deduplicates events by id in `realtimeSlice.js`, then reducers in `uiDataSlice.js` apply domain updates. Pages should read selectors instead of opening their own long-lived UI streams.

Chat session loading follows a request-then-stream pattern. Finite browser routes return HTTP 202 with `{request_id, event_types}` and emit authoritative state on `/api/ui/stream`; the frontend left pane is populated by the streamed `chat.sessions.snapshot`. `GET /api/ui/chats/{id}?request_id=...` emits `chat.session.upserted` for unknown-sidebar safety and `chat.session.selected` with the same `request_id` for active transcript hydration. The frontend only applies matching detail events so one tab cannot steal another tab's selected chat. Mutations emit targeted events such as `chat.session.created`, `chat.session.metadata.updated`, and `chat.session.deleted`, and may also emit a fresh snapshot when the whole list ordering can change.

### Configuration
- Port: `8000` (default)
- CORS: Configured in `main.py`
- Logging: Standard FastAPI logging

### Important Notes
- Always runs on port 8000
- Frontend proxies API calls in dev mode
- Supports both streaming and non-streaming responses
- IPC client is initialized at startup

### References
- API Spec: `openapi_spec.json`
- Backend Docs: `docs/BACKEND.md`
