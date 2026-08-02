---
name: managing-routines
description: Create, inspect, update, or delete an agent's editable routine graph by editing one file under /routines/ at a time. Use when a user asks to build or change a subagent's routine (the "Routines" page, formerly "Sequences") -- a declarative prompt/code/eval/subagent/agent/noop graph compiled into a runnable LangGraph agent.
allowed-tools: read_file write_file edit_file ls structured_output generate_id current_time
---

Routine graphs live as one JSON file per agent:
- `/routines/<agent-id>.json`

The filename is the owning agent's id (see `/config/agents.json` or the Agents
page for ids) -- there is no separate id-generation step like workflows have;
you never invent a routine id, you always target an existing agent's file.

Schema is mounted read-only under `/schemas/`:
- `/schemas/graph.routine.schema.v1.json` -- the whole document, self-contained
  (embeds all six node-kind shapes and both edge shapes via `oneOf`/`$defs`), so
  it is the only schema `structured_output` needs to build or rewrite a complete
  routine in one call. Unlike workflows there are no separate per-node-kind
  schema files -- read the `$defs` section of this one file for a focused shape.

Core rules:
1. Work on exactly one routine file at a time.
2. Read the routine file before editing it -- preserve `id`, `name`,
   `description`, and `version` unless the user asked to change them. `id` must
   always equal the filename (`<agent-id>`).
3. Node kinds are executable step kinds, not decoration: each one is compiled
   into a real LangGraph node by `app.progenitor.routines.factory`. Getting a
   node's `config` shape wrong breaks the agent at compile/run time, not just
   in the editor.
4. Prefer `edit_file` for focused changes. Use `write_file` only when creating
   the first version of a routine (an agent seeded with the single-step
   default) or rewriting the whole document intentionally.
5. Use `structured_output` against `/schemas/graph.routine.schema.v1.json`
   whenever creating or reshaping nodes/edges -- don't hand-assemble JSON from
   memory, the node `config` unions are strict (`additionalProperties: false`
   plus `oneOf` requiring exactly one of `prompt_template`/`prompt_file`, etc.).

Recommended flow for building out a routine:
1. `ls /routines/` to see which agents already have a graph.
2. `read_file /routines/<agent-id>.json` -- every agent has one from the moment
   it's created (seeded as a single `prompt` node), so there is no "create"
   step distinct from "edit".
3. `read_file /schemas/graph.routine.schema.v1.json`.
4. One `structured_output` call, `schema_file: graph.routine.schema.v1.json`,
   instruction describing the whole routine (every node and the edges
   connecting them), with `id`/`name`/`description`/`version` copied forward
   from the file you just read.
5. `write_file /routines/<agent-id>.json`.

Recommended flow for a small edit:
1. `read_file /routines/<agent-id>.json`.
2. If only one field changes (a prompt template, a node's `label`/position),
   update it with `edit_file` directly.
3. If you're adding, removing, or reshaping a node or edge, re-run
   `structured_output` for the whole document (step 4 above) rather than
   hand-editing JSON, then `write_file` the result -- the schema's `oneOf`
   config unions are easy to get subtly wrong by hand.

Minimal valid routine shape:
- `id` (must equal `<agent-id>`), `name`, `description`, `version`
- at least one node
- `entry` set to one of the node ids
- every edge's `from`/`to` (or router `branches`) referencing existing node ids
- the graph must be acyclic-safe for `entry` (no dangling entry, no orphaned
  router branch)

Node kinds (`kind` discriminates; every kind shares `id`, `description`,
`output_key`, `label` -- there is no `x`/`y`: layout is rigid/computed by the
canvas, not authored, so position is never part of the document):
- `prompt` -- render a template and invoke the chat model. `config`: exactly
  one of `prompt_template` (inline) or `prompt_file`; optional
  `system_prompt`.
- `code` -- evaluate a restricted deterministic Python expression (never
  `eval()`/`exec()` -- see `app.progenitor.routines.expression`). `config`:
  exactly one of `expression`/`expression_file`; optional `inputs` (local name
  -> dotted path into `data`, resolved before evaluation); `language` is
  always `"python"` if set.
- `eval` -- same restricted expression evaluator as `code`, or a model rubric.
  `config`: at most one of `expression`/`expression_file` (both omitted falls
  back to `rubric_prompt`, asking the model a yes/no question whose response
  starting with "true" evaluates true); optional `inputs`.
- `subagent` -- invoke another compiled routine graph by name. `config`:
  required `subagent_name`; exactly one of `prompt_template`/`prompt_file`.
- `agent` -- invoke a declarative object agent by name. `config`: required
  `agent_name`; exactly one of `prompt_template`/`prompt_file`.
- `noop` -- pass through without changing state. `config` is empty
  (`additionalProperties: false`, no required keys beyond the shared ones).

Edges:
- Plain edge: `{"from": <node id>, "to": <node id>}`.
- Router edge (`state_equals`): `{"from": <node id>, "router": "state_equals",
  "state_key": <dotted path into "data">, "branches": {<value>: <node id>,
  ...}, "default": <node id>}` (`default` optional). A node may have at most
  one outbound router edge, and a router edge cannot coexist with a plain edge
  from the same source.

Validation reminders:
- every node kind requires `id`, `kind`, `output_key` (`prompt`/`code`/`eval`/
  `subagent`/`agent` also require `config`)
- node ids must be unique within the graph
- `entry` must reference an existing node id
- edges must reference existing node ids on both ends (plain `to`, router
  `branches` values, and router `default`)
- a node cannot mix a router edge and a plain edge as its outbound edge
- the whole document is `additionalProperties: false` at every level --
  extra/misspelled keys fail validation, they don't get silently dropped
