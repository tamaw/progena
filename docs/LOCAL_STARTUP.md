# Local startup

This is the quickest working local setup for running:

- Postgres on `127.0.0.1:15433`
- Progenitor on `127.0.0.1:9501`
- Backend on `127.0.0.1:8000`
- Frontend on `127.0.0.1:5173`

The key dependency is Postgres. When `PROGENITOR_DATABASE_URL` points at a dead port, the
backend can appear to be up while startup requests hang, and Vite proxy requests will hang
behind it.

Shell-enabled object agents use `PROGENITOR_SHELL_WORKSPACE_DIR` for per-agent scratch
directories, independently of `PROGENITOR_WORKTREE_DIR` and repository permissions.
Locally this defaults to `data/shell-workspaces`. Docker Compose mounts the persistent
`progenitor-shell-workspaces` named volume at `/app/shell-workspaces`; Docker initializes
a fresh volume from the image directory owned by runtime UID/GID `10001:10001`.
Scratch files survive container restarts. Repository paths continue to come from `/repos/`.

## Running tests safely

Install development dependencies in the activated virtual environment:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
timeout --kill-after=5s 300s pytest tests/backend tests/progenitor tests/test_scheduler.py -q -rs
```

The `timeout` command above is the Linux whole-run limit. Pytest also has a
60-second hard timeout per test, including fixture setup and teardown.

Postgres tests automatically start a disposable `pgvector/pgvector:pg16` Docker
container and create a separate database for each test. They never fall back to
`PROGENITOR_DATABASE_URL` or `DATABASE_URL`. Without Docker, set
`PROGENITOR_TEST_DATABASE_URL` to a test server with database create/drop privileges
and pgvector installed; otherwise database tests skip with the missing prerequisite.
Local runs use a loopback-only published port; containerized runners connect over
their own Docker network instead of the Docker host's loopback.

Connections time out after 3 seconds, SQL statements after 5 seconds, and lock
waits after 2 seconds. Scratch databases are dropped at teardown. The Docker
container expires after five minutes even if the hard watchdog bypasses teardown.

## Recommended `.env` improvements

To make local startup more predictable:

1. Add `PROGENITOR_DATABASE_URL` to `.env` so backend and progenitor share the same Postgres
   server. The backend keeps its own data (agents, agent graphs) in a separate database on that
   same server; its name defaults to `backend` and is configurable via `BACKEND_DATABASE_NAME`.
   The backend creates this database automatically on startup if it does not already exist.
2. Quote values with spaces, especially `CHAT_SERVER_API_TITLE`, so `.env` works with both
   `python-dotenv` and `source .env` in a shell.
3. Keep the runtime URL explicit so the backend always points at the local progenitor process:
   - `PROGENITOR_RUNTIME_URL=http://127.0.0.1:9501`

Example additions/adjustments:

```dotenv
PROGENITOR_DATABASE_URL=postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor
BACKEND_DATABASE_NAME=backend
CHAT_SERVER_API_TITLE="Progenitor Chat API"
```

## Start Postgres locally

