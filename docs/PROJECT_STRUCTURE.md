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
│   ├── sse/      # Stream-envelope helpers for the HTTP+SSE chat/event path
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
