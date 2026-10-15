FROM python:3.12-slim

ARG AGENT_NAME
ARG AGENT_PORT=8080

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/runner \
    PYTHONPATH=/app \
    AGENT_NAME=${AGENT_NAME} \
    AGENT_PORT=${AGENT_PORT} \
    AGENT_APP_MODULE=app.agents.${AGENT_NAME}.agent:app \
    AZURE_OPENAI_BASE_URL= \
    AZURE_OPENAI_API_KEY= \
    GH_TOKEN= \
    COPILOT_GITHUB_TOKEN= \
    GIT_AUTHOR_NAME= \
    GIT_AUTHOR_EMAIL= \
    PROGENITOR_REPO_URL=https://github.com/tamaw/progenitor.git \
    AGENT_MODEL_DEFAULT= \
    OVERRIDE_AGENT_PLANNER_MODEL= \
    OVERRIDE_AGENT_BUILDER_MODEL= \
    DEFAULT_COPILOT_MODEL= \
    PROGENITOR_MODEL_DEFAULT= \
    PROGENITOR_COPILOT_ADDITIONAL_FLAGS= \
    PROGENITOR_PROJECT_ROOT=/app/worktrees \
    PROGENITOR_WORKTREE_DIR=/app/worktrees/worktrees \
    PROGENITOR_WORKTREES_ROOT=/app/worktrees \
    PROGENITOR_DATA_DIR=/app/data \
    PROGENITOR_STATE_DIR=/app/data/state \
    PROGENITOR_LOG_DIR=/app/logs

RUN test -n "$AGENT_NAME"

RUN addgroup --system agent && adduser --system --home /home/runner --ingroup agent runner

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    ripgrep \
    jq \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "$AGENT_NAME" in \
      builder_agent|builder_progenitor) \
        mkdir -p /usr/share/keyrings; \
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
          -o /usr/share/keyrings/githubcli-archive-keyring.gpg; \
        chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg; \
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
          > /etc/apt/sources.list.d/github-cli.list; \
        apt-get update; \
        apt-get install -y --no-install-recommends gh; \
        rm -rf /var/lib/apt/lists/*; \
        ;; \
    esac

RUN set -eux; \
    if [ "$AGENT_NAME" = "builder_progenitor" ]; then \
      curl -fsSL --retry 8 --retry-delay 2 --retry-all-errors https://gh.io/copilot-install \
        -o /tmp/copilot-install.sh; \
      bash /tmp/copilot-install.sh; \
      rm -f /tmp/copilot-install.sh; \
    fi

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY app/ /app/app/
COPY scripts/ /app/scripts/
# data/schemas/ is a static, git-checked-in contract (JSON Schemas for
# preferences, workflow nodes, MCP entries, ...) read from disk on every
# load_schema() call (see app/progenitor/schemas.py) -- required by any
# agent using the structured_output tool or workflow validation.
COPY data/schemas/ /app/data/schemas/

RUN test -d "/app/app/agents/${AGENT_NAME}" && \
    test -f "/app/app/agents/${AGENT_NAME}/requirements.txt" && \
    test -f "/app/app/agents/${AGENT_NAME}/docker-entrypoint.sh"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    pip install --no-cache-dir -r "/app/app/agents/${AGENT_NAME}/requirements.txt"

RUN mkdir -p /app/data/state /app/data/instructions /app/data/skills /app/logs /app/worktrees /app/tmp /home/runner/.copilot && \
    cp "/app/app/agents/${AGENT_NAME}/docker-entrypoint.sh" /usr/local/bin/agent-entrypoint.sh && \
    chmod +x /usr/local/bin/agent-entrypoint.sh && \
    if [ -f "/app/app/agents/${AGENT_NAME}/lsp-config.json" ]; then \
      cp "/app/app/agents/${AGENT_NAME}/lsp-config.json" /home/runner/.copilot/lsp-config.json; \
    fi && \
    chown -R runner:agent /app/data /app/logs /app/worktrees /app/tmp /home/runner

VOLUME ["/app/data/state", "/app/logs", "/app/worktrees"]

EXPOSE ${AGENT_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import json, os, sys, urllib.request; url=f'http://localhost:{os.getenv(\"AGENT_PORT\", \"8080\")}/health'; response=urllib.request.urlopen(url, timeout=5); payload=json.load(response); sys.exit(0 if response.status == 200 and payload.get('status') == 'healthy' else 1)" || exit 1

ENTRYPOINT ["/usr/local/bin/agent-entrypoint.sh"]

CMD ["sh", "-c", "exec python -m uvicorn \"$AGENT_APP_MODULE\" --host 0.0.0.0 --port \"$AGENT_PORT\""]
