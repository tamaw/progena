# Docker stack

This document covers the main Progenitor container stack: Postgres, runtime, backend,
frontend, and the internal Claude ACP sidecar.

## What the repo currently ships

`docker/docker-compose.progenitor.yml` defines a five-container stack:
- `postgres` - PostgreSQL with pgvector
- `progenitor` - runs `python -m app.progenitor.main`
- `backend` - runs `uvicorn app.backend.main:app`
- `frontend` - builds the Vite app and serves it with nginx
- `claude` - provides Claude Code over internal ACP transport

Supporting Dockerfiles:
- `docker/progenitor/Dockerfile`
- `docker/backend/Dockerfile`
- `docker/frontend/Dockerfile`
- `docker/claude/Dockerfile`

Standalone A2A services, their images, and their deployment workflows are owned by
[progentior.agents](https://github.com/tamaw/progentior.agents), not this repository.
Progenitor can discover an external checkout through a Compose override that mounts it
read-only into the runtime and sets `PROGENITOR_AGENT_REPOSITORIES` to its
in-container path. No external repository is required for Progenitor or its built-in
object agents.

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
docker compose -f docker/docker-compose.progenitor.yml logs -f claude
```

## Network and ports

Current compose behavior:
- Only the frontend is published to the host, bound to localhost: `127.0.0.1:8081:443`
- The backend listens on port `8000` **inside** the compose networks only
- The Progenitor HTTP server listens on port `9501` inside the compose networks
- Claude ACP listens at `tcp://progenitor-agent-claude:3001` inside the compose networks only
- The backend connects to the runtime with `PROGENITOR_RUNTIME_URL=http://progenitor:9501`
- The compose file attaches services to an internal bridge network and the external `progenitor_network`

The intended public deployment places a host-level nginx and an external OAuth2
proxy in front of the stack. Nginx terminates TLS, calls the OAuth2 proxy's
`/oauth2/auth` endpoint for authentication, and proxies authenticated traffic to
`https://127.0.0.1:8081`. The OAuth2 proxy is deliberately not part of this
Compose project; keep its credentials and provider configuration in the host
deployment. See `docs/nginx/progena.tama.id.au.conf.example` for a template.

Because the frontend is localhost-bound, direct public access cannot bypass the
nginx/OAuth2 authentication layer. The backend and runtime remain internal to
the Docker networks.

If you need direct host access to backend port `8000`, add your own port mapping
to a one-off override file; do not expose it in the public deployment.

## Volumes and ephemeral storage

- `progenitor` and `backend`: `${PROGENITOR_HOST_LOGS_DIR:-../logs}:/app/logs`, `${PROGENITOR_HOST_REPOS_DIR:-../repos}:/app/repos`

Ephemeral storage:
- `/app/tmp` is a `tmpfs` mount in `progenitor` and `backend`

Notes:
- Instructions and runtime memories are Postgres-backed content documents
  (`app/progenitor/content_documents.py`, keyed `instruction:`/`memory:` in
  the same shared `store` table as `mcp.json`/`schedule.json`/etc — see
  `app/progenitor/state/manager.py`). Neither `progenitor` nor `backend` mounts
  a host directory for them. `data/instructions/` is checked into git and
  `COPY`'d into the `progenitor` image (not `backend`'s); every boot syncs it
  into Postgres: Progenitor's own instructions (`progenitor/`) are added when
  missing and otherwise kept, since they are edited on the Instructions page.
  Standalone A2A agents own their instructions, images, and service lifecycle in the
  separate [agents repository](https://github.com/tamaw/progentior.agents); they are
  not copied into this image. `data/skills/` is a read-only disk mount (`/skills/`),
  not stored in Postgres.
- `current_state`, `schedule`, `registry/agents`, `mcp`, `knowledge`, `health`,
  `kanban`, `attachments`, `sessions`, `prompts`, `skills`, and `memories` all
  persist through the shared LangGraph/Postgres document store with no
  filesystem fallback anywhere in the stack. `data/schemas/` (static JSON
  Schema contracts read by `structured_output` and document types) is
  `COPY`'d into the images, so the schemas a container reads always match its
  code. It is not bind-mounted: the deploy's runner checkout is reused by later
  CI jobs on other branches, so a mount from it served whatever branch ran last.
- Repository/worktree state mounts from `${PROGENITOR_HOST_REPOS_DIR:-../repos}` to `/app/repos`.
- `PROGENITOR_WORKTREE_DIR` is set to `/app/repos` in `progenitor` and `backend`.

## Health checks

`/healthz` is the explicit Docker/container liveness exception to the
event-stream-only browser API. It returns an empty `200 OK` and no application
data.

Current health probes are implemented as follows (run the container-local probes with
`docker compose exec`; only the frontend is published on the host):
- `backend` container: `docker compose -f docker/docker-compose.progenitor.yml exec -T backend curl -fsS http://127.0.0.1:8000/healthz`
- `frontend` through the published Nginx port: `curl -fsS http://localhost/healthz` (proxied to `backend`)
- `frontend` container process: `docker compose -f docker/docker-compose.progenitor.yml exec -T frontend wget -q -O /dev/null http://127.0.0.1/`
- `progenitor` container: `docker compose -f docker/docker-compose.progenitor.yml exec -T progenitor curl -fsS http://127.0.0.1:9501/health` (the runtime's own HTTP endpoint)
- `claude` container: Docker probes its `socat` listener; the ACP endpoint is internal only.
