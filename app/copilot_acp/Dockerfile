FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/runner \
    PYTHONPATH=/app \
    COPILOT_GITHUB_TOKEN= \
    GH_TOKEN= \
    PROGENITOR_REPO_URL=https://github.com/tamaw/progenitor.git \
    PROGENITOR_PROJECT_ROOT=/app/worktrees \
    PROGENITOR_LOG_DIR=/app/logs/copilot_acp \
    PROGENITOR_COPILOT_MODEL=gpt-5.3-codex \
    PROGENITOR_COPILOT_FLAGS="--yolo" \
    COPILOT_ACP_PORT=3000

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

RUN mkdir -p /app/data /app/logs /app/worktrees /home/runner && \
  chown -R runner:agent /app/data /app/logs /app/worktrees /home/runner

VOLUME ["/app/data", "/app/logs", "/app/worktrees"]

COPY app/copilot_acp/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER runner

EXPOSE 3000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD []
