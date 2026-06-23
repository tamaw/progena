# Progenitor live evaluations

This document captures the repeatable live-evaluation model for Progenitor. The scenarios are derived from the tests under `tests/progenitor/` and are intended to validate the real running system, not just static correctness.

**Rule for this evaluation pass:** drive the system through the Progenitor IPC chat surface like a live user. Scenario outcomes should be judged from IPC requests, IPC responses, streaming events, and chat-visible behavior rather than direct filesystem inspection or ad hoc HTTP probes.

## Functional model inferred from tests

Progenitor is expected to behave as a graph-driven conversational orchestrator with these user-visible surfaces that can be exercised from chat:

| Capability | Expected behavior from tests | Key test sources |
| --- | --- | --- |
| IPC protocol | Uses a 4-byte length-prefixed JSON protocol; sends `ready` on connect; supports `heartbeat`/`heartbeat_ack`, non-streaming `assistant_message`, streaming chunks/events, errors, and shutdown. | `tests/progenitor/test_ipc_protocol.py`, `tests/progenitor/test_ipc_integration.py` |
| Native DeepAgent streaming | Converts native LangGraph/DeepAgent `messages`, `updates`, and `custom` stream items into IPC `stream_chunk` and `stream_event` messages; emits a final marker and avoids duplicate final text. | `tests/progenitor/test_deepagent_ipc_stream.py` |
| Chat + tool use | The conversational graph can use live tools such as `list_tools`, `self_reflect`, `schedule_task`, `list_schedule`, `new_conversation`, `get_a2a_task`, and dynamically refreshed MCP tools. | `tests/progenitor/test_system_tools.py`, `tests/progenitor/test_self_reflect.py`, `tests/progenitor/test_new_conversation_tool.py`, `tests/progenitor/test_middleware.py` |
| Conversation continuity | The current thread remembers prior user turns until explicitly reset, and `new_conversation` starts a fresh thread/context. | `tests/progenitor/test_new_conversation_tool.py`, `tests/progenitor/test_state_manager.py` |
| Memory backend | Runtime memory files are mounted into the DeepAgent environment, and the writable `/memories/` backend can be used from the live agent tool layer. | `tests/progenitor/test_memory.py`, `app/progenitor/agent/backends.py`, `app/progenitor/agent/agent.py` |
| Skill-backed behavior | The live agent should exhibit the capabilities enabled by its loaded skill pack, but the tests mostly specify skill-pack presence/wiring rather than a strict chat-visible output contract. In IPC-only evaluation, skills are covered indirectly through scheduling, self-reflection, and related tool-driven behaviors. | `tests/progenitor/test_skills.py`, `app/progenitor/agent/agent.py` |
| Runtime queues/events | Input/output/stream/event queues are bounded, expose stats, track wait and pending counts, reject or drop overflow according to queue type, and signal cycle completion. The user-visible part of this evaluation is that chat/event delivery remains responsive and consistent. | `tests/progenitor/test_bounded_queues.py`, `tests/progenitor/test_runtime_components.py` |

## Live evaluation scenarios

The repeatable script is `scripts/evaluate_progenitor_live.py`.

| Scenario | Live check | Expected outcome | Failure likely area |
| --- | --- | --- | --- |
| IPC ready/heartbeat | TCP connect to IPC, read `ready`, send `heartbeat` | `heartbeat_ack` response | `ipc/server.py`, `ipc/protocol.py` |
| Tool catalog | Ask the agent via chat to use `list_tools` and report tool names | Response lists key tools like `schedule_task`, `self_reflect`, `new_conversation`, `get_a2a_task`, `call_agent` | `tools/list_tools.py`, DeepAgent tool wiring |
| Microsoft Learn MCP | Ask the agent via chat to use `mcp_refresh`, confirm Microsoft Learn MCP tools via `list_tools`, then use `microsoft_learn_microsoft_docs_search` on a documentation query | Chat exposes `microsoft_learn_microsoft_docs_search`, `microsoft_learn_microsoft_code_sample_search`, `microsoft_learn_microsoft_docs_fetch`, and a streamed tool call returns a `learn.microsoft.com` result | `tools/mcp_refresh.py`, `mcp/context.py`, `agent/adapters.py`, shared `mcp` state document |
| Subagent delegation | Send a streaming chat request that explicitly tells the agent to use the `hello-world` subagent | The stream emits `subagent_start` and `subagent_result`, and the final answer is exactly `HELLO_FROM_SUBAGENT` | `agent/subagents.py`, `agent/agent.py`, `ipc/stream_events.py`, `ipc/deepagent_stream.py` |
| Async subagent callback | Send a streaming chat request that explicitly tells the agent to use `start_async_task` with `hello-world-async`, then keep the IPC session open | The stream emits `async_subagent_start`, the kickoff answer returns `TASK_ID=<id>`, and Heartbeat later emits an `agent_message` callback with `HELLO_FROM_ASYNC_SUBAGENT` | `agent/subagents.py`, `agent/middleware.py`, `async_subagents/`, `runtime/http.py`, `nodes/async_subagents.py`, `graph/heartbeat_graph.py` |
| Conversation history | Tell the agent `For this conversation only, the code word is marzipan`, then ask `What is the conversation-only code word?` | Same-thread chat answers `marzipan` | conversation graph/checkpointer/state persistence |
| New conversation reset | Send `new conversation`, then ask again for the conversation-only code word | Post-reset chat returns `UNKNOWN`/equivalent because the prior turn was conversation-scoped, not durable memory. The tool is not required to emit a human-readable reset confirmation string first. | `tools/new_conversation.py`, state reset handling |
| IPC non-streaming LLM turn | Send `user_message` asking the model to echo a unique token | `assistant_message` contains the token | `ipc/server.py`, `nodes/cycle.py`, `agent/adapters.py`, live Azure/model config |
| IPC streaming LLM turn | Send streaming `user_message` asking the model to echo a unique token | `stream_chunk` sequence contains token and final signal | `ipc/server.py`, `ipc/deepagent_stream.py`, live Azure/model config |
| Reasoning streaming | Send a small corpus of reasoning-heavy streaming prompts, retrying because `summary=auto` is not guaranteed per request | At least one prompt emits `reasoning_delta` before the final answer | `ipc/deepagent_stream.py`, live model/provider reasoning behavior |
| Schedule creation | Ask the agent via chat to use `schedule_task` for a near-future one-off job, then confirm with `list_schedule` | Chat confirms the schedule exists | `tools/schedule_task.py`, `tools/list_schedule.py` |
| Schedule trigger | Keep the IPC session open after schedule creation and watch for schedule-related runtime events | `scheduled_response` and `work_completed` arrive for the created task | `schedule/dispatch.py`, heartbeat scheduler path |
| Memory write/read | Ask the agent via chat to write to `/memories/` and read it back | Chat returns the exact memory token | `agent/backends.py`, backend file tools |

