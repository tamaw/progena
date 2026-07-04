FROM python:3.12-slim

# Baked in at build time (e.g. --build-arg GIT_COMMIT=$(git rev-parse HEAD)) since
# the image has no .git directory for the runtime to inspect at startup. Surfaced
# in the health document so a stale container running old code is identifiable
# without cross-referencing Postgres by hand.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}

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
RUN pip install --no-cache-dir . \
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
# Progenitor is not the HTTP backend; it serves its own HTTP /health endpoint
# (see app/progenitor/runtime/http.py) on PROGENITOR_HTTP_PORT (default 9501),
# checked here via a same-host curl - no state file, no cross-container call.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://127.0.0.1:${PROGENITOR_HTTP_PORT:-9501}/health || exit 1

# Default command (runs Progenitor agent)
CMD ["python", "-m", "app.progenitor.main"]
