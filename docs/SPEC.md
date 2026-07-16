# Progenitor System Specification

This document describes the checked-in main system: the Progenitor runtime, the
backend API, and the React frontend. The browser API contract is defined
normatively in [BACKEND.md](BACKEND.md).

## Purpose

Progenitor is a long-running conversational system. The runtime maintains
conversation state and executes the main agent graph. The frontend submits work
to the backend and receives browser application data through two SSE roles:

- one canonical lifecycle/state stream for the whole app
- one request-scoped response-content stream for each active streaming chat
  request

## Core components

### Runtime (`app/progenitor/`)

- Runs the perpetual conversation service.
- Restores and checkpoints runtime state.
- Builds the DeepAgent-backed conversation graph.
- Processes scheduled work through the same core path as user input.

### Backend (`app/backend/`)

- Exposes the browser's finite command/query routes and bridges work to the
  runtime.
- Owns the canonical `GET /api/ui/stream` SSE transport for connection
  lifecycle, retained state, and chat response lifecycle events.
- Owns the dedicated `GET /api/ui/responses/{request_id}/stream` SSE transport
  for one accepted streaming chat response's content frames.
- Persists or dispatches a finite operation, enqueues its causal canonical
  events, then returns an HTTP `202 Accepted` acknowledgement with exactly
  `{request_id,event_types}`.
- Keeps successful finite HTTP responses data-free; browser-visible results come
  from canonical events or the dedicated response-content stream.

### Frontend (`app/frontend/`)

- Provides the React/Vite web client.
- Opens one app-lifetime canonical `EventSource` to `/api/ui/stream`.
- Opens a second, request-scoped `EventSource` to
  `/api/ui/responses/{request_id}/stream` only while a streaming chat response
  is active.
- Uses Redux as the sole durable application-data owner. Canonical stream
  events hydrate Redux; pages read Redux selectors.
- Generates an `X-Request-ID` UUID for every finite application request and
  waits for matching causal canonical stream events after acknowledgement.

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

## Persistence

The system persists runtime conversation and schedule state, backend-owned
metadata, and operational logs. State-document changes are broadcast to the
canonical UI stream after the write succeeds; details are in
[STATE_DOCUMENTS.md](STATE_DOCUMENTS.md).

## Public interfaces

- **Browser transports:** app-lifetime `GET /api/ui/stream` plus request-scoped
  `GET /api/ui/responses/{request_id}/stream` for active streaming chat
  responses.
- **Finite browser routes:** retained `/api/ui/*` routes return only HTTP `202`
  acknowledgements on success.
- **Operational probes:** `GET /healthz` is the sole explicit Docker/container
  liveness exception to the event-stream-oriented browser API rule. It returns
  an empty `200 OK`, carries no application data, and is not part of the
  browser API surface.
- **Internal runtime transport:** backend-to-runtime HTTP/SSE is internal and
  distinct from the browser SSE transports.

The legacy `/health`, `/ready`, `/live`, `/health/detailed`, direct
attachment/session/conversation surfaces, response-body data routes, and legacy
status/events streams are not public browser interfaces.

## External integration

The runtime can use registered tools and optional external agent integrations.
Those internal integrations are outside this browser-client contract.
