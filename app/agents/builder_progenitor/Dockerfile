FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/runner \
    PYTHONPATH=/app \
    AZURE_OPENAI_BASE_URL= \
    AZURE_OPENAI_API_KEY= \
    GH_TOKEN= \
    COPILOT_GITHUB_TOKEN= \
    GIT_AUTHOR_NAME= \
    GIT_AUTHOR_EMAIL= \
    AGENT_NAME=builder_progenitor \
    PROGENITOR_REPO_URL=https://github.com/tamaw/progenitor.git \
    PROGENITOR_LOG_DIR=/app/logs \
    PROGENITOR_DATA_DIR=/app/data \
    AGENT_MODEL_DEFAULT= \
    OVERRIDE_AGENT_PLANNER_MODEL= \
    OVERRIDE_AGENT_BUILDER_MODEL= \
    DEFAULT_COPILOT_MODEL= \
    PROGENITOR_COPILOT_ADDITIONAL_FLAGS= \
    PROGENITOR_WORKTREES_ROOT=/app/worktrees \
    PROGENITOR_WORKTREE_DIR=/app/worktrees/worktrees

RUN addgroup --system agent && adduser --system --home /home/runner --ingroup agent runner

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    ripgrep \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI (needed for gh pr create)
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

# Install Copilot CLI extension
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN curl -fsSL --retry 8 --retry-delay 2 --retry-all-errors https://gh.io/copilot-install | bash

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY app/ /app/app/
COPY data/prompts/ /app/data/prompts/
COPY data/skills/ /app/data/skills/
COPY scripts/ /app/scripts/
COPY app/agents/builder_progenitor/requirements.txt /app/requirements.builder.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    pip install --no-cache-dir -r /app/requirements.builder.txt

RUN mkdir -p /app/data/state /app/data/prompts /app/data/skills /app/logs /app/worktrees /app/tmp /home/runner && \
    chown -R runner:agent /app/data /app/logs /app/worktrees /app/tmp /home/runner

VOLUME ["/app/data", "/app/logs", "/app/worktrees"]

COPY app/agents/builder_progenitor/docker-entrypoint.sh /usr/local/bin/builder-entrypoint.sh
RUN chmod +x /usr/local/bin/builder-entrypoint.sh

USER runner

EXPOSE 8108

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8108/health')" || exit 1

ENTRYPOINT ["/usr/local/bin/builder-entrypoint.sh"]

CMD ["python", "-m", "uvicorn", "app.agents.builder_progenitor.agent:app", "--host", "0.0.0.0", "--port", "8108"]
