# Progenitor Agent

A perpetual, conversational personal assistant and agent orchestrator for the TARDIS system.

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
- Azure OpenAI Responses deployment
- Copilot CLI (for build operations — required by Builder Agent only)

### Environment Setup

```bash
# Activate venv (Windows)
& .\.venv\Scripts\Activate.ps1

# Optional environment-backed API keys; select the credential source in Settings.
export AZURE_OPENAI_API_KEY=your_foundry_key_here
# export OPENCODE_GO_API_KEY=your_go_key_here
# For database-stored keys, generate PROGENITOR_PROVIDER_ENCRYPTION_KEY once:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Install dependencies
pip install -e ".[dev]"
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

Open `https://localhost` for the UI (self-signed cert; the browser will warn
once) and `https://localhost:8000` for the backend API. The stack is
HTTPS-only -- there is no plain-http fallback to forget to remove before a
real deployment.

The Docker Compose stack includes a Postgres database with a named volume. The
Progenitor runtime receives `PROGENITOR_DATABASE_URL` pointing at that service.

#### Reaching the stack from another host on your network

The checked-in self-signed certs (`app/frontend/certs/`, `docker/backend/certs/`)
only cover `localhost`/`127.0.0.1` and the container's own service names, so a
browser on another machine connecting by LAN IP or hostname (e.g.
`https://192.168.0.2`) will fail TLS validation -- that address isn't in the
cert's SAN list. Regenerate with the address(es) you'll actually connect from
added, then rebuild and redeploy the frontend image:

```bash
docker/generate-certs.sh IP:192.168.0.2 DNS:cronix
docker compose -f docker/docker-compose.progenitor.yml up --build -d
```

Re-run whenever the deployment's reachable address changes.

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
    graph/              # Conversation and heartbeat graph builders
    agent/              # DeepAgent integration layer
    llm/                # LLM client + prompts
    tools/              # Schedule, MCP, registry, shell, discovery tools
    runtime/            # Loop, scheduler, persistent HTTP server, DeepAgent adapter
    state/              # State models + persistence
    nodes/              # LangGraph nodes: intake, chat, respond, schedule, A2A, health
  agents/
    builder_agent/      # Build loop A2A agent (port 8107) — see agents/builder_agent/README.md
    observe_agent/      # Monitoring A2A agent
  mcp/                  # MCP servers
.env                    # Local environment configuration
data/
  config/               # Runtime-managed config (agents, MCP, schedules)
  state/                # File-backed state for non-runtime helpers/tests
  sessions/             # Session persistence
  attachments/          # Uploaded file cache/data
  instructions/         # Agent instructions (front matter + markdown body)
  skills/               # DeepAgent SKILL.md files (grouped by agent)
  repos/                # Git worktrees for builds (used by Builder Agent)
