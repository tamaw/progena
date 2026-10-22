# DevOps engineer

Read `AGENTS.md`, `docs/DOCKER.md` and `docs/LOCAL_STARTUP.md` first. Repository
safety applies to infrastructure work: do not merge PRs or trigger deployments
without an explicit human request. Cancel an accidentally started workflow that
could deploy and notify the human.

## Current stack

- `docker/docker-compose.progenitor.yml` defines the runtime, backend, frontend,
  Postgres and supported ACP sidecar services. Read it for actual ports, mounts,
  networks and image settings; do not assume a future database/Redis setup.
- Python images copy `src/` into `/app/src` and use the `src.*` namespace. `/app`
  remains the container working directory. Frontend build context is `src/frontend`.
- Runtime health is `/health` on port 9501; backend liveness is `/healthz` on port
  8000. The frontend image is an Nginx static server/API proxy, not Vite.
- Postgres owns checkpoints and mutable documents. Shell homes, repository mounts
  and scratch volumes are runtime storage, not checked-in source directories.
- `.env.example` documents environment settings. Provider/model configuration is
  database-backed; never print decrypted provider credentials or complete configs
  containing secrets.

## Workflow changes

Use `.github/workflows/ci.yml` and `.pre-commit-config.yaml` as the actual pipeline
sources. Preserve pinned Pyright and explicit venv interpreter selection, current
frontend scripts, image change detection and the existing deployment gate. Update
build contexts, COPY paths and filters together when moving source.

Validate Compose with `docker compose -f docker/docker-compose.progenitor.yml config --quiet`.
Run the relevant lint/build checks and report whether images were actually built.
For an explicitly requested deployment, follow the existing workflow and verify
service health and deployed commit/image identity afterward. Otherwise stop at a
reviewable PR; successful PR CI does not imply images were built or deployed.

Treat backups as Postgres backups, not archives of `data/*.json`. Avoid broad
Docker pruning, volume deletion, restore commands or process termination as
routine troubleshooting; inspect the affected service and preserve stored data.
