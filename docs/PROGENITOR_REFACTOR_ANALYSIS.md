# Progenitor refactor analysis

Scope: `app/progenitor` after pulling latest `origin/main`.

This is intentionally practical: remove obvious leftovers first, then consider
larger changes where the current contracts are doing too much work.

## Executive takeaways

1. **The biggest simplification is transport.** The custom IPC stack works, but it is a lot of bespoke framing, queues, and bridge code. If Progenitor remains a separate process, replacing custom IPC with a small HTTP/WebSocket or HTTP/SSE server would probably remove more code than any local refactor.
2. **State is split across too many persistence systems.** DeepAgent/LangGraph already has SQLite checkpointing, while `StateManager` still writes conversation and runtime state into JSON. That should become metadata/config only, with graph messages owned by the graph checkpoint.
3. **A2A is not spinning up temporary HTTP servers in `app/progenitor`.** It creates HTTP clients per call/poll. The waste is polling/scanning and client lifecycle churn, not server startup.
4. **`runtime/loop.py` is now the main complexity hotspot.** It owns input queues, graph streaming, IPC-facing stream queues, state checkpoints, scheduler polling, A2A polling, health writes, and message/reasoning extraction.
5. **Mock/test-only LLM code is already clean in the current tree.** `app/progenitor/llm/mock_client.py` is gone. Remaining `__pycache__` files are local artifacts, not tracked source.

## Remove or simplify now

### 1. Remove dead `_stream_callback`

**Files**

- `app/progenitor/runtime/loop.py:104-106`

`ProgenitorLoop.__init__()` still declares `_stream_callback`, with comments saying nodes can stream directly to the client. No code assigns or reads it. Streaming now comes from `graph.astream(...)` and is routed through `_handle_message_stream_event()`.

**Change**

Delete `_stream_callback` and the stale comments. This is a no-contract cleanup.

### 2. Consolidate duplicate reasoning extraction

**Files**

- `app/progenitor/runtime/loop.py:1096-1163`
- `app/progenitor/streaming_utils.py:110-147`

`runtime/loop.py::_extract_reasoning_trace()` duplicates most of `streaming_utils.extract_reasoning_summary_text()` and adds a fallback renderer. The loop already imports and uses `extract_reasoning_summary_text()` elsewhere.

**Change**

Replace `_extract_reasoning_trace()` with a call to `extract_reasoning_summary_text()` plus a small fallback helper if needed. Keep all provider-shape handling in `streaming_utils.py`.

### 3. Replace silent tool-adapter failures with explicit logging

**Files**

- `app/progenitor/agent/tools_adapter.py:29-94`

The adapter suppresses exceptions while loading tool registry state and while applying state updates:

- `_resolve_state()` suppresses `get_current_state()` errors.
- `_maybe_reload()` suppresses registry reload errors.
- state update propagation suppresses `update_current_state()` errors.

This makes debugging tool behavior harder and can make failures look like missing state.

**Change**

Replace `contextlib.suppress(Exception)` with `try/except Exception as exc: logger.warning(...)`. Do not invent a new contract; just make existing best-effort behavior visible.

### 4. Stop scanning every historical A2A task forever

**Files**

- `app/progenitor/runtime/a2a_tasks.py:441-472`
- `app/progenitor/state/manager.py:303-348`

`A2ATaskTracker.poll_once()` does **not** make HTTP calls for terminal tasks, because refresh only happens when `not record.is_terminal`. However, it still loads and scans the full `a2a_tasks.json` list forever, including old terminal tasks with notifications already sent.

**Change**

Add an active-task query or prune/archive path:

- Minimal: `StateManager.get_active_a2a_tasks()` returns non-terminal tasks plus terminal tasks needing notification.
- Better: move terminal notified tasks to an archive table/file after a retention window.

This keeps the current behavior but avoids unbounded list scans.

## Refactor next

### 5. Remove global loop lookup from tool execution

**Files**

- `app/progenitor/runtime/loop.py:65-70`
- `app/progenitor/runtime/loop.py:140-141`
- `app/progenitor/agent/tools_adapter.py:16,29-40`

