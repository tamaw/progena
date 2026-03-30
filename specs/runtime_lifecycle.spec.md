# Runtime Lifecycle

## Goal

This document describes the perpetual runtime loop and the service lifecycle that make the agent feel persistent rather than request-bound.

The goal is to preserve the behaviour of a system that can start cleanly, become ready, remain available over time, process direct and scheduled work, and shut down without losing the continuity that users care about.

## Context

This lifecycle spec builds on top of the shared workspace state (`current_state.json`, `build_state.json`, `schedule.json`) and the IPC layer (`app/progenitor/ipc/`) that connects the Progenitor loop to the backend and other clients.

Its behaviour must align with the same runtime pathways used by conversation, scheduling, and memory so that later changes preserve one understandable lifecycle model.

---

## Lifecycle Model

The runtime moves through a small number of understandable states:

| State | Description | Health file `status` |
|---|---|---|
| **Starting** | Loading config, creating directories, initialising state stores | _(not yet written)_ |
| **Ready** | Loop running, IPC server listening, health file current | `running` |
| **Processing** | Graph cycle in progress; `_request_lock` held; `_graph_lock` held | `running` |
| **Idle** | No work queued; exponential backoff sleeping | `running` (idle_cycles > 0) |
| **Stopping** | Shutdown signal received; tasks cancelling; state flushing | `stopped` (written on exit) |

The system does not rely on fragile transitions or hidden background behaviour.  Each state is visible in `health.json`, the IPC event queue, and `STATUS:` stdout lines.

---

## Bootstrap and Readiness

Startup prepares only the pieces required to become useful.  The sequence must be this order:

1. `get_config()` + `setup_logging()` — configuration loaded and validated; secrets never written to logs.
2. Storage directories created idempotently:
   - `PROGENITOR_STATE_DIR` (default `/data/state`)
   - `PROGENITOR_STATE_DIR/../config`
   - `PROGENITOR_LOG_DIR` (default `/logs`)
   - `PROGENITOR_WORKTREE_DIR` (default `/worktrees`)
3. `StateManager.initialize()` — state files confirmed/created; file locks available.
4. `ToolRegistry.load()` — agent registry loaded; startup agent discovery run.
5. `LLMClient` — provider config validated; event pusher wired.
6. LangGraph compiled (`create_deep_agent_for_loop`).
7. `ProgenitorLoop` created; `loop_runner.run_forever()` started as background task.
8. `IPCServer` started (`PROGENITOR_IPC_HOST:PROGENITOR_IPC_PORT`, default `127.0.0.1:9500`).
9. Startup config logged to `decisions.jsonl` (secrets redacted).

**Readiness means** the runtime can:
- accept IPC connections and route them to the loop
- recover conversation continuity from `current_state.json`
- produce a meaningful response path (LLM config valid, model reachable)
- report a fresh `health.json` (written within `PROGENITOR_HEALTH_STALE_SECONDS`, default 120 s)

Readiness does **not** mean the LLM has been called or any conversation has happened.  The agent is ready the moment the IPC server is listening and the health file is current.

Initialization is **idempotent**: repeated startup paths do not create duplicate loop tasks, IPC servers, or state files.

---

## Health Endpoint

The runtime must expose a lightweight health endpoint suitable for container probes and operators.

### `GET /health`

**Response (HTTP 200 — healthy):**

```json
{
  "status": "ok",
  "name":   "progenitor",
  "version": "0.1.0",
  "uptime_seconds": 123.4
}
```

**Response (HTTP 503 — shutting down / not ready):**

```json
{
  "status": "draining",
  "name":   "progenitor",
  "version": "0.1.0",
  "uptime_seconds": 45.2
}
```

Field rules:
- `status` — always present; `"ok"` when healthy, `"draining"` when shutdown is in progress. HTTP status code matches: `200` for `ok`, `503` for `draining`.
- `name` — always present; fixed string `"progenitor"`.
- `version` — always present; `api_version` from `BackendConfig` (default `"0.1.0"`).
- `uptime_seconds` — always present; float, rounded to 1 decimal, measuring time since `_server_start_time`.

### `GET /health/detailed`

