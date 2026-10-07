# Progenitor code path map

This map covers `app/progenitor` and intentionally omits most detail under
`app/progenitor/tools/`, except where tool registration or invocation affects
the main application path.

## High-level finding

`app/progenitor` is now centered on a DeepAgent graph. The old node-oriented
outer-loop code has been removed; production startup creates a DeepAgent via
`runtime/deep_agent_adapter.py` and runs it through the long-running HTTP +
SSE service path in `app.progenitor.main`.

The strongly used code is concentrated in:

- `main.py`
- `runtime/heartbeat.py`
- `runtime/deep_agent_adapter.py`
- `agent/*.py`
- `sse/*.py`
- `state/manager.py`
- `state/models.py`
- `config.py`
- `llm/client.py`
- `stream_events.py`
- `streaming_utils.py`

The lesser-used or conditional code is mostly scheduler, A2A tracking, request
thread isolation, optional reasoning, optional MCP, optional skills/memory
discovery.

Cleanup note: the previously identified legacy node/routing layer, build-state
helpers, app-level mock LLM, old agent process managers, and related tests were
removed after this map was created. Removed paths are shown with strikethroughs.

## Entrypoints

### Long-running Progenitor process

Source: `app/progenitor/main.py`.

```text
main.py::main()
  -> get_config()
  -> setup_logging()
  -> Progenitor(config)
  -> setup_signal_handlers()
  -> Progenitor.run()
      -> Progenitor.initialize()
          -> StateManager.initialize()
          -> ToolRegistry.load()
          -> refresh_mcp_runtime()
          -> discover_agents()
          -> tool_registry.refresh_agent_cards()
          -> tool_registry.refresh_agent_health()
          -> llm_client.set_event_pusher()
          -> create_deep_agent_for_loop()
          -> ProgenitorLoop(...)
      -> create_task(loop_runner.run_forever())  # background
      -> ProgenitorHTTPServer(loop_runner=...).start()
          -> mounts RuntimeChatService's /v1/chat, /v1/chat/{id}/cancel, /v1/events
```

Strength: strongly used when `python -m app.progenitor.main` runs.

Evidence:

- `main.py` constructs `LLMClient`, `StateManager`, `ToolRegistry`, and
  `ProgenitorLoop`.
- `main.py` calls `create_deep_agent_for_loop()` directly during
  `Progenitor.initialize()`.
- Backend subprocess mode and docs reference `python -m app.progenitor.main`.

## Primary DeepAgent construction path

```text
main.py
  -> runtime/deep_agent_adapter.py::create_deep_agent_for_loop()
      -> agent/deep_agent.py::create_progenitor_agent()
          -> agent/langchain_adapter.py::create_langchain_model()
          -> agent/backends.py::create_progenitor_backend()
          -> agent/tools_adapter.py::create_deepagent_tools()
          -> agent/async_subagents.py::create_progenitor_async_subagents()
          -> agent/middleware.py middleware classes
          -> deepagents.create_deep_agent()
```

### `runtime/deep_agent_adapter.py`

| Symbol | Status | Notes |
| --- | --- | --- |
| `create_deep_agent_for_loop()` | Strongly used | Called by `main.py`; this is the production graph bridge. |
| ~~`patch_loop_with_deep_agent()`~~ | Removed legacy/test-only | Production creates the graph before loop construction. |

### `agent/deep_agent.py`

`create_progenitor_agent()` is strongly used. It composes the model, injected
LangGraph checkpointer/store, virtual filesystem backend, tools, middleware,
skills, memory, subagents, and DeepAgent graph.

Important construction steps:

