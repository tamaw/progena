FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (including app.chat_server and app.progenitor)
COPY app/ ./app/
COPY config/ ./config/
COPY data/prompts/ ./data/prompts/
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
