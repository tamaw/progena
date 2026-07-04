# Heartbeat live evaluations

This document captures a repeatable live-evaluation model for the Progenitor Heartbeat loop. The scenarios are derived from `tests/progenitor/` and validate the real running loop through IPC events, persisted state/config files, and health/checkpoint artifacts.

## Runtime model inferred from tests

Heartbeat is expected to behave as a graph-driven perpetual runtime with two major modes:

| Capability | Expected behavior from tests | Key test sources |
| --- | --- | --- |
| Graph-owned orchestration | `create_runtime_heartbeat_graph()` owns startup audit/MCP/registry work, dequeue, idle maintenance, cycle execution, scheduled completion, checkpoint, health, signal, and cleanup. | `tests/progenitor/test_graphs.py` |
| Queue/input handling | `RuntimeQueues` stores bounded `QueuedInput` values with enqueue time, tracks input wait and pending counts, exposes queue stats, and signals cycle completion. | `tests/progenitor/test_bounded_queues.py`, `tests/progenitor/test_runtime_components.py` |
| Idle maintenance | When no input is queued, the graph records idle state and runs due maintenance: schedule tick, MCP refresh, A2A poll/apply/notify, registry discovery/refresh, and idle health. | `tests/progenitor/test_graphs.py`, `app/progenitor/graph/heartbeat_graph.py` |
| Scheduled work | Due one-off schedules are enqueued as Heartbeat input and removed after enqueue; due recurring schedules are retained and have `last_run` updated after enqueue. Both are processed as scheduled input and surfaced through `scheduled_task_triggered`, `scheduled_response`, and `work_completed` events. | `tests/progenitor/test_bounded_queues.py`, `app/progenitor/schedule/dispatch.py` |
| A2A completions | Terminal A2A callbacks enter the runtime completion queue, are applied to conversation state as tool messages, and are marked notified or sent through IPC user communication events. Tracked async `call_agent` tasks are also expected to be refreshed to terminal state by Heartbeat polling and then applied/notified. | `tests/progenitor/test_graphs.py`, `tests/progenitor/test_loop_a2a.py`, `tests/progenitor/test_http_server.py`, `app/progenitor/nodes/a2a.py` |
| Checkpoint and health | Input cycles checkpoint recoverable state and write the `health` Postgres document (via `StateManager.save_health`); idle health also updates it with queue stats and loop state. Liveness itself is a separate, live IPC heartbeat check on the backend side, not stored. | `tests/progenitor/test_graphs.py`, `tests/progenitor/test_runtime_components.py` |
| Error recovery | Guarded heartbeat nodes emit `error` events, set health error fields, and let the loop continue; cycle errors route through the error node before checkpoint/health/signal/cleanup. | `tests/progenitor/test_graphs.py`, `app/progenitor/graph/heartbeat_graph.py`, `app/progenitor/nodes/control.py` |

## Live evaluation scenarios

The repeatable script is `scripts/evaluate_heartbeat_live.py`.

| Scenario | Live setup/check | Expected outcome | Failure likely area |
| --- | --- | --- | --- |
| Idle health updates | Poll the runtime's own HTTP `/health` endpoint while no input is forced (health has no on-disk file anymore) | `timestamp`/`cycle_count` keep advancing, endpoint stays reachable | `nodes/health.py`, `graph/heartbeat_graph.py`, `runtime/heartbeat.py` |
| Queued input cycle flow | Use the `enqueue_background_task` tool through IPC chat, keep an IPC client connected, and wait for the background callback | Heartbeat drains the persisted background queue, runs a real `dequeue -> setup -> cycle` turn, advances `cycle_count`, and emits an `agent_message` callback with the queued result | `tools/enqueue_background_task.py`, `state/manager.py`, `nodes/intake.py`, `nodes/cycle.py`, `ipc/server.py` |
| Due schedule flow | Append a due one-off entry directly to `data/config/schedule.json` while an IPC client listens for events | Heartbeat emits `scheduled_task_triggered`, `scheduled_response`, and `work_completed`; the one-off entry is removed; checkpoint/cycle state advances | `nodes/schedule.py`, `schedule/dispatch.py`, `nodes/cycle.py`, `nodes/checkpoint.py` |
| Recurring schedule flow | Append a due recurring entry directly to `data/config/schedule.json` while an IPC client listens for events | Heartbeat emits `scheduled_task_triggered`, `scheduled_response`, and `work_completed`; the recurring entry remains in `schedule.json` and gets `last_run` updated | `nodes/schedule.py`, `schedule/dispatch.py`, `schedule/engine.py` |
| A2A completion flow | POST a terminal task callback to `/a2a/tasks/callback` while an IPC client listens | Callback is accepted, task record is terminal, and Heartbeat applies/notifies completion via IPC event or persisted notification/message state | `runtime/http.py`, `nodes/a2a.py`, `a2a/tasks.py`, `runtime/queues.py` |
| Async tracked A2A task flow | Start a temporary local A2A test agent, register it in `data/config/agents.json`, submit `call_agent` through IPC chat, then let Heartbeat poll it | Tracked task record moves from `submitted` to `completed`, Heartbeat emits/records completion, and notification side effects are visible | `nodes/a2a.py`, `a2a/tasks.py`, `tools/call_agent.py`, `runtime/http.py` |