Returns the full health payload (progenitor client status, rate limiter, memory, Python info, etc.) for operator dashboards and the frontend.  Not intended for container probes.

### Health File (`health.json`)

The loop writes `PROGENITOR_STATE_DIR/health.json` after every graph cycle and periodically during idle (every `PROGENITOR_CHECKPOINT_INTERVAL_SECONDS`, default 30 s).  The backend's detailed health endpoint reads this file to expose loop-side status without an IPC round-trip.

Shape:
```json
{
  "status": "running",
  "last_active": "2026-03-30T09:55:00Z",
  "run_id": "...",
  "cycle_count": 42,
  "pid": 12345,
  "idle_cycles": 0,
  "current_backoff_seconds": 30,
  "message_count": 7,
  "agent_count": 3,
  "queues": {
    "input_queue":  { "size": 0, "maxsize": 100, "full": false },
    "output_queue": { "size": 0, "maxsize": 100, "full": false },
    "stream_queue": { "size": 0, "maxsize": 1000, "full": false },
    "event_queue":  { "size": 0, "maxsize": 100,  "full": false }
  }
}
```

On graceful shutdown the loop writes `"status": "stopped"` (or `"offline"` from the service layer).  A stale health file (not updated within `PROGENITOR_HEALTH_STALE_SECONDS`) indicates the process has crashed or frozen.

---

## Active and Idle Operation

The runtime supports both activity and quiet periods without changing its nature.

### Active (Processing)

When a user message or scheduled task arrives:

1. The IPC server acquires `_request_lock` (serialising concurrent requests).
2. The loop's input handler acquires `_graph_lock` and begins a graph cycle.
3. `input_wait_seconds` (time the message waited in the input queue) and `pending_messages_in_queue` (depth of remaining queue after dequeue) are written to `ProgenitorState` before the cycle runs.  The intake node annotates the `ConversationMessage.metadata` with `queued_wait_seconds` (if ≥ 30 s) and `pending_messages_in_queue` (if > 0) so the LLM can acknowledge delayed or batched messages.
4. If `_request_lock` is already held when a new request arrives, the IPC server immediately pushes a `message_queued` event to all connected SSE clients so frontends show real-time feedback.
5. `health.json` is updated after every cycle.

### Idle

When the input queue is empty:

- The loop increments `idle_cycles` and sleeps for `current_backoff_seconds`.
- Backoff grows exponentially: `current_backoff_seconds × PROGENITOR_BACKOFF_MULTIPLIER` (default 1.5×), capped at `PROGENITOR_MAX_BACKOFF_SECONDS` (default 300 s).
- `health.json` is refreshed on a `PROGENITOR_CHECKPOINT_INTERVAL_SECONDS` timer even when idle.
- The scheduler task (when enabled) polls every `PROGENITOR_SCHEDULER_POLL_SECONDS` (default 30 s) for due schedule entries and injects them into the input queue as `[Scheduled Task]\n…` messages.
- Backoff resets to `PROGENITOR_IDLE_BACKOFF_SECONDS` (default 30 s) the moment new input arrives.

An idle runtime is still alive: the IPC server accepts new connections, the event broadcast loop forwards events to connected clients, and `health.json` stays fresh.

---

## Shutdown and Recovery

### Graceful Shutdown

Triggered by SIGINT / SIGTERM (handled by `setup_signal_handlers`):

1. `_shutdown_started` flag set; new work no longer accepted.
2. IPC server stopped (`IPCServer.stop()`).
3. Loop runner signalled to stop (`loop_runner.stop()`).
4. Background tasks cancelled and awaited (suppressing `CancelledError`).
5. `StateManager.commit_state()` — atomic write of all in-memory state to disk.
6. `health.json` written with `"status": "offline"`.
7. Logs flushed.

### Recovery

On restart the loop calls `state_manager.get_current_state()` and applies it over the fresh default state.  Conversation history, backlog, and schedule are all restored from disk.  The loop does **not** replay transient events (stream chunks, IPC messages); it restores only the durable state that matters to the next cycle.

Recovery focuses on:
- Conversation continuity (`current_state.json` → `messages`)
- Backlog (`build_state.json`)
- Schedule (`schedule.json`)
- Agent registry (`agents.json`)

---

