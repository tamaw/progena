FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST

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
    PROGENITOR_STATE_DIR=/app/data/state \
    AGENT_MODEL_DEFAULT= \
    PROGENITOR_MODEL_DEFAULT= \
    PROGENITOR_WORKTREES_ROOT=/app/worktrees \
    PROGENITOR_PROMPTS_DIR=/app/prompts

RUN addgroup --system agent && adduser --system --home /home/runner --ingroup agent runner

RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    ripgrep \
    jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.lock.txt /app/requirements.lock.txt
COPY app/agents/observe_agent/requirements.txt /app/requirements.observe.txt
RUN set -eux; \
    install_args="--index-url ${PIP_INDEX_URL}"; \
    if [ -n "${PIP_TRUSTED_HOST:-}" ]; then \
      install_args="$install_args --trusted-host ${PIP_TRUSTED_HOST}"; \
    fi; \
    pip install --no-cache-dir --upgrade pip $install_args && \
    pip install --no-cache-dir $install_args -r /app/requirements.lock.txt && \
    pip install --no-cache-dir $install_args -r /app/requirements.observe.txt

COPY app/ /app/app/
COPY prompts/ /app/prompts/
COPY scripts/ /app/scripts/

RUN mkdir -p /app/data/state /app/logs /app/worktrees /app/tmp /home/runner && \
    chown -R runner:agent /app/data /app/logs /app/worktrees /app/tmp /home/runner

VOLUME ["/app/data", "/app/logs", "/app/worktrees", "/app/prompts"]

COPY app/agents/observe_agent/docker-entrypoint.sh /usr/local/bin/observe-entrypoint.sh
RUN chmod +x /usr/local/bin/observe-entrypoint.sh

USER runner

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

ENTRYPOINT ["/usr/local/bin/observe-entrypoint.sh"]

CMD ["python", "-m", "uvicorn", "app.agents.observe_agent.agent:app", "--host", "0.0.0.0", "--port", "8080"]
