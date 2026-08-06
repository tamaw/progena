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
- [x] **Per-node retries** — node `config.retry`.
- [x] **Graph-wide retry default** — `settings.retry` through `StateGraph.set_node_defaults`.
- [ ] **LangGraph task API / durable side-effect task isolation** — no `task()` usage in the routine compiler.

## Edges, routing, loops, and joins

- [x] **Normal edges** — declarative `{ from, to }`.
- [x] **Conditional edges** — `state_equals` router edges with branch and default targets.
- [x] **Conditional entry behavior** — supported indirectly: the configured `entry` follows the compiler’s seed node, but there is no separate conditional-entry schema primitive.
- [x] **Cycles / loopbacks** — any edge may target an earlier node.
- [x] **Recursion limits** — `settings.recursion_limit` becomes the compiled graph’s LangGraph recursion limit.
- [x] **Convergent joins** — multiple upstream branches may target one downstream node; `messages` and `data` reducers merge updates.
- [ ] **Static parallel fan-out through multiple plain edges** — schema rejects more than one plain edge from a source. Parallel fan-out is intentionally represented through `foreach` / `Send`, not multiple fixed edges.

## Dynamic flow

- [x] **Dynamic `Send` fan-out** — `foreach` resolves an item list and emits one `Send(target, state)` per item.
- [ ] **Node-returned `Command(update=…, goto=…)`** — no routine node returns `Command`.
- [ ] **`Command.PARENT` routing** — no parent-graph routing support.
- [~] **Resume command consumption** — human nodes pause through `interrupt()`; callers can resume with LangGraph `Command(resume=...)` when a checkpointer is supplied. The routine schema itself has no command-routing primitive.

## Persistence, interrupts, and revisions

- [~] **Checkpointing / resumability** — compiler accepts an injected `checkpointer`; unit tests confirm interrupt + resume with `MemorySaver`. The live routine-subagent proxy invokes routines without supplying a checkpointer, so normal delegated routine runs are not currently resumable.
- [x] **Human interrupts** — `human` node calls `interrupt({ node, prompt })` and stores the resumed answer.
- [x] **Immutable routine revision history** — Postgres-backed routine graph revisions are stored and subagents cache compiled graphs by revision ID. This is document-version persistence, distinct from LangGraph execution checkpoints.
- [~] **Graph migrations** — immutable revisions make definition changes traceable, but there is no explicit LangGraph checkpoint migration workflow.

## Composition and runtime

- [x] **Routine-as-subagent delegation** — routines are exposed as DeepAgent `CompiledSubAgent`s.
- [~] **Subgraph invocation** — a `subgraph` node invokes a compiled child graph from a registry. It is composition, but not LangGraph’s native “compiled graph registered directly as a graph node” form.
- [x] **Runtime context API** — compiled graph declares `context_schema=RoutineContext`; prompt/seed nodes can use `user_id`, `workspace_id`, `request_id`, and metadata.
- [~] **Context in live delegation** — the compiler supports invocation context, but the routine-subagent proxy currently calls `ainvoke()` without forwarding context.

## Streaming, visualization, observability

- [x] **Custom execution events** — nodes use `runtime.stream_writer()` for seed, node start/completion, foreach expansion, and human-resume events.
- [~] **Event streaming interface** — `astream_events()` works at compiled-graph level and is tested, but delegated routine execution uses `ainvoke()` and does not currently relay routine events through the live backend/UI.
- [ ] **LangGraph graph visualization API** — no `get_graph()`, Mermaid, or LangGraph visualization integration in the routine compiler.
- [ ] **LangSmith-specific tracing configuration** — no direct LangSmith integration in `app/progenitor/routines`; standard LangGraph tracing may still occur if configured externally.

## Verification

- `pytest tests/progenitor/routines/test_advanced_graph.py tests/progenitor/routines/test_factory.py -q` → **43 passed**
- `pytest tests/progenitor/routines/test_graph_schema.py tests/progenitor/routines/test_store.py tests/progenitor/routines/test_subagent.py -q` → **38 passed**

## Primary implementation files

- `app/progenitor/routines/factory.py`
- `app/progenitor/routines/graph.py`
- `app/progenitor/routines/subagent.py`
- `data/schemas/graph.routine.schema.v1.json`
