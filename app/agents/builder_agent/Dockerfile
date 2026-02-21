FROM python:3.12-slim

# Builder Agent - self-contained A2A agent for inner-loop build operations.
# Requires git, GitHub CLI (gh), and GitHub Copilot CLI for build execution.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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

# Install GitHub Copilot CLI (provides `gh copilot` / `copilot --acp`)
RUN curl -fsSL https://gh.io/copilot-install | bash

WORKDIR /app

# Install Python dependencies before copying code (better layer caching)
COPY app/agents/builder_agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy the builder agent package
COPY app/agents/builder_agent/ /app/agent/

# Copy prompts used by inner-loop nodes (skills, system prompts)
COPY prompts/ /app/prompts/

# Runtime directories (state, logs, worktrees are mounted as volumes)
RUN mkdir -p /app/data/state /app/logs /app/worktrees

ENV PYTHONPATH=/app/agent
ENV PYTHONUNBUFFERED=1

USER runner

EXPOSE 8107

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8107/health')" || exit 1

WORKDIR /app/agent
CMD ["python", "-m", "uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8107"]
