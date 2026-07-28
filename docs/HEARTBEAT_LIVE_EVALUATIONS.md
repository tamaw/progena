# Heartbeat live evaluations

This document captures a repeatable live-evaluation model for the Progenitor
Heartbeat loop. Runtime state is stored in the shared LangGraph Postgres store;
the `.json` names used by the agent are virtual-file views, not files under
`data/state` or `data/config`.

The current runtime serves background events over HTTP + SSE
(`GET /v1/events` on `:9501`) from `runtime/http_chat.py`. References to the
former TCP IPC transport below are historical observations from earlier runs.

## Runtime model inferred from tests

Heartbeat is expected to behave as a graph-driven perpetual runtime with two major modes:

| Capability | Expected behavior from tests | Key test sources |
| --- | --- | --- |
| Graph-owned orchestration | `create_runtime_heartbeat_graph()` owns startup audit/MCP/registry work, dequeue, idle maintenance, cycle execution, scheduled completion, checkpoint, health, signal, and cleanup. | `tests/progenitor/test_graphs.py` |
| Queue/input handling | `RuntimeQueues` stores bounded `QueuedInput` values with enqueue time, tracks input wait and pending counts, exposes queue stats, and signals cycle completion. | `tests/progenitor/test_bounded_queues.py`, `tests/progenitor/test_runtime_components.py` |
| Idle maintenance | When no input is queued, the graph records idle state and runs due maintenance: schedule tick, MCP refresh, A2A poll/apply/notify, registry discovery/refresh, and idle health. | `tests/progenitor/test_graphs.py`, `app/progenitor/graph/heartbeat_graph.py` |
| Scheduled work | Due one-off schedules are enqueued as Heartbeat input and removed after enqueue; due recurring schedules are retained and have `last_run` updated after enqueue. Both are processed as scheduled input and surfaced through `scheduled_task_triggered`, `scheduled_response`, and `work_completed` events. | `tests/progenitor/test_bounded_queues.py`, `app/progenitor/schedule/dispatch.py` |
| A2A completions | Terminal A2A callbacks enter the runtime completion queue, are applied to conversation state as tool messages, and are marked notified or emitted through SSE user-communication events. Tracked async `call_agent` tasks are also expected to be refreshed to terminal state by Heartbeat polling and then applied/notified. | `tests/progenitor/test_graphs.py`, `tests/progenitor/test_loop_a2a.py`, `tests/progenitor/test_http_server.py`, `app/progenitor/nodes/a2a.py` |
| Checkpoint and health | Input cycles checkpoint recoverable state and write the `health` Postgres document (via `StateManager.save_health`); idle health also updates it with queue stats and loop state. Liveness is exposed by the runtime HTTP `/health` endpoint, not stored. | `tests/progenitor/test_graphs.py`, `tests/progenitor/test_runtime_components.py` |
| Error recovery | Guarded heartbeat nodes emit `error` events, set health error fields, and let the loop continue; cycle errors route through the error node before checkpoint/health/signal/cleanup. | `tests/progenitor/test_graphs.py`, `app/progenitor/graph/heartbeat_graph.py`, `app/progenitor/nodes/control.py` |

## Live evaluation scenarios

The repeatable script is `scripts/evaluate_heartbeat_live.py`.

