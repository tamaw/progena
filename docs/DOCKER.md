# Docker stack

This document covers the main Progenitor container stack: Postgres, runtime, backend,
frontend, and the internal Claude ACP sidecar.

## What the repo currently ships

`docker/docker-compose.progenitor.yml` defines a five-container stack:
- `postgres` - PostgreSQL with pgvector
- `progenitor` - runs `python -m src.progenitor.main`
- `backend` - runs `uvicorn src.backend.main:app`
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

Model connections are configured in **Settings → Providers**, not required endpoint environment variables. Foundry and OpenCode Go currently use the Responses API only.

For database-stored API keys, generate one Fernet key and set `PROGENITOR_PROVIDER_ENCRYPTION_KEY` in the deployment `.env`; Compose passes it to backend and runtime:

```bash
# Activate the project virtual environment first.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep that key outside version control and retain it across restarts. Alternatively select environment credentials using `AZURE_OPENAI_API_KEY` or `OPENCODE_GO_API_KEY`. Custom `PROGENITOR_PROVIDER_*_API_KEY` names must be passed explicitly to both containers.

Provider tables live in `BACKEND_DATABASE_NAME` (default `backend`) on the configured PostgreSQL server; both services need access to it. Compose must give both services the same database name and encryption key. Existing `AZURE_OPENAI_BASE_URL` and `PROGENITOR_MODEL_DEFAULT` are imported once on initial provider-table setup; subsequent connection edits come from the database. An installation with no configured provider starts normally but rejects model calls until configured.

Common optional overrides:
- `PROGENITOR_MODEL_DEFAULT`
- `GH_TOKEN`
- `PROGENITOR_REASONING_ENABLED`
- `PROGENITOR_REASONING_EFFORT`
- `PROGENITOR_REASONING_SUMMARY`
- `TELEGRAM_*` backend settings

Foundry starts with reasoning enabled by default. `PROGENITOR_REASONING_EFFORT`
controls the default effort and `PROGENITOR_REASONING_SUMMARY` the visible reasoning
summary preference. These Foundry-specific overrides are not sent to OpenCode Go.

The compose file also expects the external Docker network `progenitor_network`
and the external volume `progenitor-shared` to exist:

```bash
docker network create progenitor_network
docker volume create progenitor-shared
```

`progenitor-shared` is mounted at `/shared` in the runtime and exposed to the
agent as the writable `/shared/` route (`PROGENITOR_SHARED_DIR`). Mount the same
volume into A2A agent containers to exchange files with them; nothing in it is
access-controlled, so any container that mounts it can change anything there.

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
- Only the frontend is published to the host, bound to localhost: `127.0.0.1:8081:80` over HTTP
- The backend listens on port `8000` over HTTP **inside** the compose networks only
- The Progenitor HTTP server listens on port `9501` inside the compose networks
- Claude ACP listens at `tcp://progenitor-agent-claude:3001` inside the compose networks only
- The backend connects to the runtime with `PROGENITOR_RUNTIME_URL=http://progenitor:9501`
- The compose file attaches services to an internal bridge network and the external `progenitor_network`

For public deployment, terminate TLS and enforce authentication at a trusted
reverse proxy outside this Compose project. Forward requests to
`http://127.0.0.1:8081`; keep certificates and authentication credentials outside
the repository.

When upgrading from the old HTTPS containers, switch the reverse-proxy upstream
from HTTPS to HTTP together with the frontend container port change from 443 to
80. Public client traffic should remain HTTPS.

The localhost binding prevents direct remote access to the frontend; the backend
and runtime remain internal to the Docker networks.
Frontend nginx preserves the host proxy's `X-Forwarded-Proto: https` header,
and Uvicorn trusts forwarded headers on its Docker-internal port so redirects
retain the public HTTPS scheme. Do not publish the backend directly.


If you need direct host access to backend port `8000`, add your own port mapping
to a one-off override file; do not expose it in the public deployment.

## Volumes and ephemeral storage

- `progenitor` and `backend`: `${PROGENITOR_HOST_LOGS_DIR:-../logs}:/app/logs`, `${PROGENITOR_HOST_REPOS_DIR:-../repos}:/app/repos`

The CI deployment uses the GitHub Environment `production`. Store credentials in
its secrets and deployment options such as `PROGENITOR_TIMEZONE` in its variables.
CI derives the host `logs`, `repos`, and `claude` directories from the single
installation root `/var/lib/progenitor`; changing example timezone values does
not override an environment's configured timezone.

Ephemeral storage:
- `/app/tmp` is a `tmpfs` mount in `progenitor` and `backend`

Notes:
- Instructions and runtime memories are Postgres-backed content documents
  (`src/progenitor/documents/content.py`, keyed `instruction:`/`memory:` in
  the same shared `store` table as `mcp.json`/`schedule.json`/etc — see
  `src/progenitor/state/manager.py`). Neither `progenitor` nor `backend` mounts
  a host directory for them. `data/instructions/` is checked into git and
  `COPY`'d into the `progenitor` image (not `backend`'s); every boot syncs it
  into Postgres: top-level Markdown files retain logical paths such as
  `progenitor/genesis.md`. They are added when missing and otherwise kept,
  since they are edited on the Instructions page. Explicit subdirectories in
  custom instruction sources retain their own group names.
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
- `frontend` through the published nginx port: `curl -fsS http://127.0.0.1:8081/healthz` (proxied to `backend`)
- `frontend` container process: `docker compose -f docker/docker-compose.progenitor.yml exec -T frontend wget -q -O /dev/null http://127.0.0.1/`
- `progenitor` container: `docker compose -f docker/docker-compose.progenitor.yml exec -T progenitor curl -fsS http://127.0.0.1:9501/health` (the runtime's own HTTP endpoint)
- `claude` container: Docker probes its `socat` listener; the ACP endpoint is internal only.

## Self-hosted Actions runners

`docker/gha-runner/Dockerfile` builds the runner image with GitHub Actions runner
2.338.0, Node.js 24, Python 3.12, and the Docker CLI/Buildx plugin.
Build it from the repository root:

```bash
docker build -f docker/gha-runner/Dockerfile -t local/gha-runner:2.338.0 .
```

Repository-scoped runners need a separate registration for each repository, even
when their containers share a Docker host. Generate a short-lived registration
token using `gh api --method POST repos/OWNER/REPO/actions/runners/registration-token`;
never reuse another runner's credentials or commit tokens.

Use a distinct runner name, persistent registration volume, and work directory
per runner. Bind the work directory at the same absolute path on the host and
inside the container so Docker-based actions can mount job files correctly.
The container needs `/var/run/docker.sock`; this grants host-level Docker access,
so restrict it to trusted workflows. Deployment jobs require the `docker` label.

Configure the destination repository's deployment environment, secrets, variables,
and host mounts before allowing queued deployment jobs onto a new runner.