`tools_adapter.py` reaches into the current loop via `get_current_loop()` and then reads `loop._state`. That is hidden global coupling: tools appear to receive state through `ToolContext`, but the adapter actually resolves it from a process-global loop.

**Change**

Pass a state provider explicitly when creating DeepAgent tools. Keep it small:

```python
create_deepagent_tools(
    tool_registry=tool_registry,
    llm_client=llm_client,
    state_manager=state_manager,
    get_live_state=lambda: self._state,
)
```

Then delete `_current_loop` and `get_current_loop()`. This keeps the existing mutable-state contract but makes ownership explicit.

### 6. Shrink `runtime/loop.py`

**Files**

- `app/progenitor/runtime/loop.py` (~1,399 lines)

The loop is doing too much. A full rewrite is unnecessary, but the following moves would make it much easier to reason about:

| Extract | Current methods |
| --- | --- |
| Streaming event router | `_handle_message_stream_event()`, `_handle_custom_stream_event()`, `_send_stream_event()`, `_send_stream_chunk()`, `_send_stream_end()`, `_ensure_response_streamed()` |
| Graph result extraction | `_get_last_ai_message()`, `_extract_message_text()`, `_extract_stream_chunk_text()`, `_summarize_message_schema()` |
| Health writer | `_update_health_file()` |
| Scheduler runner | `_scheduler_loop()`, `_check_and_enqueue_scheduled_work()` |
| A2A completion bridge | `_record_completed_a2a_task()` |

Do this incrementally. The win is not new abstractions; it is making the loop a coordinator instead of a utility grab-bag.

### 7. Split `LLMClient` by responsibility

**Files**

- `app/progenitor/llm/client.py` (~1,010 lines)

`LLMClient` still mixes:

- Responses API HTTP calls.
- message conversion.
- structured JSON schema format building.
- MCP runtime context and tool lookup.
- tool-call loop execution.
- system-tool execution fallback.

**Suggested shape**

| New module | Responsibility |
| --- | --- |
| `llm/client.py` | public `chat()` / `chat_structured()` and raw Responses API post |
| `llm/schemas.py` | response-format and JSON-schema helpers |
| `llm/messages.py` | input message conversion and output text extraction |
| `llm/tool_calls.py` | tool-call extraction and tool result message construction |
| `llm/mcp_runtime.py` | MCP context, client closure, MCP tool lookup/call |

Do not add new Pydantic contracts here unless needed. Most of this can be plain functions.

### 8. Decide whether `runtime/deep_agent_adapter.py` should exist

**Files**

- `app/progenitor/runtime/deep_agent_adapter.py`
- `app/progenitor/main.py`
- `app/progenitor/agent/deep_agent.py`

The adapter is now a thin pass-through to `create_progenitor_agent()`. It was useful during migration, but it no longer hides complexity.

**Two reasonable options**

1. Delete it and import `create_progenitor_agent()` directly from `main.py`.
2. Move graph assembly into `app/progenitor/graph/main.py` and make that the public graph factory.

If the direction is “main and subagents are graph concepts,” choose option 2.

## Bigger architecture bets

### 9. Replace custom IPC with HTTP/WebSocket or HTTP/SSE

**Files**

- `app/progenitor/ipc/client.py` (~555 lines)
- `app/progenitor/ipc/protocol.py` (~348 lines)
- `app/progenitor/ipc/server.py` (~572 lines)
- `app/backend/ipc_client.py` (~347 lines)

The custom IPC protocol is clean, but it is still a bespoke transport:

- custom message framing;
- heartbeat loop;
- event broadcasting;
- request locking;
- stream chunk/event/end envelopes;
- reconnection and process-management glue in the backend client.

If Progenitor stays as a separate process, a small HTTP server in the Progenitor process would likely be simpler:

- `POST /v1/messages` for non-streaming calls;
- `GET/POST /v1/messages/stream` using SSE, or `/ws` using WebSocket;
- same JSON event envelope already used by `stream_event`, `stream_chunk`, and `stream_end`;
- backend becomes an HTTP client/proxy instead of a custom IPC client.

**Would WebSocket require less code?**

Probably yes if you delete the custom protocol rather than wrap it. The contract can stay tiny:

