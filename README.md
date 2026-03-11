# Progenitor

A perpetual, conversational personal assistant and bootstrap builder implementing the **Last Algorithm** dual-loop pattern.

## Overview

Progenitor is an always-on agent that accepts user input conversationally, routes requests to chat or delegates to specialised A2A agents, maintains a persistent task backlog and scheduler, and runs indefinitely — recovering from idle via exponential backoff.

Stack: Python 3.12 · LangGraph · FastAPI · React/Vite · asyncio

---

## Architecture

### Outer Loop

Runs forever, processing one unit of work per cycle: a user message, a scheduled trigger, or a backlog item.

```
Intake → TimeCheck → EnqueueDue → Decide → [respond / delegate] → Idle → repeat
```

Direct answers go straight to `Respond`. Delegated work is pushed to the backlog and processed via `PickNext`. When empty, the loop enters `Idle` with exponential backoff (30 s → 300 s, ×1.5).

### Inner Loop

Runs inside the **Builder Agent** per delegated work item. Bounded (≤ 5 iterations) and exits when a verification score meets the threshold.

```
Observe → Think → Plan → Build → Execute → Verify → Learn → (repeat or end)
```

`Observe` refines the ideal state; `Think` generates approaches; `Plan` picks an experiment; `Build` produces output; `Execute` runs tools; `Verify` scores the result; `Learn` updates strategy.

---

## Agents

| Agent | Port | Responsibility |
|---|---|---|
| **Progenitor** | — | Conversational orchestrator, task router, outer-loop host |
| **Builder Agent** | 8107 | Code generation and repo mutation; hosts the inner loop |
| **Observe Agent** | — | Lightweight monitoring/observation A2A responder |

---

## How to Run

### Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd app/frontend && npm install && cd ../..
```

### Dev mode

```bash
# Terminal 1 – agent
python -m app.progenitor.main

# Terminal 2 – backend API (port 8000)
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 3 – frontend (port 5173)
cd app/frontend && npm run dev
```

Or use the VS Code task **"Start All (dev)"**. For Docker: `docker compose -f docker/docker-compose.progenitor.yml up --build -d`

### Tests

```bash
pytest                       # all
pytest tests/progenitor/ -v  # progenitor unit tests
```

---

## Async Model

The runtime (`app/progenitor/runtime/loop.py`) runs two concurrent `asyncio` tasks:

1. **Input handler** — awaits messages from `_input_queue` (cap 100), acquires `_graph_lock`, runs one graph cycle.
2. **Scheduler** — polls `schedule.json` every `poll_seconds`, enqueues due items.

Both race via `asyncio.wait(return_when=FIRST_EXCEPTION)`. Graph cycles are serialised by a single lock. State checkpoints to `data/state/*.json` after every cycle.

Key queues (all `asyncio.Queue`): `_input_queue` (100), `_output_queue` (100), `_stream_queue` (1 000 tokens).

Mock mode (no real API calls): `export PROGENITOR_MOCK_LLM=true`

---

## Logs

| Path | Content |
|---|---|
| `/logs/<agent>/<agent>.stdout.log` | INFO and below (rotating, 10 MB × 5) |
| `/logs/<agent>/<agent>.stderr.log` | ERROR and above |
| `logs/conversation.jsonl` | Full message history |
| `logs/decisions.jsonl` | Routing decisions |
| `data/state/health.json` | Health checkpoint (every 30 s) |

```bash
PROGENITOR_LOG_LEVEL=info   # debug | info | warn | error
PROGENITOR_LOG_FORMAT=text  # text | json (use json for aggregators)
PROGENITOR_LOG_DIR=/logs    # override log root
```

Rotation is handled by Python's `RotatingFileHandler`.

---

## Development

```bash
ruff check app/   # lint
mypy app/         # type check
pytest            # test
```

All three must pass before opening a PR. Tests use `pytest-asyncio` (`asyncio_mode = auto`); mock the LLM client and state manager with `AsyncMock`.

**Contributing:** branch from `main`, make focused changes, add tests for new behaviour, open a PR with a clear description. PRs are merged by human maintainers — do not trigger deployments without explicit approval.
