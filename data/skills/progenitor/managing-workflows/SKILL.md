---
name: managing-workflows
description: Create, inspect, update, pause/resume, wire, or delete backend-native workflow definitions by editing one file under /state/workflows/ at a time. Use when a user asks to create or modify a workflow that reacts to kanban events and runs prompt nodes through the backend workflow executor.
allowed-tools: read_file write_file edit_file ls structured_output generate_id current_time
---

Workflow definitions live as one JSON file per workflow:
- `/state/workflows/<workflow-id>.json`

Schemas are mounted read-only under `/schemas/`:
- `/schemas/workflow_definition.schema.v1.json`
- `/schemas/workflow_trigger.schema.v1.json`
- `/schemas/workflow_node_event.schema.v1.json`
- `/schemas/workflow_node_prompt.schema.v1.json`

Core rules:
1. Work on exactly one workflow file at a time.
2. Read the workflow file before editing it.
3. Preserve `id`, `created_at`, and unrelated `ui` fields unless the user asked to change them.
4. Do not add runtime execution fields to the definition file.
5. Prefer `edit_file` for focused changes. Use `write_file` only when creating a new workflow or rewriting the whole document intentionally.
6. Use `structured_output` against the workflow schema when creating or reshaping nodes/edges.

Recommended flow for a new workflow:
1. `ls /state/workflows/`
2. `read_file /schemas/workflow_definition.schema.v1.json`
3. `read_file /schemas/workflow_node_event.schema.v1.json`
4. `read_file /schemas/workflow_node_prompt.schema.v1.json`
5. Generate ids for the workflow and its nodes.
6. Use `structured_output` to produce a valid workflow definition.
7. `write_file /state/workflows/<workflow-id>.json`

Recommended flow for editing an existing workflow:
1. `ls /state/workflows/`
2. `read_file /state/workflows/<workflow-id>.json`
3. `read_file /schemas/workflow_definition.schema.v1.json`
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

Validation reminders:
- exactly one `event` node
- `event` nodes cannot have inbound edges
- `prompt` nodes may have at most one inbound and one outbound edge
- edges must point to existing node ids
- the graph must be acyclic
