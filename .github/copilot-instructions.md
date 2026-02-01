# Progenitor - Copilot Instructions

## ⚠️ CRITICAL: Python Virtual Environment

**ALWAYS activate the Python virtual environment before running any Python commands!**

```powershell
# Windows PowerShell
& D:\tmp\agent\progenitor\.venv\Scripts\Activate.ps1

# Or use the activation script
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux/Mac
source .venv/bin/activate
```

### Running Commands

After activating venv, you can run:

```powershell
# Start the backend
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000

# Run tests
pytest

# Run specific tests
pytest tests/progenitor/ -v

# Run the main progenitor agent
python -m app.progenitor.main
```

### Why This Matters

- All dependencies (uvicorn, fastapi, langchain, etc.) are installed in `.venv`
- Running without venv will cause "command not found" errors
- The venv isolates project dependencies from system Python

---

## Project Overview

Progenitor is a dual-loop LangGraph AI agent with:
- **Backend**: FastAPI-based API-only server at `http://localhost:8000`
- **Frontend**: Standalone React app at `http://localhost:5173` (dev) or `http://localhost:4173` (prod)
- **Progenitor Agent**: Autonomous planning/execution loop

The frontend proxies `/api`, `/v1`, `/health` requests to the backend.

## Project Structure

```
app/
├── backend/         # FastAPI API server (no static files)
├── frontend/        # Web UI (React/Vite - runs standalone)
├── progenitor/      # Main agent code
│   ├── nodes/       # LangGraph nodes
│   ├── state/       # State management
│   ├── tools/       # Agent tools
│   └── llm/         # LLM client wrappers
tests/               # pytest test suite
config/              # Configuration files
docs/                # Documentation
```

## Key Commands

| Task | Command |
|------|---------|
| Activate venv | `.\.venv\Scripts\Activate.ps1` |
| Start backend | `python -m uvicorn app.backend.main:app --port 8000` |
| Start frontend (dev) | `cd app/frontend && npm run dev` |
| Start frontend (prod) | `cd app/frontend && npm run prod` |
| Run all tests | `pytest` |
| Run with coverage | `pytest --cov=app` |
| Type checking | `mypy app/` |
| Linting | `ruff check app/` |

## VS Code Tasks

Use the "Start All (dev)" task to run everything, or individual tasks:
- **Start progenitor agent (venv)** - Runs the agent
- **Start backend (venv)** - Runs the API server
- **Start frontend (dev)** - Runs Vite dev server
- **Start frontend (prod)** - Builds and previews production
- **Start All (dev)** - Runs all three in parallel (default build task)
- **Start All (prod)** - Runs all in production mode

## Environment Variables

For demo/testing without real LLM:
```powershell
$env:PROGENITOR_MOCK_LLM = "true"
```

For production with Azure OpenAI:
```powershell
$env:AZURE_OPENAI_API_KEY = "your-key"
$env:AZURE_OPENAI_ENDPOINT = "https://your-endpoint.openai.azure.com"
```
