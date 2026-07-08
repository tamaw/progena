# Backend-Native Workflows via Postgres State, Virtual Files, Schemas, and Skill Editing

## Summary

Build workflows as a backend-owned runtime feature with Postgres as the canonical store, and expose workflow definitions to Progenitor as virtual JSON files under a new `/state/workflows/` mount. The frontend becomes a live editor/viewer over backend state, not the execution engine. Progenitor can create and modify workflows without the frontend by reading the workflow files, reading JSON Schemas from `/schemas/`, and using a new workflow-management skill that calls `structured_output` and writes back through the mounted virtual files.

For v1, keep the executable graph intentionally small:
- `event` nodes as entrypoints, initially only kanban-backed event types
- `prompt` nodes as sequential agent calls
- single-thread continuation across connected prompt nodes via persisted `thread_id`
- multiple workflows supported
- pause/resume supported per workflow definition and per workflow run

The plan artifact is this file at project root:
- `WORKFLOW_BACKEND_PLAN.md`

## Key Decisions and Defaults

- Canonical workflow definitions live in Postgres-backed state, not checked-in repo files.
- Agent visibility/editability is provided by a new virtual file directory mount: `/state/workflows/<workflow-id>.json`.
- Backend execution reads workflow definitions directly from the same Postgres-backed documents; the frontend is not required for create/edit/run.
- Schemas stay in `data/schemas/` and are mounted at `/schemas/` as they are today.
- A new Progenitor skill owns workflow creation/editing, following the existing `managing-schedules` pattern.
- Each workflow definition is one independently addressable JSON file/document, not one giant `workflows.json` blob.
- Runtime state is separated from workflow definition state.
- v1 execution semantics are single-path and deterministic: one `event` trigger fans into connected `prompt` nodes; no branching conditions, joins, loops, or parallel node execution yet.
- One active run per workflow by default; additional triggers queue behind it. Different workflows may run concurrently.
- No migration/compat shim should be added for any pre-existing browser-only workflow shape. Replace the old browser-local model directly.

## Implementation Changes

### 1. Workflow definition storage and agent mount

Add a new backend/runtime storage surface for workflow definitions:
- New Postgres-backed document family keyed by workflow id, e.g. `workflow:<id>`.
- New virtual file backend mounted at `/state/workflows/`.
- Each file path maps one-to-one to one workflow definition, e.g. `/state/workflows/customer-followup.json`.
- Add list/read/write/edit support similar in spirit to the existing task and inbox directory mounts, but with JSON documents rather than markdown frontmatter.

Define workflow definition shape for v1:
- Top-level fields:
  - `id`
  - `title`
  - `description`
  - `enabled`
  - `paused`
  - `version`
  - `trigger`
  - `graph`
  - `ui`
  - `updated_at`
  - `created_at`
- `trigger`:
  - `type: "event"`
  - `event_type`
- `graph.nodes`:
  - map keyed by node id
- `graph.edges`:
  - array of `{ from: { node, port }, to: { node, port } }`
- Node kinds:
  - `event`
  - `prompt`
- `prompt` node fields:
  - `kind`
  - `title`
  - `prompt`
  - `agent_name` defaulting to `progenitor`
  - optional `role`
- `ui` holds canvas-only state:
  - node positions
  - node sizes
  - viewport
- Execution-only data must not live in the definition file.

Add runtime-side validation:
- New schema files in `data/schemas/`:
  - `definition.workflow.schema.v1.json`
  - `trigger.workflow.schema.v1.json`
  - `node_event.workflow.schema.v1.json`
  - `node_prompt.workflow.schema.v1.json`
- Add runtime validators in the state backend so writes through `/state/workflows/*.json` are rejected unless valid.
- Validation rules:
  - top-level object required
  - unique node ids implied by graph map keys
  - edge endpoints must reference existing nodes
  - exactly one `event` node for v1
  - `event` nodes have no inbound edges
  - `prompt` nodes may have at most one inbound and one outbound edge in v1
  - graph must be acyclic
  - only allowed event types are current kanban event types
  - unknown node kinds rejected in v1

### 2. Workflow editing skill and schema-driven authoring path

Add a new skill, modeled after `data/skills/progenitor/managing-schedules/SKILL.md`:
- `data/skills/progenitor/managing-workflows/SKILL.md`

