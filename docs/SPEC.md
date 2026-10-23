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
- Generates an `X-Request-ID` UUID for every finite application request and
  waits for matching causal canonical stream events after acknowledgement.
- Renders agent-built canvases (`/canvases`) in sandboxed, network-less iframes
  (`sandbox="allow-scripts"`, `srcdoc`); canvas files, vendored libraries,
  documents and events reach the frame only through the parent's postMessage
  bridge over the canonical stream (`docs/CANVASES.md`).

There are no browser `/api/ui/events` or `/api/ui/status` streams, no
response-body fallback, and no inline response-body token SSE.

## Request and event flow

1. The frontend creates one operation UUID and sends it in `X-Request-ID`.
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

## Scheduled work

- Scheduled entries are persisted with runtime state.
- Due work is re-enqueued through the same input path as user messages.
- Runtime state is updated after scheduled work is processed.

The system persists runtime conversation/checkpoint state, state documents,
backend-owned metadata, and operational logs in Postgres. Progena exposes
selected state documents through a validated virtual filesystem (`/config/*.json`
and related task/workflow paths); those JSON names are views over database
documents, not files in `data/state` or `data/config`. State-document changes are
broadcast to the canonical UI stream after the write succeeds; details are in
[STATE_DOCUMENTS.md](STATE_DOCUMENTS.md).

Chat history is exposed through `/sessions/`: one
`<date>_<title-slug>_<id8>.md` per non-archived chat, backed by `session:`
documents in Postgres (`src/progena/sessions/`). Each file is YAML front
matter (all payload fields except `content`, plus `display_title`) followed by
a blank line and the IRC-style `content`. Only the `title`, `pinned`, and
`archived` front-matter fields are editable; the body is read-only. Listing,
grep, and read operate directly on the store documents (no cache).

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
