---
name: managing-workflows
description: Create, inspect, update, pause/resume, wire, or delete backend-native workflow definitions by editing one file under /workflows/ at a time. Use when a user asks to create or modify a workflow that reacts to document create/update/delete events (kanban cards, inbox messages, workflow runs, ACP tasks, repos, preferences, ...) and runs agent, routine, repo (git worktree/push/PR), condition, approval, or direct document nodes — including stage pipelines that drive Claude or other ACP agents through acp_task documents.
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
7. Never hand-write `input_schema`/`output_schema` for `event`, `cron`, `document_read`, `document_query`, `document_write`, `document_create`, `document_delete`, `format`, or `repo` nodes: the server derives them on save. Wire edges to the port names listed below.

Document events:
- Every create/update/delete of a stored document is an event. An `event` node subscribes by `document_type` (a registered type id). Built-in ids: `kanban_card`, `kanban_task` (task body), `inbox_message`, `workflow`, `workflow_run`, `routine`, `policy`, `agent`, `skill`, `memory`, `instruction`, `preferences`, `schedule`, `mcp`, `plugins`, `marketplaces`, `knowledge`, `knowledge_page`, `knowledge_index`, `repo`, `acp_task` (below), `alert`, `kanban_board`, and `session` (below). Writing a workflow with an unknown id fails with the list of registered ids.
- `knowledge_page` receives events for pages under `knowledge:<base-id>/`; `knowledge_index` receives system-maintained indexing-status updates at `knowledge_index`. The index type is read-only, while knowledge page content has the standard content-document ports.
- Optional filters on the event node: `key` (exact document key), `operations` (subset of `created`/`updated`/`deleted`; empty = any), `changed_fields` (fire only if one of these top-level fields changed), `match` (equality at dotted output paths, e.g. `{"after.column_id": "done"}`; the path must exist).
- Event output ports: `event_id`, `document_type`, `key`, `id` (key without prefix), `op`, `timestamp`, `changed_fields`, `before`, `after`. `before`/`after` are the whole document, typed with the document type's label (e.g. `Kanban Card`); wire them into an `unpack` node to reach individual fields. `before` is absent on created, `after` on deleted.
- Example — "a card moved to Done": `{"kind": "event", "title": "Card done", "document_type": "kanban_card", "operations": ["updated"], "changed_fields": ["column_id"], "match": {"after.column_id": "done"}}`.
- `session` is created when a user starts a new chat thread. It fires once per thread, with `op` `created`, when the first user message is checkpointed. `after` has `id`, `thread_id`, `source` (`ui`, `telegram`, `api`), `title` (initially `"New chat"`), `first_message` (the typed text, capped at 4000 characters; `"* sends an attachment"` or `"* sends an image"` when there was no text), `pinned`, `archived`, `created_at`, `last_activity`, and `content` (empty until the first turn finishes). Use `document_write` to rename, pin, or archive it.
  Example — "someone started a Telegram chat": `{"kind": "event", "title": "New Telegram chat", "document_type": "session", "operations": ["created"], "match": {"after.source": "telegram"}}`.
  Example — auto-title workflow: `event(session, created)` → `extract` `{first_message}` → `document_write(session, id=thread_id, title)`.

