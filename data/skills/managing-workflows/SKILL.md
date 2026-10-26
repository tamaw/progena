---
name: managing-workflows
description: Create, inspect, update, pause/resume, wire, or delete backend-native workflow definitions by editing one file under /workflows/ at a time. Use when a user asks to create or modify a workflow that reacts to document create/update/delete events (kanban cards, inbox messages, workflow runs, ACP tasks, repos, preferences, ...) and runs agent, routine, condition, approval, or direct document nodes.
allowed-tools: read_file write_file edit_file delete ls structured_output generate_id current_time
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
7. Never hand-write `input_schema`/`output_schema` for `event`, `cron`, `document_read`, `document_query`, `document_write`, `document_create`, `document_delete`, or `format` nodes: the server derives them on save. `manual` nodes are the exception: author their `output_schema` as the typed manual-run payload. Wire edges to the port names listed below.
8. To delete a workflow, read it first, then call `delete` with its exact
   `/workflows/<workflow-id>.json` path.

Node names and canvas layout:
- Give every node a short, meaningful `title`, normally two to four words:
  `Batch settings`, `Run batch`, `Format results`, or `Notify operator`.
  Name its purpose rather than repeating its kind (`Routine`, `Format`) or
  using numbered placeholders. Distinguish parallel branches with concise
  qualifiers. Keep long instructions in templates, not titles.
- Node titles are display text. Preserve graph node IDs, edge endpoints, port
  names, routine references, and executable configuration when renaming or
  arranging nodes. Routine nodes inside `/routines/` use `label`, not `title`;
  see the managing-routines skill.
- Persist a position for every graph node at
  `ui.nodes[<node-id>].position: {"x": 0, "y": 0}`. These are canvas coordinates,
  not fields on `graph.nodes[<node-id>]`. Increase `x` from sources to outcomes;
  use separate `y` lanes for parallel branches and align their joins downstream.
  Put independent triggers in separate lanes and supporting literal-data nodes
  near their consumers. Minimize crossing edges and edges through other cards.
- Space actual card rectangles, not just their origins. Start with roughly
  480 horizontal units between ordinary columns and at least 80 units of clear
  vertical space between cards, then increase spacing for tall/wide cards.
  Port-heavy manual/routine nodes can be much taller than ordinary nodes:
  the editor's automatic data-view height is `69 + (maxPorts - 1) * 28`,
  or the node-kind minimum if larger; saved sizes can override it. A 29-port
  node without a saved size needs 853 units of height, so a fixed 300-unit
  lane spacing would overlap it.
  Unpack nodes are the exception: their height follows the fields they show,
  `69 + shownFields * 28` (at least 120), not every property of the unpacked object.
  Account for saved `ui.nodes[id].size.data` and `.size.settings` width/height
  when present; don't shrink cards or hide ports to make the layout fit.