```text
create_progenitor_agent()  # async; requires state_manager
  -> create_langchain_model(config.llm)
  -> use injected Postgres checkpointer/store
  -> ensure_content_documents_seeded(state_manager, "prompt"/"skill", ...)  # no-op once seeded
  -> create_progenitor_backend(...)
       -> mount /config/* virtual documents over StateManager
       -> mount Postgres-backed /prompts/, /skills/ (read-only), /memories/ (read-write)
       -> mount read-only /schemas/, /self/, /repos/, /logs/ (disk)
  -> create_deepagent_tools(...)
  -> [DynamicMCPToolMiddleware, SecretRedactionMiddleware,
      AuditLoggingMiddleware]
  -> auto-discover skills from Postgres "skill" content documents
  -> _ensure_default_runtime_memory(state_manager)
  -> _resolve_runtime_memory_paths(state_manager, memory_paths)
  -> state_manager.get_content_document("prompt", "progenitor/genesis.md")
  -> create_progenitor_async_subagents()
  -> create_deep_agent(...)
```

Strength: strongly used.

Conditional branches:

- A local checkpointer fallback may be used only when the standalone factory is
  called without an injected checkpointer; the production runtime injects the
  Postgres checkpointer/store. `state_manager` itself has no fallback --
  `create_progenitor_agent()` raises if it's `None`.
- Skills are loaded when Postgres has `skill:` content documents under
  `progenitor/`.
- Runtime memory documents are discovered via
  `state_manager.list_content_documents("memory")`; a default `AGENTS.md`
  memory document is seeded if none exists.
- ~~Build subagent / `inner_loop_graph` wiring~~ was removed.

### `agent/langchain_adapter.py`

Strength: strongly used.

Path:

```text
create_langchain_model(config.llm)
  -> normalize configured base URL
  -> choose model via config.model_default or role-specific config
  -> optionally configure reasoning_effort and extra_body.reasoning.summary
  -> ChatOpenAI(..., use_responses_api=True, streaming=True)
```

Conditional branches:

- Reasoning parameters are only set when `config.llm.reasoning_enabled` is true.
- ~~Role-based model routing~~ was removed; the active model adapter uses
  `model_default`.

### `agent/backends.py`

Strength: strongly used.

DeepAgent backend map:

| Virtual route | Backing store | Mode | Purpose |
| --- | --- | --- | --- |
| `/memories/` | Postgres content documents (`memory:`) | read-write | Durable agent memory. |
| `/prompts/` | Postgres content documents (`prompt:`) | read-only | Prompt pack visibility. |
| `/skills/` | Postgres content documents (`skill:`) | read-only | Skill pack visibility. |
| `/self/` | project `app/` (disk) | read-only | Self-inspection of application code. |
| `/schemas/` | configured schemas dir (disk) | read-only | JSON Schema contracts for `structured_output`. |
| `/logs/` | configured log dir (disk) | read-only | Audit/log visibility. |
| default | DeepAgents `StateBackend` | read-write | Ephemeral workspace state. |

`ReadOnlyBackend` denies write/delete operations while delegating
reads/listing to the wrapped backend (disk or Postgres-backed).

### `agent/tools_adapter.py`

Strength: strongly used.

Path:

```text
create_deepagent_tools()
  -> tools.system_registry.get_all_system_tools()
  -> wrap each BaseSystemTool as a LangChain StructuredTool
  -> tool execution builds ToolContext(...)
  -> system_tool.execute(ctx)
  -> merge ToolResult.state_updates into loop state and StateManager
```

Important detail: the active registry was moved out of the deleted node package
to `tools/system_registry.py`.

### `agent/middleware.py`

Strength: strongly used.

Middleware stack created by `create_progenitor_agent()`:

| Middleware | Status | Purpose |
| --- | --- | --- |
| `DynamicMCPToolMiddleware` | Strongly used | Injects live LangChain MCP tools from `llm_client.mcp_langchain_tools` into each model call and binds MCP tool-call requests. |
| `SecretRedactionMiddleware` | Strongly used | Redacts secrets for logging paths while preserving original args for execution. |
| `AuditLoggingMiddleware` | Strongly used when `log_dir` is provided | Emits tool stream events and writes `tool_calls.jsonl`. |

### `agent/async_subagents.py`