Node kinds:
- `event` — entrypoint described above; no inbound edges.
- `cron` — time entrypoint run by the backend clock; no inbound edges. Fields `kind`, `title`, `expression` (5-field cron, e.g. `0 9 * * 1-5`), `timezone` (IANA, e.g. `Australia/Melbourne`), `overlap` (`skip` default: drop a fire while the workflow still has an active run; `queue`: run it afterwards). Outputs `scheduled_for`, `fired_at` (ISO date-times in the node's timezone), `timezone`. A fire missed while the backend was down runs once on restart; a paused workflow does not replay missed fires. This is for workflows only — to remind yourself or the user later, use your own schedules (`/config/schedule.json`), not a workflow.
  Example — weekday digest: `{"kind": "cron", "title": "Weekdays 9am", "expression": "0 9 * * 1-5", "timezone": "Australia/Melbourne"}` with `cron.scheduled_for` → an `agent` template `{scheduled_for}` placeholder.
- `data` — literal `value` with an `output_schema`; output port `value`.
- `document_read` — reads one document directly (no LLM). Fields `kind`, `title`, `document_type`. Input `id` for collection types (e.g. wire `event.id` → `read.id` to load a card's `kanban_task` body), or `trigger` (any value, for sequencing) for single-document types. Outputs `found`, `key`, `id`, `document` (typed with the document type).
- `unpack` — pure step (no LLM) exposing one object's fields. Fields `kind`, `title`. Single input `value`: wire any object port (`event.after`, `read.document`, or another unpack's object field). Output ports are that object's properties, derived on save. Example — card column into a query: `event.after` → `card.value`, `card.column_id` → `peers.column_id`.
- `document_query` — filtered query over a collection type (no LLM). Fields `kind`, `title`, `document_type`, `filters`, optional `order_by`, `order` (`asc`/`desc`, default `asc`), and `limit` (1–200). A filter is `{"field": "column_id", "op": "eq"}`; supported operators are `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `contains`, and `exists`. Each filter is an optional input: an unwired or `null` input does not constrain results. Its port is the field name unless filters repeat a field, when it is `field_op`. `in` takes an array; `contains` matches a substring or an array member; `exists` takes a boolean. Results order by `order_by` with missing values last, then return `items`, `keys`, `count` (returned after limit), and `total` (before limit). With no filters it takes a `trigger` input.
- `document_write` — atomic partial write of one document (no LLM). Fields `kind`, `title`, `document_type`. Input `id` for collection types, plus one optional input port for every top-level property in the document schema that is not `readOnly: true`. Only provided (connected/non-null) fields are written; the document must already exist (never inserts). Outputs `found` and the written fields. Example — set a session title: `{"kind": "document_write", "title": "Rename chat", "document_type": "session"}`.
- `document_create` — atomic insert into a collection type with a schema (no LLM). Inputs are every writable schema property; schema-required writable properties without a schema `default` are required. A defaulted property is optional and an unwired or `null` input gets a deep copy of its schema default; a connected non-null value wins. Optional `id` is used as the key suffix, or a UUID hex id is generated when omitted/empty. The backend fills schema-declared `id`, and sets `created_at`, `updated_at` and `timestamp` to the real creation time (Unix seconds for a number field such as an alert's `timestamp`, ISO text otherwise), validates the complete document, and rejects an existing key. Outputs `id`, `key`, and typed `document`.
- `document_delete` — atomically removes one document from a collection type (no LLM). Input `id`; outputs `found` and typed nullable `before`. Create/delete cannot target a single-document type.
- `format` — deterministic plain-text template rendering (no LLM). Fields `kind`, `title`, `template`; front matter is rejected. Every `{placeholder}` root is a required untyped input port, including `thread_id` when named; output is `text`. Dotted paths work as in prompt templates, strings render verbatim, and other values render as formatted JSON.
- `extract` — fields `kind`, `title`, exactly one of `instruction` (the instruction's path relative to `/instructions/`, e.g. `workflows/card-summary.md` for `/instructions/workflows/card-summary.md`; no leading `/instructions/`) or `template` (inline), optional `schema` (document type id, schema file name, or inline JSON schema). Inputs: one port per `{placeholder}` root, plus optional `thread_id`. Progenitor answers via `structured_output`; output `result` (typed by the schema) and `thread_id`. Schema comes from `schema`, else the template's front matter `output_schema`, else the typed input `result` is wired into; with none, saving fails.
- `agent` — same template fields plus optional `subagent` (a registered sub-agent to delegate to). Outputs `message` (Progenitor's reply text) and `thread_id`.
- `routine` — runs one routine deterministically (no LLM decides whether it runs). Fields `kind`, `title`, `routine` (the routine id, as in `/routines/<id>.json`). Inputs are the routine's `input_schema` fields, or one `message` string when it declares none; outputs its `output_schema` fields, or `message` (its final reply), plus `thread_id` (the routine run's thread). Ports are derived on save; an unknown routine id fails the save. If the routine pauses at a `human` node the workflow run pauses until the routine is answered (e.g. with `resume_routine`).
- Templates are markdown with optional front matter (`title`, `description`, `inputs` mapping placeholder roots to types, `output_schema`, `system`). Placeholders `{card}` / `{card.title}` become input ports; a missing value fails the step. All prompt steps in a run share one thread unless `thread_id` is wired. Reusable templates are instructions: write them under `/instructions/workflows/` (see the maintaining-instructions skill) and reference them by `instruction` path.
- `condition` — deterministic branch with `true`/`false` outputs.
- `approval` — pauses for an approve/reject decision. Fields `kind`, `title`, `message`, optional `approver`: `any` (default; you may decide it with `resolve_workflow_approval` when the user asks) or `human` (only a person, in the UI; the tool refuses).
- `repo` — deterministic git operation on a declared repository (`/config/repos/<id>.json`; no LLM). Fields `kind`, `title`, `action`. Ports by action:
  - `sync`: in `repo_id` → out `repo_id`, `path`, `default_branch`, `head`. Clones `remote_url` into the repo's `local_path` if missing, else fetches.
  - `worktree`: in `repo_id`, `branch`, optional `base` → out `repo_id`, `branch`, `path`, `head`, `created`. One worktree per branch; an existing branch is checked out as is (never reset), a new one starts from `base` or `origin/<default branch>`. Needs `access: "write"`.
  - `publish`: in `repo_id`, `branch`, `title`, optional `body` → out `repo_id`, `branch`, `pushed`, `pr_url`, `pr_number`. Pushes the branch and opens a GitHub pull request, or returns the open one for that branch. Needs `access: "write"`.
  - `remove_worktree`: in `repo_id`, `branch` → out `repo_id`, `branch`, `removed`. Discards uncommitted changes in that worktree.
  A failed git or GitHub call fails the run with the error.
- To alert the user, use `format` to render any dynamic message and wire its `text` output to a `document_create` node for document type `alert`. Supply title and message; alert defaults supply type `info`, `urgent` false, `requires_response` false, and `viewed` false.

ACP tasks (driving Claude and other ACP agents):
- An `acp_task` document is one agent session run by the backend; workflows drive it with the generic document nodes, never an `agent` node. See `/schemas/acp_task.schema.v1.json`.
  - Start: `document_create(acp_task)` with `agent_name` (e.g. `claude`), `prompt`, `cwd` (wire a `repo` worktree `path`), and `card_id`. It starts `queued`.
  - Nudge / continue: `document_write(acp_task)` with `id`, a new `prompt`, and `status` `"queued"`. The next turn resumes the same session; a running turn is cancelled first.
  - Cancel: `document_write(acp_task)` with `id` and `cancel_requested` `true`.
- The backend sets `status` to `working`, then `completed`, `failed`, `canceled`, or `interrupted` (the backend restarted mid-turn; queue another turn to resume). It also keeps `output` (the last reply), `progress` (`last_message`, `plan`, `tool_call_count`, `last_tool`, `last_activity_at`), `turn`, `history`, and `stalled` (true after a long silence while working).
- Do not wait inside one run: end the run after starting the task and react in another workflow. Examples: "turn finished" `{"kind": "event", "document_type": "acp_task", "changed_fields": ["status"], "match": {"after.status": "completed"}}`; "agent went quiet" `{"kind": "event", "document_type": "acp_task", "changed_fields": ["stalled"], "match": {"after.stalled": true}}`. Read the card back with `document_read(kanban_card)` from `after.card_id`.
- Record the link on the card: `document_write(kanban_card)` with `acp_task_id`, `repo_id`, and `branch_name`.

Example — a build stage: `event(kanban_card entered "build")` → `unpack` card → `repo(worktree)` with `branch` from the card → `document_create(acp_task)` with `cwd` = worktree `path` and the plan as `prompt` → `document_write(kanban_card)` with `acp_task_id`. A second workflow on `acp_task` `completed` moves the card to `validate`; a third on `stalled` queues a nudge prompt; a publish workflow runs `repo(publish)` and writes `pr_url` to the card.

Set `"notify_on_pause": true` on the workflow to raise an alert whenever a run pauses at an `approval` node or at a routine's `human` step. Pause alerts are under `/alerts/<id>.json` with a `context` saying what is waiting (`workflow_id`/`run_id`, or a routine `thread_id`) and its `approver`. Answer one only when the user asks you to: `resolve_workflow_approval` for approvals, `resume_routine` for routine steps; `approver: "human"` ones must be answered by the user in the UI.

Prefer `document_read`/`document_query` over an `agent` node whenever a step only needs to look a document up, and a `routine` node over an `agent` node with `subagent` when a step must run a routine.

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
- `document_query`, `document_create`, and `document_delete` require a collection document type
- `document_write` requires a type with at least one schema property that is not `readOnly`
- the graph must be acyclic
- a workflow's own run writes never re-trigger it (runs carry their ancestry)
