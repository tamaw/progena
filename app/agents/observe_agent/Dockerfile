FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROGENITOR_STATE_DIR=/app/data/state \
    PROGENITOR_LOG_DIR=/app/logs \
    OBSERVE_STATE_DIR=/app/data/state \
    OBSERVE_LOGS_DIR=/app/logs

RUN addgroup --system agent && adduser --system --ingroup agent runner

WORKDIR /app/agent

COPY app/agents/observe_agent/ /app/agent/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/data/state /app/logs && \
    chown -R runner:agent /app/data /app/logs

VOLUME ["/app/data", "/app/logs"]

USER runner

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

ENTRYPOINT ["python", "-m", "uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]
