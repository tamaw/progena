# Docker Deployment

## Architecture

The Docker setup includes two services:

1. **progenitor** - The main Progenitor agent (perpetual loop)
2. **backend** - FastAPI gateway (OpenAI-compatible REST API)

Both run in the same network and share state/logs volumes.

## Quick Start

### Build and Run
```bash
docker-compose up --build
```

### Run in Background
```bash
docker-compose up -d
```

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend
docker-compose logs -f progenitor
```

### Stop Services
```bash
docker-compose down
```

## Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# OpenAI (if using OpenAI directly)
OPENAI_API_KEY=sk-...

# Azure OpenAI (recommended)
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_DEPLOYMENT_DEFAULT=your-deployment-name
```

## Services

### Progenitor Agent
- **Container**: `progenitor`
- **Purpose**: Main orchestration agent
- **Mode**: Perpetual loop with stdin/stdout
- **Network**: Internal only (no exposed ports)

### Backend
- **Container**: `progenitor-backend`
- **Purpose**: REST API gateway
- **Port**: 8000 (exposed to host)
- **Endpoints**:
  - `GET /health` - Health check
  - `GET /v1/models` - List models
  - `POST /v1/chat/completions` - Chat (OpenAI-compatible)
  - `POST /chat` - Simple chat

### Access Backend

Once running, the backend is available at:
```
http://localhost:8000
```

Test with curl:
```bash
curl http://localhost:8000/health
```

Or use the OpenAI SDK:
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed-locally"
)

response = client.chat.completions.create(
    model="progenitor-v1",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

## Volumes

- `./state` → `/app/state` - Persistent agent state
- `./logs` → `/app/logs` - Logs (JSONL format)
- `./worktrees` → `/app/worktrees` - Git worktrees for builds
- `./config` → `/app/config` - Configuration files (read-only)

## Network

Both services run in a bridge network (`progenitor-net`) allowing them to communicate.

## Health Check

The backend includes a health check that runs every 30s:
```bash
docker-compose ps
```

## Troubleshooting

### Backend not responding
```bash
docker-compose logs backend
```

### Progenitor agent not starting
```bash
docker-compose logs progenitor
```

### Rebuild after code changes
```bash
docker-compose up --build
```

### Access container shell
```bash
# Backend
docker exec -it progenitor-backend sh

# Progenitor agent
docker exec -it progenitor sh
```

## Development

For development, you can mount the source code:

```yaml
services:
  backend:
    volumes:
      - ./app:/app/app:ro  # Mount source code
    command: ["python", "-m", "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

This enables auto-reload on code changes.
