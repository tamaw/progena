FROM python:3.12-slim

WORKDIR /app

# Required scheduler env vars (wired in CI/deploy):
# - SCHEDULER_ENABLED
# - SCHEDULER_POLL_SECONDS
# - SCHEDULER_SCHEDULE_PATH
# - SCHEDULER_IPC_HOST
# - SCHEDULER_IPC_PORT
# - SCHEDULER_REQUEST_TIMEOUT_SECONDS
# Optional:
# - SCHEDULER_LOG_LEVEL

COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt && pip install --no-cache-dir croniter

COPY app/ ./app/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.scheduler.main"]

