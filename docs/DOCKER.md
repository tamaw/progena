# Docker Deployment

The project ships compose stacks under `docker/`.

## Compose Files

- `docker/docker-compose.litellm.yml` - LiteLLM stack (db + litellm + caddy reverse proxy)
- `docker/docker-compose.progenitor.yml` - Progenitor stack (progenitor + backend) that connects to the external LiteLLM stack

## Start Full Stack

From repository root:

```bash
docker compose -f docker/docker-compose.litellm.yml up --build -d
docker compose -f docker/docker-compose.progenitor.yml up --build -d
```

## Stop Stack

```bash
docker compose -f docker/docker-compose.progenitor.yml down
```

## View Logs

```bash
docker compose -f docker/docker-compose.progenitor.yml logs -f
docker compose -f docker/docker-compose.progenitor.yml logs -f progenitor
docker compose -f docker/docker-compose.progenitor.yml logs -f backend
docker compose -f docker/docker-compose.litellm.yml logs -f litellm
```

## Exposed Services

- LiteLLM: `http://localhost/` (via caddy) or `http://localhost:4000`
- Backend API: `http://localhost:8000`

Health checks:

```bash
curl http://localhost:4000/health
curl http://localhost:8000/health
```

## Required Environment

Set values in your shell or `.env` before startup:

- `LITELLM_MASTER_KEY`
- `LITELLM_BASE_URL` (defaults to `http://litellm:4000` on the shared external network)
- `PROGENITOR_MODEL_DEFAULT` (optional override)

## Notes

- The compose stack mounts runtime paths such as `data/state`, `logs`, `worktrees`, `prompts`, and `tmp`.
- Backend runs `uvicorn app.backend.main:app` in container and depends on both `progenitor` and `litellm`.
- Runner container images for spawned A2A/MCP processes are documented in `CONTAINER_RUNNERS.md`.
