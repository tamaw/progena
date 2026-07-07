---
name: managing-workflows
description: Create, inspect, update, pause/resume, wire, or delete backend-native workflow definitions by editing one file under /state/workflows/ at a time. Use when a user asks to create or modify a workflow that reacts to kanban events and runs prompt, user-message, or code nodes through the backend workflow executor.
allowed-tools: read_file write_file edit_file ls structured_output generate_id current_time
---

Workflow definitions live as one JSON file per workflow:
- `/state/workflows/<workflow-id>.json`

Schemas are mounted read-only under `/schemas/`:
- `/schemas/definition.workflow.schema.v1.json`
- `/schemas/trigger.workflow.schema.v1.json`
- `/schemas/node_event.workflow.schema.v1.json`
- `/schemas/node_prompt.workflow.schema.v1.json`

Core rules:
1. Work on exactly one workflow file at a time.
2. Read the workflow file before editing it.
3. Preserve `id`, `created_at`, and unrelated `ui` fields unless the user asked to change them.
4. Do not add runtime execution fields to the definition file.
5. Prefer `edit_file` for focused changes. Use `write_file` only when creating a new workflow or rewriting the whole document intentionally.
6. Use `structured_output` against the workflow schema when creating or reshaping nodes/edges.

Recommended flow for a new workflow:
1. `ls /state/workflows/`
2. `read_file /schemas/definition.workflow.schema.v1.json`
3. `read_file /schemas/node_event.workflow.schema.v1.json`
4. `read_file /schemas/node_prompt.workflow.schema.v1.json`
5. Generate ids for the workflow and its nodes.
6. Use `structured_output` to produce a valid workflow definition.
7. `write_file /state/workflows/<workflow-id>.json`

Recommended flow for editing an existing workflow:
1. `ls /state/workflows/`
2. `read_file /state/workflows/<workflow-id>.json`
3. `read_file /schemas/definition.workflow.schema.v1.json`
4. If only one field changes, update it with `edit_file`.
5. If multiple connected fields change, use `structured_output` to produce the updated workflow definition, then persist only that workflow file.

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

Node kinds (all defined inline in `definition.workflow.schema.v1.json`, no separate schema file per non-event/prompt kind):
- `event` — the fixed trigger/start node. Fields: `kind`, `title`.
- `prompt` — runs a prompt through the backend IPC path. Fields: `kind`, `title`, `prompt`, `agent_name`, `role` (nullable).
- `user` — delivers a message to the user via an agent tool. Fields: `kind`, `title`, `message_type` (`say_to_user` | `notify_user` | `ask_user`), `message`, `notification_title` (required, non-null, only when `message_type` is `notify_user`), `level` (`info` | `warning` | `error` | `success`).
- `code` — asks the agent to run a code snippet through the same IPC path (no sandboxed executor). Fields: `kind`, `title`, `language`, `content`.

Validation reminders:
- exactly one `event` node
- `event` nodes cannot have inbound edges
- `prompt`, `user`, and `code` nodes may each have at most one inbound and one outbound edge
- `user` nodes with `message_type: "notify_user"` must have a non-empty `notification_title`
- `code` nodes must have non-empty `content` and `language`
- edges must point to existing node ids
- the graph must be acyclic
