# Declarative agent definitions — design todo

## Goal

Make code-defined-style, tool-using agents persistable and revisioned so routines can invoke them through the existing `agent` node without duplicating prompts, tools, or middleware in every graph.

```text
Persisted agent-definition document
        ↓
validate + resolve approved capabilities
        ↓
materialize a runnable DeepAgent/LangGraph agent
        ↓
cache by immutable revision ID
        ↓
routine `agent` node delegates to it
```

## Boundary

Keep the two concepts distinct:

```text
Routine document
───────────────
Explicit state-machine graph:
  nodes, edges, routers, schemas, loops

Agent-definition document
─────────────────────────
One autonomous agent/ReAct loop:
  instructions, tools, model policy, middleware policy
```

A routine agent node should identify the target and supply only this invocation's task:

```json
{
  "id": "repo_manager",
  "kind": "agent",
  "output_key": "repo_state",
  "config": {
    "agent_name": "repo-manager",
    "prompt_template": "Prepare {repo_url} on branch {branch}."
  }
}
```

The agent definition remains the canonical owner of its instructions, tool set, model policy, and middleware.

## Proposed agent-definition document

```json
{
  "id": "repo-manager",
  "name": "Repo Manager",
  "description": "Safely inspects, clones, and updates repositories.",
  "version": "1.0.0",
  "status": "published",
  "instructions": "You manage repositories safely...",
  "model": {
    "profile": "default"
  },
  "tools": [
    "repositories.inspect",
    "repositories.clone",
    "repositories.pull"
  ],
  "middleware": [
    "audit-logging"
  ]
}
```

Persist capability references, never arbitrary executable Python:

```text
Allowed:
  tools: ["repositories.clone"]
  middleware: ["audit-logging"]
  model.profile: "default"

Never allow:
  import_path: "some.user.module.run_anything"
  code: "..."
```

The runtime resolves each permitted ID through fixed tool, middleware, and model-policy registries.

## Revision semantics

Persist immutable agent-definition revisions alongside the latest document.

```text
agent:repo-manager              → current document
agent-revisions:repo-manager    → immutable historical versions
```

Pin both routine and agent revisions for each root task/thread:

```text
routine revision: repo-setup-worker@abc123
agent revision:   repo-manager@def456
```

Invariant:

```text
One root task/thread uses one resolved revision of every routine and agent definition.
New tasks use the latest published revisions.
```

This prevents one in-flight routine execution from calling different versions of the same agent after an edit.

## Runtime resolver

Implement an agent-definition resolver analogous to the existing routine proxy:

1. Resolve the requested agent document by stable ID.
2. Use the thread-pinned revision when present; otherwise resolve the latest published revision.
3. Validate the document.
4. Resolve only approved tool/middleware/model-policy identifiers.
5. Materialize a runnable DeepAgent.
6. Cache the runnable by immutable revision ID.
7. Invoke it with the routine node's rendered prompt.
8. Store the resolved agent revision in root task state.

This replaces the current empty registry passed by the routine proxy:

```python
compile_routine_graph(
    ...,
    agent_registry={},
)
```

## Dynamic loading scope

### Required first: routine graph delegation

An `agent` node should resolve its target at execution time:

```text
routine agent node → resolver → latest/pinned agent revision → compiled agent
```

This lets a graph invoke a persisted agent without rebuilding every routine.

### Separate later concern: parent task menu

The parent Progenitor DeepAgent constructs its `task(subagent_type=...)` targets once at startup. Dynamically exposing newly created agent definitions directly to the parent task menu therefore needs either:

1. a runtime restart/reload; or
2. a generic dynamic dispatcher subagent.

Do not block routine-node delegation on this second problem.

## Migration direction

1. Define and validate the document schema.
2. Add durable latest-document and revision storage.
3. Implement the safe capability registries and runtime resolver.
4. Wire routine `agent` nodes to the resolver.
5. Add revision pinning to root execution state.
6. Migrate selected code-defined agents to documents once behavior is verified.
7. Later, make the Agents page edit and display these documents as the canonical agent definitions.

## Current facts

- The routine `agent` node already has the intended target-and-prompt shape.
- Its live path is not wired: `create_routine_subagent()` passes an empty `agent_registry`.
- A routine `subagent` node is for another persisted routine graph, not a code-defined ReAct agent.
- A `prompt` node makes one direct model call; it is not a tool/ReAct agent loop.