Status: minimal -- regular (synchronous) subagent delegation is no longer
code-defined here. `create_progenitor_agent()` (`agent/agent.py`) now builds
its full subagent menu from persisted object definitions
(`agent/object_agent_manifest.py`) plus routine-graph subagents. Async descriptors
are supplied by `LiveAsyncSubagentMiddleware`, using the SDK's native background
tools. `LiveSubagentMiddleware` supplies object/routine task descriptors;
both see object registration refreshes after Postgres notifications. The only thing `agent/async_subagents.py`
still defines is `create_hello_world_async_subagent()`, the deterministic
`AsyncSubAgent` used for background-delegation smoke tests.

Object definitions are parsed/discovered separately from their runtime in
`agent/object_agents.py`. Its reusable construction entry point preserves
scoped tools, mounts and permissions; the awaited proxy reloads the manifest
from Postgres on each invocation. Native manifests are read-only bundled files,
loaded at startup without importing them into Postgres; restart after dev edits.
Routine Prompt, Classify, and Agent nodes can select `config.model_profile`
from `/config/profile.json`. The profile resolver reads the current document
at execution time, builds the Azure model adapter with its deployment and
optional reasoning settings, and reports unknown profiles as execution errors.
Agent-node overrides take precedence over the manifest model for that invocation,
including background calls; definitions and unrelated calls retain their defaults.
The inspector reads public profile names/deployments through `/api/ui/model-profiles`.
The runtime's `async_subagents/protocol.py` exposes these same object agents
through Agent Protocol: SDK `runs.wait` awaits completion, while `runs.create`
starts background execution and returns its identity. Neither needs an async
manifest flag. Thread/run results persist in Postgres runtime documents, and
object agents use separate checkpoint threads. Background chat delegation
reuses task tracking and completion notifications. Routine nodes default to
waiting; `execution: "background"` returns a tracked task reference instead of
awaiting the child result. Shutdown records cancellation, and startup marks abandoned
running tasks as errors rather than automatically replaying tool side effects.

| Subagent | Current use | Notes |
| --- | --- | --- |
| `hello-world-async` | Registered | Deterministic async smoke-test subagent, returns `HELLO_FROM_ASYNC_SUBAGENT`. |
| ~~Build subagent~~ | Removed | Legacy inner-loop graph wrapper was deleted. |
| ~~Code reviewer~~ / ~~Researcher~~ | Removed | Migrated to manifest-backed object agents (`data/agents/objects/`); no longer code-defined. |

## Runtime loop path

Source: `app/progenitor/runtime/heartbeat.py` (`ProgenitorLoop` moved here from
the now-deleted `runtime/loop.py`).

> **Staleness note:** this section (through "Graph cycle path" below) was not
> re-verified as part of the IPC->SSE rename. `runtime/http_chat.py`'s own
> docstring says it "replaces the TCP IPC server's direct-DeepAgent request
> path" and that the old IPC server's separate queue-drain path (which this
> section's `_input_handler_loop()`/`_run_graph_cycle()` describes) "was dead
> code" even before removal -- so the diagrams below may now describe a path
> that chat requests no longer take at all (see "HTTP + SSE chat/event path"
> instead for the live request path). Needs a dedicated verification pass.

```text
ProgenitorLoop.run_forever()
  -> initialize shared state
  -> state_manager.get_current_state()
  -> _update_health_file()
  -> _push_event("loop_started")
  -> create_task(_input_handler_loop())
  -> optionally create_task(_scheduler_loop())
  -> create A2ATaskTracker(...)
  -> create_task(A2ATaskTracker.run())
```

Strength: unverified -- see staleness note above.

### User input path

```text
Scheduled work (see "HTTP + SSE chat/event path" for live chat requests)
  -> ProgenitorLoop.enqueue_input(...)
  -> _input_handler_loop()
      -> _get_input()
      -> _extract_input_payload()
      -> set current_input/current_input_metadata/cycle_count
      -> _run_graph_cycle() or _run_isolated_graph_cycle()
      -> stream finalization / scheduled_response events
```

### Graph cycle path

