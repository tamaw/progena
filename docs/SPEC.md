# Progena AI System Specification

This document describes the checked-in main system: the Progena runtime, the
backend API, and the React frontend. The browser API contract is defined
normatively in [BACKEND.md](BACKEND.md).

## Purpose

Progena is a long-running conversational system. The runtime maintains
conversation state and executes the main agent graph. The frontend submits work
to the backend and receives browser application data through two SSE roles:

- one canonical lifecycle/state stream for the whole app
- one request-scoped response-content stream for each active streaming chat
  request

## Core components

### Runtime (`src/progena/`)

- Runs the perpetual conversation service.
- Restores and checkpoints runtime state.
- Builds the DeepAgent-backed conversation graph.
- Processes scheduled work through the same core path as user input.
- Exposes an OpenAI Responses/Conversations-API-compatible HTTP contract to the
  backend; internally every endpoint is native LangGraph (`graph.astream`,
  `graph.aget_state`, `checkpointer.adelete_thread`):
  - `POST /v1/responses` runs one turn. `conversation` (string or `{id}`) is the
    LangGraph `thread_id`; without one the turn runs in a fresh isolated
    `conv_<uuid>` conversation, returned as `response.conversation.id`. The
    backend names Telegram turns `tele_<chat_id>[_<topic_id>]` so each Telegram
    chat keeps its history. `metadata.request_id` becomes the response id. With
    `stream: true` it emits `response.created` / `response.queued` /
    `response.in_progress`, `response.output_item.*`,
    `response.content_part.*`, `response.output_text.delta|done`, then
    `response.completed` (response `output` = model items added this turn),
    `response.failed`, or `response.incomplete` with status `cancelled`.
  - `POST /v1/responses/{id}/cancel` cancels an in-flight response (404 when
    none). The text streamed so far is saved to the conversation as an
    assistant message marked `response_metadata.status = "cancelled"` (written
    as the agent's `model` node via `aupdate_state`), so it stays in history,
    shows as `… [Cancelled]` in the UI, and is context for the next turn. Its
    items carry status `incomplete` and are the cancelled response's `output`.
  - `GET /v1/conversations/{id}/items` lists the thread's messages as Responses
    items (`message`, `reasoning`, `function_call`, `function_call_output`) with
    `after` / `limit` (1–100) / `order` paging.
  - `DELETE /v1/conversations/{id}` deletes the thread's checkpoints and its
    runtime `current_state` document (409 while a turn is in flight).
  - The backend reads LangGraph checkpoints directly to derive pending steps for
    interrupted chat and routine displays; thread-state inspection is not a
    runtime HTTP endpoint.
  - Vendor extensions outside the OpenAI spec: `progena.stream_event` SSE
    events carry the runtime's tool/reasoning/subagent activity; the
    `include=progena.langchain_message` items option adds
    `source_message_id` to every item and the serialized LangChain message to
    the first item of each message in the page; the `checkpoint_id` query
    parameter reads a specific checkpoint; `metadata` values may be
    non-strings.
- `GET /v1/events` broadcasts runtime agent events to the backend.

### JavaScript execution

The main conversational agent exposes `javascript(code)` through Deep Agents'
official `CodeInterpreterMiddleware` (`langchain-quickjs`). QuickJS-NG runs as a
Wasm guest in the local Wasmtime runtime. No hosted sandbox, service account or
sandbox API key is required.

- Calls share scratch variables within one conversation turn. A new turn starts
  with a fresh context, separate from other conversations; no JS session
  snapshot is stored in checkpoints.
- The JS heap is capped at 64 MiB and execution at five seconds per call.
  Results and captured console output are each truncated to 4,000 characters.
  These are interpreter limits, not a whole-process memory or wall-time bound.
- The `tools` bridge allowlists `ls`, `read_file`, `glob`, `grep`, `list_agents`,
  `a2a_send`, `a2a_send_async`, `a2a_status` and `a2a_list`. JavaScript uses their
  camelCase names, such as `await tools.readFile(...)` and
  `await tools.a2aSend(...)`. Reads retain the existing backend permissions;
  communication uses the existing registered-agent and tracked-task contracts.
- Top-level `await task(...)` dispatches configured object agents and routines
  through Progena's live task resolution, including targets added after graph
  compilation. The SDK's built-in `general-purpose` delegate remains available.
- No direct filesystem, network, shell, Node.js packages or browser APIs are
  exposed. Write/edit/execute, arbitrary MCP tools, agent registration and A2A
  cancellation are not in the JavaScript tool allowlist. Delegated agents retain
  their own permissions and may perform writes or shell work they already own.
- Bridge calls bypass the parent ToolNode's per-operation approval/interceptor
  path. Backend validation and approvals inside delegated agents/routines still
  apply. Use normal parent tool calls when individual parent approval is needed.
  The five-second JS budget does not bound host-tool or delegated-agent waits.
  Upstream limits each evaluation to 256 PTC tool calls and 32 simultaneous
  subagent dispatches; the latter is a concurrency cap, not a total task budget.
- Results can be persisted through normal validated document tools outside
  JavaScript. The interpreter does not replace or mount over virtual paths.
- A routine `code` node is separate from this conversational interpreter. It
  accepts only inline `{"language":"javascript","code":"..."}` source, receives
  JSON-compatible routine state as `input`, and returns one JSON-compatible
  value to its `output_key`. Each execution creates and disposes a fresh
  QuickJS runtime with a 16 MiB heap, 512 KiB stack, one-second JS deadline,
  and 1 MiB serialized-result cap. It exposes no tools, host functions,
  modules, network, filesystem, or shell;
  sandbox and JSON-boundary failures fail the node.
  The SDK lifecycle runs in a worker thread to keep the event loop and graph
  timeout watchdog responsive. Cancelling the awaiting node does not kill
  the worker; its independent one-second guest deadline still bounds execution.
  The host also checks serialized UTF-8 bytes before accepting the result.
  The JavaScript editor reuses graph-state autocomplete with `input`-rooted
  dot/bracket references, rather than Python expression or prompt-template
  syntax. It suggests declared routine inputs and predecessor output keys/
  response fields; script results have no inferred field schema. Source is
  never message-template interpolated, so object braces and JavaScript
  `${...}` template literals retain their normal meaning.

The upstream interpreter integration is beta and `quickjs-rs` is experimental.
Wasm is an in-process isolation boundary, not a separate OS sandbox: Wasmtime/JIT
vulnerabilities remain a host-escape risk. Keep Wasmtime updated and add isolated
workers/containers for hostile multi-tenant execution. See the
[upstream security guidance](https://github.com/langchain-ai/quickjs-rs#security)
and [Deep Agents interpreter documentation](https://docs.langchain.com/oss/python/deepagents/interpreters).

### Routine execution controls

The routine toolbar reuses one icon action: Run, Stop while an exact execution
attempt is active, a spinner while stopping, and Resume after confirmed stop.
If stopping is still pending after five seconds, the action offers Cancel run.
Queued attempts without an execution identity are not presented as stoppable.
The START parameter modal's Cancel only dismisses that modal.

Stop cancels the native asynchronous LangGraph execution task and preserves its
checkpoints. Pending `stopping`/`cancelling` intent is durable; `stopped` and
`cancelled` are recorded only after the owned task terminates. Each `started`
lifecycle records the runtime process that owns it (`runtime_instance`). A run
still `started`/`stopping`/`cancelling` whose owner is an earlier runtime
process (or that predates ownership records) has no executor after a restart:
Stop settles it `stopped` (resumable from its last checkpoint) and Cancel
settles it `cancelled`, unless its session's control already belongs to another
attempt. Unknown attempts, terminal or paused runs, and runs owned by the current
process without a live task return a conflict. No timestamp-based stale-run
detection or automatic replay is performed. Stopping a nested routine unwinds
its root execution and settles the affected active descendants; parent
settlement must not continue as success.

Resume submits a new attempt with the same `session_id` and exact
`resume_thread_id`. It uses the pinned graph revision, recorded policy and
retained inputs with native `ainvoke(None, ...)`, not a fresh seed or the edited
live graph. Human approval pauses still require their explicit answer path.
An unfinished node may replay, including an unfinished nested-routine call:
completed external effects are not rolled back and must be replay-safe.

Cancel closes that logical routine/session permanently within Progena; its
durable guard rejects resume, node stepping, ordinary session reuse and approval
continuation. Checkpoints/history remain inspectable and a separate fresh Run
uses a new session. Cancelling graph execution does not itself confirm that an
external A2A job, subprocess or background thread has stopped.

The backend command is
`POST /api/ui/agent-profiles/{agent_id}/run-control` with
`{thread_id, request_id, action: "stop" | "cancel"}`. Its operation ID is separate
from the targeted attempt ID. `agent.routine.control.updated` acknowledges the
command with the operation ID as its `request_id` and the targeted attempt's
lifecycle under `run`; persisted lifecycle events carry execution outcomes.
A Resume claims the stopped checkpoint (`resuming`) when it is enqueued, so a
second Resume is rejected; a resume that fails before starting releases the
claim. The runtime
implements the native task ownership boundary at
`POST /v1/routine-runs/control`.

An executing run whose owner is gone (an earlier `runtime_instance`, or a
pre-ownership run started before the live runtime) is presented as
`interrupted`; this is derived on read from the health document's live
`runtime_instance` and never persisted. Its stopped step is LangGraph's pending
`next`, read through the runtime. Run on an interrupted run offers Continue
(`POST /api/ui/agent-profiles/{agent_id}/run-continue` with
`{thread_id, request_id}`: settle the orphan `stopped`, then the normal Resume),
Start again (cancel the orphan, then a new session seeded with the first
attempt's `inputs`, recorded on its `started` lifecycle), or Cancel run. An
older interrupted run picked from run history shows the same step marker, and
Run on it adopts that run before offering the same choices. Scheduled runs stamp
`runtime_instance` when they start running, and their recent runs show the
same derived `interrupted` status.

### Backend (`src/backend/`)

- Exposes the browser's finite command/query routes and bridges work to the
  runtime.
- Discovers chats from the checkpoint tables by SQL, but reads chat messages
  only through the runtime's conversation items endpoint, cached per
  `(thread_id, checkpoint_id)`. Reads run off the event loop with pooled
  connections. While the runtime is unreachable the last copy seen of a chat
  is served; a chat never read before lists with `history_unavailable: true`
  and its detail returns 503 instead of an empty chat. A deleted chat stays
  deleted even while live runtime state for it exists.
- Owns the canonical `GET /api/ui/stream` SSE transport for connection
  lifecycle, retained state, and chat response lifecycle events.
- Owns the dedicated `GET /api/ui/responses/{request_id}/stream` SSE transport
  for one accepted streaming chat response's content frames.
- Persists or dispatches a finite operation, enqueues its causal canonical
  events, then returns an HTTP `202 Accepted` acknowledgement with exactly
  `{request_id,event_types}`.
- Keeps successful finite HTTP responses data-free; browser-visible results come
  from canonical events or the dedicated response-content stream.

### Frontend (`src/frontend/`)

- Provides the React/Vite web client.
- Opens one app-lifetime canonical `EventSource` to `/api/ui/stream`.
- Opens a second, request-scoped `EventSource` to
  `/api/ui/responses/{request_id}/stream` only while a streaming chat response
  is active.
- Uses Redux as the sole durable application-data owner. Canonical stream
  events hydrate Redux; pages read Redux selectors.
- Generates an `X-Operation-Id` UUID for every finite application request and
  waits for matching causal canonical stream events after acknowledgement.
- Renders agent-built canvases (`/canvases`) in sandboxed, network-less iframes
  (`sandbox="allow-scripts"`, `srcdoc`); canvas files, vendored libraries,
  documents and events reach the frame only through the parent's postMessage
  bridge over the canonical stream (`docs/CANVASES.md`).

There are no browser `/api/ui/events` or `/api/ui/status` streams, no
response-body fallback, and no inline response-body token SSE.

## Request and event flow

1. The frontend creates one operation UUID and sends it in `X-Operation-Id`.
2. The backend validates, persists or synchronously dispatches the operation,
   then enqueues every causal canonical `ui.event` envelope with the UUID at
   `payload.request_id`.
3. The backend returns HTTP `202 Accepted` with only
   `{request_id, event_types}`.
4. The canonical stream delivers lifecycle/state event data. Redux applies an
   event idempotently and resolves only the pending operation with the same
   request ID and declared event type.
5. For browser chat requests with `stream: true`, the frontend also opens
   `GET /api/ui/responses/{request_id}/stream` and consumes only that request's
   replayable `chat.response.delta` and `chat.response.event` frames there.
6. Components render selectors and live chat callbacks; they do not hydrate from
   acknowledgement data.

HTTP `202` means dispatched and accepted, not delivered or rendered. HTTP
failures keep their non-`202` status and produce no success event. Connection
loss also does not complete an operation: the frontend applies its explicit
reconnect or timeout policy.

## Reasoning timeline lifecycle

Reasoning is streamed as `reasoning_delta` events while a response is being
generated, followed by one terminal `reasoning_summary` event after generation
finishes. The frontend keeps one reasoning timeline item and updates its
accumulated content for each delta; it does not create one card per fragment.
The terminal summary replaces that item's presentation and resets its
disclosure to the collapsed state. If a summary is received before a delta,
the summary is authoritative and subsequent deltas are ignored for display.

The live item is positioned using the timestamp of the first reasoning event,
so reasoning remains chronologically before tools that were invoked afterward.
Persisted reconstruction uses the same timestamp-aware ordering. Header
reasoning totals use provider-reported reasoning-token usage when available and
show a `~`-prefixed text estimate only when persisted/live usage is absent.

## Canonical stream

`GET /api/ui/stream` returns `text/event-stream`. Every frame uses SSE event
name `ui.event` and carries a `UiEventEnvelope`:

```json
{
  "id": "ui-42",
  "seq": 42,
  "type": "chat.sessions.snapshot",
  "scope": "chat",
  "session_id": null,
  "payload": { "request_id": "operation-uuid" },
  "timestamp": "2026-01-01T00:00:00+00:00"
}
```

The server retains a bounded in-memory replay window (currently 200 broadcast
events). On reconnect it replays retained events after `Last-Event-ID`, in
sequence order, then emits `connection.ready`; it cannot guarantee recovery
when the cursor predates the bounded buffer. Clients deduplicate by envelope ID
and request fresh correlated snapshots when their state cannot safely continue.
Idle streams emit `connection.heartbeat`; graceful shutdown emits the canonical
`ui.event` envelope `connection.shutdown`, not a separate `server.shutdown`
SSE event.

Representative canonical domain types include chat session events, chat
response lifecycle events (`started`, `completed`, `failed`, `cancelled`),
agent and inbox snapshots, knowledge search results, workflow/run events,
kanban/task updates, widget/MCP/schedule snapshots, prompt/skill snapshots,
alerts, and log page/export chunk events. See [BACKEND.md](BACKEND.md) for
route-to-event disposition.

## Dedicated response stream

`GET /api/ui/responses/{request_id}/stream` is a request-scoped SSE transport
for one accepted browser chat response. It does not carry `UiEventEnvelope`
frames. Instead it emits direct response payloads on these SSE event names:

- `chat.response.delta`
- `chat.response.event`
- `chat.response.heartbeat`
- `chat.response.resync_required`

Only `chat.response.delta` and `chat.response.event` are replayable. Their SSE
IDs are `chat-response:{request_id}:{seq}` and the backend retains a bounded
per-request replay buffer (currently 1024 frames). `Last-Event-ID` takes
precedence over the `after` query parameter for replay. Invalid or expired
cursors cause exactly one `chat.response.resync_required` frame and then stream
closure.

The dedicated stream owns response content transport only. It never carries
`chat.response.started`, `chat.response.completed`, `chat.response.failed`, or
`chat.response.cancelled`; those remain canonical lifecycle envelopes on
`/api/ui/stream`.

## Workflow triggers

Workflow graphs start from `event`, `cron`, or `manual` trigger nodes. A manual
trigger is never dispatched by document events or scheduling; its object
`output_schema` is the typed explicit-run payload and property defaults are
applied before it reaches connected ports. For browser and `run_workflow`
invocations, the backend chooses the first manual trigger when present, then
falls back to the first event or cron trigger for existing workflows.

## Scheduled work

- Scheduled entries are persisted with runtime state.
- Due work is re-enqueued through the same input path as user messages.
- Runtime state is updated after scheduled work is processed.

- Stored timestamps, APIs, logs, agent shells, and containers use UTC; the browser renders instants in its own timezone.
- User preferences provide the wall-clock timezone for “tomorrow”, conversational time, and unstamped schedules. It defaults to `UTC`.
- Each new schedule entry and workflow cron trigger stamps that preference as its IANA timezone. Existing entries without one fall back to the current preference, then UTC.
- One-shot schedule timestamps are UTC instants; legacy timestamps without an offset are interpreted as UTC. Schedule displays resolve the entry timezone, then the preference, then UTC.
- Cron nodes created before preferences arrive leave the timezone unset so the server stamps its current preference on save. Cron previews use that same fallback.
- Date-level agent context changes at the user's local midnight. Exact-time guidance is limited to available tools; tool-less calls request a timestamp from their caller.
- Cron trigger payload instants (`scheduled_for`, `fired_at`) are UTC; their separate `timezone` field retains the wall-clock evaluation zone.
- Calendar dates are not instants. The Logs page keeps browser-local dates in its URL and converts their local midnight boundaries to UTC API instants, including short/long DST days. Date-only filters sent directly to the Logs API retain UTC-day semantics.
- Known legacy server timestamps without offsets are interpreted as UTC at persistence/consumer boundaries. External provider timestamps require a timezone or producer-defined epoch units; no timezone or seconds/milliseconds guessing is applied.

The system persists runtime conversation/checkpoint state, state documents, and
backend-owned metadata in Postgres. Operational logs are plain rotating files
in the shared `logs/` directory (`/app/logs` in containers): each service writes
separate stdout and stderr files, with five 10 MiB backups, and the Logs page
and exports read those files, not database rows. Progena exposes selected state
documents through a validated virtual filesystem (`/config/*.json` and related
task/workflow paths); those JSON names are views over database documents, not
files in `data/state` or `data/config`. State-document changes are broadcast to
the canonical UI stream after the write succeeds; details are in
[STATE_DOCUMENTS.md](STATE_DOCUMENTS.md).

Chat history is exposed through `/sessions/`: one
`<date>_<title-slug>_<id8>.md` per non-archived chat, backed by `session:`
documents in Postgres (`src/progena/sessions/`). Each file is YAML front
matter (all payload fields except `content`, plus `display_title`) followed by
a blank line and the IRC-style `content`. Only the `title`, `pinned`, and
`archived` front-matter fields are editable; the body is read-only. Listing,
grep, and read operate directly on the store documents (no cache).

`/runs/` is a read-only execution-history mount: one
`<date>_<slug>_<full-run-id>.md` per top-level run under
`/runs/{routines,workflows,schedules}/`, YAML front matter plus an IRC-style
log, rendered on read from existing records (routine lifecycle events and the
attempt's latest checkpoint `run_meta`, `workflow_run:` documents,
`schedule_runs` plus the run's thread). Nothing is persisted, so older runs
need no backfill. Child routine runs (a workflow routine step, a routine node)
are inlined between join/part lines, never listed. Speech is shown in full;
data is summarised (changed fields, document mount path and title, counts,
head…tail of long text) with a pointer to the full value: a mount path, or
`<run-file>#<n>` for step `n`, which is never listed, globbed or grepped.
Status uses the same interrupted rule as the UI
(`src/progena/runtime/ownership.py`). Terminal runs, including interrupted runs,
are cached in-process; updated records and subsequent lifecycle boundaries invalidate
the entry. Cached output validates checkpoint IDs in every namespace of each related
thread, compact lifecycle revisions, and immutable fingerprints of consumed mutable
definitions and preferences. Previously missing threads, late subagent namespaces,
changed child graphs and renamed agent speakers refresh cached output. Source changes
during hydration prevent cache admission. Concurrent reads only mutate the cache entry
they validated; unrelated reads are not serialized behind a global I/O lock.
Cache hits reuse per-read snapshots and never fetch terminal lifecycle outputs or
replay conversation history; full lifecycle content is loaded only on render misses.
No new event stream or polling is involved.
Directory listings use compact run records and bulk title maps, sorted
by normalized start time and full run ID. Reading one file looks up its exact ID
and loads only related lifecycle threads, not every run's payload. Schedule history
remains one JSON document; native reads select the requested item inside Postgres.
Full IDs prevent filename collisions; dates and slugs are descriptive, not lookup
identity. Existing full-ID paths remain readable after title/timezone changes or
a queued run starting on a different day. There are no short-ID aliases or persisted
files to migrate. `run_workflow` returns the new run's `run_id` and `/runs/` path.
Virtual grep scopes traversal to the requested subtree and applies the SDK's
include-glob matcher before rendering files, preserving match lines and truncation.
It processes files incrementally and stops once the result limit and truncation are
known. Exact-file grep/glob resolve the same valid full-ID paths as reads, including
earlier filename dates/titles; missing files and `#n` step references are not discovered.

Nested routine invocations use their persisted request IDs, including pending native
interrupts while a parent node awaits child approval. Completed child work remains
visible before the parent returns. Workflow routine steps inline every ordered attempt
inside their recorded execution interval, including approval resumes, without
double-counting usage or guessing the nearest attempt. Workflow step kinds and
conversation threads come from execution audits rather than edited definitions or
cleared outputs. Actual structured `node_outputs` are authoritative by key presence;
bounded audit output is display text, not a replacement value or JSON to reconstruct.
Unknown links are not inferred from nearby timestamps. Scheduled failures retain the
native `error` status and recorded explanation in both front matter and the log.
All three run kinds expose `tokens` with recorded input/output/total counts;
unknown usage/components remain null and known zero remains zero. Recorded
descendants replace parent aggregates, message IDs deduplicate recursive delegation,
and reused threads are scoped to the routine attempt or workflow audit interval.
Within a routine attempt, each agent-node transcript is further bounded by its
persisted start, measured duration, next invocation and attempt boundary. Repeated
nodes do not replay the entire shared conversation, and unknown invocation starts
do not authorize guessing a transcript window.

## Runs

**Spectate → Runs** (`/runs`) is a full-width execution-history flame-chart grid for
workflows, top-level routine attempts, and schedules. Kind, status, text and rolling
started-time filters use server-side cursor pagination behind an infinite-scrolling
chart. Loading, retry and end-of-results states stay inside the chart; there are no
page buttons. Virtualized run groups bound the rendered DOM while preserving
expanded activity. Changing filters or sorting resets the loaded result window.
Live arrivals and refreshed snapshots retain displaced cursor-boundary rows in
the loaded window. Timeline selection, collapsed branches and open disclosures
are owned outside virtualized rows so scrolling away does not reset inspection.
The toolbar retains the sliding 1h/24h/7d/30d/All time selector and right-aligned
search. Activity and Status headings open styled kind/status filter popovers.
Started and Duration headings toggle newest/oldest and longest/shortest ordering;
Tokens and Timeline remain noninteractive. Selections remain URL-backed.
Finite ranges use inclusive UTC cutoffs and omit runs without a recorded start.
Filtering and sorting happen before pagination across the full matching result set.
The page has no estimated step counter, bottom legend or footer text.

Clicking anywhere on a run row, or pressing Enter/Space while the row is focused,
expands its child activity into the same grid, with one run expanded at a time.
Filters, search, sort and the expanded run are represented in the URL, so reload,
shared links and browser navigation restore the inline view.
Row tooltips expose the existing run ID, trigger, timestamps, schedule request
context and, once loaded, the recorded root failure reason. The synthetic run span
is not repeated.
Structural rows collapse their descendants; non-overlapping sibling model calls
share compact lanes, while tool declarations without timing share marker lanes.
Overlapping measured calls stay on separate lanes. Each run has its own elapsed
time scale; bars across different runs do not imply a common wall-clock window.
The fixed page has one internally scrolling chart and sticky column headers.
Controls reuse the design library's icons, theme tokens and custom select.
Run and structural parent timelines overlay descendant activity from start to
end, retaining concurrent intervals rather than replacing them with a blank
duration band. Collapsed branches retain their summary and usage. Styled
hover/focus tooltips use the shared theme and render outside the scroll viewport.
There is no separate trace page, nested table, page heading or Refresh control.

Selecting a bar highlights a call without revealing payloads. A separate row
disclosure opens bounded input/output/error details immediately below that lane;
packed lanes provide a call selector. Details do not repeat duration/token fields.
Selected runs outside the loaded window expose loading and failure/retry states
without requiring an existing row or a successfully loaded trace. Virtual row
measurements are invalidated when expansion changes, including unmounted rows.
Model intervals are derived from checkpoint commits, not provider latency.
Tool duration is measured only when an audit record exists; otherwise a
transcript declaration marker explicitly reports timing unavailable. Available
usage appears in run, structural-parent and model-lane columns. Compact workflow
audits and selected routine checkpoint tips populate list summaries without
loading checkpoint histories. Expansion reconstructs per-model usage, which can
include calls absent from the compact node metadata and therefore increase the
recorded total. Known descendant usage supersedes inclusive parent metadata;
those amounts are never added together. Missing usage is a dash, while a
recorded zero remains zero. Schedules without persisted usage need expansion to
reconstruct their model messages; no runtime instrumentation or invented totals
are introduced by the visual read model.
Resumed routine requests sharing a checkpoint thread are separate attempts.
Lifecycle-derived, end-exclusive attempt windows isolate their metadata and
model/tool history; checkpoint notifications refresh each affected attempt using
one shared checkpoint-tip read per thread.
Nested subagent transcripts attach beneath their actual delegating tool, including
further delegation levels. Interrupted records without a terminal event keep
completion and duration unknown; they do not acquire a fabricated end or grow
as though still running. Duration sorts place unknown values after measured ones.

The backend reads Postgres directly; it does not ask the runtime or the agent-facing
`/runs/` mount to build this visual model. Document and checkpoint notifications
drive canonical `ui.event` updates; the table receives broadcast changes while
the page is open, without polling. While inspecting below the top, incoming runs
do not shift the viewport; a compact new-runs control lets the user return to
the latest entries. Expanding a trace establishes a targeted, connection-scoped
interest. Collapsing, filtering away, changing the selection, or leaving releases it.
Reconnect restores the interest and an authoritative snapshot through the same
canonical event lane. Its returned rows replace the cached result window, then
updates/deletes received during that request are applied; URL selection and
inspection state are retained independently. Initial trace builds listen for
checkpoint changes before accepting their snapshot; no extra stream or polling
lane is introduced. Search uses lowercase matching consistently in Python and SQL.
Lifecycle publications supersede checkpoint rows hydrated from an older lifecycle
state. Existing build invalidation also covers unindexed GET hydration and child
routine discovery, so stale snapshots cannot enter the terminal trace cache.
Failed authoritative hydration retains its resync intent through Retry, so a
successful retry cannot merge records deleted while disconnected back into view.
The client expires rows from finite time windows using its display clock, without
polling the server. Open spans grow on the client; terminal spans do not.

Workflow audit entries persist `started_at` before work, then settle the same
entry on completion, failure or cancellation. Approval and child-routine waits
preserve that entry. Only unanswered approvals block Resume; retained resolved
approval records do not prevent resuming a manually paused routine continuation.
Agent/extract steps persist their resolved conversation thread before awaiting
execution, so running and failed traces subscribe to the actual conversation.
Both compact summaries and expanded traces classify historical steps using the
recorded audit kind rather than the current editable workflow graph, and consume
its recorded routine thread. Failed/cancelled waits and completed outputs rejected
by schema validation retain that reference before pending state is cleared.
Existing entries require a manual one-time backfill before cutover; reconstructed
historical boundaries are not exact recovered starts.
Drain workflow execution during the final writer cutover so old writers cannot
introduce further missing start times. There is no historical timing fallback.

## Routine Code filesystem access

Routine Code nodes run in a fresh QuickJS sandbox and are pure unless their
`config.filesystem` explicitly grants a virtual filesystem scope. A grant
declares `"access": "read"` or `"read_write"` and one or more canonical
directory roots below `/artifacts/` or `/shared/`; no other mount family,
relative path, dot traversal, backslash, NUL, or empty root is valid. Read
access provides `ls`, `readFile`, `glob`, and `grep`; write access adds
`writeFile`, `editFile`, `copyFile`, `moveFile`, and `deleteFile`. It never
provides shell, network, agent, browser, or generic host access. `writeFile`
and `copyFile` do not overwrite; replace a file with `deleteFile` then
`writeFile`. Moves operate on files only within one mount, but are not atomic
and may be copy-then-delete. Filesystem failures throw catchable errors whose
message is available as `error.message`; use `ls` or `glob` for existence
checks. File operations are nontransactional: completed writes are not rolled
back on cancellation or a later error, and manual reruns/replays can repeat
them. Disabling automatic caching and retries does not provide exactly-once
execution. The QuickJS CPU deadline excludes
async filesystem waits, which remain bounded by the enclosing node timeout.

## Insights

**Spectate → Insights** (`/insights`) displays checkpoint-backed token totals,
time buckets, profile/deployment rankings and estimated costs for 24-hour,
7-day and 30-day windows. The top-bar Insights panel uses the same reporting
contract as a compact summary and preserves docking/pinning.
Range/profile filters use the shared `Select` component; pricing fields use
the `.set-text-input` treatment showcased at `/design`. Surfaces retain labels,
coverage counts and actionable errors without explanatory helper paragraphs.

Model profile pricing is editable in Profiles and persisted in the profile
state document exposed through `/config/profiles.json`. Costs use current
configured decimal rates, remain separated by currency, and show incomplete
pricing/usage explicitly. Historical messages without reliable profile
identity remain **Unattributed**. Azure model messages now retain non-secret
profile/deployment/call-time metadata in checkpoints, including streamed
responses; no deployment-to-profile guess is made for old history.

Reporting uses retained checkpoint messages, including committed message
deltas, deduplicated across versions. Checkpoint deletion/retention affects
available history. SQL reads serialized sources and the trusted LangGraph
serializer normalizes them before aggregation. See [BACKEND.md](BACKEND.md)
for the acknowledgement/SSE API, pricing semantics and scaling constraints.

## Public interfaces

- **Browser transports:** app-lifetime `GET /api/ui/stream` plus request-scoped
  `GET /api/ui/responses/{request_id}/stream` for active streaming chat
  responses.
- **Finite browser routes:** retained `/api/ui/*` routes return only HTTP `202`
  acknowledgements on success. The active `/api/attachments` API is the
  attachment exception: it supports upload, thread-scoped metadata listing or
  blob download, and deletion.
- **Operational probes:** `GET /healthz` is the sole explicit Docker/container
  liveness exception to the event-stream-oriented browser API rule. It returns
  an empty `200 OK`, carries no application data, and is not part of the
  browser API surface.
- **Internal runtime transport:** backend-to-runtime HTTP/SSE is internal and
  distinct from the browser SSE transports.

The legacy `/health`, `/ready`, `/live`, `/health/detailed`, direct
session/conversation surfaces, response-body data routes, and legacy
status/events streams are not public browser interfaces. This does not remove
the active `/api/attachments` API.

## External integration

The runtime can use registered tools and optional external agent integrations.
Those internal integrations are outside this browser-client contract.
