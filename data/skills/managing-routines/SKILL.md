---
name: managing-routines
description: Create, inspect, edit, validate, and delete executable routine graphs stored under /routines/. Use when a user asks to build or change a routine, formerly called a sequence, including branching, loops, parallel fan-out, human approval, or delegated agent steps.
allowed-tools: read_file write_file edit_file ls structured_output generate_id current_time
---

# Managing routines

Routine graphs are executable LangGraph definitions stored as one JSON document
per routine: `/routines/<routine-id>.json`.

The authoritative contract is `/schemas/graph.routine.schema.v1.json`. Read it
before creating or reshaping a graph. The compiler and topology validator are
implemented in `app/progenitor/routines/graph.py` and
`app/progenitor/routines/factory.py`.

## Operating rules

1. Work on exactly one routine file at a time.
2. Resolve the user's intent before choosing a routine file. An explicit request
   to create a new routine (for example, "create a new routine" or "add a
   routine") always means create a separate file with a fresh routine ID. A
   selected routine ID supplied as page context is only context in this case;
   do not read or edit that routine as the creation target. Use the selected
   routine as the target only when the user asks to modify, update, or otherwise
   change the current/selected routine.
3. For an existing routine, read it first and preserve `id`, `name`,
   `description`, and `version` unless the user asks to change them.
4. The document `id` must match the filename's routine id. Never silently
   rename a routine or overwrite another routine while creating one.
5. Use `edit_file` for focused field changes. Use `structured_output` followed
   by `write_file` when adding, removing, or reshaping nodes or edges.
6. For a new routine, call `generate_id` with `prefix=routine` and `count=1`.
   Use that returned id in the filename and document. Before adding nodes, call
   `generate_id` with `prefix=node` and a count equal to the number of new ids.
   Never invent ids or use `__seed__`,
   `__project_output__`, `__start__`, or `__end__`.
7. Validate the complete document against the routine schema before writing it.
   Also check topology: valid entry, existing endpoints, no duplicate outgoing
   flow declarations, valid condition branches, foreach wiring, and loop guards.
8. Do not claim success until the file write succeeds.

## Create, inspect, edit, and delete

For creation, call `generate_id` with `prefix=routine` and `count=1`; the
returned id is the owning filename and document id. Create a complete v2
document with `version: "2.0.0"`, at least one node, a valid `entry`, and valid
edges. A single `prompt` node is a valid seed.

For inspection, list `/routines/`, read the requested document, and summarize
its nodes, flow, inputs, outputs, and validation-relevant gaps without changing
it.

For edits, read the current document and preserve unrelated fields. Directly
edit prompts, labels, descriptions, or optional coordinates when appropriate.
For topology or config changes, regenerate the complete document through the
schema, validate it, then write exactly one routine file.

## Node kinds

The current schema supports: `prompt`, `eval`, `condition`, `switch`, `split`,
`classify`, `agent`, `a2a`, `noop`, `human`, `foreach`, `reduce`, and `routine`.

- `prompt` invokes the model using exactly one of `prompt_template` or
  `prompt_file`.
- `eval` uses a restricted Python expression or expression file; `defaults`
  provides fallbacks for missing top-level state values.
- `condition` is a boolean gate whose router must have exactly `true` and
  `false` branches and no default.
- `switch` routes an expression through author-defined branches.
- `split` is a structural parallel gateway that runs every branch.
- `classify` uses a model rubric and required response schema.
- `agent` invokes a local object agent; `a2a` invokes a registered A2A peer.
- `noop` passes state through unchanged.
- `human` pauses for a user response and is suitable for approval/review.
- `foreach` supports parallel item fan-out (`mode: "send"`) or per-item agent
  execution (`mode: "agent"`).
- `reduce` collects the output from every execution of `source_node` and applies
  `collect`, `sum`, `product`, `count`, `min`, or `max`. For map/reduce, put the
  reduce node after the Send foreach body's join marker and set `source_node` to
  the body node whose output should be aggregated. Empty input returns `[]` for
  collect, `0` for sum/count, `1` for product, and `null` for min/max.
- `routine` invokes another routine by name with optional input/output maps.

### Prompt template placeholders

Use Python format-string placeholders resolved from the routine's top-level
state. Use `{note}` for a top-level input:

```text
Summarize this note concisely:

{note}
```

Use `{context[user_id]}` for an indexed value:

```text
The current user is {context[user_id]}.
```

Every node shares `id`, `description`, and `output_key`, with optional `label`,
`x`, and `y` editor metadata. Layout coordinates are not required for
execution.

Use only the fields allowed by the selected node kind. For example:

```text
prompt  -> config.prompt_template
eval    -> config.expression
human   -> config.message
routine -> config.routine_name
```

Do not copy fields between kinds. The schema's `oneOf` branches are strict.

## Edge kinds and loops

- Normal flow: `from`, `to`, `level: "normal"`; it may carry `loop_guard`.
- Fan-in barrier: a list of sources, one target, `level: "fan_in"`.
- Router: `level: "route"`, `router: "state_equals"`, `state_key`, and a
  non-empty `branches` map; `default` is optional.
- Error flow: `from`, `to`, `level: "error"`; it handles exhausted retries and
  does not count as the normal outgoing flow.

There is no separate loop node. A normal edge's `loop_guard` contains an
expression and optional `on_false` destination. Use it for retry and
conditional loops, and ensure every loop has a safe exit.

## Human approval

Use a `human` node before a consequential `agent`, `routine`, file-writing, or
external-call step. Give it a clear `message` and, when useful, an
`allowed_responses` list such as `approve` and `reject`. The runtime checkpoints
the routine and resumes the same thread after the user's response.

## Standard workflow

1. List `/routines/` and identify the target.
2. Read the target document if it exists.
3. Read the full routine schema for creation or structural changes.
4. Generate ids for new nodes.
5. Produce or reshape the graph with `structured_output` using the full schema;
   use its JSON result rather than hand-assembling the document.
6. Validate schema and topology, correcting all errors.
7. Write exactly the intended routine file.
8. Report the routine id and changes; do not imply UI navigation or execution
   unless it actually occurred.
