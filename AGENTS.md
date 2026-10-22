# Progena AI — repository guide

Progena is a conversational LangGraph/DeepAgent runtime with a FastAPI gateway
and a React/Vite frontend. Read this guide first, then the `AGENTS.md` in each
component you change. Paths and commands below are relative to the repository root.

## Layout and ownership

```text
src/
├── progena/     # Agent composition, domain contracts/stores, orchestration and runtime
├── backend/        # Feature routes/services, shared HTTP/events/storage and lifecycle
└── frontend/       # Vite project; application code under src/frontend/src/features/
tests/
├── backend/        # Backend tests
├── progena/     # Runtime/domain tests, including scheduler tests
├── frontend/       # Unit tests, browser specs, helpers and fixtures
└── conftest.py     # Shared environment setup and disposable Postgres fixtures
```

Python imports use `src.backend.*` and `src.progena.*`. After changing layouts,
refresh editable metadata with `pip install -e ".[dev]"` in the activated venv.
Bundled instructions, skills, schemas, object agents, canvas libraries and shell
shims live under `data/`.
Standalone A2A agents and their tests/deployment assets live in
[progena.agents](https://github.com/tamaw/progena.agents). Configure external
checkout discovery with `PROGENA_AGENT_REPOSITORIES`; do not copy those agents
back here.

## Setup and checks

Always activate the project venv before Python commands. On Linux/macOS:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
ruff check src/backend src/progena tests/backend tests/progena
pyright --pythonpath .venv/bin/python src/
pytest tests/backend tests/progena
pre-commit run --all-files
```

On Windows, activate with `& .\.venv\Scripts\Activate.ps1`; use the corresponding
venv Python executable for Pyright's `--pythonpath` argument.

Pyright is the Python type checker; use the pinned project version and explicit
venv interpreter, matching `.github/workflows/ci.yml` and `.pre-commit-config.yaml`.
Python dependencies and tool configuration live in `pyproject.toml`. Do not add
parallel Pyright or pytest configuration files.

From the repository root, use Node 24 (compatible with the current Vite/Vitest engines):

```bash
npm ci
npm run lint
npm run test:unit
npm run build
```

Frontend tests live under `tests/frontend/`: `unit/` mirrors the source layout,
`e2e/` contains browser specs, and `helpers/` and `fixtures/` hold shared support.
The root `package.json` and lockfile provide one dependency tree for both source
and tests. `npm run test:e2e` requires an already-running stack;
`FRONTEND_BASE_URL` overrides its URL. Some scenarios invoke real models and mutate
application data, so run them against a test stack. Listing tests alone does not
verify their execution.

Python tests use `pytest-asyncio` with automatic async mode. Root `conftest.py`
pins checkout-local paths and provides disposable Postgres databases; component
conftests provide mocks and cleanup. Use `PROGENA_TEST_DATABASE_URL` only for a
test server; otherwise persistence tests provision Postgres with Docker. Report skipped/unrun checks.

## Running locally

Install frontend dependencies and configure `.env` using `.env.example` and
`docs/LOCAL_STARTUP.md`. Postgres is required. Run these in separate terminals:

```bash
python -m src.progena.main
python -m uvicorn src.backend.main:app --host 0.0.0.0 --port 8000
# From the repository root:
npm run dev -- --port 5173
```

Runtime health is `http://127.0.0.1:9501/health`; backend liveness is
`http://127.0.0.1:8000/healthz`. Vite proxies `/api/*` and `/v1/*` to the backend.
Docker keeps `/app` as its working directory and source under `/app/src`.

## Contracts to preserve

- Domain contracts/stores own behavior. Runtime and API adapters assemble them;
  shared modules must not eagerly import application assembly. Read the component
  guides before moving responsibilities.
- State, mutable configuration and conversation checkpoints live in Postgres.
  Agent-facing `/config/*.json` and similar paths are virtual filesystem views,
  not local JSON persistence. Provider/model profiles are configured through the
  application and persisted in the database; environment-backed credentials are
  optional credential sources.
- Conversation state is scoped by LangGraph `thread_id`. Keep different threads
  isolated and serialize overlapping runs for the same conversation.
- Use `CheckpointSerializer` from `src/progena/persistence/serialization.py`
  when opening checkpoint savers or decoding blobs. Preserve historical model
  aliases; existing `app.progena.*` checkpoint identities remain readable.
- Browser application data arrives through the canonical `/api/ui/stream` and
  request-scoped chat content streams. Finite application routes return `202`
  acknowledgements with request correlation. Follow the exact contract and
  exceptions in `docs/BACKEND.md`; do not add response-body fallbacks.
- Shell homes and scratch workspaces are runtime-managed. Keep generated caches,
  logs, dependency trees, credentials and test artifacts out of commits.

## Repository safety

- **Do not merge PRs.** Create PRs and wait for human approval/merge.
- **Do not trigger deployments** unless explicitly instructed by the human
  (`workflow_dispatch` is allowed only on request).
- If a workflow is accidentally started that could deploy, cancel it immediately
  and notify the human.

## References

- `docs/README.md` — documentation index
- `docs/PROJECT_STRUCTURE.md` — source layout
- `docs/SPEC.md` — runtime behavior and public interfaces
- `docs/BACKEND.md` — authoritative browser API and event contract
- `docs/ARCHITECTURE_DIAGRAM.md` — topology and data flow
- `docs/STATE_DOCUMENTS.md` — persistence contracts
- `docs/LOCAL_STARTUP.md` and `docs/DOCKER.md` — local and container operation
