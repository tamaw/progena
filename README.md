# Progenitor Agent

A perpetual, conversational personal assistant and bootstrap builder for the TARDIS system.

## Overview

Progenitor is an always-on agent that:
- Accepts user messages conversationally
- Routes requests: answer directly, delegate via A2A, or build/upgrade agents
- Uses Copilot CLI as the builder actuator
- Maintains persistent state and logs
- Runs as a perpetual loop in Docker

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (optional, recommended)
- LiteLLM proxy with configured models
- Copilot CLI (for build operations)

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

### LangGraph Topology

```
User (outer loop):
Intake → Chat → Respond → CycleLimit → (PickNext | Idle)

Execution path (when tools/work items are needed):
PickNext → WorktreePrep → Act → Validate → CommitCore → Respond

Background (scheduled jobs):
PickDueSchedule → Intake → Chat → Respond → AdvanceSchedule
```

### Directory Structure

```
app/
  progenitor/
    main.py          # Service entrypoint
    graph_chat_loop.py   # Chat loop topology
    graph_build_loop.py  # Build loop topology
    llm/             # OpenAI wrapper + prompts
    tools/           # Copilot, git, MCP clients
    runtime/         # Loop, scheduler, time
    state/           # State management + models
    nodes/           # LangGraph node implementations
  agents/            # A2A agents (created by Progenitor)
  mcp/               # MCP servers (created by Progenitor)
.env                 # Local environment configuration
.env.example         # Example environment configuration
data/
  state/             # Persisted state (JSON)
  sessions/          # Session persistence
  attachments/       # Uploaded file cache/data
logs/                # Append-only logs (JSONL)
scripts/             # CLI utilities
worktrees/           # Git worktrees for builds
```

### State Files

- `data/state/build_state.json` - Build state (backlog, messages, ideal_state)
- `data/state/registry.json` - Agent and tool registry
- `data/state/schedule.json` - Scheduled tasks

### Prompt Files (Editable)

- `prompts/genesis.md` - System prompt
- `prompts/decide.md` - Decision routing
- `prompts/build.md` - Build specifications

### Log Files

- `logs/conversation.jsonl` - All user/assistant messages
- `logs/decisions.jsonl` - Routing decisions
- `logs/acp_calls.jsonl` - Copilot ACP invocations
- `logs/validation.jsonl` - Validation results
- `logs/schedule.jsonl` - Schedule events

## Configuration

Configuration is sourced from environment variables. See .env.example for defaults.

```dotenv
# Runtime
PROGENITOR_TIMEZONE=Australia/Melbourne
PROGENITOR_IDLE_BACKOFF_SECONDS=30
PROGENITOR_MAX_BACKOFF_SECONDS=300
PROGENITOR_MAX_ACTIONS_PER_CYCLE=10
PROGENITOR_CHECKPOINT_INTERVAL_SECONDS=30
PROGENITOR_MAX_INNER_ITERATIONS=20

# Copilot
PROGENITOR_COPILOT_BIN=copilot
PROGENITOR_COPILOT_TIMEOUT_SECONDS=300
PROGENITOR_COPILOT_MODEL=gpt-5-mini
PROGENITOR_COPILOT_DEFAULT_FLAGS=--allow-all-tools,--no-ask-user,--no-color,--yolo
```

## Usage

Progenitor accepts natural language input and decides how to handle it:

1. **Direct answers**: Simple questions answered immediately
2. **A2A delegation**: Calls existing agents for specialized tasks
3. **MCP tools**: Uses registered MCP servers for tool access
4. **Build/Upgrade**: Creates new agents or tools via Copilot CLI
5. **Scheduling**: Creates time-based recurring or one-off tasks

### Examples

```
User: What time is it?
→ Direct answer

User: Send an email to John about the meeting
→ A2A delegation (if email agent exists) or build agent

User: Remind me every day at 9am to check my tasks
→ Schedule creation

User: I need a tool that can fetch weather data
→ Build MCP server
```

## Architecture

Progenitor implements a dual-loop architecture based on the [loop.spec.md](docs/loop.spec.md):

- **Outer Loop**: Ideal state management (define, refine, verify)
- **Inner Loop**: OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN

See [docs/](docs/) for detailed documentation:
- [SPEC.md](docs/SPEC.md) - Original system specification
- [loop.spec.md](docs/loop.spec.md) - Inner loop architecture
- [ARCHITECTURE_DIAGRAM.md](docs/ARCHITECTURE_DIAGRAM.md) - Visual architecture
- [BACKEND.md](docs/BACKEND.md) - Backend API
- [DOCKER.md](docs/DOCKER.md) - Docker deployment
- [CONTAINER_RUNNERS.md](docs/CONTAINER_RUNNERS.md) - Isolated A2A/MCP runner containers

## Development

### Running Tests

```bash
# All tests
pytest tests/ -v

# Specific test suite
pytest tests/test_dual_loop.py -v
```

### Adding New Capabilities

1. Create an agent in `build/agents/<agent_name>/`
2. Or create an MCP server in `build/mcp/`
3. Register in `data/state/registry.json`

Or simply ask Progenitor to build it for you!

## License

MIT
