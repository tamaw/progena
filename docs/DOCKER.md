# Docker Deployment

The project ships compose stacks under `docker/`.

## Compose Files

- `docker/docker-compose.progenitor.yml` - Progenitor stack (progenitor + backend) with direct Azure Responses configuration

## Start Full Stack

From repository root:

```bash
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
```

## Exposed Services

- Backend API: `http://localhost:8000`

Health checks:

```bash
curl http://localhost:8000/health
```

## Required Environment

Set values in your shell or `.env` before startup:

- `AZURE_OPENAI_BASE_URL` (for example `https://<resource>.openai.azure.com/openai/v1`)
- `AZURE_OPENAI_API_KEY`
- `PROGENITOR_MODEL_DEFAULT` (optional override)

## Notes

- The compose stack mounts runtime paths such as `data/state`, `logs`, `worktrees`, `prompts`, and `tmp`.
- Backend runs `uvicorn app.backend.main:app` in container and depends on `progenitor`.
- Runner container images for spawned A2A/MCP processes are documented in `CONTAINER_RUNNERS.md`.
