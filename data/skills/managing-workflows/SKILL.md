---
name: managing-workflows
description: Create, inspect, update, pause/resume, wire, or delete backend-native workflow definitions by editing one file under /workflows/ at a time. Use when a user asks to create or modify a workflow that reacts to kanban events and runs prompt, user-message, or code nodes through the backend workflow executor.
allowed-tools: read_file write_file edit_file ls structured_output generate_id current_time
---

Workflow definitions live as one JSON file per workflow:
- `/workflows/<workflow-id>.json`

Schemas are mounted read-only under `/schemas/`:
- `/schemas/definition.workflow.schema.v1.json` — the complete graph-native definition schema; use it to create or rewrite a workflow.
- `/schemas/node_event.workflow.schema.v1.json` — an event entrypoint node.
- `/schemas/node_prompt.workflow.schema.v1.json` — the `prompt` node.
- `/schemas/node_user.workflow.schema.v1.json` — the `user` (notify) node.
- `/schemas/node_code.workflow.schema.v1.json` — the `code` node.

Event nodes are stored in `graph.nodes`; there is no top-level trigger field.

Core rules:
1. Work on exactly one workflow file at a time.
2. Read the workflow file before editing it.
3. Preserve `id`, `created_at`, and unrelated `ui` fields unless the user asked to change them.
4. Do not add runtime execution fields to the definition file.
5. Prefer `edit_file` for focused changes. Use `write_file` only when creating a new workflow or rewriting the whole document intentionally.
6. Use `structured_output` against the complete definition schema whenever creating or reshaping event nodes, other nodes, or edges — do not hand-assemble JSON from memory.

Recommended flow for a new workflow:
1. `ls /workflows/`
2. `read_file /schemas/definition.workflow.schema.v1.json`
3. Generate ids for the workflow and its nodes.
4. One `structured_output` call, `schema_file: definition.workflow.schema.v1.json`, describing every event node, executable node, and edge — this produces the complete, schema-valid definition in one shot.
5. `write_file /workflows/<workflow-id>.json`

Recommended flow for editing an existing workflow:
1. `ls /workflows/`
2. `read_file /workflows/<workflow-id>.json`
3. If only one field changes, update it with `edit_file` directly.
4. If an event node, several nodes, or edge wiring changes together, read the complete definition schema and use `structured_output` to produce the whole updated definition, then persist only that workflow file.

Minimal valid v2 workflow shape:
- one or more `event` nodes
- one or more executable nodes
- every event node has no inbound edges and an edge to its first executable step
- each event node carries its own `event_type`, optional `from_column`, and optional `to_column`
- event types are one of:
  - `kanban.card.created`
  - `kanban.card.updated`
  - `kanban.card.moved`
  - `kanban.card.deleted`
  - `kanban.card.status_changed`
- `from_column` / `to_column` are only meaningful for `kanban.card.moved`; filters are one of `backlog`, `plan`, `build`, `validate`, `done`, or `null`.

Node kinds:
- `event` — graph entrypoint with fields `kind`, `title`, `event_type`, optional `from_column`, optional `to_column`.
- `prompt` — runs a prompt through the backend SSE path.
- `condition` — deterministic branch with `true`/`false` outputs.
- `approval` — pauses for an approval decision.
- `mcp` — invokes a configured MCP tool.

Validation reminders:
- at least one `event` node is required
- event nodes cannot have inbound edges
- event nodes have at most one outbound edge
- non-event roots are invalid
- edges must point to existing node ids
- the graph must be acyclic
