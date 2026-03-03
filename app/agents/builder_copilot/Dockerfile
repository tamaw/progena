FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/runner \
    PYTHONPATH=/app \
    AZURE_OPENAI_BASE_URL= \
    AZURE_OPENAI_API_KEY= \
    GH_TOKEN= \
    GITHUB_TOKEN= \
    PROGENITOR_REPO_URL=https://github.com/tamaw/progenitor.git \
    PROGENITOR_MODEL_DEFAULT= \
    PROGENITOR_MODEL_PLANNER= \
    PROGENITOR_MODEL_BUILDER= \
    PROGENITOR_COPILOT_MODEL=gpt-5.3-codex \
    GIT_AUTHOR_NAME= \
    GIT_AUTHOR_EMAIL= \
    TEST_MODE=false \
    PROGENITOR_PROJECT_ROOT=/app/worktrees \
    PROGENITOR_WORKTREE_DIR=/app/worktrees/worktrees \
    PROGENITOR_STATE_DIR=/app/data/state \
    PROGENITOR_LOG_DIR=/app/logs/builder_copilot

RUN addgroup --system agent && adduser --system --home /home/runner --ingroup agent runner

RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    ripgrep \
    jq \
    && rm -rf /var/lib/apt/lists/*

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

RUN curl -fsSL --retry 8 --retry-delay 2 --retry-all-errors https://gh.io/copilot-install | bash

WORKDIR /app

COPY requirements.lock.txt /app/requirements.lock.txt
COPY app/agents/builder_copilot/requirements.txt /app/requirements.builder_copilot.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.lock.txt && \
    pip install --no-cache-dir -r /app/requirements.builder_copilot.txt

COPY app/ /app/app/
COPY prompts/ /app/prompts/
COPY scripts/ /app/scripts/

RUN mkdir -p /app/data/state /app/logs/builder_copilot /app/worktrees /home/runner && \
    chown -R runner:agent /app/data /app/logs /app/worktrees /home/runner

VOLUME ["/app/data", "/app/logs", "/app/worktrees"]

USER runner

EXPOSE 8108

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8108/health')" || exit 1

CMD ["python", "-m", "uvicorn", "app.agents.builder_copilot.agent:app", "--host", "0.0.0.0", "--port", "8108"]
