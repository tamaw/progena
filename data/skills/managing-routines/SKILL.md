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
implemented in `src/progena/routines/graph.py` and
`src/progena/routines/factory.py`.

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

The current schema supports: `prompt`, `eval`, `code`, `condition`, `switch`, `split`,
`classify`, `agent`, `a2a`, `noop`, `human`, `foreach`, `reduce`, and `routine`.

- `prompt` invokes the model using exactly one of `prompt_template` or
  `prompt_file`.
- `eval` uses a restricted Python expression or expression file; `defaults`
  provides fallbacks for missing top-level state values.
- `code` runs an inline JavaScript body in a fresh bounded QuickJS sandbox.
  Its config requires `language: "javascript"` and nonblank `code`, with optional
  `filesystem` and the standard `retry`, `timeout`, and `cache` options.
  The body receives all current routine data as JSON-compatible `input` and must `return` one
  JSON-compatible value; the whole value is stored at its `output_key`.
  `return { total: input.items.reduce((sum, item) => sum + item.price, 0) };`
  is valid. Host capabilities are absent by default; only the explicit
  filesystem capability below can add scoped file tools. There are no direct
  filesystem, network or module APIs. QuickJS execution, memory, stack and
  result size are independently bounded; a sandbox exception, timeout or
  non-JSON result fails the node.
  Reference routine parameters and earlier outputs with `input.<key>`, where
  `<key>` is the producer's `output_key` (or node ID when omitted), not its
  display label. For example, `return input.quote.price * input.quantity;`.
  Use brackets for literal keys containing punctuation or spaces:
  `input["market-data"]["close.price"]`. Only values already produced on the
  executed graph path exist; code-node outputs have no inferred field schema.
  JavaScript source is **not** a message template: do not inject `{key}` or
  double/escape JavaScript object braces. `${...}` inside backticks is normal
  JavaScript interpolation. In contrast, Eval/Branch/Switch expressions use
  `quote.price`, and prompt templates use `{quote[price]}`.
  The editor reuses state-path autocomplete: type `input.` (or a partial
  `input` reference), then select with arrows/Enter or the mouse; Escape
  dismisses it. Suggestions include routine input keys, seeded `request` and
  `context`, and predecessor outputs/declared response fields. It inserts
  JavaScript-safe dot/bracket access; it does not infer arbitrary script results.

  A Code node is pure when `config.filesystem` is omitted or null. To grant a
  narrowly scoped virtual filesystem, set `config.filesystem` to:

  ```json
  {
    "access": "read",
    "paths": ["/artifacts/", "/shared/reports/"]
  }
  ```

  `access` is `"read"` or `"read_write"`. Each `paths` entry is a directory
  root under `/artifacts/` or `/shared/`; use absolute slash paths only. Paths
  are normalized with a trailing slash. Dot segments, backslashes, NULs, empty
  paths, and every other mount family are rejected. Read access exposes only
  `ls`, `readFile`, `glob`, and `grep`; write access additionally exposes
  `writeFile`, `editFile`, `copyFile`, `moveFile`, and `deleteFile`. Text only:
  binary data is rejected rather than lossy-converted. There is no shell,
  network, agent, or generic host-tool escape hatch. Writes and copies never
  overwrite; moves are file-only and must stay on one mount, but are not atomic
  (they may be implemented as copy then delete). File operations are
  nontransactional: cancellation or a later failure does not roll back completed
  writes, and a manual rerun/replay can repeat them. Filesystem-enabled Code
  nodes never cache and never retry automatically, which is not exactly-once
  execution. The QuickJS CPU deadline excludes async filesystem waits; the
  enclosing node timeout still bounds those waits.

  With `access: "read_write"` and `/artifacts/` allowed, for example:

  ```javascript
  await tools.writeFile({
    file_path: "/artifacts/report.json",
    content: JSON.stringify(input.result)
  });
  await tools.moveFile({
    source_path: "/artifacts/report.json",
    destination_path: "/artifacts/finished.json"
  });
  return JSON.parse(await tools.readFile({file_path: "/artifacts/finished.json"}));
  ```

  Calls are asynchronous and must be awaited. `ls` takes `{path}`; `readFile`
  takes `{file_path, offset?, limit?}`; `glob` takes `{pattern, path?}` and
  returns `{matches, truncated, truncation_reason}`; `grep` takes
  `{pattern, path?, glob?, max_count?}` and returns `{matches, truncated}`.
  `editFile` takes `{file_path, old_string, new_string, replace_all?}`;
  `copyFile`/`moveFile` take `{source_path, destination_path}`; `deleteFile`
  takes `{file_path}`. Filesystem methods throw on failure. Scope checks also
  reject symlinks whose resolved target escapes the configured directory roots.
