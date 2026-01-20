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
COPY cli.py ./

# Create directories for mounted volumes
RUN mkdir -p /app/state /app/logs /app/worktrees

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose chat server port
EXPOSE 8000

# Default command (runs Progenitor agent)
CMD ["python", "-m", "app.progenitor.main"]
