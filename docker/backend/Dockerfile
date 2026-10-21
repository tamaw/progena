FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project metadata and source
COPY pyproject.toml README.md ./
COPY app/ ./app/
COPY scripts/ ./scripts/
# data/schemas/ is a static, git-checked-in contract (JSON Schemas for
# preferences, workflow nodes, MCP entries, ...) baked into the image --
# schemas are read from disk on every load_schema() call, so this directory
# must exist.
COPY data/schemas/ ./data/schemas/
# Bundled object agents are read-only files, shared with the runtime image.
COPY data/agents/objects/ ./data/agents/objects/
RUN pip install --no-cache-dir . \
    && chmod -R a-w /app/data/agents/objects

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Create directories for the (now Postgres-backed) data root and ephemeral scratch space.
RUN mkdir -p /app/logs /app/tmp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || exit 1

# This port is Docker-internal only; accept the frontend's public-scheme headers.
CMD ["python", "-m", "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
