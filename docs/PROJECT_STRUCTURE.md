# Progena AI Project Structure

This document focuses on the checked-in main stack only. For runtime relationships and request flow, see [`ARCHITECTURE_DIAGRAM.md`](ARCHITECTURE_DIAGRAM.md).

## Top level

```text
progena/
├── src/          # Main application code
├── tests/        # backend/, progena/, frontend/{unit,e2e,helpers,fixtures}
├── package.json  # Shared frontend dependencies and npm scripts
├── package-lock.json
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

The Python package namespace is `src`: start the runtime with
`python -m src.progena.main` and the gateway with
`python -m uvicorn src.backend.main:app`. Install the repository with
`pip install -e ".[dev]"` after changing source layouts so editable package
metadata points at the current tree.

## Application code (`src/`)

```text
src/
├── shared/    # Packaged JSON contracts shared by Python and the browser
├── progena/   # Long-running runtime service
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
│   ├── canvases/
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

### Behavioural string contracts

- `src/shared/ui_event_vocabulary.json` owns app SSE names and routine lifecycle
  categories. Python consumes it through `backend/events/contracts.py` and
  `progena/routines/lifecycle.py`; the browser uses
  `frontend/src/realtime/eventVocabulary.js`. Feature models still own event
  payloads; the vocabulary does not replace payload validation. Canonical UI
  lifecycle traffic and request-scoped chat content remain separate streams.
  CI's frontend change filter includes both this shared directory and the
  imported routine schema, so contract-only changes rebuild the browser image.
- Routine statuses retain their persisted spellings. A paused attempt is
  terminal for display but resumable through approval; generic stop/cancel
  admission deliberately excludes it from its settled-control category.
  `interrupted` remains a derived display state. ACP, workflow and Kanban
  statuses are separate domains, not aliases for routine statuses.
- `progena/documents/kinds.py` owns durable document prefixes;
  `progena/orchestration/background.py` owns the background queue key and task
  discriminator. `progena/threads.py` owns checkpoint identity
  construction and existing routine control parsing. Preserve serialized forms:
  changing a key or thread format requires a persistence migration.
- `progena/workflows/documents.py` owns workflow-run ID generation and prefix
  predicates. The broad workflow-owned session predicate and strict invocation
  ID validator intentionally have different acceptance rules.
- `frontend/src/lib/browserContracts.js` owns window event names, quick-chat
  modes and browser storage keys. Workspace cache clearing consumes its scoped
  key registry; global preferences and workspace routing records survive.
- The routine editor derives reserved node IDs from
  `data/schemas/graph.routine.schema.v1.json`, rejecting compiler/framework IDs
  before a save instead of maintaining a separate browser list.
- `frontend/src/features/canvases/canvasProtocol.js` owns the canvas iframe
  channel, message types and methods. The generated sandbox bootstrap embeds
  those definitions; both sides still check message source and channel.
- Async-subagent `interrupted` means waiting for human approval, not completion.
  `async_subagent_pause` preserves that state in the stream, alongside stable
  task IDs used to update one delegation card across checks, resume and result.
- Chat metadata/deletion acknowledgements carry their request ID in the durable
  document-event origin. The publisher correlates snapshot and per-session
  events. Empty/unchanged PATCHes remain successful and acknowledge only a
  directly correlated snapshot because they produce no durable change.
- Plugin fetch failures expose typed kind, code and upstream HTTP status.
  Routes never infer rate limiting from human-readable wording; a GitHub 403
  needs rate-limit response evidence to become 429.

Add new app-owned values to the owning contract and migrate producers and
consumers together. Keep vendor protocol fields at their adapter boundary;
do not rename externally required strings or collapse unrelated status domains.


## Docker assets

```text
docker/
├── docker-compose.progena.yml
├── progena/Dockerfile
├── backend/Dockerfile
├── frontend/Dockerfile
├── claude/Dockerfile       # deployed ACP sidecar
├── codex/Dockerfile        # standalone ACP runtime tooling
├── ghcopilot/Dockerfile    # standalone ACP runtime tooling
└── gha-runner/Dockerfile   # standalone GitHub Actions runner tooling
```
