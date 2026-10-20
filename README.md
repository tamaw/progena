# Progenitor

Progenitor is a perpetual, conversational AI agent system built with LangGraph. It includes a web frontend, backend API, and PostgreSQL-backed runtime.

## Prerequisites

- Docker Engine with Docker Compose
- An Azure OpenAI Responses API deployment and its base URL and API key

For local development, also install Python 3.11+ and Node.js 22.12+ with npm.

## Docker quick start

From a fresh clone, create the Docker resources required by the Compose stack:

```bash
docker network create progenitor_network
docker volume create progenitor-postgres-data
```

Copy the environment template, then set your Azure endpoint, API key and deployment name in `.env`:

```bash
cp .env.example .env
```

```dotenv
AZURE_OPENAI_BASE_URL=https://<resource>.openai.azure.com/openai/v1
AZURE_OPENAI_API_KEY=<your-api-key>
PROGENITOR_MODEL_DEFAULT=<your-deployment-name>
```

Start the stack from the repository root:

```bash
docker compose --env-file .env -f docker/docker-compose.progenitor.yml up --build -d
```

Open [https://localhost:8081](https://localhost:8081). The local certificate is self-signed, so the browser will display a certificate warning.

Useful commands:

```bash
docker compose --env-file .env -f docker/docker-compose.progenitor.yml logs -f
docker compose --env-file .env -f docker/docker-compose.progenitor.yml down
```

## Local development

Create and activate a virtual environment, then install the development dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

A local run also needs PostgreSQL with pgvector, the Azure OpenAI environment variables above, the runtime, backend, and frontend processes. Follow the complete [local startup guide](docs/LOCAL_STARTUP.md).

## Documentation

- [System specification](docs/SPEC.md)
- [Architecture diagram](docs/ARCHITECTURE_DIAGRAM.md)
- [Docker stack reference](docs/DOCKER.md)
- [Local startup guide](docs/LOCAL_STARTUP.md)
- [Backend API](docs/BACKEND.md)
- [Project structure](docs/PROJECT_STRUCTURE.md)

## License

[MIT](LICENSE)
