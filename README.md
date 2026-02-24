# Progenitor Agent

A perpetual, conversational personal assistant and bootstrap builder for the TARDIS system.

## Overview

Progenitor is an always-on agent that:
- Accepts user messages conversationally (DeepAgent)
- Delegates specialized work to external A2A agents
- Maintains persistent conversation state and logs
- Runs as a perpetual loop in Docker

Build/upgrade functionality (Copilot/worktrees/PRs) is owned by a separate A2A agent: **Builder Agent** (`app/agents/builder_agent/`).

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (optional, recommended)
- LiteLLM proxy with configured models
- Copilot CLI (for build operations — required by Builder Agent only)

### Environment Setup

```bash
# Activate venv (Windows)
& .\.venv\Scripts\Activate.ps1

# Set your LiteLLM master key
export LITELLM_MASTER_KEY=your_key_here

# Install dependencies
pip install -r requirements.txt
```

### Running Locally

```bash
# Option 1: Use VS Code Tasks (recommended)
# Run "Start All (dev)" task from VS Code

# Option 2: Start each component manually
# Terminal 1: Progenitor agent
python -m app.progenitor.main

# Terminal 2: Backend API
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000

# Terminal 3: Frontend (from app/frontend directory)
cd app/frontend
npm install
npm run dev
```

Open `http://localhost:5173` for the UI (proxies to backend automatically).

### Running with Docker

```bash
# Build and run
docker compose -f docker/docker-compose.progenitor.yml up --build -d

# View logs
docker compose -f docker/docker-compose.progenitor.yml logs -f progenitor

# Stop
docker compose -f docker/docker-compose.progenitor.yml down
```

## Architecture

### LangGraph Topology (Outer Loop)

```
User messages:
Intake → Chat (DeepAgent) → Respond → Idle → (repeat)

Delegation path (build/upgrade requests):
Decide → PickNext → A2A call to Builder Agent → Respond

Scheduled jobs:
ScheduleDispatch → Intake → Chat → Respond
```

### Directory Structure

```
app/
  progenitor/           # Core perpetual agent (chat + delegation)
    main.py             # Service entrypoint
    routing.py          # Graph routing
    agent/              # DeepAgent integration layer
    llm/                # LLM client + prompts
    tools/              # Schedule, MCP, registry, shell, discovery tools
    runtime/            # Loop, scheduler, HTTP agent manager, DeepAgent adapter
    state/              # State models + persistence
    nodes/              # LangGraph nodes: intake, decide, chat, respond, idle, etc.
  agents/
    builder_agent/      # Build loop A2A agent (port 8107) — see agents/builder_agent/README.md
    hello_world_agent/  # Example A2A agent
    observe_agent/      # Monitoring A2A agent
  mcp/                  # MCP servers
.env                    # Local environment configuration
data/
  state/                # Persisted state (JSON)
  sessions/             # Session persistence
  attachments/          # Uploaded file cache/data
logs/                   # Append-only logs (JSONL)
prompts/                # Prompt templates
  skills/               # DeepAgent SKILL.md files
worktrees/              # Git worktrees for builds (used by Builder Agent)
```

### State Files

- `data/state/current_state.json` - Conversation state (messages, tool calls)
- `data/state/registry.json` - Agent and tool registry
- `data/state/schedule.json` - Scheduled tasks
- `data/state/build_checkpoints.db` - Builder Agent LangGraph checkpoints (SQLite)

### Log Files

- `logs/conversation.jsonl` - All user/assistant messages
- `logs/decisions.jsonl` - Routing decisions
- `logs/schedule.jsonl` - Schedule events

## Configuration

Configuration is sourced from environment variables. See `.env.example` for defaults.

```dotenv
# Runtime
PROGENITOR_TIMEZONE=Australia/Melbourne
PROGENITOR_IDLE_BACKOFF_SECONDS=30
PROGENITOR_MAX_BACKOFF_SECONDS=300
PROGENITOR_MAX_ACTIONS_PER_CYCLE=10

# LLM
PROGENITOR_LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT_DEFAULT=gpt-4o
```

## Usage

Progenitor accepts natural language input and decides how to handle it:

1. **Direct answers**: Simple questions answered immediately
2. **A2A delegation**: Calls existing agents for specialized tasks  
3. **MCP tools**: Uses registered MCP servers for tool access
4. **Build/Upgrade**: Delegates to Builder Agent (creates new agents/tools)
5. **Scheduling**: Creates time-based recurring or one-off tasks

### Examples

```
User: What time is it?
→ Direct answer

User: Send an email to John about the meeting
→ A2A delegation (if email agent exists)

User: Remind me every day at 9am to check my tasks
→ Schedule creation

User: new conversation
→ Start a new conversation (clears older context)

User: Build a weather MCP tool
→ A2A call to Builder Agent → OBSERVE→THINK→PLAN→BUILD→VERIFY→LEARN loop
```

## Builder Agent

The Builder Agent (`app/agents/builder_agent/`) is a fully self-contained A2A server
that owns all code-generation and repo-mutation capabilities:

- **Port**: 8107
- **Protocol**: A2A JSON-RPC (Google a2a-sdk)
- **Skills**: `build_agent`, `build_mcp`, `build_progenitor`, `upgrade_repo`
- **Loop**: OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN
- **Checkpointing**: SQLite per-build (resumable)
- **Tools**: GitHub Copilot CLI, git worktrees, gh CLI

See [`app/agents/builder_agent/README.md`](app/agents/builder_agent/README.md) for full documentation.

## Development

### Running Tests

```bash
# All tests
pytest tests/ -v

# Builder agent tests
pytest tests/agents/builder_agent/ -v

# Progenitor tests
pytest tests/progenitor/ -v
```

### Lint + Type Check

```bash
ruff check app/
mypy app/
```

### Adding New Capabilities

1. Create an agent in `app/agents/<agent_name>/`
2. Or create an MCP server in `app/mcp/`
3. Register in `data/state/registry.json`

Or ask Progenitor to build it for you — it will delegate to the Builder Agent automatically.

## Docs

- [SPEC.md](docs/SPEC.md) — System specification
- [loop.spec.md](docs/loop.spec.md) — Inner loop (Last Algorithm) spec
- [ARCHITECTURE_DIAGRAM.md](docs/ARCHITECTURE_DIAGRAM.md) — Visual architecture
- [BACKEND.md](docs/BACKEND.md) — Backend API
- [DOCKER.md](docs/DOCKER.md) — Docker deployment
- [CONTAINER_RUNNERS.md](docs/CONTAINER_RUNNERS.md) — Isolated A2A/MCP runner containers

## License

MIT
