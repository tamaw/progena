FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OBSERVE_STATE_DIR=/app/data/state \
    OBSERVE_LOGS_DIR=/app/logs

WORKDIR /app/agent

COPY . /app/agent

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Documented mount points (mount read-only at runtime).
VOLUME ["/app/data/state", "/app/logs"]

EXPOSE 8080

ENTRYPOINT ["python", "-m", "uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]

