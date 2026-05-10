# Progenitor code path map

This map covers `app/progenitor` and intentionally omits most detail under
`app/progenitor/tools/`, except where tool registration or invocation affects
the main application path.

## High-level finding

`app/progenitor` is now centered on a DeepAgent graph. The old node-oriented
outer-loop code has been removed; production startup creates a DeepAgent via
`runtime/deep_agent_adapter.py` and runs it through the long-running IPC
service path in `app.progenitor.main`.

The strongly used code is concentrated in:

- `main.py`
- `runtime/loop.py`
- `runtime/deep_agent_adapter.py`
- `agent/*.py`
- `ipc/*.py`
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
      -> ProgenitorLoop.run_forever()
      -> IPCServer.start()
      -> IPCServer.serve_forever()
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
          -> agent/subagents.py::create_progenitor_subagents()
          -> agent/middleware.py middleware classes
          -> deepagents.create_deep_agent()
```

### `runtime/deep_agent_adapter.py`

| Symbol | Status | Notes |
| --- | --- | --- |
| `create_deep_agent_for_loop()` | Strongly used | Called by `main.py`; this is the production graph bridge. |
| ~~`patch_loop_with_deep_agent()`~~ | Removed legacy/test-only | Production creates the graph before loop construction. |

### `agent/deep_agent.py`

`create_progenitor_agent()` is strongly used. It composes the model, state
checkpointing, filesystem backend, tools, middleware, skills, memory, subagents,
and DeepAgent graph.

Important construction steps:

```text
create_progenitor_agent()
  -> create_langchain_model(config.llm)
  -> _build_checkpointer(state_dir / "conversations.db")
  -> create_progenitor_backend(...)
  -> create_deepagent_tools(...)
  -> [DynamicMCPToolMiddleware, SecretRedactionMiddleware,
      AuditLoggingMiddleware, MetricsMiddleware]
  -> resolve_skills_dir("progenitor", prompts_dir=...)
  -> _ensure_default_runtime_memory(data_dir)
  -> _resolve_runtime_memory_paths(data_dir, memory_paths)
  -> PromptTemplates(...).get_genesis_prompt()
  -> create_progenitor_subagents()
  -> create_deep_agent(...)
