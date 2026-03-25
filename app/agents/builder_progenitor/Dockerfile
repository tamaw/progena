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
    AGENT_MODEL_DEFAULT= \
    OVERRIDE_AGENT_PLANNER_MODEL= \
    OVERRIDE_AGENT_BUILDER_MODEL= \
    DEFAULT_COPILOT_MODEL= \
    PROGENITOR_COPILOT_ADDITIONAL_FLAGS= \
    PROGENITOR_WORKTREES_ROOT=/app/worktrees \
    PROGENITOR_PROMPTS_DIR=/app/prompts \
    PROGENITOR_BUNDLED_PROMPTS_DIR=/opt/progenitor/prompts

RUN addgroup --system agent && adduser --system --home /home/runner --ingroup agent runner

RUN apt-get update && apt-get install -y \
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
    apt-get install -y gh; \
    rm -rf /var/lib/apt/lists/*

# Install Copilot CLI extension
RUN curl -fsSL --retry 8 --retry-delay 2 --retry-all-errors https://gh.io/copilot-install | bash

WORKDIR /app

COPY requirements.lock.txt /app/requirements.lock.txt
COPY app/agents/builder_progenitor/requirements.txt /app/requirements.builder.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.lock.txt && \
    pip install --no-cache-dir -r /app/requirements.builder.txt

COPY app/ /app/app/
COPY prompts/ /app/prompts/
COPY prompts/ /opt/progenitor/prompts/
COPY scripts/ /app/scripts/

RUN mkdir -p /app/data/state /app/logs /app/worktrees /app/tmp /home/runner && \
    chown -R runner:agent /app/data /app/logs /app/worktrees /app/tmp /home/runner

VOLUME ["/app/data", "/app/logs", "/app/worktrees", "/app/prompts"]

COPY app/agents/builder_progenitor/docker-entrypoint.sh /usr/local/bin/builder-entrypoint.sh
RUN chmod +x /usr/local/bin/builder-entrypoint.sh

USER runner

EXPOSE 8108

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8108/health')" || exit 1

ENTRYPOINT ["/usr/local/bin/builder-entrypoint.sh"]

CMD ["python", "-m", "uvicorn", "app.agents.builder_progenitor.agent:app", "--host", "0.0.0.0", "--port", "8108"]