| Scenario | Live setup/check | Expected outcome | Failure likely area |
| --- | --- | --- | --- |
| Idle health updates | Poll the runtime's own HTTP `/health` endpoint while no input is forced (health has no on-disk file anymore) | `timestamp`/`cycle_count` keep advancing, endpoint stays reachable | `nodes/health.py`, `graph/heartbeat_graph.py`, `runtime/heartbeat.py` |
| Queued input cycle flow | Use the `enqueue_background_task` tool through the HTTP chat surface, keep an SSE event client connected, and wait for the background callback | Heartbeat drains the persisted background queue from Postgres, runs a real `dequeue -> setup -> cycle` turn, advances `cycle_count`, and emits an `agent_message` callback with the queued result | `tools/enqueue_background_task.py`, `state/manager.py`, `nodes/intake.py`, `nodes/cycle.py`, `runtime/http_chat.py` |
| Due schedule flow | Add a due entry through the `/config/schedule.json` virtual file or the backend schedule operation while an SSE client listens for events | Heartbeat emits `scheduled_task_triggered`, `scheduled_response`, and `work_completed`; the one-shot entry is disabled/paused in the Postgres schedule document; checkpoint/cycle state advances | `nodes/schedule.py`, `schedule/dispatch.py`, `nodes/cycle.py`, `nodes/checkpoint.py` |
| Recurring schedule flow | Add a due recurring entry through the `/config/schedule.json` virtual file or the backend schedule operation | Heartbeat emits `scheduled_task_triggered`, `scheduled_response`, and `work_completed`; the recurring entry remains in the Postgres schedule document and receives updated run metadata | `nodes/schedule.py`, `schedule/dispatch.py`, `schedule/engine.py` |
| A2A completion flow | POST a terminal task callback to `/a2a/tasks/callback` while an SSE client listens | Callback is accepted, the task record is terminal in the Postgres state store, and Heartbeat applies/notifies completion through the event or conversation path | `runtime/http.py`, `nodes/a2a.py`, `a2a/tasks.py`, `runtime/queues.py` |
| Async tracked A2A task flow | Start a temporary local A2A test agent, register it through the registry tool or `/config/agents.json` virtual file, submit `call_agent` through HTTP chat, then let Heartbeat poll it | Tracked task record moves from `submitted` to `completed`, Heartbeat emits/records completion, and notification side effects are visible | `nodes/a2a.py`, `a2a/tasks.py`, `tools/call_agent.py`, `runtime/http.py` |

## Runtime setup

The evaluator assumes Progenitor is already running.

Default surfaces:

- HTTP: `http://127.0.0.1:9501`
- Postgres: `PROGENITOR_DATABASE_URL` and the `("progenitor", "state")` namespace
- Agent state surface: `/config/*.json` virtual files backed by Postgres

Minimum runtime startup:

```bash
source .venv/bin/activate
export AGENT_NAME=progenitor
export AZURE_OPENAI_BASE_URL=...
export AZURE_OPENAI_API_KEY=...
PYTHONPATH="$PWD" python -m app.progenitor.main
```

## Running evaluations

The live evaluator script predates the Postgres state-document cutover. Its
schedule, registry, task, and checkpoint setup still reads and writes local
JSON paths, so it is **not a valid end-to-end evaluator for the current
runtime**. Do not use `--state-dir` or `--config-dir` to create production
state; those flags describe the legacy harness only.

For the current runtime, run the focused Heartbeat tests and exercise live
operations through HTTP/SSE plus the virtual-file surface:

```bash
source .venv/bin/activate
pytest -q tests/progenitor/test_graphs.py tests/progenitor/test_bounded_queues.py
```

When the evaluator is migrated, schedule and registry setup must write through
`/config/schedule.json` and `/config/agents.json` (or their backend API operations)
and assertions must read the shared Postgres documents/events rather than local
`current_state.json` or `a2a_tasks.json` files.

## Interpreting results

The focused tests exercise the current store-backed Heartbeat behavior. For a
live run, separate transport failures from Heartbeat failures:

- Connection failures usually mean Progenitor is not running or its HTTP
  server is bound to a different port.
- HTTP callback failures point to runtime HTTP/A2A ingress before Heartbeat can
  apply completion.
- State-document failures should be checked through the Postgres store,
  `/health`, the SSE event stream, or the corresponding `/config/*.json`
  virtual file; do not inspect or edit `data/config/*.json`.
- Idle health failures may require checking whether another long-running cycle
  was active; logs are supporting evidence only.

The historical `evaluate_heartbeat_live.py` run below is retained as evidence
of the former TCP/SSE behavior, but its local JSON setup is not a current
verification of the Postgres runtime. It must be ported before being used as a
release check.

## Historical live run

The last recorded run reported `6/6` scenarios passed against the old mixed
runtime (`IPC 127.0.0.1:9500` and HTTP `127.0.0.1:9501`). Its observations are
historical and should not be interpreted as proof of the current
store-backed/virtual-file implementation.