## Runtime setup

The live evaluator assumes Progenitor is already running and reachable over IPC.

Default endpoint:

- IPC: `127.0.0.1:9500`

Minimum environment for starting Progenitor from Python:

```bash
source .venv/bin/activate
export AGENT_NAME=progenitor
export AZURE_OPENAI_BASE_URL=...
export AZURE_OPENAI_API_KEY=...
export PROGENITOR_MODEL_DEFAULT=gpt-5.3-chat
export PROGENITOR_REASONING_ENABLED=true
export PROGENITOR_REASONING_EFFORT=medium
export PROGENITOR_REASONING_SUMMARY=auto
PYTHONPATH="$PWD" python -m app.progenitor.main
```

If secrets are available only inside a running container, extract only the required env keys to a local protected temp file and do not print values.

## Running evaluations

Full IPC-driven live evaluation:

```bash
source .venv/bin/activate
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py
```

Machine-readable output:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py --json
```

Transport-only smoke checks:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py --skip-llm
```

Skip the schedule scenario:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py --skip-schedule
```

Skip the `/memories/` round-trip:

```bash
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py \
  --ipc-host 127.0.0.1 \
  --ipc-port 9500 \
  --skip-memory-write
```

## Interpreting results

The script exits `0` only if all selected scenarios pass. Each failure includes the observed behavior and a likely code/runtime area to investigate.

Separate environment failures from application failures:

- Connection refused on IPC usually means Progenitor is not running or is bound to a different port.
- LLM IPC failures with HTTP/Azure/auth errors usually indicate `AZURE_OPENAI_*` or model deployment configuration, not IPC protocol failure.
- Token mismatch in chat scenarios can be model behavior drift; inspect the actual response before treating it as an orchestration failure.
- Missing reasoning events on a single prompt are not sufficient evidence of a bug when `PROGENITOR_REASONING_SUMMARY=auto`; the evaluator now uses multiple reasoning-heavy prompts before treating it as a mismatch.
- Schedule-trigger failures should be separated into creation failure, listing failure, and event-delivery failure; those point at different code areas.
- Microsoft Learn MCP failures should be separated into config/discovery (shared `mcp` state document, `mcp_refresh`, `list_tools`) versus live tool execution (`mcp/context.py`, middleware injection, upstream Learn MCP availability).
- DeepAgent in this environment also exposes remote `AsyncSubAgent` support via LangGraph SDK, but this live evaluator currently covers the inline `task`/subagent path only.
- `self_reflect` remains unit-tested and visible in the live tool catalog, but the default IPC suite does not force a conversational self-introspection turn because the live assistant may decline internal source-inspection requests at the chat layer.

## Latest live run

Command:

```bash
source .venv/bin/activate
PYTHONPATH="$PWD" python scripts/evaluate_progenitor_live.py --json
```

Observed result: `12/12` scenarios passed against the live Progenitor runtime on IPC `127.0.0.1:9500`.

| Scenario | Result | Observation |
| --- | --- | --- |
| IPC ready/heartbeat | Pass | `ready` and `heartbeat_ack` were observed on the live IPC socket. |
| Tool catalog | Pass | Chat-exposed tools included `schedule_task`, `self_reflect`, `new_conversation`, `get_a2a_task`, and `call_agent`. |
| Microsoft Learn MCP | Pass | `mcp_refresh` exposed the Learn MCP tools and a streamed docs search returned a `learn.microsoft.com` URL. |
| Subagent delegation | Pass | The `hello-world` subagent emitted `subagent_start` and `subagent_result`, and the final streamed answer was exactly `HELLO_FROM_SUBAGENT`. |
| Async subagent callback | Pass | `hello-world-async` emitted `async_subagent_start`, returned `TASK_ID=<id>`, and Heartbeat later delivered an IPC callback with `HELLO_FROM_ASYNC_SUBAGENT`. |
| Conversation history | Pass | Same-thread recall returned the seeded `marzipan` token. |
| New conversation reset | Pass | After `new_conversation`, the follow-up prompt no longer exposed the prior conversation-only token. |
| Reasoning streaming | Pass | A reasoning-heavy prompt emitted `reasoning_delta` events before the final answer. |
| Non-streaming LLM turn | Pass | Non-streaming IPC returned the expected marker token. |
| Streaming LLM turn | Pass | Stream chunks contained the expected marker and terminated cleanly. |
| Schedule create/trigger | Pass | Chat-created schedule persisted and later emitted `scheduled_task_triggered`, `scheduled_response`, and `work_completed`. |
| Memory write/read | Pass | The agent wrote and immediately read back durable `/memories/` content. |
