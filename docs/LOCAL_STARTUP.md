# Local startup

This is the quickest working local setup for running:

- Postgres on `127.0.0.1:15433`
- Progenitor on `127.0.0.1:9500`
- Backend on `127.0.0.1:8000`
- Frontend on `127.0.0.1:5173`

The key dependency is Postgres. When `PROGENITOR_DATABASE_URL` points at a dead port, the
backend can appear to be up while `/health` hangs during startup, and Vite proxy requests
will hang behind it.

## Recommended `.env` improvements

To make local startup more predictable:

1. Add `PROGENITOR_DATABASE_URL` to `.env` so backend and progenitor share the same database config.
2. Quote values with spaces, especially `CHAT_SERVER_API_TITLE`, so `.env` works with both
   `python-dotenv` and `source .env` in a shell.
3. Keep the IPC settings explicit so the backend always points at the local progenitor process:
   - `CHAT_SERVER_IPC_HOST=127.0.0.1`
   - `CHAT_SERVER_IPC_PORT=9500`

Example additions/adjustments:

```dotenv
PROGENITOR_DATABASE_URL=postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor
CHAT_SERVER_API_TITLE="Progenitor Chat API"
```

## Start Postgres locally

If PostgreSQL is not installed on the host, `nix-shell` works:

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

nix-shell -p postgresql --run "initdb -D '$DATA' --username=progenitor --pwfile='$PASSFILE' --auth-host=scram-sha-256 --auth-local=trust --locale=C --encoding=UTF8"
nix-shell -p postgresql --run "pg_ctl -D '$DATA' -l '$LOG' -o \"-h 127.0.0.1 -p 15433 -k '$SOCK'\" start"
nix-shell -p postgresql --run "createdb -h '$SOCK' -p 15433 -U progenitor progenitor" || true
```

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
python -m app.progenitor.main
```

The IPC server should listen on `127.0.0.1:9500`.

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

## Quick checks

These should all return `200`:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:5173/health
curl http://127.0.0.1:5173/v1/session
```

Expected wiring:

- frontend root is served by Vite on `5173`
- `/health`, `/api/*`, and `/v1/*` go through the Vite proxy to backend `8000`
- backend talks to progenitor over IPC on `9500`

## If the frontend looks like a proxy problem

Check backend health directly first:

```bash
curl -i http://127.0.0.1:8000/health
```

If that hangs, the usual cause is missing Postgres or a bad `PROGENITOR_DATABASE_URL`, not Vite.