logs/                   # File-backed logs for non-runtime helpers/tests
```

### Runtime persistence

- Native LangGraph checkpoint tables store conversation checkpoint history.
- Native LangGraph `store` table stores Progenitor runtime documents under
  `("progenitor", "state")`, including current state, schedules, MCP config,
  agent registry data, background tasks, A2A tasks, and async subagent tasks.
- Native LangGraph `store` table also stores structured append-only logs under
  `("progenitor", "logs", <log-name>)`.
- `data/state/build_checkpoints.db` remains the Builder Agent checkpoint store.

### Postgres persistence

Set `PROGENITOR_DATABASE_URL` to enable Postgres-backed persistence for new
runtime data. Startup initializes native LangGraph Postgres checkpoint/store
tables when `PROGENITOR_DATABASE_SETUP=true`.

The main Progenitor runtime requires Postgres and does not write runtime state
through backend-managed conversation/session stores.

### File-backed logs

- `logs/conversation.jsonl` - All user/assistant messages
- `logs/decisions.jsonl` - Routing decisions
- `logs/schedule.jsonl` - Schedule events

## Configuration

Configure model connections in **Settings → Providers**, then select a connection in each model profile or inherit the default. Foundry and OpenCode Go currently use the **Responses API only**; Go's model selector excludes models requiring other APIs.

Provider configuration lives in ordinary `provider_connection` / `provider_settings` tables in the backend PostgreSQL database (`BACKEND_DATABASE_NAME`, default `backend`), not the agent store. Backend and runtime require access to that database. Saved changes apply to subsequent turns; active turns keep their existing connection.

API keys can come from an explicitly selected environment variable or be encrypted in the database. For database-stored keys, set the same `PROGENITOR_PROVIDER_ENCRYPTION_KEY` Fernet key in backend and runtime and keep it stable across restarts. Never commit it; losing it makes stored credentials unreadable. Environment sources may reference `AZURE_OPENAI_API_KEY`, `OPENCODE_GO_API_KEY`, or `PROGENITOR_PROVIDER_*_API_KEY`; custom variables must be supplied to both services. Keys are write-only in the API/UI.

On first provider-database initialization only, an existing `AZURE_OPENAI_BASE_URL` / `PROGENITOR_MODEL_DEFAULT` connection is imported with an environment-backed `AZURE_OPENAI_API_KEY`. Later endpoint environment changes do not override saved connections. An empty installation still starts so Settings can be used; model requests explain that a provider must be configured.

Legacy `/openai/responses?api-version=...` endpoints are normalized to `/openai/v1` during this import. Before initial setup completes, an invalid configured endpoint reports a startup error without completing the import; correct it and restart to retry. After initial setup completes, the legacy endpoint is ignored, including invalid values. Legacy profile migration removes obsolete connection/private fields and retains model behavior settings.

Go sends a conversation-scoped `x-opencode-session`, its own User-Agent and bearer authentication. Foundry reasoning overrides are not sent to Go. Embeddings use a separately resolved Foundry connection; a Go default does not redirect indexing to an unsupported API.

These settings configure Progenitor's models and local agents; independent A2A service credentials and Claude ACP authentication are unchanged.

See `.env.example` for deployment defaults. MCP and other agent configuration continue to use shared runtime state.

Progenitor loads external MCP servers from the shared `mcp` state document. A typical payload looks like:

```json
{
  "mcpServers": {
    "jina": {
      "enabled": false,
      "transport": "http",
      "url": "https://mcp.jina.ai/v1?include_tags=search,read",
      "headers": {
        "Authorization": "Bearer ${JINA_API_KEY}"
      }
    }
  }
}
```

```dotenv
# Runtime
PROGENITOR_TIMEZONE=Australia/Melbourne
PROGENITOR_IDLE_BACKOFF_SECONDS=30
PROGENITOR_MAX_BACKOFF_SECONDS=300
PROGENITOR_MAX_ACTIONS_PER_CYCLE=10

# Optional provider credential sources (connections/defaults live in the database)
AZURE_OPENAI_API_KEY=
OPENCODE_GO_API_KEY=
# Needed for database-stored keys; generate once and share with backend/runtime
PROGENITOR_PROVIDER_ENCRYPTION_KEY=

# MCP auth referenced by the shared MCP registry
JINA_API_KEY=
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

User: reset the conversation
→ Progenitor invokes the new_conversation system tool (clears older context)

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
pyright app/
```

### Adding New Capabilities

1. Create an agent in `app/agents/<agent_name>/`
2. Or create an MCP server in `app/mcp/<mcp_name>/` with its own `Dockerfile`
3. Register in `data/config/agents.json`

Or ask Progenitor to build it for you — it will delegate to the Builder Agent automatically.

## Docs

- [SPEC.md](docs/SPEC.md) — System specification
- [ARCHITECTURE_DIAGRAM.md](docs/ARCHITECTURE_DIAGRAM.md) — Visual architecture
- [PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) — Checked-in repository layout
- [BACKEND.md](docs/BACKEND.md) — Backend API
- [DOCKER.md](docs/DOCKER.md) — Docker deployment

## License

MIT
