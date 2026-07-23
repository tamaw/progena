# Progenitor Architecture Decisions

## Current State (2026-07-21)

### Goal
Make Progenitor capable of serving **multiple concurrent users** without a shared in-memory conversation state dict. The heartbeat loop should be a system dispatcher, not a single-user conversation owner.

### What We Learned

The runtime is currently architecturally single-user despite the backend supporting session-scoped `thread_id`s:

- **One `ProgenitorLoop`** with one `self._state` dict mixing system timestamps and conversation fields
- **One global input queue** — queue items carry `thread_id` in metadata, but the loop ignores it for regular chat
- **One `graph_lock`** serializing ALL graph invocations across ALL threads
- **Monolithic heartbeat graph** embedding both system maintenance and conversation nodes
- **Direct chat bridge** writes into the same shared `self._state` dict even though checkpoints are per-thread

### Decisions

#### 1. Remove prompt-cache warming feature (completed)
Azure's unconditional 24h default caching made the feature's maintenance burden unjustified. Deleted `PromptCacheManager`, startup warmup node, heartbeat wiring, retention config, and backend read surface.

#### 2. Relocate middleware to `middleware/` package (completed)
`SchemaCompactionMiddleware` and `DynamicMCPToolMiddleware` moved from `adapters.py` to dedicated files under `agent/middleware/` to match the existing pattern.

#### 3. Align `model_default` with environment (completed)
Changed `LLMConfig.model_default` from `"gpt-5.1-codex-max"` to `"gpt-chat-latest"` matching `PROGENITOR_MODEL_DEFAULT`.

#### 4. Fix TTFT graph_lock contention (completed)
Moved `queues.get_input()` wait **outside** `graph_lock` in `_input_handler_loop`. The lock now only covers actual graph work, not idle polling. TTFT dropped from 20–30s escalating to stable 4–5s.

#### 5. Multi-user architecture — conversation state (identified, not started)
Conversation state (`messages`, `tool_calls`, `attachments`, `pending_response`, `cycle_count`) must be loaded from the **LangGraph checkpoint DB per `thread_id`** on demand, not kept in a shared in-memory dict.

The heartbeat loop's `chat_node` should use `requested_thread_id` (already extracted in `cycle_setup_node`) to load the right checkpoint, run the graph, and save back. The `isolated_cycle_node` machinery is the closest existing pattern.

#### 6. Multi-user architecture — system state (identified, not started)
System state (`last_mcp_refresh_at`, `idle_cycles`, `health_status`) should be a **separate lightweight in-memory dict** (or optionally a `system.json` Postgres document for restart resilience). It must not share a dict with conversation state.

#### 7. Multi-user architecture — input queue (identified, not started)
Queue items already carry `thread_id` in metadata. The heartbeat loop should dequeue items and dispatch per-thread graph runs rather than treating all input as belonging to `self._state["thread_id"]`.

#### 8. Multi-user architecture — event routing (identified, not started)
Runtime `push_event` calls must include the target `thread_id` in metadata so the backend routes events to the correct browser session / SSE subscriber.

#### 9. Multi-user architecture — graph lock (identified, not started)
The global `graph_lock` can become per-thread once conversation state is loaded per-thread. Or it can be removed entirely if LangGraph's checkpointer handles isolation. The current lock exists to protect the shared `self._state` dict, not the graph itself.

### Architecture Pattern to Apply

Use the existing **two-surface state document pattern** from `docs/STATE_DOCUMENTS.md`:
- Runtime owns writes, backend is read-only for system diagnostics
- System state as a lightweight `system.json` document (runtime-owned)
- Conversation state stays in the LangGraph checkpoint DB (not a document — the checkpointer already handles this)

The heartbeat loop should become a **dispatcher**: poll for work, load per-thread checkpoint, invoke chat node with that `thread_id`, save checkpoint, emit events tagged with `thread_id`.

### PR
Implementation changes merged in [#309](https://github.com/tamaw/progenitor/pull/309).
