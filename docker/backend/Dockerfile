FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.lock.txt .
RUN set -eux; \
    install_args="--index-url ${PIP_INDEX_URL}"; \
    if [ -n "${PIP_TRUSTED_HOST:-}" ]; then \
      install_args="$install_args --trusted-host ${PIP_TRUSTED_HOST}"; \
    fi; \
    pip install --no-cache-dir $install_args -r requirements.lock.txt

# Copy application code
COPY app/ ./app/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Create directories for mounted volumes
RUN mkdir -p /app/data /app/logs /app/tmp /app/prompts

VOLUME ["/app/data", "/app/logs", "/app/prompts"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
