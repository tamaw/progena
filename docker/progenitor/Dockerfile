FROM python:3.12-slim

# Install system dependencies (curl for healthchecks, ca-certs for TLS)
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Note: GitHub CLI and Copilot CLI are NOT installed here.
# Build/git operations are delegated to the builder_agent (see app/agents/builder_agent/).

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

# Health check for container orchestration.
# Progenitor is not the HTTP backend; it writes /app/data/state/health.json periodically.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python /app/scripts/healthcheck.py || exit 1

# Default command (runs Progenitor agent)
CMD ["python", "-m", "app.progenitor.main"]