- The canvas is two-dimensional: there is no persisted node `z` coordinate or
  layering workaround for overlaps. `ui.viewport` uses `x`, `y`, and `zoom`
  for pan/scale, not node placement. Preserve an existing viewport and unrelated
  UI metadata unless reframing is requested; zooming out does not fix overlap.
  Saving in the workflow editor rewrites each `ui.nodes[id]` to only `position`,
  `size`, and unpack `show_all_fields`, so don't store other per-node keys there.

  Layout fragment (keys must be the graph's actual node IDs):

  ```json
  {
    "ui": {
      "nodes": {
        "node_settings": {"position": {"x": 0, "y": 0}},
        "node_batch": {"position": {"x": 480, "y": 0}},
        "node_results": {"position": {"x": 960, "y": 0}},
        "node_alert_title": {"position": {"x": 960, "y": 400}},
        "node_notify": {"position": {"x": 1440, "y": 0}}
      },
      "viewport": {"x": 48, "y": 80, "zoom": 0.75}
    }
  }
  ```

  This is a compact-card example, not a universal spacing rule. Generate real
  node IDs for new graphs; do not copy the illustrative IDs into a definition.
- Include titles and complete positions in `structured_output` when creating
  or reshaping a workflow. Preserve a readable existing layout during focused
  edits. A node without a saved position renders at `{"x": 456, "y": 160}`, so
  give every new node its own position instead of piling them up there.
- After saving, read the definition back: every graph node should have a useful
  title and finite numeric `x`/`y` coordinates under `ui.nodes`, with separated
  card bounds. If the editor is accessible, open it and check labels, ports,
  branch lanes, joins, and overlaps at a readable zoom. Otherwise report that
  the persisted layout was checked but visual appearance was not verified.

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
- `cron` — time entrypoint run by the backend clock; no inbound edges. Fields `kind`, `title`, `expression` (5-field cron, e.g. `0 9 * * 1-5`), `timezone` (IANA, e.g. `Europe/London`), `overlap` (`skip` default: drop a fire while the workflow still has an active run; `queue`: run it afterwards). Outputs `scheduled_for`, `fired_at` (ISO date-times in the node's timezone), `timezone`. A fire missed while the backend was down runs once on restart; a paused workflow does not replay missed fires. This is for workflows only — to remind yourself or the user later, use your own schedules (`/config/schedule.json`), not a workflow.
  Example — weekday digest: `{"kind": "cron", "title": "Weekdays 9am", "expression": "0 9 * * 1-5", "timezone": "Europe/London"}` with `cron.scheduled_for` → an `agent` template `{scheduled_for}` placeholder.
- `manual` — an on-demand-only entrypoint; no inbound edges and it never responds to document events or the cron clock. Its required `output_schema` is an object schema whose properties are output ports and whose property defaults are applied to omitted manual-run payload fields. `run_workflow` and the browser Run action choose the first manual node when one exists; otherwise they retain the existing first `event`/`cron` fallback. Example: `{"kind": "manual", "title": "Run round", "output_schema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "string"}, "round_count": {"type": "integer", "default": 3}}}}`.
- `data` — literal `value` with an `output_schema`; output port `value`.
- `document_read` — reads one document directly (no LLM). Fields `kind`, `title`, `document_type`. Input `id` for collection types (e.g. wire `event.id` → `read.id` to load a card's `kanban_task` body), or `trigger` (any value, for sequencing) for single-document types. Outputs `found`, `key`, `id`, `document` (typed with the document type).
- `unpack` — pure step (no LLM) exposing one object's fields. Fields `kind`, `title`. Single input `value`: wire any object port (`event.after`, `read.document`, or another unpack's object field). Output ports are that object's properties, derived on save. Example — card column into a query: `event.after` → `card.value`, `card.column_id` → `peers.column_id`.
- `document_query` — filtered query over a collection type (no LLM). Fields `kind`, `title`, `document_type`, `filters`, optional `order_by`, `order` (`asc`/`desc`, default `asc`), and `limit` (1–200). A filter is `{"field": "column_id", "op": "eq"}`; supported operators are `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `contains`, and `exists`. Each filter is an optional input: an unwired or `null` input does not constrain results. Its port is the field name unless filters repeat a field, when it is `field_op`. `in` takes an array; `contains` matches a substring or an array member; `exists` takes a boolean. Results order by `order_by` with missing values last, then return `items`, `keys`, `count` (returned after limit), and `total` (before limit). With no filters it takes a `trigger` input.
- `document_write` — atomic partial write of one document (no LLM). Fields `kind`, `title`, `document_type`. Input `id` for collection types, plus one optional input port for every top-level property in the document schema that is not `readOnly: true`. Only provided (connected/non-null) fields are written; the document must already exist (never inserts). Outputs `found` and the written fields. Example — set a session title: `{"kind": "document_write", "title": "Rename chat", "document_type": "session"}`.
- `document_create` — atomic insert into a collection type with a schema (no LLM). Inputs are every writable schema property; schema-required writable properties without a schema `default` are required. A defaulted property is optional and an unwired or `null` input gets a deep copy of its schema default; a connected non-null value wins. Optional `id` is used as the key suffix, or a UUID hex id is generated when omitted/empty. The backend fills schema-declared `id`, and sets `created_at`, `updated_at` and `timestamp` to the real creation time (Unix seconds for a number field such as an alert's `timestamp`, ISO text otherwise), validates the complete document, and rejects an existing key. Outputs `id`, `key`, and typed `document`.
- `document_delete` — atomically removes one document from a collection type (no LLM). Input `id`; outputs `found` and typed nullable `before`. Create/delete cannot target a single-document type.
- `format` — deterministic plain-text template rendering (no LLM). Fields `kind`, `title`, `template`; front matter is rejected. Every `{placeholder}` root is a required untyped input port, including `thread_id` when named; output is `text`. Dotted paths work as in prompt templates, strings render verbatim, and other values render as formatted JSON.
- `extract` — fields `kind`, `title`, exactly one of `instruction` (the instruction's path relative to `/instructions/`, e.g. `workflows/card-summary.md` for `/instructions/workflows/card-summary.md`; no leading `/instructions/`) or `template` (inline), optional `schema` (document type id, schema file name, or inline JSON schema). Inputs: one port per `{placeholder}` root, plus optional `thread_id`. Progena answers via `structured_output`; output `result` (typed by the schema) and `thread_id`. Schema comes from `schema`, else the template's front matter `output_schema`, else the typed input `result` is wired into; with none, saving fails.
- `agent` — same template fields plus optional `subagent` (a registered sub-agent to delegate to). Outputs `message` (Progena's reply text) and `thread_id`.
- `routine` — runs one routine deterministically (no LLM decides whether it runs). Fields `kind`, `title`, `routine` (the routine id, as in `/routines/<id>.json`). Inputs are the routine's `input_schema` fields, or one `message` string when it declares none; outputs its `output_schema` fields, or `message` (its final reply), plus `thread_id` (the routine run's thread). Ports are derived on save; an unknown routine id fails the save. If the routine pauses at a `human` node the workflow run pauses until the routine is answered (e.g. with `resume_routine`).
- Templates are markdown with optional front matter (`title`, `description`, `inputs` mapping placeholder roots to types, `output_schema`, `system`). Placeholders `{card}` / `{card.title}` become input ports; a missing value fails the step. All prompt steps in a run share one thread unless `thread_id` is wired. Reusable templates are instructions: write them under `/instructions/workflows/` (see the maintaining-instructions skill) and reference them by `instruction` path.
- `condition` — deterministic branch (no LLM). Fields `kind`, `title`, `expression`, and `input_schema`: unlike the derived kinds, you write it, declaring the ports the expression reads (e.g. `{"type": "object", "required": ["decision"], "properties": {"decision": {"type": "string"}}}`). `expression` is an input port path (truthy test, e.g. `ready`) or one comparison `path == value` / `path != value` with a JSON value (e.g. `decision == "approved"`). It emits only the selected port, `true` or `false`, as a boolean.
  - Gating: a node runs only when every connected input has produced a value. A condition's unselected port never produces one, so nodes wired from it, and everything downstream of them, do not run. To gate a node on a condition, wire `true` or `false` into one of its ports of a compatible type (boolean). A node with no such port (e.g. a `document_write` to a type without boolean fields) cannot be gated by a condition. Branch on triggers instead.
  - Several triggers: a workflow may have more than one `event`, `cron`, or `manual` node. A run starts from the trigger that fired; an explicit run prefers the first manual node in graph order, then falls back to the first event/cron node. Nodes fed only by other triggers do not run. One trigger per case is usually simpler than a condition, e.g. two `inbox_message` event nodes with `changed_fields: ["decision"]` and `match` `{"after.decision": "approved"}` and `{"after.decision": "rejected"}`, each feeding its own nodes. `match` cannot test that a field merely exists; when a value such as `task_id` may be missing, a step that requires it fails that run, which is visible in the workflow's run history.
- `approval` — pauses for an approve/reject decision. Fields `kind`, `title`, `message`, optional `approver`: `any` (default; you may decide it with `resolve_workflow_approval` when the user asks) or `human` (only a person, in the UI; the tool refuses).
- To alert the user, use `format` to render any dynamic message and wire its `text` output to a `document_create` node for document type `alert`. Supply title and message; alert defaults supply type `info`, `urgent` false, `requires_response` false, and `viewed` false.

ACP tasks (driving Claude and other ACP agents) — orchestrate, never babysit:
- Never start or wait on an ACP agent from an `agent`/`extract` step with
  `acp_status`. Every poll is a full model turn that resends your whole
  context, so a 20-minute Claude run costs dozens of large turns. The agent does
  the work; you only decide at its turn boundaries.
- An `acp_task` document is one agent session run by the backend; see
  `/schemas/acp_task.schema.v1.json`. Starting, steering and reacting are plain
  document nodes, so none of it calls a model.
- Split each stage into two workflows:
  1. Kick-off: trigger (e.g. card moved to `build`) → `format` the prompt from
     the card → `document_create(acp_task)` with `agent_name` (e.g. `claude`),
     `prompt`, `card_id`, `request_metadata` from a `data` node such as
     `{"stage": "build"}`, and optionally `cwd` (a repo-manager worktree path)
     and `mode`. It starts `queued`; the backend runs it. Optionally
     `document_write` the card (status `running`, the created task `id`).
  2. React: `event` on `acp_task` with `changed_fields: ["status"]`, one
     trigger per outcome, each also matching `"after.request_metadata.stage"`
     so only this stage's tasks (not chat-started ones) fire it. Then
     `document_read` the card by `after.card_id`:
     - `"after.status": "completed"`: the agent finished the turn. Its reply is
       `after.output`. Feed only that (and the card) to one short `extract`
       step to decide what happens next, or route it without a model.
     - `"after.status": "failed", "after.failure_kind": "usage_limit"`: the
       agent's account hit its session/usage/rate limit. Stop: mark the card
       `blocked` with `after.last_error`; retry only after it resets.
     - `"after.status": "failed", "after.failure_kind": "error"`: any other
       failure. Mark the card `failed` with `after.last_error`.
- Steer: to give the agent its next instruction, `document_write` the task with
  a new `prompt` and `status: "queued"`. The next turn resumes the same session,
  and its completion fires the react workflow again. To change course while a
  turn is still running, append a pending `messages` entry instead (the
  supervisor injects it into that turn).
- Questions: `pending_input` is set when the agent needs a question or plan
  answered; it stays blocked until then. React with
  `{"kind": "event", "document_type": "acp_task", "changed_fields": ["pending_input"]}`
  and write `answers[input_id]`; `cancel_requested: true` cancels.
- The backend sets `status` to `working`, then `completed`, `failed`,
  `canceled`, or `interrupted`, and records `output`, `last_error`,
  `failure_kind`, progress, turn, history, and `stalled`.

Set `"notify_on_pause": true` on the workflow to raise an alert whenever a run pauses at an `approval` node or at a routine's `human` step. Pause alerts are under `/alerts/<id>.json` with a `context` saying what is waiting (`workflow_id`/`run_id`, or a routine `thread_id`) and its `approver`. Answer one only when the user asks you to: `resolve_workflow_approval` for approvals, `resume_routine` for routine steps; `approver: "human"` ones must be answered by the user in the UI.

Prefer `document_read`/`document_query` over an `agent` node whenever a step only needs to look a document up, and a `routine` node over an `agent` node with `subagent` when a step must run a routine.

Recommended flow for a new workflow:
1. `ls /workflows/`
2. `read_file /schemas/definition.workflow.schema.v1.json`
3. Generate ids for the workflow and its nodes.
4. One `structured_output` call with `schema_file: definition.workflow.schema.v1.json` (not an inline `json_schema`), describing every node and edge — this produces the complete, schema-valid definition in one shot.
5. `write_file /workflows/<workflow-id>.json`
6. If the save is rejected, the error names the problem (unknown port, missing edge, bad type ref, unknown routine id, ...). Fix it — `edit_file` for a local fix, another `structured_output` for rewiring — and write again. A rejection is feedback, not a blocker: do not stop or report until the workflow saves, unless the same error repeats with no fix left to try. Templates the workflow references must save first (see maintaining-instructions).
7. Read the saved file back and check titles and layout as described under "Node names and canvas layout".

Recommended flow for editing an existing workflow:
1. `ls /workflows/`
2. `read_file /workflows/<workflow-id>.json`
3. If only one field changes, update it with `edit_file` directly.
4. If several nodes or edge wiring change together, read the complete definition schema and use `structured_output` to produce the whole updated definition, then persist only that workflow file.
5. Read the saved file back; when nodes were added, moved, or renamed, check titles and layout as described under "Node names and canvas layout".

Validation reminders:
- at least one trigger (`event`, `cron`, or `manual`) is required; trigger nodes cannot have inbound edges
- `cron` needs a valid 5-field `expression` and a real IANA `timezone`
- every non-source node needs an inbound edge, and every required input port needs exactly one edge
- edge ports must exist on both nodes and have compatible types
- `match` keys must be dotted paths starting at an event output port (e.g. `after.column_id`); `changed_fields` must be document fields
- `document_query`, `document_create`, and `document_delete` require a collection document type
- `document_write` requires a type with at least one schema property that is not `readOnly`
- the graph must be acyclic
- a workflow's own run writes never re-trigger it (runs carry their ancestry)
