FROM python:3.12-slim

# Builder Agent - self-contained A2A agent for inner-loop build operations.
# Requires git and GitHub CLI (gh) for PR management.
# Copilot ACP is accessed via the copilot_acp container over TCP.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/runner \
    PYTHONPATH=/app \
    AZURE_OPENAI_BASE_URL= \
    AZURE_OPENAI_API_KEY= \
    AGENT_NAME=builder_agent \
    GH_TOKEN= \
    COPILOT_GITHUB_TOKEN= \
    GIT_AUTHOR_NAME= \
    GIT_AUTHOR_EMAIL= \
    COPILOT_ACP_URL= \
    PROGENITOR_REPO_URL=https://github.com/tamaw/progenitor.git \
    PROGENITOR_MODEL_DEFAULT= \
    PROGENITOR_MODEL_PLANNER= \
    PROGENITOR_MODEL_BUILDER= \
    PROGENITOR_COPILOT_MODEL= \
    PROGENITOR_BUILDER_AGENT_MODEL_DEFAULT= \
    PROGENITOR_BUILDER_AGENT_MODEL_PLANNER= \
    PROGENITOR_BUILDER_AGENT_MODEL_BUILDER= \
    PROGENITOR_BUILDER_AGENT_COPILOT_MODEL= \
    TEST_MODE=false \
    PROGENITOR_PROJECT_ROOT=/app/worktrees \
    PROGENITOR_WORKTREE_DIR=/app/worktrees/worktrees \
    PROGENITOR_STATE_DIR=/app/data/state \
    PROGENITOR_LOG_DIR=/app/logs

RUN addgroup --system agent && adduser --system --ingroup agent runner

# Install system dependencies: git for worktrees, curl/ca-certs for CLI installs
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI (gh) for PR lifecycle management
RUN set -eux; \
    mkdir -p /usr/share/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y gh; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies before copying code (better layer caching)
COPY app/agents/builder_agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy the builder agent package
COPY app/agents/builder_agent/ /app/agent/

# Copy prompts used by inner-loop nodes (skills, system prompts)
COPY prompts/ /app/prompts/

# Runtime directories (state and logs are mounted as named volumes).
# /app/worktrees is the git workspace used for worktrees — populated on first
# boot if PROGENITOR_REPO_URL is set, or bind-mounted with a pre-cloned repo.
RUN mkdir -p /app/data/state /app/logs /app/worktrees && \
    chown -R runner:agent /app/data /app/logs /app/worktrees

# Named volume mount-points — Docker Compose overrides these with named volumes.
VOLUME ["/app/data", "/app/logs", "/app/worktrees"]

COPY app/agents/builder_agent/docker-entrypoint.sh /usr/local/bin/builder-entrypoint.sh
RUN chmod +x /usr/local/bin/builder-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/builder-entrypoint.sh"]

EXPOSE 8107

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8107/health')" || exit 1

WORKDIR /app
CMD ["python", "-m", "uvicorn", "agent.agent:app", "--host", "0.0.0.0", "--port", "8107"]
