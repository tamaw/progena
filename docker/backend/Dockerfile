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
COPY data/prompts/ ./data/prompts/
COPY data/skills/ ./data/skills/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Create directories for mounted volumes
RUN mkdir -p /app/data/prompts /app/data/skills /app/logs /app/tmp

VOLUME ["/app/data/prompts", "/app/data/skills", "/app/logs"]

EXPOSE 8000

# Explicit Docker-only exception to the event-stream API rule: this probe only
# verifies that the backend process accepts HTTP; it carries no app data.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || exit 1


CMD ["python", "-m", "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