```text
_run_graph_cycle()
  -> build {"messages": [HumanMessage(content=current_input)]}
  -> graph.astream(config={"configurable": {"thread_id": effective_thread_id}},
                   stream_mode=["messages", "updates", "custom"] if supported)
  -> messages mode:
       _handle_message_stream_event()
       -> extract_stream_reasoning_deltas()
       -> build_reasoning_delta_event()
       -> extract_stream_chunk_text()
       -> stream_token()
       -> _send_stream_event("message_delta")
  -> custom mode:
       _handle_custom_stream_event()
       -> normalize_custom_event()
       -> _send_stream_event(...)
  -> updates mode:
       merge node_output into loop state
       optionally emit node_update stream event
  -> extract final AI message response text
  -> extract reasoning summary and token count
  -> _checkpoint_state()
  -> _update_health_file()
```

Strength: unverified -- see staleness note above.

### Conditional runtime branches

| Branch | Status | Trigger |
| --- | --- | --- |
| Scheduler loop | Conditional | `config.scheduler.enabled`. |
| Scheduled input handling | Conditional | Scheduler enqueues messages prefixed with `[Scheduled Task]\n`. |
| Isolated graph cycle | Conditional | Request metadata has a `thread_id` different from current state thread. |
| Streaming response | Unverified, see staleness note above | Request has `stream=True` or service streaming API is used. |
| A2A polling | Always running in long-running loop, work is conditional | Existing tracked A2A tasks in state. |
| Health updates | Strongly used | Startup, idle checkpoint interval, graph cycle completion, shutdown. |

## HTTP + SSE chat/event path

Source: `app/progenitor/runtime/http_chat.py` (`RuntimeChatService`), mounted
onto `app/progenitor/runtime/http.py`'s `ProgenitorHTTPServer` (the same HTTP
server that also answers `/health`, the A2A agent card, and A2A callbacks).
This replaces a former TCP socket IPC server/client pair (`IPCServer`,
`IPCClient`) that has been fully removed from the codebase; only the
transport-agnostic stream-envelope helpers it used survive, now in
`app/progenitor/sse/`.

### Server

```text
main.py::Progenitor.run()
  -> ProgenitorHTTPServer(loop_runner=..., ...)
  -> ProgenitorHTTPServer.start()
      -> web.Application() with RuntimeChatService.routes():
           POST /v1/chat
           POST /v1/chat/{request_id}/cancel
           GET  /v1/events
      -> RuntimeChatService.start() -> create_task(_drain_loop())
```

Request processing:

```text
RuntimeChatService.handle_chat()  # POST /v1/chat
  -> parse JSON body, require "message"
  -> open a StreamResponse (Content-Type: text/event-stream) immediately
  -> serialize concurrent requests per thread_id (_inflight_by_thread)
  -> _stream_chat()
      -> _create_direct_stream_bridge() -> DeepAgentSSEStream
      -> background _produce() task drains bridge.stream() into a queue
      -> foreground loop writes SSE frames as items arrive, or an SSE
         keepalive comment every EVENTS_KEEPALIVE_SECONDS if the bridge
         goes quiet (long tool call/subagent with no interim output)
      -> stream_event / stream_chunk / error frames written via
         _write_frame(); a POST /v1/chat/{request_id}/cancel request or a
         dropped client connection sets the per-request cancel_event
```

