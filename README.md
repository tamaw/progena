# Progena AI

[Progena AI](https://progena.ai) is a perpetual, conversational AI agent system built with LangGraph. It includes a web frontend, backend API, and PostgreSQL-backed runtime.

## Prerequisites

- Docker Engine with Docker Compose
- An Azure OpenAI Responses API deployment and its base URL and API key

For local development, also install Python 3.11+ and Node.js 22.12+ with npm.

## Docker quick start

From a fresh clone, create the Docker resources required by the Compose stack:

```bash
docker network create progena_network
docker volume create progena-postgres-data
```

Copy the environment template, then set your Azure endpoint, API key and deployment name in `.env`:

```bash
cp .env.example .env
```

Configure model connections in **Settings → Providers**. Foundry and OpenCode Go use the **Responses API only**. Profiles can select an explicit connection or inherit the default. Saved changes apply to new turns; active turns retain their connection.

Connections live in ordinary `provider_connection` / `provider_settings` tables in the backend PostgreSQL database (`BACKEND_DATABASE_NAME`, default `backend`), not agent state. Both services need access to this database.

Keys are write-only and either reference `AZURE_OPENAI_API_KEY`, `OPENCODE_GO_API_KEY`, or `PROGENA_PROVIDER_*_API_KEY`, or are encrypted in the database. Database credentials require the same stable `PROGENA_PROVIDER_ENCRYPTION_KEY` Fernet key in backend and runtime; losing it makes stored keys unreadable. Custom environment sources must be supplied to both services.

A legacy Azure endpoint is imported once on initial provider-database setup. `/openai/responses?api-version=...` becomes `/openai/v1`. An invalid initial endpoint leaves setup retryable; after setup completes, legacy endpoint environment changes are ignored. An empty installation starts without making model calls so providers can be configured.

Go uses conversation-scoped `x-opencode-session` headers and does not receive Foundry reasoning overrides. Knowledge embeddings require an explicitly selected Foundry connection. Independent A2A credentials and Claude ACP authentication are unchanged.

Optional environment credentials and legacy first-run import settings:

```dotenv
AZURE_OPENAI_BASE_URL=https://<resource>.openai.azure.com/openai/v1
AZURE_OPENAI_API_KEY=<your-api-key>
OPENCODE_GO_API_KEY=<your-go-api-key>
PROGENA_MODEL_DEFAULT=<your-deployment-name>
# Required only for database-stored keys:
PROGENA_PROVIDER_ENCRYPTION_KEY=<shared-fernet-key>
```

Start the stack from the repository root:

```bash
docker compose --env-file .env -f docker/docker-compose.progena.yml up --build -d
```

Open [http://127.0.0.1:8081](http://127.0.0.1:8081) for loopback-only access. In a public deployment, use the host nginx public HTTPS URL: the host owns TLS certificates and OAuth2 authentication, while the Compose frontend and backend communicate over HTTP.

When rolling out this stack, change the host nginx upstream for `127.0.0.1:8081` from `https://` to `http://` at the same time as the Compose frontend port mapping changes to container port 80. This does not change public HTTPS; TLS remains terminated at host nginx.

Useful commands:

```bash
docker compose --env-file .env -f docker/docker-compose.progena.yml logs -f
docker compose --env-file .env -f docker/docker-compose.progena.yml down
```

## Local development

Create and activate a virtual environment, then install the development dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

A local run also needs PostgreSQL with pgvector, the Azure OpenAI environment variables above, the runtime, backend, and frontend processes. Follow the complete [local startup guide](docs/LOCAL_STARTUP.md).

Standalone A2A agents and their image/deployment ownership live in [progena.agents](https://github.com/tamaw/progena.agents). This repository contains Progena and its built-in object agents.

## Documentation

- [System specification](docs/SPEC.md)
- [Architecture diagram](docs/ARCHITECTURE_DIAGRAM.md)
- [Docker stack reference](docs/DOCKER.md)
- [Local startup guide](docs/LOCAL_STARTUP.md)
- [Backend API](docs/BACKEND.md)
- [Project structure](docs/PROJECT_STRUCTURE.md)

## License

[MIT](LICENSE)
