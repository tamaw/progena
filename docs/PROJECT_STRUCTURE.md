# Progenitor Project Structure

This document reflects the current repository layout and the major runtime components.

## Root Layout

```
progenitor/
├── app/                         # Runtime applications (agent, backend, frontend)
├── data/                        # Persistent runtime data (state, sessions, attachments)
├── docker/                      # Docker compose files and runner Dockerfiles
├── docs/                        # Project documentation
├── prompts/                     # Prompt templates used by the agent
├── scripts/                     # Utility and demo scripts
├── tests/                       # Test suite
├── worktrees/                   # Worktree artifacts for build flows
├── logs/                        # Runtime logs
├── Dockerfile
├── pyproject.toml
└── requirements.txt
```

## Application Code

```
app/
├── progenitor/                  # Core perpetual agent
│   ├── main.py                  # Service entrypoint
│   ├── graph_chat_loop.py       # Chat loop graph topology
│   ├── graph_build_loop.py      # Build loop graph topology
│   ├── routing.py               # Graph routing helpers
│   ├── nodes/                   # Node implementations (intake, decide, act, validate, etc.)
│   ├── state/                   # State models, persistence, verification
│   ├── llm/                     # LLM provider client and prompt handling
│   ├── tools/                   # Tool registry + integrations (MCP, git, copilot)
│   ├── runtime/                 # Loop runner, scheduler, IPC, execution utilities
│   └── ipc/                     # IPC protocol/server/client
├── backend/                     # FastAPI OpenAI-compatible API
├── frontend/                    # React + Vite web client
├── agents/                      # Built/registered A2A agents
└── mcp/                         # MCP servers
```

## Runtime Data

```
data/
├── state/                       # Core state files (json)
│   ├── build_state.json
│   ├── current_state.json
│   ├── registry.json
│   └── schedule.json
├── sessions/                    # Session persistence for backend/UI
├── attachments/                 # Uploaded file storage/cache
└── memory/                      # Additional memory artifacts
```

## Docker Assets

```
docker/
├── docker-compose.progenitor.yml  # Main stack (litellm + progenitor + backend)
├── docker-compose.litellm.yml     # LiteLLM-focused stack
└── runner/
    ├── Dockerfile.agent           # Runner image for A2A agent processes
    └── Dockerfile.mcp             # Runner image for MCP processes
```

## Common Development Commands

```bash
# Activate virtual environment first
& .\.venv\Scripts\Activate.ps1

# Run tests
pytest

# Type check
mypy app/

# Lint
ruff check app/
```