Skill responsibilities:
- create a new workflow
- rename a workflow
- add/remove/update nodes
- wire/unwire nodes
- pause/resume workflow definitions
- inspect workflow validity
- update only the targeted workflow file, never regenerate unrelated workflows

Skill workflow:
1. `ls /state/workflows/`
2. `read_file /state/workflows/<id>.json` when editing
3. `read_file /schemas/definition.workflow.schema.v1.json` and relevant node schemas
4. call `structured_output` for one workflow definition or one node payload
5. persist with `edit_file` or `write_file`

Skill rules:
- treat one workflow file as the unit of change
- preserve `id`, `created_at`, and unrelated UI fields unless the user asked to change them
- do not invent runtime execution fields inside the definition
- prefer `edit_file` over full rewrite when changing one node or edge
- use generated workflow ids and node ids rather than ad hoc names
- when creating a workflow, scaffold the minimal valid graph:
  - one `event` node
  - one `prompt` node
  - one connecting edge

This is the core non-frontend management path and must be considered first-class, not a fallback.

### 3. Backend HTTP and SSE surface

Add backend routes for workflow definitions and execution state:
- `GET /api/ui/workflows`
  - returns workflow list for the sidebar/cards
- `GET /api/ui/workflows/content?path=<virtual-path-or-id>`
  - returns one workflow definition
- `PUT /api/ui/workflows/content`
  - persists one workflow definition
- `POST /api/ui/workflows`
  - creates a new workflow
- `DELETE /api/ui/workflows/{workflow_id}`
  - removes one workflow definition
- `POST /api/ui/workflows/{workflow_id}/pause`
- `POST /api/ui/workflows/{workflow_id}/resume`
- `GET /api/ui/workflow-runs`
  - returns active/recent run state
- `POST /api/ui/workflows/{workflow_id}/trigger`
  - manual trigger for testing and operator use

Add SSE events:
- `workflows.snapshot`
- `workflow.updated`
- `workflow.deleted`
- `workflow.run.started`
- `workflow.run.updated`
- `workflow.run.completed`
- `workflow.run.failed`

Frontend and any other backend clients should render from these events the same way tasks and widgets render from backend snapshots today.

### 4. Backend execution engine

Add a backend-side workflow executor service. It should:
- run in the backend process initially
- load definitions directly from the workflow documents
- never rely on browser state
- checkpoint after every node execution
- resume from persisted state

Runtime state documents:
- `workflow_run:<run_id>` for one run
- optional `workflow_queue:<workflow_id>` or queue payload embedded under workflow runtime state

Run-state shape:
- `run_id`
- `workflow_id`
- `status` with values like `queued`, `running`, `paused`, `completed`, `failed`
- `current_node_id`
- `completed_node_ids`
- `thread_id`
- `trigger_event`
- `last_output`
- `last_error`
- `queued_at`
- `started_at`
- `updated_at`
- `completed_at`

Execution semantics for v1:
- trigger resolves workflow by matching `trigger.event_type`
- if workflow is disabled or paused, do not start a run
- if one run is already active for that workflow, enqueue the trigger
- start at the `event` node
- walk the single outgoing chain in topological order
- `event` node emits execution context payload
- first `prompt` node calls existing backend IPC path
- returned `thread_id` is persisted on the run and passed to downstream prompt nodes
- downstream prompt nodes reuse the same `thread_id`
- downstream prompt input is previous node output text plus event context
- after each node:
  - persist run checkpoint
  - emit `workflow.run.updated`
- on failure:
  - mark run failed
  - stop further nodes
  - emit failure event
- on completion:
  - mark run complete
  - emit completion event
  - dequeue next pending trigger for that workflow if present

### 5. Event integration with existing kanban trigger path

Extend the existing kanban event path in backend routes:
- keep `_apply_event_widget_triggers(...)` behavior for event widgets
- add workflow dispatch alongside it, using the same emitted backend event source
- do not create a second independent event ingestion path for the same kanban mutations

Kanban events to support in v1:
- `kanban.card.created`
- `kanban.card.updated`
- `kanban.card.moved`
- `kanban.card.deleted`
- `kanban.card.status_changed`

