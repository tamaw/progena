# Progenitor Project Structure

This document focuses on the checked-in main stack only. For runtime relationships and request flow, see [`ARCHITECTURE_DIAGRAM.md`](ARCHITECTURE_DIAGRAM.md).

## Top level

```text
progenitor/
├── app/          # Main application code
├── data/         # Bundled assets and managed-repository workspace
├── docker/       # Container definitions
├── docs/         # Project documentation
├── logs/         # Runtime logs
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
│   ├── orchestration/ # Graphs, nodes and composed loop state
│   ├── llm/      # Provider context, model calls, schemas and response parsing
│   ├── runtime/  # Lifecycle, heartbeat, HTTP server and streaming adapters
│   ├── documents/ # Shared keys, kinds, schema catalog and document contracts
│   ├── persistence/ # Database connections, checkpoints, cache, store and listener
│   ├── workflows/ # Models, ports, derivation, validation and document store
│   ├── routines/
│   ├── schedules/
│   ├── repositories/
│   ├── sessions/
│   ├── settings/
│   ├── knowledge/ / instructions/ / plugins/
│   ├── a2a/ / acp/ / async_subagents/ / mcp/
│   ├── state/    # State lifecycle facade over domain stores
│   ├── tools/    # Runtime tool registry and implementations
│   └── main.py   # Runtime entry point
├── backend/      # FastAPI API and UI data backend
│   ├── main.py   # HTTP entry point
│   ├── http/         # Shared API contracts, middleware and router assembly
│   ├── events/       # Canonical SSE transport and durable event dispatch
│   ├── storage/      # Shared Postgres document access
│   ├── lifecycle/    # Startup, shutdown and process state
│   ├── chat/         # Chat/read-model/response endpoints and session handling
│   ├── attachments/  # Uploads, text extraction and attachment blobs
│   ├── agents/       # Agent catalog and live routine reconciliation
│   ├── instructions/
│   ├── knowledge/
│   ├── plugins/      # Plugins and marketplaces
│   ├── repositories/
│   ├── modules/
│   ├── workflows/    # Execution, cron, approvals and ACP supervision
│   ├── tasks/        # Kanban cards, content and activity
│   ├── schedules/    # Schedules and event widgets
│   ├── settings/     # Preferences, providers, profiles, policies and MCP
│   ├── insights/
│   ├── inbox/
│   ├── alerts/
│   ├── search/
│   ├── monitoring/   # State snapshots, logs and metrics
│   └── integrations/ # Runtime clients and Telegram
└── frontend/     # React/Vite web client
    ├── src/features/   # Feature pages, components, models and domain state
    ├── src/components/
    ├── src/context/
    ├── src/hooks/
    ├── src/shell/      # Application chrome and panel placement
    ├── src/store/      # Redux assembly and shared request lifecycle
    ├── src/realtime/   # Canonical SSE transport
    └── src/styles/     # Shared foundations
```

## Bundled data assets and runtime persistence

`data/` contains repository-managed assets and a local managed-repository
workspace; it is not the runtime state store:

```text
data/
├── agents/        # Bundled native agent manifests
├── instructions/  # Bundled instruction templates, seeded into Postgres
├── repos/         # Local managed-repository/worktree root
├── schemas/       # Static JSON Schema contracts
├── shims/          # Bundled command shims
└── skills/         # Bundled native skill bundles

logs/               # Runtime and backend logs
```

Runtime state, mutable configuration, chat/session records, schedules, and
agent-facing virtual documents are Postgres-backed. They are exposed through
virtual mounts
rather than physical `data/state/`, `data/config/`, `data/sessions/`,
`data/attachments/`, or `data/prompts/` directories. Uploaded attachments are
stored in the Postgres attachment table. See
[`STATE_DOCUMENTS.md`](STATE_DOCUMENTS.md) for the virtual-document layout.

## Docker assets

```text
docker/
├── docker-compose.progenitor.yml
├── progenitor/Dockerfile
├── backend/Dockerfile
├── frontend/Dockerfile
├── claude/Dockerfile       # deployed ACP sidecar
├── codex/Dockerfile        # standalone ACP runtime tooling
├── ghcopilot/Dockerfile    # standalone ACP runtime tooling
└── gha-runner/Dockerfile   # standalone GitHub Actions runner tooling
```
