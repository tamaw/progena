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
RUN pip install --no-cache-dir .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Create directories for the (now Postgres-backed) data root and ephemeral scratch space.
RUN mkdir -p /app/data /app/logs /app/tmp

COPY docker/backend/certs/ /app/certs/

EXPOSE 8000

# Explicit Docker-only exception to the event-stream API rule: this probe only
# verifies that the backend process accepts HTTPS; it carries no app data.
# Self-signed cert, only ever reached by nginx over the internal docker
# network -- no insecure http fallback to forget to remove before a real
# deployment.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsSk https://127.0.0.1:8000/healthz >/dev/null || exit 1


CMD ["python", "-m", "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--ssl-keyfile", "/app/certs/key.pem", "--ssl-certfile", "/app/certs/cert.pem"]