Event broadcasting (out-of-band events: alerts, schedule, async-subagent
callbacks -- distinct from one request's own chat stream above):

```text
RuntimeQueues.push_event(...)  # e.g. via Progenitor's deferred_push_event
  -> loop event queue
  -> RuntimeChatService._drain_loop()
  -> fan out to every open GET /v1/events subscriber queue
  -> RuntimeChatService.handle_events() writes SSE frames to each subscriber
  -> app/backend/sse_client.py's SSEProgenitorClient consumes /v1/events
     and /v1/chat, forwarding to the backend's own broadcast callback
```

Strength: strongly used whenever the backend talks to the Progenitor runtime
process -- this is the only chat/event transport; there is no non-SSE
fallback.

## State and persistence path

### Strongly used

Source: `app/progenitor/state/manager.py` and
`app/progenitor/state/langgraph_store.py`.

```text
Progenitor.initialize()
  -> open LangGraph Postgres checkpointer/store
  -> StateManager.initialize()
  -> ensure default state documents in ("progenitor", "state")

Runtime:
  -> get_current_state()/update_current_state()
  -> get_schedule()/save_schedule()
  -> get_mcp_config()/save_mcp_config()
  -> get_registry()/save_registry()
  -> save_health()
  -> write_log() for conversation, schedule, checkpoint, and audit events

DeepAgent virtual filesystem:
  -> /config/*.json reads/writes StateManager documents
  -> /tasks/* reads/writes kanban documents
  -> /workflows/*.json reads/writes workflow documents
```

State documents are Postgres records, not local JSON files. Conversation
messages live in the LangGraph checkpoint; per-thread `current_state` documents
hold scalar bookkeeping and UI/runtime metadata. The `.json` names are the
stable virtual-file presentation used by the agent.

Append-only logs use the Postgres `("progenitor", "logs", <source>)`
namespace. The `relative_path` field is a display label for the log entry, not
a filesystem write target.


### Lesser-used or legacy state methods

These were removed because they were tied to old node/build-state concepts:

- ~~`get_backlog()`~~
- ~~`save_backlog()`~~
- ~~`add_work_item()`~~
- ~~`update_work_item()`~~
- ~~`get_spec_doc()`~~
- ~~`save_spec_doc()`~~
- ~~`clear_spec_doc()`~~
- ~~`log_iteration_start()`~~
- ~~`log_iteration_end()`~~
- ~~`get_iteration_history()`~~
- ~~`save_verification_report()`~~

### Models

Source: `app/progenitor/state/models.py`.

Strongly used:

- `ProgenitorState`
- `ConversationMessage`
- `A2ATaskRecord`
- schedule-related models
- secret redaction helpers

Removed legacy models:

- ~~`WorkItem`~~
- ~~`Decision`~~
- ~~`DecisionAction`~~
- ~~`WorkItemType`~~
- ~~`BuildPhase`~~
- ~~`IterationStopReason`~~
- ~~inner-loop/build fields inherited into `ProgenitorState`~~

`ProgenitorState` now contains the active conversation, scheduling, and loop
control substates only.

## LLM path

### Strongly used in startup and tools

Source: `app/progenitor/llm/client.py`.

`LLMClient` is constructed by `main.py`. In the current DeepAgent path, direct
end-user chat is performed by the LangChain model adapter, not by
`LLMClient.chat()`. `LLMClient` is still strongly used for:

- MCP runtime context and LangChain MCP tools.
- Event pushing from tool/MCP refresh paths.
- Tool implementations that need AI calls.
- Agent discovery/self-reflection style tools.
- A2A task notification support.

### Direct LLM methods

| Method | Current status |
| --- | --- |
| `chat()` | Active for tools and helper flows; accepts either a prompt string or message-history list. Not the main DeepAgent conversation model call. |
| `chat_structured()` | Active for tools/agent discovery; single-prompt wrapper that passes a response format to `chat()`. |
| ~~`chat_with_reasoning()`~~ | Removed redundant direct helper. |
| ~~`generate()` / `generate_with_reasoning()` / `complete()`~~ | Removed redundant prompt-only convenience APIs. Callers now use `chat()`. |
| ~~`decide()` / `generate_response()`~~ | Removed legacy outer-loop APIs. |

### Supporting LLM modules

| Module | Status | Notes |
| --- | --- | --- |
| `llm/prompts.py` | Strongly used | DeepAgent genesis prompt and older prompt templates. |
| ~~`llm/tokenizer.py`~~ | Removed legacy | Token counting and context-window warning logic were removed; history sizing is controlled upstream. |
| ~~`llm/response_models.py`~~ | Removed legacy | Supported structured legacy decision/model responses and validation. |
| ~~`llm/mock_client.py`~~ | Removed test/demo | Tests should use test-local mocks instead. |
| ~~`llm/circuit_breaker.py`~~ | Removed unused | No active wrapper path was present in `app/progenitor`. |

## Streaming/events path

Sources:

- `app/progenitor/streaming_utils.py`
- `app/progenitor/stream_events.py`

Strongly used by `runtime/loop.py`.

```text
AIMessageChunk
  -> extract_stream_reasoning_deltas()
  -> build_reasoning_delta_event()
  -> extract_stream_chunk_text()
  -> message_delta stream_event

Final AI message
  -> extract_reasoning_summary_text()
  -> extract_reasoning_token_count()
  -> build_reasoning_summary_event()

Tool/middleware custom events
  -> build_tool_start_event()
  -> build_tool_result_event()
  -> build_tool_error_event()
  -> emit_stream_event()
  -> normalize_custom_event()
```

Strength: strongly used for streaming UI behavior and reasoning display.

## Scheduler path

Sources:

- `app/progenitor/schedule/engine.py`
- `app/progenitor/schedule/dispatch.py`
- `app/progenitor/runtime/heartbeat.py`
- `app/progenitor/graph/heartbeat_graph.py`

```text
HeartbeatRuntime
  -> heartbeat graph maintenance/schedule tick
      -> state_manager.get_schedule()
      -> Scheduler.get_due_items()
      -> queues.enqueue_input("[Scheduled Task]\n" + message)
      -> state_manager.upsert_schedule_run(...)
      -> state_manager.update_schedule_entry(...)
      -> state_manager.log_schedule_event(...)
```

Schedule changes are persisted in the Postgres `schedule` document. One-shot
entries are disabled/paused after enqueue; recurring entries retain their
schedule and receive updated run metadata.

Status: conditional. The code is live when scheduler config is enabled and
schedule entries exist.

## A2A task tracking path

Sources:

- `app/progenitor/runtime/a2a_tasks.py`
- `app/progenitor/runtime/loop.py`

```text
ProgenitorLoop.run_forever()
  -> A2ATaskTracker(state_manager, llm_client, completion_callback=...)
  -> create_task(A2ATaskTracker.run())
      -> poll_once()
          -> state_manager.get_a2a_tasks()
          -> refresh_tracked_a2a_task()
              -> resolve agent config
              -> A2AClient.get_task(...)
              -> state_manager.update_a2a_task()
          -> completion_callback(record)
              -> ProgenitorLoop._record_completed_a2a_task()
              -> append completion message into state
              -> push agent_message/work_completed events
```

Status: always started in the long-running loop, but work is conditional on
tracked A2A tasks.

## Node and routing code

### Active replacement: `tools/system_registry.py`

The active registry was moved out of the deleted node package.

```text
agent/tools_adapter.py
  -> tools.system_registry.get_all_system_tools()

llm/client.py
  -> tools.system_registry.execute_system_tool()

tools/registry.py and tools/list_tools.py
  -> tools.system_registry.get_all_system_tools()
```

Status: strongly used as the system-tool discovery/execution registry.

### Old graph nodes

Files:

- ~~`nodes/chat.py`~~
- ~~`nodes/respond.py`~~
- ~~`nodes/intake.py`~~
- ~~`nodes/pick_next.py`~~
- ~~`nodes/register.py`~~
- ~~`nodes/idle.py`~~
- ~~`nodes/schedule_dispatch.py`~~
- ~~`nodes/__init__.py`~~
- ~~`nodes/node_helpers/registry.py`~~ re-homed to `tools/system_registry.py`
- ~~`nodes/node_helpers/formatters.py`~~
- ~~`nodes/node_helpers/__init__.py`~~

Status: removed legacy/test-only code.

Evidence:

- Production DeepAgent construction does not import or assemble these nodes.
- ~~`nodes/chat.py`~~ implemented an older `Intake -> Chat -> Respond` style
  LLM tool-calling path, but current startup did not build that graph.

### ~~`routing.py`~~

Status: removed legacy/test-only code.

Evidence:

- Current `app/progenitor` keeps graph builders under `graph/`.
- `create_deep_agent_for_loop()` returns a DeepAgent graph directly.
- No production graph builder imports `route_after_decide()` or routing utility
  functions in this checkout.

## Runtime agent process managers

### ~~`runtime/agent_runner.py`~~

Status: removed legacy code.

Evidence:

- Used by ~~`nodes/register.py`~~, which itself is not on the production DeepAgent
  path.
- The `runtime/__init__.py` export was removed.

### ~~`runtime/http_agent_manager.py`~~

Status: removed redundant code.

Evidence:

- No production call site found under `app/progenitor`.
- It is not wired into `main.py`, `runtime/loop.py`, or
  DeepAgent construction.
- `runtime/port_utils.py` has been removed from Progenitor ownership. External
  agents are expected to register explicit URLs or agent-card URLs instead of
  receiving dynamically allocated ports from Progenitor.

## Activity classification

### Strongly used

| Area | Files |
| --- | --- |
| Long-running startup | `main.py` |
| DeepAgent bridge/factory | `runtime/deep_agent_adapter.py`, `agent/deep_agent.py` |
| Model adapter | `agent/langchain_adapter.py` |
| DeepAgent backend | `agent/backends.py` |
| DeepAgent tools | `agent/tools_adapter.py`, `tools/system_registry.py` |
| DeepAgent middleware | `agent/middleware.py` |
| Runtime loop | `runtime/heartbeat.py` |
| HTTP + SSE chat/event path | `runtime/http_chat.py`, `runtime/http.py`, `sse/*.py` |
| State persistence | `state/manager.py`, `state/persistence.py`, active parts of `state/models.py` |
| Config/logging | `config.py`, `utils.py` |
| Streaming/reasoning events | `streaming_utils.py`, `stream_events.py` |
| MCP support bridge | `mcp_tool_utils.py`, `LLMClient` MCP fields/methods |

### Conditional or lesser-used

| Area | Files | Trigger |
| --- | --- | --- |
| Scheduler | `runtime/scheduler.py` | Scheduler enabled and due entries exist. |
| A2A task polling | `runtime/a2a_tasks.py` | Tracked tasks exist. |
| Object-agent/routine-graph subagents | `agent/object_agents.py`, `routines/subagent.py` | Postgres agent/routine documents refresh through LISTEN/NOTIFY; native agents are read-only bundled files loaded at startup. |
| Reasoning config | `agent/langchain_adapter.py`, `streaming_utils.py`, `stream_events.py` | `config.llm.reasoning_enabled`. |
| Skills | `agent/agent.py` | Postgres `skill:` content documents under `progenitor/` exist. |
| Runtime memories | `agent/agent.py` | Postgres `memory:` content documents; default `AGENTS.md` is seeded. |
| Direct `LLMClient` helper APIs | `llm/client.py` | Tools call `chat()` / `chat_structured()` where needed. |
| ~~Mock LLM~~ | ~~`llm/mock_client.py`~~ | Removed; tests should use test-local mocks. |
| ~~Old build/backlog state helpers~~ | ~~`state/manager.py`, `state/models.py`~~ | Removed with old node/build-state concepts. |

### Likely redundant or candidates for deprecation

| Area | Files | Reason |
| --- | --- | --- |
| ~~Old graph nodes~~ | ~~`nodes/chat.py`, `nodes/respond.py`, `nodes/intake.py`, `nodes/pick_next.py`, `nodes/register.py`, `nodes/idle.py`, `nodes/schedule_dispatch.py`~~ | Removed. |
| ~~Old routing~~ | ~~`routing.py`~~ | Removed. |
| ~~Loop patch helper~~ | ~~`runtime/deep_agent_adapter.py::patch_loop_with_deep_agent()`~~ | Removed. |
| ~~Process-based agent runner~~ | ~~`runtime/agent_runner.py`~~ | Removed. |
| ~~HTTP agent manager~~ | ~~`runtime/http_agent_manager.py`~~ | Removed. |
| ~~Circuit breaker~~ | ~~`llm/circuit_breaker.py`~~ | Removed. |
| ~~Direct in-process service path~~ | ~~`service.py`, `app/backend/service_client.py`, `tests/progenitor/test_service.py`~~ | Removed; HTTP + SSE is the supported backend integration path. |

## Cleanup applied

The old node/routing layer is no longer retained as a fallback architecture.
The active system-tool registry was moved to `tools/system_registry.py`; old
node tests, builder/backlog state tests, app-level mock LLM tests, response
model tests, and register/chat/intake tests were removed or rewritten around the
active DeepAgent path.