## STATUS Protocol (stdout)

The runtime emits lifecycle status lines to stdout so operators can see transitions in logs and orchestrators can parse them:

```
STATUS:starting:<message>
STATUS:running:<message>
STATUS:executing:<message>
STATUS:waiting:<message>
STATUS:failed:<message>
STATUS:stopping:<message>
STATUS:stopped:<message>
```

Each line is flushed immediately (`flush=True`) so orchestration and log capture reflect real-time state.

The `HTTPAgentManager` parses these lines from managed sub-processes using the regex `^STATUS:(\w+):(.*)$` and updates the in-memory `AgentStatus` for each agent.

Status values map to `AgentStatus` enum: `starting`, `running`, `executing`, `waiting`, `failed`, `stopping`, `stopped`.

---

## Initialization and Shutdown Mechanics

### Initialization (idempotent)

- Repeated calls to `initialize()` are no-ops after the first successful run (`_initialized` guard).
- Required directories (`state_dir`, `config_dir`, `log_dir`, `worktree_dir`) are created with `mkdir(parents=True, exist_ok=True)` before any state I/O.
- The service does not report ready until `GET /health` returns HTTP 200 with `status: ok`.

### Shutdown sequence (explicit and predictable)

1. Stop accepting new work — IPC server closes.
2. Signal loop runner — `ProgenitorLoop.running = False`.
3. Cancel and await all `asyncio.Task` objects in `_background_tasks`.
4. Close HTTP clients (LLMClient, A2AClient).
5. `StateManager.commit_state()` — flush in-memory state atomically.
6. Write `health.json` with `status: offline`.
7. Log and telemetry flush.

---

## Operational Guidance

- **Prefer clear readiness and health semantics.** The simple `/health` endpoint returns exactly the four fields callers need for probes. The detailed endpoint answers operator questions.
- **Prefer continuity over aggressive background complexity.** Scheduled work and user work share the same input queue and the same graph cycle; there is one lifecycle model.
- **Prefer restart-safe behaviour over optimisation that risks state ambiguity.** Every cycle checkpoints state.  Shutdown writes a terminal health record.
- **Queue depth is observable.** `health.json → queues` and `GET /health/detailed` both expose queue sizes so operators can diagnose backup before it becomes a problem.
- **Delayed messages are acknowledged.** When a message waits > 30 s in the input queue, its metadata records `queued_wait_seconds` so the LLM can transparently tell the user it was queued behind earlier work.

The runtime lifecycle remains understandable enough that operators and developers can explain what the system is doing at any moment.

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `PROGENITOR_STATE_DIR` | `/data/state` | State and health file directory |
| `PROGENITOR_LOG_DIR` | `/logs` | Log directory |
| `PROGENITOR_WORKTREE_DIR` | `/worktrees` | Git worktree directory |
| `PROGENITOR_IDLE_BACKOFF_SECONDS` | `30` | Initial idle sleep |
| `PROGENITOR_MAX_BACKOFF_SECONDS` | `300` | Idle backoff ceiling |
| `PROGENITOR_BACKOFF_MULTIPLIER` | `1.5` | Backoff growth factor |
| `PROGENITOR_CHECKPOINT_INTERVAL_SECONDS` | `30` | Health file refresh interval |
| `PROGENITOR_HEALTH_STALE_SECONDS` | `120` | Age threshold for stale health |
| `PROGENITOR_IPC_HOST` | `127.0.0.1` | IPC server bind host |
| `PROGENITOR_IPC_PORT` | `9500` | IPC server bind port |
| `PROGENITOR_IPC_MAX_CLIENTS` | `10` | Max concurrent IPC clients |
| `PROGENITOR_SCHEDULER_ENABLED` | `true` | Enable scheduled tasks |
| `PROGENITOR_SCHEDULER_POLL_SECONDS` | `30` | Scheduler poll interval |
| `PROGENITOR_MAX_INPUT_QUEUE_SIZE` | `100` | Input queue capacity |
| `PROGENITOR_TIMEZONE` | `Australia/Melbourne` | Scheduler timezone |

Container images, entrypoint bootstrap, health probes, and service-wide logging conventions are defined under deployment and observability requirements for this runtime.
