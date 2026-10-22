FROM python:3.12-slim

# Baked in at build time (e.g. --build-arg GIT_COMMIT=$(git rev-parse HEAD)) since
# the image has no .git directory for the runtime to inspect at startup. Surfaced
# in the health document so a stale container running old code is identifiable
# without cross-referencing Postgres by hand.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}

# Install runtime CLI dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p /usr/share/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    rm -rf /var/lib/apt/lists/*

RUN addgroup --system --gid 10001 agent \
    && adduser --system --uid 10001 --ingroup agent --home /home/agent agent

# Set working directory
WORKDIR /app

# Copy project metadata and source (including src.backend and src.progenitor)
COPY pyproject.toml README.md ./
COPY src/ ./src/
# Synced into Postgres on every boot (see content_documents.sync_instruction_documents).
COPY data/instructions/ ./data/instructions/
# Progenitor's own skills: the read-only /skills/ mount.
COPY data/skills/ ./data/skills/
# data/schemas/ is a static, git-checked-in contract (JSON Schemas for
# preferences, workflow nodes, MCP entries, ...), read from disk on every
# load_schema() call.
COPY data/schemas/ ./data/schemas/
# Vendored canvas library catalogue: validates manifests and backs
# /canvases/_catalogue.json.
COPY data/canvases/ ./data/canvases/
# Native manifests are read-only runtime files, never imported into Postgres.
COPY data/agents/ ./data/agents/
COPY data/shims/ ./data/shims/
RUN pip install --no-cache-dir . \
    && chown -R root:root /app/data/shims \
    && chmod 0755 /app/data/shims/bin/* \
    && chmod -R a-w /app/data/shims \
    && chmod -R a-w /app/data/agents \
    && mkdir -p /app/agent-home /app/repos /app/logs /app/tmp /app/shell-workspaces /shared \
    && chown -R agent:agent /app/agent-home /app/repos /app/logs /app/tmp /app/shell-workspaces /shared

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV AGENT_NAME=progenitor
ENV PROGENITOR_DATA_DIR=/app/data
ENV PROGENITOR_LOG_DIR=/app/logs

ENV PROGENITOR_SHELL_HOME=/app/agent-home
ENV PROGENITOR_SHELL_WORKSPACE_DIR=/app/shell-workspaces
ENV PROGENITOR_SHELL_SHIM_BIN=/app/data/shims/bin
ENV PYTHONDONTWRITEBYTECODE=1
# Expose chat server port
EXPOSE 8000

# Health check for container orchestration.
# Progenitor is not the HTTP backend; it serves its own HTTP /health endpoint
# (see src/progenitor/runtime/http/server.py) on PROGENITOR_HTTP_PORT (default 9501),
# checked here via a same-host curl - no state file, no cross-container call.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD ["sh", "-c", "curl -f http://127.0.0.1:${PROGENITOR_HTTP_PORT:-9501}/health || exit 1"]

# Default command (runs Progenitor agent)
CMD ["python", "-m", "src.progenitor.main"]
USER 10001:10001