- `condition` is a boolean gate whose router must have exactly `true` and
  `false` branches and no default.
- `switch` routes an expression through author-defined branches.
- `split` is a structural parallel gateway that runs every branch.
- `classify` uses a model rubric and required response schema.
- `prompt`, `classify`, and `agent` accept optional `model_profile`, a named
  entry in `/config/profile.json`. Omit it or use null for existing defaults.
  Resolve profile names from that document; do not put deployment names in this
  field. Agent overrides apply to both wait and background execution without
  editing the agent manifest. Other node kinds do not accept this setting.
- `agent` invokes a local object agent; `a2a` invokes a registered A2A peer.
  An agent's request is exactly one of inline `prompt_template`, reusable
  `instruction` (a path relative to `/instructions/`, such as
  `workflows/card-summary.md`), or `inputs`. Instruction placeholders resolve
  from routine state just like prompt-template placeholders.
  Agent nodes default to `execution: "wait"`, which waits for the response. Use
  `execution: "background"` only when later steps can proceed without that answer.
  Background output is a task reference (`task_id`, `thread_id`, `run_id`,
  `agent_name`, `status`), not the final response. Each launch creates a separate
  conversation; `response_schema` validates the eventual task result.
  Every node stops after 120 seconds unless it sets `timeout` (seconds; `0`
  means no limit). Agent steps that run shell work (cloning, worktrees, builds)
  usually need more: set e.g. `"timeout": 900`.
  Do not make an agent step that starts Claude and polls `acp_status`: every
  poll is a full model turn. Start ACP work from a workflow with
  `document_create(acp_task)` and react to its status events instead (see the
  managing-workflows skill).
  Repository work goes to the bundled `repo-manager` agent: it clones declared
  repos (`/config/repos/<id>.json`), creates and removes per-branch worktrees
  under `$PROGENA_REPOS_DIR/<repo-id>.worktrees/`, and publishes branches
  (push + pull request). Tell it the repo id and branch, and ask it to reply
  with the paths and URLs it produced.
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

A node stores its whole result under `data[<output_key>]` (default: its `id`);
it never spreads an object's fields into `data`. The routine's `output_schema`
returns each declared field from top-level `data`, so an agent node with
`output_key: "result"` and `response_schema` `{worktree_path, branch_name}`
returns nothing for those fields on its own. Lift each one with an `eval` node
whose `output_key` is the field name, e.g. `output_key: "worktree_path"`,
`expression: "result.worktree_path"`.

Use only the fields allowed by the selected node kind. For example:

```text
prompt  -> config.prompt_template
eval    -> config.expression
code    -> config.code (+ optional config.filesystem)
human   -> config.message
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
`allowed_responses` list such as `approve` and `reject`. Set `approver` to
`human` when only a person may answer (the UI; `resume_routine` refuses it), or
leave the default `any`. The runtime checkpoints the routine and resumes the
same thread after the response.

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

## Running a routine from chat

Read the routine document first. Use its routine id as the `task` subagent
type. If the graph has an `input_schema`, set the `task` description to **only**
a JSON object satisfying that schema; the subagent validates and binds those
fields before running the graph. For example, a prompt using `{question}` needs
`task(subagent_type="...", description='{"question":"What is 40 + 2?"}')`.
For a graph without `input_schema`, the description remains a natural-language
request available to the graph as `{request}`. A human step returns its paused
thread id; use `resume_routine` to answer it.

## Execution policy

Select one existing execution policy with `policy_id` (default `default`).
Read `/policies/` and use the managing-policies skill to create or change policies.
Graph-level inline `settings` are not part of the contract. Node-specific
settings override policy defaults; graph concurrency and step limits still apply.
