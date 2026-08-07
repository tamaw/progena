# Routine Graph API feature audit

Legend: **[x] implemented**, **[~] partial / SDK-only**, **[ ] not implemented**.

## Graph structure and state

- [x] **`StateGraph` compilation** — `compile_routine_graph()` builds a LangGraph `StateGraph`.
- [x] **Typed shared state** — `messages` uses LangGraph’s `add_messages` reducer; `data` uses a merge reducer.
- [x] **Input schema** — `input_schema` creates real top-level input channels and seeds them into internal `data`.
- [x] **Output schema** — `output_schema` projects declared final fields and excludes internal `data`.
- [~] **Private/internal state schema** — internal `RoutineState` plus compiler-only seed/project nodes exist, but no separately authorable `private_schema` field exists.
- [x] **Per-node structured-output schemas** — prompt/eval nodes accept `response_schema`, including reusable top-level `$defs` references.

## Nodes

- [x] **Async executable nodes** — prompt, code, eval, agent, subagent, no-op, human, foreach, and subgraph nodes.
- [x] **`START` / `END` lifecycle** — compiler creates `START → __seed__ → entry`; terminal nodes route to `END`.
- [x] **Node result caching** — node `config.cache` creates `CachePolicy`; graph `settings.cache` supplies `InMemoryCache`.
- [x] **Per-node retries and recovery** — node `config.retry` uses LangGraph `RetryPolicy`; `on_exhausted.route_to` records the terminal error in `data.errors` and transitions to the named recovery node.
- [x] **Graph-wide retry default** — `settings.retry` is applied to every node without a node-specific retry policy.
- [ ] **LangGraph task API / durable side-effect task isolation** — no `task()` usage in the routine compiler.

## Edges, routing, loops, and joins

- [x] **Normal edges** — `{ from, to, level: "normal" }`, optionally with an expression-backed `loop_guard`.
- [x] **Conditional edges** — `level: "route"` / `state_equals` router edges with branch and default targets; compiler path maps preserve every branch in LangGraph topology introspection.
- [x] **Conditional entry behavior** — supported indirectly: the configured `entry` follows the compiler’s seed node, but there is no separate conditional-entry schema primitive.
- [x] **Cycles / loopbacks** — normal and fan-out edges may target an earlier node; `loop_guard` can terminate or route elsewhere when false.
- [x] **Recursion limits** — `settings.recursion_limit` becomes the compiled graph’s LangGraph recursion limit.
- [x] **Barrier fan-in** — `{ from: ["a", "b"], to: "join", level: "fan_in" }` waits for every named source.
- [x] **Static fan-out** — `{ from: "source", to: ["a", "b"], level: "fan_out" }` schedules every target.

## Dynamic flow

- [x] **Dynamic `Send` fan-out** — `foreach` `mode: "send"` resolves `items_from`, applies `max_items`, and emits a `Send` to its one explicit normal-edge target with a copied item context.
- [x] **Named-agent foreach** — `foreach` `mode: "agent"` concurrently invokes `agent_name` with a per-item prompt, optional cap/concurrency bound, and ordered aggregated results.
- [x] **Retry recovery commands** — compiler-installed exhausted-retry handlers return `Command(update=…, goto=…)`; general user-authored `Command` nodes remain unsupported.
- [ ] **`Command.PARENT` routing** — no parent-graph routing support.
- [~] **Resume command consumption** — human nodes pause through `interrupt()` and validate `allowed_responses`; callers can resume with LangGraph `Command(resume=...)` when a checkpointer is supplied.

## Persistence, interrupts, and revisions

- [~] **Checkpointing / resumability** — compiler accepts an injected `checkpointer`; unit tests confirm interrupt + resume with `MemorySaver`. The live routine-subagent proxy invokes routines without supplying a checkpointer, so normal delegated routine runs are not currently resumable.
- [x] **Human interrupts** — `human` requires `message`, publishes it plus optional `allowed_responses` in the interrupt payload, validates resumed answers, and stores the answer.
- [x] **Immutable routine revision history** — Postgres-backed routine graph revisions are stored and subagents cache compiled graphs by revision ID. Current v1 documents are atomically upgraded to v2 on store initialization, producing a new v2 revision while retaining the immutable historical snapshot.
- [~] **Graph migrations** — definition migration from v1 to v2 is automatic for current documents; there is still no explicit LangGraph execution-checkpoint migration workflow.

## Composition and runtime

- [x] **Routine-as-subagent delegation** — routines are exposed as DeepAgent `CompiledSubAgent`s.
- [x] **Subgraph invocation** — a `subgraph` node invokes a compiled child graph from a registry with explicit `input_map` and `output_map`. It is composition, but not LangGraph’s native “compiled graph registered directly as a graph node” form.
- [x] **Runtime context API** — compiled graph declares `context_schema=RoutineContext`; prompt/seed nodes can use `user_id`, `workspace_id`, `request_id`, and metadata.
- [~] **Context in live delegation** — the compiler supports invocation context, but the routine-subagent proxy currently calls `ainvoke()` without forwarding context.

## Streaming, visualization, observability

- [x] **Custom execution events** — nodes use `runtime.stream_writer()` for seed, node start/completion, foreach expansion, and human-resume events.
- [~] **Event streaming interface** — `astream_events()` works at compiled-graph level and is tested, but delegated routine execution uses `ainvoke()` and does not currently relay routine events through the live backend/UI.
- [ ] **LangGraph graph visualization API** — no `get_graph()`, Mermaid, or LangGraph visualization integration in the routine compiler.
- [ ] **LangSmith-specific tracing configuration** — no direct LangSmith integration in `app/progenitor/routines`; standard LangGraph tracing may still occur if configured externally.

## Verification

- `pytest tests/progenitor/routines tests/backend/test_document_store.py -q` → **122 passed**
- `pyright app/` → **0 errors**

## Primary implementation files

- `app/progenitor/routines/factory.py`
- `app/progenitor/routines/graph.py`
- `app/progenitor/routines/subagent.py`
- `data/schemas/graph.routine.schema.v1.json`
