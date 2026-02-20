FROM python:3.12-slim

# Install system dependencies
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

# Install Copilot CLI (provides `copilot --acp`)
RUN curl -fsSL https://gh.io/copilot-install | bash

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

# Copy application code (including app.chat_server and app.progenitor)
COPY app/ ./app/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/

# Create directories for mounted volumes
RUN mkdir -p /app/data/state /app/logs /app/worktrees

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose chat server port
EXPOSE 8000

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Default command (runs Progenitor agent)
CMD ["python", "-m", "app.progenitor.main"]