```

Strength: strongly used.

Conditional branches:

- SQLite checkpointer is used when available; fallback is in-memory saver.
- Skills are loaded when `data/skills/progenitor/` exists.
- Runtime memory files are discovered under `data/memories/*.md`; a default
  runtime memory file is created if needed.
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

| Virtual route | Backing path | Mode | Purpose |
| --- | --- | --- | --- |
| `/memories/` | `data/memories/` | read-write | Durable agent memory. |
| `/self/` | project `app/` | read-only | Self-inspection of application code. |
| `/data/` | configured data dir | read-only | Runtime data/config visibility. |
| `/logs/` | configured log dir | read-only | Audit/log visibility. |
| `/prompts/` | configured prompts dir | read-only | Prompt pack visibility. |
| default | DeepAgents `StateBackend` | read-write | Ephemeral workspace state. |

`ReadOnlyFilesystemBackend` denies write/delete operations while delegating
reads/listing to the wrapped filesystem backend.

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
| `MetricsMiddleware` | Strongly used | Tracks in-memory model/tool metrics. |

### `agent/subagents.py`

Status: active for code-review and research helper delegation.

| Subagent | Current use | Notes |
| --- | --- | --- |
| ~~Build subagent~~ | Removed | Legacy inner-loop graph wrapper was deleted. |
| Code reviewer | Registered | Always created as a DeepAgent subagent. Actual use depends on model delegation. |
| Researcher | Registered | Always created as a DeepAgent subagent. Actual use depends on model delegation. |

## Runtime loop path

Source: `app/progenitor/runtime/loop.py`.

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

Strength: strongly used in the long-running IPC process.

### User input path

```text
IPCServer or scheduled work
  -> ProgenitorLoop.enqueue_input(...)
  -> _input_handler_loop()
      -> _get_input()
      -> _extract_input_payload()
      -> set current_input/current_input_metadata/cycle_count
      -> _run_graph_cycle() or _run_isolated_graph_cycle()
      -> stream finalization / scheduled_response events
      -> _signal_cycle_complete()
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

Strength: strongly used for every IPC user message processed by the long-running
loop.

### Conditional runtime branches

| Branch | Status | Trigger |
| --- | --- | --- |
| Scheduler loop | Conditional | `config.scheduler.enabled`. |
| Scheduled input handling | Conditional | Scheduler enqueues messages prefixed with `[Scheduled Task]\n`. |
| Isolated graph cycle | Conditional | Request metadata has a `thread_id` different from current state thread. |
| Streaming response | Strongly used for streaming clients, conditional per request | IPC message has `stream=True` or service streaming API is used. |
| A2A polling | Always running in long-running loop, work is conditional | Existing tracked A2A tasks in state. |
| Health updates | Strongly used | Startup, idle checkpoint interval, graph cycle completion, shutdown. |

## IPC path

Source: `app/progenitor/ipc`.

### Server

```text
main.py::Progenitor.run()
  -> IPCServer(loop_runner=...)
  -> IPCServer.start()
      -> asyncio.start_server(_handle_client, ...)
      -> create_task(_event_broadcast_loop())
  -> IPCServer.serve_forever()
```

Request processing:

```text
IPCServer._handle_client()
  -> read_message()
  -> _process_message()
      -> HEARTBEAT: heartbeat_ack()
      -> SHUTDOWN: set shutdown event
      -> USER_MESSAGE:
          -> _handle_request() if not msg.stream
          -> _handle_streaming_request() if msg.stream
```

Non-streaming request:

```text
_handle_request()
  -> _notify_queued_request()
  -> _request_lock
  -> _drain_response_queues()
  -> _enqueue_loop_input()
  -> _wait_for_response()
      -> loop_runner.get_stream_item()
      -> ignore stream_event
      -> accumulate stream_chunk
  -> write assistant_message()
  -> loop_runner.wait_cycle_complete()
```

Streaming request:

```text
_handle_streaming_request()
  -> _notify_queued_request()
  -> _request_lock
  -> _drain_response_queues()
  -> _enqueue_loop_input()
  -> loop_runner.get_stream_item()
      -> STREAM_EVENT: write stream_event()
      -> STREAM_CHUNK: write stream_chunk()
      -> STREAM_END: write stream_end()
  -> loop_runner.wait_cycle_complete()
```

Event broadcasting:

```text
ProgenitorLoop._push_event(...)
  -> loop event queue
  -> IPCServer._event_broadcast_loop()
  -> write agent_event messages to connected clients
  -> app/backend/ipc_client.py forwards to SSE callback
```

Strength: strongly used when backend talks to a separately running Progenitor
process.

### Client

Source: `app/progenitor/ipc/client.py`.

```text
IPCClient.connect()
  -> open socket
  -> read READY message
  -> start _heartbeat_loop()

IPCClient.send_message()
  -> user_message(stream=False)
  -> read until ASSISTANT_MESSAGE
  -> handle interleaved AGENT_EVENT messages

IPCClient.send_message_stream_events()
  -> user_message(stream=True)
  -> yield STREAM_CHUNK and STREAM_EVENT messages
  -> handle interleaved AGENT_EVENT messages
```

Strength: strongly used by `app/backend/ipc_client.py`.

## State and persistence path

### Strongly used

Source: `app/progenitor/state/manager.py`.

```text
StateManager.initialize()
  -> create state/log directories
  -> _ensure_initial_state()
  -> _cleanup_stale_temp_files()

Runtime:
  -> get_current_state()
  -> save_current_state()
  -> update_current_state()
  -> commit_state()
  -> append_jsonl/log_* helpers
  -> get_schedule()/save_schedule()
  -> get_a2a_tasks()/save_a2a_tasks()/upsert/update
```

State files managed:

- `current_state.json`
- `schedule.json`
- `registry.json`
- `a2a_tasks.json`

Append-only logs include conversation and schedule events.

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
- `app/progenitor/runtime/loop.py`

```text
ProgenitorLoop.run_forever()
  -> _is_scheduler_enabled()
  -> create_task(_scheduler_loop())
      -> Scheduler()
      -> _check_and_enqueue_scheduled_work()
          -> state_manager.get_schedule()
          -> Scheduler.get_due_items()
          -> enqueue_input("[Scheduled Task]\n" + message)
          -> Scheduler.mark_executed()
          -> state_manager.save_schedule()
```

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
| Runtime loop | `runtime/loop.py` |
| IPC | `ipc/server.py`, `ipc/client.py`, `ipc/protocol.py` |
| State persistence | `state/manager.py`, `state/persistence.py`, active parts of `state/models.py` |
| Config/logging | `config.py`, `utils.py` |
| Streaming/reasoning events | `streaming_utils.py`, `stream_events.py` |
| MCP support bridge | `mcp_tool_utils.py`, `LLMClient` MCP fields/methods |

### Conditional or lesser-used

| Area | Files | Trigger |
| --- | --- | --- |
| Scheduler | `runtime/scheduler.py` | Scheduler enabled and due entries exist. |
| A2A task polling | `runtime/a2a_tasks.py` | Tracked tasks exist. |
| Code-review/research subagents | `agent/subagents.py` | Registered, actual use depends on model delegation. |
| Reasoning config | `agent/langchain_adapter.py`, `streaming_utils.py`, `stream_events.py` | `config.llm.reasoning_enabled`. |
| Skills | `agent/deep_agent.py` | `data/skills/progenitor` exists. |
| Runtime memories | `agent/deep_agent.py` | `data/memories/*.md`; default is ensured. |
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
| ~~Direct in-process service path~~ | ~~`service.py`, `app/backend/service_client.py`, `tests/progenitor/test_service.py`~~ | Removed; IPC is the supported backend integration path. |

## Cleanup applied

The old node/routing layer is no longer retained as a fallback architecture.
The active system-tool registry was moved to `tools/system_registry.py`; old
node tests, builder/backlog state tests, app-level mock LLM tests, response
model tests, and register/chat/intake tests were removed or rewritten around the
active DeepAgent path.
