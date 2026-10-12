---
name: managing-workflows
description: Create, inspect, update, pause/resume, wire, or delete backend-native workflow definitions by editing one file under /workflows/ at a time. Use when a user asks to create or modify a workflow that reacts to document create/update/delete events (kanban cards, inbox messages, workflow runs, preferences, ...) and runs agent, MCP, condition, approval, or direct document read/query nodes through the backend workflow executor.
allowed-tools: read_file write_file edit_file ls structured_output generate_id current_time
---

Workflow definitions live as one JSON file per workflow:
- `/workflows/<workflow-id>.json`

Schemas are mounted read-only under `/schemas/`:
- `/schemas/definition.workflow.schema.v1.json` — the complete graph-native definition schema; use it to create or rewrite a workflow.
- Document schemas (e.g. `/schemas/task.schema.v1.json` for kanban cards) describe the fields a document type exposes as ports.

Core rules:
1. Work on exactly one workflow file at a time.
2. Read the workflow file before editing it.
3. Preserve `id`, `created_at`, and unrelated `ui` fields unless the user asked to change them.
4. Do not add runtime execution fields to the definition file.
5. Prefer `edit_file` for focused changes. Use `write_file` only when creating a new workflow or rewriting the whole document intentionally.
6. Use `structured_output` against the complete definition schema whenever creating or reshaping nodes or edges — do not hand-assemble JSON from memory.
7. Never hand-write `input_schema`/`output_schema` for `event`, `cron`, `document_read`, or `document_query` nodes: the server derives them on save. Wire edges to the port names listed below.

Document events:
- Every create/update/delete of a stored document is an event. An `event` node subscribes by `document_type` (a registered type id). Built-in ids: `kanban_card`, `kanban_task` (task body), `inbox_message`, `workflow`, `workflow_run`, `routine`, `policy`, `agent`, `skill`, `memory`, `prompt`, `preferences`, `schedule`, `mcp`, `plugins`, `marketplaces`, `knowledge`, `repos`, `kanban_board`. Writing a workflow with an unknown id fails with the list of registered ids.
- Optional filters on the event node: `key` (exact document key), `operations` (subset of `created`/`updated`/`deleted`; empty = any), `changed_fields` (fire only if one of these top-level fields changed), `match` (equality at dotted output paths, e.g. `{"after.column_id": "done"}`; the path must exist).
- Event output ports: `event_id`, `document_type`, `key`, `id` (key without prefix), `op`, `timestamp`, `changed_fields`, `before`, `after`. `before`/`after` are the whole document, typed with the document type's label (e.g. `Kanban Card`); wire them into an `unpack` node to reach individual fields. `before` is absent on created, `after` on deleted.
- Example — "a card moved to Done": `{"kind": "event", "title": "Card done", "document_type": "kanban_card", "operations": ["updated"], "changed_fields": ["column_id"], "match": {"after.column_id": "done"}}`.

Node kinds:
- `event` — entrypoint described above; no inbound edges.
- `cron` — time entrypoint run by the backend clock; no inbound edges. Fields `kind`, `title`, `expression` (5-field cron, e.g. `0 9 * * 1-5`), `timezone` (IANA, e.g. `Australia/Melbourne`), `overlap` (`skip` default: drop a fire while the workflow still has an active run; `queue`: run it afterwards). Outputs `scheduled_for`, `fired_at` (ISO date-times in the node's timezone), `timezone`. A fire missed while the backend was down runs once on restart; a paused workflow does not replay missed fires. This is for workflows only — to remind yourself or the user later, use your own schedules (`/config/schedule.json`), not a workflow.
  Example — weekday digest: `{"kind": "cron", "title": "Weekdays 9am", "expression": "0 9 * * 1-5", "timezone": "Australia/Melbourne"}` with `cron.scheduled_for` → an `agent` template `{scheduled_for}` placeholder.
- `data` — literal `value` with an `output_schema`; output port `value`.
- `document_read` — reads one document directly (no LLM). Fields `kind`, `title`, `document_type`. Input `id` for collection types (e.g. wire `event.id` → `read.id` to load a card's `kanban_task` body), or `trigger` (any value, for sequencing) for single-document types. Outputs `found`, `key`, `id`, `document` (typed with the document type).
- `unpack` — pure step (no LLM) exposing one object's fields. Fields `kind`, `title`. Single input `value`: wire any object port (`event.after`, `read.document`, or another unpack's object field). Output ports are that object's properties, derived on save. Example — card column into a query: `event.after` → `card.value`, `card.column_id` → `peers.column_id`.
- `document_query` — equality query over a collection type (no LLM). Fields `kind`, `title`, `document_type`, `filter_fields` (each becomes a required input port), `limit` (1–200). With no filters it takes a `trigger` input. Outputs `items`, `keys`, `count`.
- `extract` — fields `kind`, `title`, exactly one of `prompt` (a `/prompts/` path) or `template` (inline), optional `schema` (document type id, schema file name, or inline JSON schema). Inputs: one port per `{placeholder}` root, plus optional `thread_id`. Progenitor answers via `structured_output`; output `result` (typed by the schema) and `thread_id`. Schema comes from `schema`, else the template's front matter `output_schema`, else the typed input `result` is wired into; with none, saving fails.
- `agent` — same template fields plus optional `subagent` (a registered sub-agent to delegate to). Outputs `message` (Progenitor's reply text) and `thread_id`.
- Templates are markdown with optional front matter (`title`, `description`, `inputs` mapping placeholder roots to types, `output_schema`, `system`). Placeholders `{card}` / `{card.title}` become input ports; a missing value fails the step. All prompt steps in a run share one thread unless `thread_id` is wired.
- `condition` — deterministic branch with `true`/`false` outputs.
- `approval` — pauses for an approval decision.
- `human_input` — pauses for a typed human-supplied value.

Prefer `document_read`/`document_query` over an `agent` node whenever a step only needs to look a document up.

Recommended flow for a new workflow:
1. `ls /workflows/`
2. `read_file /schemas/definition.workflow.schema.v1.json`
3. Generate ids for the workflow and its nodes.
4. One `structured_output` call, `schema_file: definition.workflow.schema.v1.json`, describing every node and edge — this produces the complete, schema-valid definition in one shot.
5. `write_file /workflows/<workflow-id>.json`

Recommended flow for editing an existing workflow:
1. `ls /workflows/`
2. `read_file /workflows/<workflow-id>.json`
3. If only one field changes, update it with `edit_file` directly.
4. If several nodes or edge wiring change together, read the complete definition schema and use `structured_output` to produce the whole updated definition, then persist only that workflow file.

Validation reminders:
- at least one trigger (`event` or `cron`) is required; trigger nodes cannot have inbound edges
- `cron` needs a valid 5-field `expression` and a real IANA `timezone`
- every non-source node needs an inbound edge, and every required input port needs exactly one edge
- edge ports must exist on both nodes and have compatible types
- `match` keys must be dotted paths starting at an event output port (e.g. `after.column_id`); `changed_fields` must be document fields
- `document_query` requires a collection document type
- the graph must be acyclic
- a workflow's own run writes never re-trigger it (runs carry their ancestry)
