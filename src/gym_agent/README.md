# Gym Agent

A lightweight, **A2A-compatible** FastAPI service for **structured gym sessions and bodyweight tracking**.

## Features

- Log structured lifting sessions with exercises and sets
- Query lifting history with date and exercise filters
- Summarize strength progress with set, rep, and volume totals
- Track personal records by exercise
- Log bodyweight entries over time
- Summarize bodyweight change over a rolling window
- SQLite-backed storage
- Full Progenitor A2A protocol support (`X-A2A-Context-ID` header echo)

## Running

```bash
pip install fastapi "uvicorn[standard]" pydantic
uvicorn src.gym_agent.main:app --reload --port 8001
```

If you install the project from a built wheel or sdist, the `src` package is
included, so the same module path works after installation.

## Agent Card

```bash
curl http://localhost:8001/agent/card
```

## A2A Capabilities

### log_lifting_session

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "log_lifting_session",
    "payload": {
      "user_id": "alice",
      "date": "2024-06-01",
      "workout_name": "Upper A",
      "notes": "Bench felt strong today",
      "exercises": [
        {
          "name": "Bench Press",
          "sets": [
            {"reps": 5, "weight_kg": 80, "set_type": "working"},
            {"reps": 5, "weight_kg": 82.5, "set_type": "working"}
          ]
        },
        {
          "name": "Barbell Row",
          "sets": [
            {"reps": 8, "weight_kg": 70, "set_type": "working"}
          ]
        }
      ]
    }
  }'
```

### get_lifting_history

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "get_lifting_history",
    "payload": {
      "user_id": "alice",
      "exercise_name": "Bench Press",
      "start_date": "2024-06-01",
      "end_date": "2024-06-30",
      "limit": 25
    }
  }'
```

### summarize_strength_progress

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "summarize_strength_progress",
    "payload": {
      "user_id": "alice",
      "days": 30,
      "exercise_name": "Bench Press"
    }
  }'
```

### get_personal_records

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "get_personal_records",
    "payload": {
      "user_id": "alice"
    }
  }'
```

### log_bodyweight

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "log_bodyweight",
    "payload": {
      "user_id": "alice",
      "date": "2024-06-01",
      "weight_kg": 79.8,
      "notes": "Morning weigh-in"
    }
  }'
```

### get_bodyweight_history

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "get_bodyweight_history",
    "payload": {
      "user_id": "alice",
      "start_date": "2024-06-01",
      "end_date": "2024-06-30"
    }
  }'
```

### summarize_bodyweight_progress

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "summarize_bodyweight_progress",
    "payload": {
      "user_id": "alice",
      "days": 30
    }
  }'
```

### With A2A Context ID

Pass `X-A2A-Context-ID` to correlate requests; the header value is echoed back in the response:

```bash
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -H "X-A2A-Context-ID: my-context-123" \
  -d '{"capability":"get_bodyweight_history","payload":{"user_id":"alice"}}'
```

## Testing

```bash
pytest tests/test_a2a.py -v
```