If PostgreSQL is not installed on the host, `nix-shell` works. The Knowledge
tab stores embeddings with the [`pgvector`](https://github.com/pgvector/pgvector)
extension, so use a Postgres build that bundles it (`postgresql_13.withPackages`)
for **every** start — not just the first init. Starting the same data directory
with a plain `postgresql` build leaves the `vector` type unloadable.

```bash
PGROOT="$HOME/.copilot/session-state/local-progenitor-postgres"
DATA="$PGROOT/data"
SOCK="$PGROOT/socket"
LOG="$PGROOT/postgres.log"
PASSFILE="$PGROOT/pw"

mkdir -p "$PGROOT"
rm -rf "$DATA" "$SOCK"
mkdir -p "$DATA" "$SOCK"
printf 'progenitor\n' > "$PASSFILE"

export LANG=C.UTF-8
export LC_ALL=C.UTF-8

PG='postgresql_13.withPackages (ps: [ ps.pgvector ])'
nix-shell -p "$PG" --run "initdb -D '$DATA' --username=progenitor --pwfile='$PASSFILE' --auth-host=scram-sha-256 --auth-local=trust --locale=C --encoding=UTF8"
nix-shell -p "$PG" --run "pg_ctl -D '$DATA' -l '$LOG' -o \"-h 127.0.0.1 -p 15433 -k '$SOCK'\" start"
nix-shell -p "$PG" --run "createdb -h '$SOCK' -p 15433 -U progenitor progenitor" || true
# The knowledge store creates the extension automatically, but you can pre-create it:
nix-shell -p "$PG" --run "psql 'postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor' -c 'CREATE EXTENSION IF NOT EXISTS vector;'"
```

To restart an existing data directory with pgvector available (e.g. after a
reboot), reuse the same `pg_ctl ... start` line above — do not re-run `initdb`,
which would wipe the data.

If Postgres is already installed locally, the important part is just matching the app config:

- host: `127.0.0.1`
- port: `15433`
- user: `progenitor`
- password: `progenitor`
- database: `progenitor`

## Start progenitor

From the repository root:

```bash
source .venv/bin/activate
export PYTHONPATH="$PWD"
export PROGENITOR_DATABASE_URL="postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor"
export AGENT_NAME=progenitor
python -m app.progenitor.main
```

The runtime HTTP server should listen on `127.0.0.1:9501` (`curl http://127.0.0.1:9501/health`).

## Start backend

In a second shell:

```bash
source .venv/bin/activate
export PYTHONPATH="$PWD"
export PROGENITOR_DATABASE_URL="postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor"
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

## Start frontend

In a third shell:

```bash
cd app/frontend
npm install
npm run dev -- --port 5173
```

## External A2A agents (optional)

The standalone A2A agents, including their images and service deployment, are owned
by [progentior.agents](https://github.com/tamaw/progentior.agents), not this checkout.
Follow that repository's installation and service instructions. Progenitor's built-in
object agents, including `repo-manager`, remain here.

To discover the separately installed agents, start their services and point the
Progenitor runtime at the external checkout before starting it:

```bash
export PROGENITOR_AGENT_REPOSITORIES="/absolute/path/to/progentior.agents"
```

On Linux, separate fallback repository roots with `:`; the first checkout that
registers agents seeds an empty registry. Existing saved registry entries remain
authoritative. For containerized Progenitor, mount the external checkout read-only
and use its container-visible path in this variable.

For browser integration tests that start a real Greetings Agent, install that
repository's development environment and set:

```bash
export PROGENITOR_TEST_AGENTS_ROOT="/absolute/path/to/progentior.agents"
```

The browser fixture launches the agent with that checkout's `.venv`; it does not
reuse or copy the standalone implementation into Progenitor.

## Quick checks

Liveness checks should return `200`:

```bash
curl http://127.0.0.1:9501/health
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:5173/
```

Check the Vite-to-backend proxy with
`curl http://127.0.0.1:5173/api/ui/agent-profiles` (HTTP `202` acknowledgement),
and `curl -N http://127.0.0.1:5173/api/ui/stream` (`connection.ready` event).
The old `/v1/session` endpoint is not a current startup probe.

Bundled object agents are read-only files under `data/agents/objects/`, not
Postgres seeds. Restart progenitor and backend after editing these files in
development; file changes do not emit Postgres notifications. User/plugin
agents remain Postgres-backed and refresh through notifications.

Expected wiring:

- frontend root is served by Vite on `5173`
- `/api/*` and `/v1/*` go through the Vite proxy to backend `8000`
- backend talks to progenitor over HTTP + SSE on `9501`

## If the frontend looks like a proxy problem

Inspect backend startup output and its `PROGENITOR_DATABASE_URL` first. If backend requests
hang, the usual cause is missing Postgres or a bad connection URL, not Vite.