```json
{"type":"user_message","request_id":"...","content":"...","metadata":{}}
{"type":"stream_event","request_id":"...","event_type":"reasoning_delta","content":"...","metadata":{}}
{"type":"stream_chunk","request_id":"...","content":"...","is_final":false}
{"type":"stream_end","request_id":"..."}
```

SSE may be even simpler than WebSocket because current traffic is mostly request-in, events-out. Use WebSocket only if bidirectional mid-run control matters.

**Caveat**

Keep the current IPC until the loop/streaming code is split. Replacing transport before simplifying `runtime/loop.py` risks moving complexity rather than deleting it.

### 10. Persist graph state in the graph, not in `current_state.json`

**Files**

- `app/progenitor/agent/deep_agent.py:57-80,176-179`
- `app/progenitor/runtime/loop.py:1024-1046`
- `app/progenitor/state/manager.py:265-297`

DeepAgent already uses a LangGraph SQLite checkpointer at `state/conversations.db`. The loop still writes messages into `current_state.json`.

**Change**

Make `current_state.json` metadata only:

- `thread_id`
- last checkpoint timestamp
- operational counters if still useful
- no `messages`
- no transient `current_input` / `pending_response` unless a UI explicitly reads them

Then use the LangGraph checkpointer as the source of truth for conversation state.

### 11. Move A2A tasks from JSON list to SQLite

**Files**

- `app/progenitor/state/manager.py:303-348`
- `app/progenitor/runtime/a2a_tasks.py`

A2A task state is a better fit for a table than a JSON list. It needs upserts, lookup by task id, lookup of active tasks, and terminal/archive behavior.

**Minimal table**

```sql
CREATE TABLE a2a_tasks (
  task_id TEXT PRIMARY KEY,
  agent_name TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  notify_on_completion INTEGER NOT NULL,
  notification_sent_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_a2a_tasks_active
  ON a2a_tasks(status, notification_sent_at);
```

This is worth doing if A2A remains a first-class feature. If A2A is rarely used, do the active-task filter first and defer the database work.

### 12. Health state should not be another full JSON state surface

**Files**

- `app/progenitor/main.py:265-283`
- `app/progenitor/runtime/loop.py:1358-1399`

`health.json` is useful for Docker/process monitoring, but it should stay operational. Avoid turning it into another state API.

**Change**

Keep `health.json`, but shrink it:

- status
- last_active
- pid
- maybe queue sizes

Do not include derived conversation/message counts. The health endpoint/UI can read richer state elsewhere.

## Graph folder recommendation

Yes, but only if it replaces existing placement rather than adding another layer.

Suggested structure:

```text
app/progenitor/graph/
  __init__.py
  main.py          # create_progenitor_agent()
  tools.py         # create_deepagent_tools()
  subagents.py     # create_progenitor_subagents()
  middleware.py    # optional move from agent/middleware.py
```

Then either delete `runtime/deep_agent_adapter.py` or make it a compatibility import for one release. Avoid both `agent/deep_agent.py` and `graph/main.py` owning graph creation.

## What not to remove right now

| Area | Reason |
| --- | --- |
| Scheduler | Conditional but live; schedule files are system config, not legacy state. |
| A2A support | The implementation can be optimized, but it is wired through `call_agent`, `get_a2a_task`, registry refresh, and loop polling. |
| Reasoning streaming | Actively used in loop/backend UX; consolidate helpers instead of deleting. |
| MCP bridge | Actively used by `LLMClient`, middleware, refresh tool, and dynamic tool injection. |
| Runtime memories/skills | Pulled `origin/main` added `data/memories/AGENTS.md`; this is now part of the active DeepAgent context path. |

## Priority order

1. Delete `_stream_callback`.
2. Replace silent suppressions in `tools_adapter.py` with logging.
3. Remove `_current_loop` by passing a live-state getter into tool adapters.
4. Trim `current_state.json` so graph messages live only in LangGraph SQLite.
5. Extract streaming/result helpers out of `runtime/loop.py`.
6. Split `LLMClient` into message/schema/tool/MCP modules.
7. Decide on HTTP/SSE or WebSocket as the replacement for custom IPC.
8. Move A2A tasks to SQLite if A2A remains important after the transport decision.
