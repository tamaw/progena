# Backend - GitHub Copilot Agent

## FastAPI Backend Agent

OpenAI-compatible API server providing chat completions and streaming endpoints.

### Agent Capabilities
- FastAPI application development
- OpenAI-compatible API implementation
- SSE (Server-Sent Events) streaming
- IPC via async queues
- CORS configuration
- Health checks and monitoring

### Architecture
```
backend/
├── main.py              # FastAPI app and endpoints
├── models.py            # Request/response models
└── routes/              # API route handlers
```

### Key Endpoints
- `POST /v1/chat/completions` - OpenAI-compatible chat endpoint
- `GET /health` - Health check endpoint
- `GET /` - Root endpoint with status

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
- **Progenitor Loop**: Communicates via IPC queues (`send_queue`, `receive_queue`)
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
- IPC queues created at startup

### References
- API Spec: `openapi_spec.json`
- Backend Docs: `docs/BACKEND.md`
