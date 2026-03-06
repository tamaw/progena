FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/runner \
    PYTHONPATH=/app \
    COPILOT_GITHUB_TOKEN= \
    GH_TOKEN= \
    GITHUB_TOKEN= \
    PROGENITOR_COPILOT_MODEL=gpt-5.3-codex \
    PROGENITOR_COPILOT_FLAGS="--yolo" \
    COPILOT_ACP_PORT=3000 \
    COPILOT_ACP_HEALTH_PORT=3001

RUN addgroup --system agent && adduser --system --home /home/runner --ingroup agent runner

RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI (needed for Copilot CLI extension)
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

COPY app/agents/copilot_acp/ /app/app/agents/copilot_acp/

RUN mkdir -p /app/logs /home/runner && \
    chown -R runner:agent /app/logs /home/runner

VOLUME ["/app/data", "/app/logs", "/app/worktrees"]

COPY app/agents/copilot_acp/docker-entrypoint.sh /usr/local/bin/copilot-acp-entrypoint.sh
RUN chmod +x /usr/local/bin/copilot-acp-entrypoint.sh

USER runner

EXPOSE 3000
EXPOSE 3001

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3001/')" || exit 1

ENTRYPOINT ["/usr/local/bin/copilot-acp-entrypoint.sh"]

CMD ["python", "-m", "app.agents.copilot_acp.acp_tcp_proxy"]
