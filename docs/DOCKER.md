# Docker stack

This document covers the main Progenitor container stack only: runtime, backend, and frontend.

## What the repo currently ships

`docker/docker-compose.progenitor.yml` defines a three-container stack:
- `progenitor` - runs `python -m app.progenitor.main`
- `backend` - runs `uvicorn app.backend.main:app`
- `frontend` - builds the Vite app and serves it with nginx

Supporting Dockerfiles:
- `docker/progenitor/Dockerfile`
- `docker/backend/Dockerfile`
- `docker/frontend/Dockerfile`

## Before starting

Required runtime environment:
- `AZURE_OPENAI_BASE_URL`
- `AZURE_OPENAI_API_KEY`

Common optional overrides:
- `PROGENITOR_MODEL_DEFAULT`
- `GH_TOKEN`
- `PROGENITOR_REASONING_ENABLED`
- `PROGENITOR_REASONING_EFFORT`
- `PROGENITOR_REASONING_SUMMARY`
- `TELEGRAM_*` backend settings

By default, Progenitor now starts with reasoning enabled. `PROGENITOR_REASONING_EFFORT`
controls the effort sent to the provider, and `PROGENITOR_REASONING_SUMMARY` controls
whether the provider is asked for `auto` or `detailed` visible reasoning summaries.

The compose file also expects the external Docker network `progenitor_network` to exist:

```bash
docker network create progenitor_network
```

## Start and stop

From the repository root:

```bash
docker compose -f docker/docker-compose.progenitor.yml up --build -d
docker compose -f docker/docker-compose.progenitor.yml down
```

Useful inspection commands:

```bash
docker compose -f docker/docker-compose.progenitor.yml ps
docker compose -f docker/docker-compose.progenitor.yml logs -f
docker compose -f docker/docker-compose.progenitor.yml logs -f progenitor
docker compose -f docker/docker-compose.progenitor.yml logs -f backend
docker compose -f docker/docker-compose.progenitor.yml logs -f frontend
```

## Network and ports

Current compose behavior:
- Only the frontend is published to the host: `80:80`
- The backend listens on port `8000` **inside** the compose networks only
- The Progenitor IPC server listens on port `9500` inside the compose networks
- The backend connects to the runtime with `CHAT_SERVER_IPC_HOST=progenitor`
- The compose file attaches services to an internal bridge network and the external `progenitor_network`

Because the backend is not host-published, host access normally goes through nginx on `http://localhost`:
- UI: `http://localhost/`
- Proxied backend health: `http://localhost/health`, `/ready`, `/live`
- Proxied API: `http://localhost/v1/...` and `http://localhost/api/ui/...`

If you need direct host access to backend port `8000`, add your own port mapping to the compose file or use a one-off override file.

## Volumes and ephemeral storage

Host-mounted by default:
- `${PROGENITOR_HOST_DATA_DIR:-../data}:/app/data`
- `${PROGENITOR_HOST_LOGS_DIR:-../logs}:/app/logs`

Ephemeral storage:
- `/app/tmp` is a `tmpfs` mount in `progenitor` and `backend`

Notes:
- Prompt assets live inside `${PROGENITOR_HOST_DATA_DIR}/prompts` and resolve to `/app/data/prompts` in containers.
- Deployment does not copy repo prompt files into `${PROGENITOR_HOST_DATA_DIR}/prompts`; that directory is treated as persistent runtime state.
- Use the manual `prompts` workflow when you want to copy checked-in repo prompts into the persistent prompt volume without overwriting existing files.
- Skill assets now live inside `${PROGENITOR_HOST_DATA_DIR}/skills` and resolve to `/app/data/skills` in containers.
- The compose file does **not** currently mount `/app/worktrees` from the host.
- `PROGENITOR_WORKTREE_DIR` is still set in the `progenitor` container, so the directory exists there, but it is container-local unless you add your own volume.

## Health checks

Current health probes are implemented as follows:
- `progenitor`: `python /app/scripts/healthcheck.py` checks `/app/data/state/health.json`
- `backend`: `curl -f http://localhost:8000/health`
- `frontend`: `wget -q -O /dev/null http://127.0.0.1/`
