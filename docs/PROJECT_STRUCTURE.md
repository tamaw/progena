# Progenitor Project Structure

This document focuses on the checked-in main stack only. For runtime relationships and request flow, see [`ARCHITECTURE_DIAGRAM.md`](ARCHITECTURE_DIAGRAM.md).

## Top level

```text
progenitor/
├── app/          # Main application code
├── data/         # Runtime state and application data
├── docker/       # Container definitions for the main stack
├── docs/         # Project documentation
├── logs/         # Runtime logs
├── scripts/      # Utility scripts
├── AGENTS.md
├── CHANGELOG.md
├── README.md
└── pyproject.toml
```

This overview is intentionally selective and centered on the runtime, backend, and frontend.

## Application code (`app/`)

```text
app/
├── progenitor/   # Long-running runtime service
│   ├── agent/    # DeepAgent setup and runtime integration
│   ├── ipc/      # IPC protocol and server/client pieces
│   ├── llm/      # Model and prompt-loading helpers
│   ├── runtime/  # Loop runner, scheduler, and runtime managers
│   ├── state/    # State models and persistence helpers
│   ├── tools/    # Runtime tool registry and implementations
│   └── main.py   # Runtime entry point
├── backend/      # FastAPI API and UI data backend
│   ├── main.py   # HTTP entry point
│   ├── attachments.py
│   ├── session.py
│   ├── state_reader.py
│   └── ui_routes.py
└── frontend/     # React/Vite web client
    ├── src/pages/
    ├── src/components/
    ├── src/context/
    ├── src/hooks/
    └── src/styles/
```

## Runtime data and logs

Most runtime data is created while the system is running.

```text
data/
├── state/         # Runtime checkpoints, schedules, and health data
├── config/        # Runtime-managed agent registry and MCP config data
├── sessions/      # Backend session persistence
├── attachments/   # Uploaded file storage
├── prompts/       # Checked-in markdown prompt templates by agent
└── skills/        # Checked-in DeepAgent skill bundles by agent

logs/              # Runtime and backend logs
```

Live Progenitor MCP server definitions live in `data/config/mcp.json` so the backend
and runtime can share the same mutable config file.

## Docker assets

```text
docker/
├── docker-compose.progenitor.yml
├── progenitor/Dockerfile
├── backend/Dockerfile
└── frontend/Dockerfile
```
