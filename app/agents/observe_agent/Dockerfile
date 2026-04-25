FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/runner \
    PYTHONPATH=/app \
    AZURE_OPENAI_BASE_URL= \
    AZURE_OPENAI_API_KEY= \
    GH_TOKEN= \
    COPILOT_GITHUB_TOKEN= \
    AGENT_NAME=observe_agent \
    PROGENITOR_REPO_URL=https://github.com/tamaw/progenitor.git \
    PROGENITOR_LOG_DIR=/app/logs \
    PROGENITOR_DATA_DIR=/app/data \
    AGENT_MODEL_DEFAULT= \
    PROGENITOR_MODEL_DEFAULT= \
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

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY app/ /app/app/
COPY data/prompts/ /app/data/prompts/
COPY data/skills/ /app/data/skills/
COPY scripts/ /app/scripts/
COPY app/agents/observe_agent/requirements.txt /app/requirements.observe.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    pip install --no-cache-dir -r /app/requirements.observe.txt

RUN mkdir -p /app/data/state /app/data/prompts /app/data/skills /app/logs /app/worktrees /app/tmp /home/runner && \
    chown -R runner:agent /app/data /app/logs /app/worktrees /app/tmp /home/runner

VOLUME ["/app/data/state", "/app/logs", "/app/worktrees"]

COPY app/agents/observe_agent/docker-entrypoint.sh /usr/local/bin/observe-entrypoint.sh
RUN chmod +x /usr/local/bin/observe-entrypoint.sh

USER runner

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

ENTRYPOINT ["/usr/local/bin/observe-entrypoint.sh"]

CMD ["python", "-m", "uvicorn", "app.agents.observe_agent.agent:app", "--host", "0.0.0.0", "--port", "8080"]
