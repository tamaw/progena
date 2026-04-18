# Builder Agent

The `builder` agent is Progenitor's A2A-facing build specialist. Externally it
presents a single agent surface; internally it is implemented as a thin
**router** LangGraph that classifies each request with Azure OpenAI and
delegates to one of three specialist subagent graphs: `plan`, `build`, and
`validate`.

## Why the rename from `builder_progenitor`?

The agent was previously named `builder_progenitor` to distinguish it from the
older `builder_agent` experiment. With the router refactor this is now the
single canonical builder project, so the redundant suffix has been dropped.
External A2A callers should refer to it simply as `builder`.

## Top-level shape: a router, not a worker

The top-level graph (see [`router/graph.py`](router/graph.py)) does **only**
two things:

1. **classify** — call Azure OpenAI to decide whether the incoming request is
   primarily a planning, building, or validation task. The classifier is the
   single source of truth for routing — every request, including queue-worker
   tasks, flows through it. Planning happens on demand inside the build
   subagent when needed, not as a precondition for routing.
2. **dispatch** — invoke the appropriate compiled subagent graph with a fresh
   state object so each subagent runs in isolation.

The router never executes specialist work itself.

```
classify → (plan | build | validate) → respond → END
```

## Subagents

Each subagent lives under [`subagents/`](subagents/) and is a self-contained
LangGraph with its own state, nodes, and prompt.

### `plan`
Owns request shaping and build preparation. Wraps
`BuilderClient.chat_structured_with_reasoning` with the `BuildPlan` schema to
turn an unstructured request into a concrete plan. The shape of the plan and
its emphasis on success criteria / validation targets is informed by the
planning behaviour from the previous `builder_agent` plan node.

### `build`
The implementation specialist. This is the **previous builder_progenitor build
flow** (DeepAgent setup → execute → teardown driven by the prompts in
[`subagents/build/prompts.py`](subagents/build/prompts.py)) wrapped as a
LangGraph. The current topology is:

```
setup → execute → validate → ┬─► teardown          (verdict ∈ {pass, pass_with_concerns})
                             └─► teardown_failed   (verdict == fail)
```

`validate` runs **before** any teardown step so the verdict is computed from
real execution evidence. The graph then branches on
`validation_report.verdict`: a passing verdict reaches the standard `teardown`
node which commits, pushes, and opens a pull request; a failing verdict is
routed to `teardown_failed`, which only cleans up the worktree and does not
push or create a PR. Validation therefore gates shipping.

If no `PlannedBuild` is supplied to the build subagent (the normal queue-worker
case), `setup` plans on demand by calling `BuilderClient.plan_build` and fires
the optional `on_planned` callback so the queue worker can emit `build_started`.

### `validate`
Owns verification and quality gates. Calls Azure OpenAI with a
`ValidationReport` Pydantic schema (verdict / score / criteria / gaps /
next_actions / summary) to produce a serious pass/fail judgement on the
supplied evidence. The report shape and the emphasis on execution evidence
and scoring are derived from the verify node in the older `builder_agent`
project.

## A2A surface mapping

Externally there is **one** builder agent. Its A2A description advertises the
router behaviour and the three specialists so that callers understand the
internal model from the agent card alone, but it is invoked as a single
endpoint. The queue-worker path no longer preplans: every request — interactive
or queued — is sent through `BuilderService.process(request_text, context, *,
on_planned=None)`, which invokes the router graph. Classification picks the
specialist; planning happens on demand inside the build subagent when needed
and is reported back through the optional `on_planned` callback. The queue
worker uses the structured `BuildOutcome` returned by `process` (and the
`run_task` wrapper that adds task tracking) to decide success vs failure and
to surface PR/commit metadata, with no regex scraping of the assistant text.

## Source layout

```
app/agents/builder/
  agent.py           # A2A entrypoint, BuilderService composition
  router/
    graph.py         # router LangGraph
    nodes/           # classify / dispatch / respond
  subagents/
    plan/            # plan graph + nodes
    build/           # build graph + nodes + prompts
    validate/        # validate graph + nodes
data/prompts/builder/
  router_system.md
  plan_system.md
  validate_system.md
  ... (existing builder prompts)
```

## Isolation

The `builder` agent does not import from any other agent under `app/agents/*`.
The `builder_agent` project is referenced only as design inspiration for the
plan and validate subagents — its code is not consumed.
