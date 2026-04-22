# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

# Install system dependencies (curl for healthchecks, ca-certs for TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Note: GitHub CLI and Copilot CLI are NOT installed here.
# Build/git operations are delegated to the builder_agent (see app/agents/builder_agent/).

# Set working directory
WORKDIR /app

# Copy project metadata and source (including app.chat_server and app.progenitor)
COPY pyproject.toml README.md ./
COPY app/ ./app/
COPY data/prompts/ ./data/prompts/
COPY data/skills/ ./data/skills/
COPY scripts/ ./scripts/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install . \
    && mkdir -p /app/data/state /app/data/prompts /app/data/skills /app/logs /app/tmp

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV AGENT_NAME=progenitor
ENV PROGENITOR_DATA_DIR=/app/data
ENV PROGENITOR_LOG_DIR=/app/logs

# Expose chat server port
EXPOSE 8000

# Health check for container orchestration.
# Progenitor is not the HTTP backend; it writes /app/data/state/health.json periodically.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python /app/scripts/healthcheck.py || exit 1

# Default command (runs Progenitor agent)
CMD ["python", "-m", "app.progenitor.main"]
