# Progenitor live evaluations

This document captures the repeatable live-evaluation model for Progenitor. The scenarios are derived from the tests under `tests/progenitor/` and are intended to validate the real running system, not just static correctness.

## Functional model inferred from tests

Progenitor is expected to behave as a graph-driven conversational orchestrator with these user-visible and operational surfaces:

| Capability | Expected behavior from tests | Key test sources |
| --- | --- | --- |
| Persistent HTTP runtime | Serves `/health`, `/.well-known/agent-card.json`, and `/a2a/tasks/callback`; health includes thread/cycle/queue state; agent card advertises Progenitor and callback URLs. | `tests/progenitor/test_http_server.py`, `tests/progenitor/test_runtime_components.py` |
| IPC protocol | Uses a 4-byte length-prefixed JSON protocol; sends `ready` on connect; supports `heartbeat`/`heartbeat_ack`, non-streaming `assistant_message`, streaming chunks/events, errors, and shutdown. | `tests/progenitor/test_ipc_protocol.py`, `tests/progenitor/test_ipc_integration.py` |
| Native DeepAgent streaming | Converts native LangGraph/DeepAgent `messages`, `updates`, and `custom` stream items into IPC `stream_chunk` and `stream_event` messages; emits a final marker and avoids duplicate final text. | `tests/progenitor/test_deepagent_ipc_stream.py` |
| Heartbeat graph orchestration | Startup and idle paths run audit, MCP refresh, registry discovery/refresh, schedule tick, A2A poll/apply/notify, health, checkpoint, cycle signal, and cleanup as graph nodes. | `tests/progenitor/test_graphs.py`, `tests/progenitor/test_bounded_queues.py` |
| A2A task lifecycle | Dispatch persists submitted A2A tasks; callbacks/upserts persist task status and summary; terminal tasks are queued for graph-owned completion handling; refresh errors surface without hiding the saved task. | `tests/progenitor/test_a2a_tasks.py`, `tests/progenitor/test_loop_a2a.py`, `tests/progenitor/test_http_server.py` |
| Scheduling | Schedule tool requires exactly one scheduling mode, stores cron or one-off ISO tasks, lists `next_run`, rejects malformed timestamps, and heartbeat enqueues due tasks. | `tests/progenitor/test_schedule_tool.py`, `tests/progenitor/test_bounded_queues.py` |
| Runtime queues | Input/output/stream/event queues are bounded, expose stats, track wait and pending counts, reject or drop overflow according to queue type, and signal cycle completion. | `tests/progenitor/test_bounded_queues.py`, `tests/progenitor/test_runtime_components.py` |
| Runtime configuration and state | Environment drives Azure/model/runtime settings; state/config files live under data state/config layout; health/audit files are written by runtime nodes. | `tests/progenitor/test_config.py`, `tests/progenitor/test_state_manager.py`, `tests/progenitor/test_runtime_components.py` |

## Live evaluation scenarios

The repeatable script is `scripts/evaluate_progenitor_live.py`.

| Scenario | Live check | Expected outcome | Failure likely area |
| --- | --- | --- | --- |
| HTTP health | `GET /health` | HTTP 200, `status=ok`, `service=progenitor`, queue stats present | `app/progenitor/runtime/http.py`, `runtime/queues.py` |
| Agent card | `GET /.well-known/agent-card.json` | HTTP 200, `name=progenitor`, callback endpoint ends with `/a2a/tasks/callback` | `app/progenitor/runtime/http.py` |
| Invalid A2A callback | `POST /a2a/tasks/callback` with malformed JSON | HTTP 400 and `success=false` | `app/progenitor/runtime/http.py` |
| Terminal A2A callback | `POST /a2a/tasks/callback?agent=live_eval_agent` with a generated completed task | HTTP 200, `success=true`, task persisted in `data/state/a2a_tasks.json` | `runtime/http.py`, `a2a/tasks.py`, `state/a2a_store.py` |
| IPC ready/heartbeat | TCP connect to IPC, read `ready`, send `heartbeat` | `heartbeat_ack` response | `ipc/server.py`, `ipc/protocol.py` |
| IPC non-streaming LLM turn | Send `user_message` asking the model to echo a unique token | `assistant_message` contains the token | `ipc/server.py`, `nodes/cycle.py`, `agent/adapters.py`, live Azure/model config |
| IPC streaming LLM turn | Send streaming `user_message` asking the model to echo a unique token | `stream_chunk` sequence contains token and final signal | `ipc/server.py`, `ipc/deepagent_stream.py`, live Azure/model config |

## Runtime setup

The live evaluator assumes Progenitor is already running.

Default endpoints:

- HTTP: `http://127.0.0.1:9501`
- IPC: `127.0.0.1:9500`
- State dir: `data/state`

Minimum environment for starting Progenitor from Python:

```bash
source .venv/bin/activate
export AGENT_NAME=progenitor
export AZURE_OPENAI_BASE_URL=...
export AZURE_OPENAI_API_KEY=...
export PROGENITOR_MODEL_DEFAULT=gpt-5.3-chat
export PROGENITOR_REASONING_ENABLED=true
export PROGENITOR_REASONING_EFFORT=medium
PYTHONPATH="$PWD" python -m app.progenitor.main
```

If secrets are available only inside a running container, extract only the required env keys to a local protected temp file and do not print values.

## Running evaluations

Full live evaluation, including LLM-backed IPC turns and a mutating A2A callback:

```bash
source .venv/bin/activate
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py
```

Machine-readable output:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py --json
```

Non-LLM smoke checks only:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py --skip-llm
```

Avoid mutating A2A state:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py --skip-mutating-callback
```

Target a non-default runtime:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py \
  --http-base http://127.0.0.1:9501 \
  --ipc-host 127.0.0.1 \
  --ipc-port 9500 \
  --state-dir data/state
```

## Interpreting results

The script exits `0` only if all selected scenarios pass. Each failure includes the observed behavior and a likely code/runtime area to investigate.

Separate environment failures from application failures:

- Connection refused on HTTP/IPC usually means Progenitor is not running or bound to different ports.
- LLM IPC failures with HTTP/Azure/auth errors usually indicate `AZURE_OPENAI_*` or model deployment configuration, not IPC protocol failure.
- Token mismatch in LLM scenarios can be model behavior drift; inspect the actual response before treating it as an orchestration failure.
- A2A callback persistence failures usually indicate state path mismatch, state manager errors, or callback payload parsing/upsert regressions.
