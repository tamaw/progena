# Progenitor — repository guide

Progenitor is a conversational LangGraph/DeepAgent runtime with a FastAPI gateway
and a React/Vite frontend. Read this guide first, then the `AGENTS.md` in each
component you change. Paths and commands below are relative to the repository root.

## Layout and ownership

```text
src/
├── progenitor/     # Agent composition, domain contracts/stores, orchestration and runtime
├── backend/        # Feature routes/services, shared HTTP/events/storage and lifecycle
└── frontend/       # Vite project; application code under src/frontend/src/features/
tests/
├── backend/        # Backend tests
├── progenitor/     # Runtime/domain tests, including scheduler tests
└── conftest.py     # Shared environment setup and disposable Postgres fixtures
data/
├── instructions/   # Bundled instruction templates synced to Postgres
├── skills/         # Bundled native skills
├── schemas/        # Checked-in document contracts
├── agents/         # Built-in object-agent instructions
├── modules/        # Vendored module library catalogue
└── shims/          # Agent shell command shims
```

Python imports use `src.backend.*` and `src.progenitor.*`. After changing layouts,
refresh editable metadata with `pip install -e ".[dev]"` in the activated venv.
Standalone A2A agents and their tests/deployment assets live in
[progentior.agents](https://github.com/tamaw/progentior.agents). Configure external
checkout discovery with `PROGENITOR_AGENT_REPOSITORIES`; do not copy those agents
back here.

## Setup and checks

Always activate the project venv before Python commands. On Linux/macOS:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
ruff check src/backend src/progenitor tests/backend tests/progenitor
pyright --pythonpath .venv/bin/python src/
pytest tests/backend tests/progenitor
pre-commit run --all-files
```

On Windows, activate with `& .\.venv\Scripts\Activate.ps1`; use the corresponding
venv Python executable for Pyright's `--pythonpath` argument.

Pyright is the Python type checker; use the pinned project version and explicit
venv interpreter, matching `.github/workflows/ci.yml` and `.pre-commit-config.yaml`.
Review existing tool-specific overrides alongside `pyproject.toml` when changing
configuration. Do not introduce a parallel type-checking setup.

From `src/frontend`, use Node 24 (compatible with the current Vite/Vitest engines):

```bash
npm ci
npm run lint
npm run test:unit
npm run build
```

Frontend unit tests are colocated with source. Browser specs, helpers and fixtures
live in `src/frontend/tests/`. `npm run test:e2e` requires an already-running stack;
`FRONTEND_BASE_URL` overrides its URL. Some scenarios invoke real models and mutate
application data, so run them against a test stack. Listing tests alone does not
verify their execution.

Python tests use `pytest-asyncio` with automatic async mode. Root `conftest.py`
pins checkout-local paths and provides disposable Postgres databases; component
conftests provide mocks and cleanup. Use `PROGENITOR_TEST_DATABASE_URL` only for a
test server; otherwise persistence tests provision Postgres with Docker. Keep
regressions focused on observable behavior and report skipped/unrun checks.

## Running locally

Install frontend dependencies and configure `.env` using `.env.example` and
`docs/LOCAL_STARTUP.md`. Postgres is required. Run these in separate terminals:

```bash
python -m src.progenitor.main
python -m uvicorn src.backend.main:app --host 0.0.0.0 --port 8000
# From src/frontend:
npm run dev -- --port 5173
```

Runtime health is `http://127.0.0.1:9501/health`; backend liveness is
`http://127.0.0.1:8000/healthz`. Vite proxies `/api/*` and `/v1/*` to the backend.
It does not proxy `/health`. Docker keeps `/app` as its working directory and copies
source into `/app/src`; these container paths differ from the Python namespace.

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
- Use `CheckpointSerializer` from `src/progenitor/persistence/serialization.py`
  when opening checkpoint savers or decoding blobs. Preserve historical model
  aliases; existing `app.progenitor.*` checkpoint identities remain readable.
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
