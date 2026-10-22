# Claude Code repository guide

Read `AGENTS.md` first. It owns shared setup, validation commands, repository
safety and the current architecture constraints. Also read the guide for every
component you change:

- `src/progenitor/AGENTS.md` — domains, DeepAgent integration, checkpoints and runtime
- `src/backend/AGENTS.md` — feature routes/services, lifecycle and browser events
- `src/frontend/AGENTS.md` — feature UI, Redux ownership, streaming and editor boundaries

## Orientation

The Python namespace is `src.*`. Start the runtime with
`python -m src.progenitor.main` and the gateway with
`python -m uvicorn src.backend.main:app`. Activate the venv before Python commands;
refresh editable installation metadata after source moves. Use pinned Pyright with
an explicit venv interpreter, as documented in the root guide.

The browser talks to the gateway, which bridges to the runtime over HTTP/SSE.
Vite proxies `/api/*` and `/v1/*`; backend liveness is `/healthz`, while runtime
health is `/health` on port 9501. Ports and database configuration come from
`.env.example` and `docs/LOCAL_STARTUP.md`.

## Where to work

- `src/progenitor/agent/factory.py` composes DeepAgent. Domain contracts and stores
  live beside their owners; `state/manager.py` coordinates lifecycle and domain stores.
- `src/progenitor/orchestration/graphs/` and `orchestration/nodes/` assemble graph
  execution. `runtime/http/` owns conversation transport/execution;
  `runtime/streaming/` translates stream events.
- `src/progenitor/persistence/` owns database lifecycle, native stores and checkpoint
  serialization. Preserve `CheckpointSerializer` and historical model aliases.
- `src/backend/<feature>/` owns routes and services. `http/`, `events/`, `storage/`
  and `lifecycle/` provide shared infrastructure; `main.py` assembles the app.
- `src/frontend/src/features/` owns product UI and domain state. `store/` assembles
  Redux; `realtime/realtimeListener.js` owns the canonical event connection;
  `features/chat/hooks/` owns request-scoped response content.

Mutable state/configuration, provider profiles and conversation history are
Postgres-backed. Bundled instruction sources live in `data/instructions/`;
agent-facing JSON paths are virtual views of database documents. External A2A
implementations belong to the separate agents repository linked in `AGENTS.md`.

Use `docs/BACKEND.md` for the browser's acknowledgement/event contract and its
explicit exceptions. Preserve per-thread conversation isolation, request IDs,
event replay ordering and workspace resets. Test changes at their owning layer;
report actual validation and any live scenarios left unrun.

Create PRs for review. Do not merge or trigger deployments without an explicit
human request; follow the root guide's workflow-cancellation rule.
