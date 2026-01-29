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
# Start the chat server
python -m uvicorn app.chat_server.main:app --host 0.0.0.0 --port 8000

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
- **Chat Server**: FastAPI-based OpenAI-compatible API at `http://localhost:8000`
- **Chat UI**: Web interface at `http://localhost:8000/ui`
- **Progenitor Agent**: Autonomous planning/execution loop

## Project Structure

```
app/
├── chat_server/     # FastAPI chat server
├── chat-ui/         # Web UI (HTML/CSS/JS)
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
| Start server | `python -m uvicorn app.chat_server.main:app --port 8000` |
| Run all tests | `pytest` |
| Run with coverage | `pytest --cov=app` |
| Type checking | `mypy app/` |
| Linting | `ruff check app/` |

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
