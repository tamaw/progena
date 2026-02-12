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
├── ipc_client.py        # IPC client for Progenitor runtime
└── client.py            # Legacy subprocess client
```

### Key Endpoints
- `POST /v1/responses` - OpenAI-compatible responses endpoint
- `GET /health` - Health check endpoint
- `GET /ready` - Readiness endpoint
- `GET /live` - Liveness endpoint

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
- Health checks return JSON status

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
