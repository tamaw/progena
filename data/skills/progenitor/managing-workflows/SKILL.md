---
name: managing-workflows
description: Create, inspect, update, pause/resume, wire, or delete backend-native workflow definitions by editing one file under /config/workflows/ at a time. Use when a user asks to create or modify a workflow that reacts to kanban events and runs prompt, user-message, or code nodes through the backend workflow executor.
allowed-tools: read_file write_file edit_file ls structured_output generate_id current_time
---

Workflow definitions live as one JSON file per workflow:
- `/config/workflows/<workflow-id>.json`

Schemas are mounted read-only under `/schemas/`:
- `/schemas/definition.workflow.schema.v1.json` — the whole document; self-contained (embeds the trigger shape and all four node-kind shapes via `oneOf`), so it is the only schema `structured_output` needs to build or rewrite a complete workflow in one call.
- `/schemas/trigger.workflow.schema.v1.json` — the `trigger` object alone.
- `/schemas/node_event.workflow.schema.v1.json` — the `event` node alone.
- `/schemas/node_prompt.workflow.schema.v1.json` — the `prompt` node alone.
- `/schemas/node_user.workflow.schema.v1.json` — the `user` (notify) node alone.
- `/schemas/node_code.workflow.schema.v1.json` — the `code` node alone.

The four `node_*` files and `trigger.workflow.schema.v1.json` exist for editing a single piece of an existing workflow in isolation; they are not required reading to build a new one, since `definition.workflow.schema.v1.json` already contains every shape they document.

Core rules:
1. Work on exactly one workflow file at a time.
2. Read the workflow file before editing it.
3. Preserve `id`, `created_at`, and unrelated `ui` fields unless the user asked to change them.
4. Do not add runtime execution fields to the definition file.
5. Prefer `edit_file` for focused changes. Use `write_file` only when creating a new workflow or rewriting the whole document intentionally.
6. Use `structured_output` against a schema (the full definition, or one `node_*`/`trigger` schema for a focused edit) whenever creating or reshaping nodes/edges — don't hand-assemble JSON from memory.

Recommended flow for a new workflow:
1. `ls /config/workflows/`
2. `read_file /schemas/definition.workflow.schema.v1.json`
3. Generate ids for the workflow and its nodes.
4. One `structured_output` call, `schema_file: definition.workflow.schema.v1.json`, instruction describing the whole workflow (trigger, every node, and the edges connecting them) — this produces the complete, schema-valid definition in a single shot.
5. `write_file /config/workflows/<workflow-id>.json`

Recommended flow for editing an existing workflow:
1. `ls /config/workflows/`
2. `read_file /config/workflows/<workflow-id>.json`
3. If only one field changes, update it with `edit_file` directly.
4. If one node's shape changes (e.g. rewriting a prompt node into a user node), `read_file` that node kind's `node_*.workflow.schema.v1.json`, run `structured_output` scoped to just that node object, then `edit_file` to replace `graph.nodes.<id>` in place — smaller and safer than regenerating the whole document.
5. If the trigger, several nodes, or the edge wiring all change together, `read_file /schemas/definition.workflow.schema.v1.json` and use `structured_output` to produce the whole updated definition, then persist only that workflow file.

Minimal valid v1 workflow shape:
- one `event` node
- one `prompt` node
- one edge from the event node to the prompt node
- top-level `trigger.type = "event"`
- top-level `trigger.event_type` set to one of:
  - `kanban.card.created`
  - `kanban.card.updated`
  - `kanban.card.moved`
  - `kanban.card.deleted`
  - `kanban.card.status_changed`
- optional `trigger.from_column` / `trigger.to_column` — only meaningful when `event_type` is `kanban.card.moved`; filters to a specific column transition instead of any move. Each is one of `backlog`, `plan`, `build`, `validate`, `done`, or `null` for "any column". Omit both for "any move".

Node kinds (each also has its own `node_<kind>.workflow.schema.v1.json` for focused edits; shapes are identical to the inline `oneOf` branch in `definition.workflow.schema.v1.json`):
- `event` (`node_event.workflow.schema.v1.json`) — the fixed trigger/start node. Fields: `kind`, `title`.
- `prompt` (`node_prompt.workflow.schema.v1.json`) — runs a prompt through the backend IPC path. Fields: `kind`, `title`, `prompt`, `agent_name`, `role` (nullable).
- `user` (`node_user.workflow.schema.v1.json`) — delivers a message to the user via an agent tool. Fields: `kind`, `title`, `message_type` (`say_to_user` | `notify_user` | `ask_user`), `message`, `notification_title` (required, non-null, only when `message_type` is `notify_user`), `level` (`info` | `warning` | `error` | `success`).
- `code` (`node_code.workflow.schema.v1.json`) — asks the agent to run a code snippet through the same IPC path (no sandboxed executor). Fields: `kind`, `title`, `language`, `content`.

Validation reminders:
- exactly one `event` node
- `event` nodes cannot have inbound edges
- `prompt`, `user`, and `code` nodes may each have at most one inbound and one outbound edge
- `user` nodes with `message_type: "notify_user"` must have a non-empty `notification_title`
- `code` nodes must have non-empty `content` and `language`
- edges must point to existing node ids
- the graph must be acyclic
