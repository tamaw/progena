# Gym Agent

A lightweight, **A2A-compatible** FastAPI service for logging and analysing gym workouts.

## Features

- Log workouts (type, duration, distance, notes)
- Query history with optional date filters
- Summarise progress over a rolling window
- Calculate habit streaks per workout type
- SQLite persistence with JSON fallback
- Full Progenitor A2A protocol support (`X-A2A-Context-ID` header echo)

## Running

```bash
pip install fastapi "uvicorn[standard]" pydantic python-dateutil
uvicorn src.gym_agent.main:app --reload --port 8001
```

## Agent Card

```bash
curl http://localhost:8001/agent/card
```

## A2A Capabilities

### log_workout

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "log_workout",
    "payload": {
      "user_id": "alice",
      "date": "2024-06-01",
      "workout": {
        "type": "running",
        "duration_min": 45,
        "distance_km": 8.5,
        "notes": "Morning run in the park"
      }
    }
  }'
```

### get_history

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "get_history",
    "payload": {
      "user_id": "alice",
      "start_date": "2024-06-01",
      "end_date": "2024-06-30",
      "limit": 50
    }
  }'
```

### summarize_progress

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "summarize_progress",
    "payload": {
      "user_id": "alice",
      "days": 30
    }
  }'
```

### habit_streak

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "habit_streak",
    "payload": {
      "user_id": "alice",
      "habit": "running",
      "min_days": 1
    }
  }'
```

### With A2A Context ID

Pass `X-A2A-Context-ID` to correlate requests; the header value is echoed back in the response:

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -H "X-A2A-Context-ID: my-context-123" \
  -d '{"capability":"get_history","payload":{"user_id":"alice"}}'
```

## Testing

```bash
pytest tests/test_a2a.py -v
```