Event payload contract passed into workflow runs:
- `event_type`
- `timestamp`
- `card`
- optional `previous_card` where available
- source metadata such as `source: "kanban"`

Prompt node input composition default:
- prepend a compact event summary
- include serialized event payload as structured context
- append upstream prompt output when continuing the chain

### 6. Frontend changes

Convert the workflows page from IndexedDB/browser-owned graph state to backend-backed editing:
- remove workflow definition persistence from IndexedDB
- load workflow list from new workflow routes
- load selected workflow definition from backend
- save canvas edits by sending normalized workflow JSON to backend
- subscribe to `workflows.snapshot` and `workflow.updated`
- subscribe to run-status events to show live execution state

Frontend model adapter:
- keep React Flow for rendering/editing
- map workflow definition `graph` + `ui` to React Flow nodes/edges
- map React Flow changes back into workflow definition JSON
- do not persist raw React Flow transient flags

Sidebar behavior:
- replace mock `INITIAL_WORKSPACES`
- list real workflow definitions
- each card links to one workflow file identity, analogous to prompt/skill path-driven selection
- create/delete/rename actions go through backend routes

Node palette for v1:
- show only `event` and `prompt` as insertable executable workflow nodes
- non-executable annotation/group nodes may remain only if they clearly live under `ui` and are excluded from execution semantics
- if that separation is not clean in the current page, exclude non-executable nodes from v1 and reintroduce them later

### 7. Public interfaces and types to add

Add new backend/domain types:
- `WorkflowDefinition`
- `WorkflowTrigger`
- `WorkflowGraph`
- `WorkflowNodeEvent`
- `WorkflowNodePrompt`
- `WorkflowEdge`
- `WorkflowListItem`
- `WorkflowRun`
- `WorkflowRunStatus`

Add new schema/tool/skill-visible contracts:
- `/state/workflows/*.json` virtual files
- `/schemas/definition.workflow.schema.v1.json`
- `managing-workflows` skill

Reuse existing structured-output interface:
- no new bespoke “workflow edit” tool
- use current `structured_output` plus native file tools, matching the current architecture

## Test Plan

### Backend unit tests

- workflow definition validator accepts valid event->prompt chain
- validator rejects cycles, missing node references, unknown kinds, bad event types, and malformed edges
- workflow file backend lists, reads, writes, and deletes one workflow correctly
- workflow routes return stable list/content payloads
- kanban event dispatch matches only enabled, unpaused workflows
- queued trigger behavior works when one run is already active
- pause blocks new runs; resume unblocks them
- run checkpoint persists `thread_id` and node progress after each prompt node

### Backend integration tests

- creating a kanban card starts a matching workflow run
- a two-prompt workflow reuses the same `thread_id` on the second backend IPC call
- failed first prompt marks run failed and stops downstream execution
- paused run resumes from persisted checkpoint rather than restarting
- `workflows.snapshot` and `workflow.run.*` events are broadcast to SSE listeners
- agent-side edits to `/state/workflows/<id>.json` are reflected in backend reads without frontend involvement

### Frontend tests

- workflow sidebar renders backend-provided list instead of mock data
- selecting a workflow loads its backend definition
- canvas edits serialize back to the workflow definition shape
- live SSE update refreshes an open workflow without full reload
- run status badges update from `workflow.run.*` events
- no IndexedDB dependency remains for workflow definitions

### Acceptance scenarios

- user creates a workflow in the frontend canvas, saves it, and a kanban event triggers it without the page being open
- Progenitor creates a workflow entirely through `/state/workflows/` and `/schemas/` with the `managing-workflows` skill
- Progenitor edits an existing workflow and the frontend reflects the change live
- two prompt widgets wired in sequence continue the same thread
- multiple workflows can listen to the same kanban event and run independently
- one workflow can be paused while others still execute

## Assumptions and Defaults

- v1 excludes branching, joins, retries, conditional routing, loops, and parallel node execution.
- v1 canonical execution nodes are only `event` and `prompt`.
- Existing browser-only workflow state is treated as disposable; no compatibility layer is added.
- Workflow definitions are stored in Postgres-backed runtime state and exposed as virtual files, not committed source files.
- The existing `structured_output` tool, `/schemas/` mount, and skill-driven edit model are reused as-is rather than replaced with a custom workflow authoring tool.