## Runtime setup

The evaluator assumes Progenitor is already running.

Default surfaces:

- IPC: `127.0.0.1:9500`
- HTTP: `http://127.0.0.1:9501`
- State dir: `data/state`
- Config dir: `data/config`

Minimum runtime startup:

```bash
source .venv/bin/activate
export AGENT_NAME=progenitor
export AZURE_OPENAI_BASE_URL=...
export AZURE_OPENAI_API_KEY=...
PYTHONPATH="$PWD" python -m app.progenitor.main
```

## Running evaluations

Full Heartbeat live evaluation:

```bash
source .venv/bin/activate
PYTHONPATH="$PWD" python scripts/evaluate_heartbeat_live.py
```

Machine-readable output:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_heartbeat_live.py --json
```

Target a non-default runtime:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_heartbeat_live.py \
  --http-base http://127.0.0.1:9501 \
  --ipc-host 127.0.0.1 \
  --ipc-port 9500 \
  --state-dir data/state \
  --config-dir data/config
```

## Interpreting results

The script exits `0` only if all selected scenarios pass. Each failure includes observed behavior and a likely code/runtime area.

Separate setup failures from Heartbeat failures:

- IPC connection failures usually mean Progenitor is not running or the IPC port differs.
- HTTP callback failures point to runtime HTTP/A2A ingress before Heartbeat has a chance to apply completion.
- Schedule creation failures should be split into file setup, scheduler pickup, cycle processing, and event delivery.
- Idle health failures may require checking whether another long-running cycle was active; logs are supporting evidence only.

The script uses the `enqueue_background_task` tool to persist one background prompt, edits `data/config/schedule.json`, temporarily registers a disposable A2A eval agent in `data/config/agents.json`, and posts synthetic A2A callbacks. It cleans up its own schedule entries, registry additions, and temporary tracked tasks. The temporary A2A helper lives in `scripts/test_a2a_eval_agent.py`.

## Latest live run

Command:

```bash
source .venv/bin/activate
PYTHONPATH="$PWD" python scripts/evaluate_heartbeat_live.py --json
```

Observed result: `6/6` scenarios passed against the live Progenitor runtime on IPC `127.0.0.1:9500` and HTTP `http://127.0.0.1:9501`.

| Scenario | Result | Observation |
| --- | --- | --- |
| Idle health updates | Pass | `health.json` advanced during idle heartbeat and retained `running` status with queue stats. |
| Queued input cycle flow | Pass | `enqueue_background_task` persisted one background prompt, Heartbeat drained it, `cycle_count` advanced, and IPC received an `agent_message` callback from `heartbeat_input_completion`. |
| Due schedule flow | Pass | Heartbeat emitted `scheduled_task_triggered`, `scheduled_response`, and `work_completed`; the injected one-off schedule was removed; `current_state.json` cycle count advanced. |
| Recurring schedule flow | Pass | Heartbeat emitted scheduled completion events, retained the recurring entry, and updated `last_run`. |
| A2A completion flow | Pass | HTTP callback was accepted and a matching IPC `agent_message` event was observed for the terminal task. |
| Async tracked A2A task flow | Pass | A temporary A2A agent accepted the task, Heartbeat polling completed it, and completion was surfaced through IPC/state side effects. |

Notes:

- Scheduled completion events can be observed before the checkpoint write is visible on disk. The evaluator waits briefly for `current_state.json` to advance after seeing completion events.
- Tracked A2A tasks are cached in-process by the live runtime, so cleanup for failed async-A2A experiments should go through the live callback endpoint instead of direct file edits to avoid poisoning later `a2a_poll` cycles.
