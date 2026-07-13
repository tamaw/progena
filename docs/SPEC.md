# Progenitor System Specification

This document describes the checked-in main system: the Progenitor runtime, the
backend API, and the React frontend. The browser API contract is defined
normatively in [BACKEND.md](BACKEND.md).

## Purpose

Progenitor is a long-running conversational system. The runtime maintains
conversation state and executes the main agent graph. The frontend submits work
to the backend and receives all browser application data from one canonical
event stream.

## Core components

### Runtime (`app/progenitor/`)

- Runs the perpetual conversation service.
- Restores and checkpoints runtime state.
- Builds the DeepAgent-backed conversation graph.
- Processes scheduled work through the same core path as user input.

### Backend (`app/backend/`)

- Exposes the browser's finite command/query routes and bridges work to the
  runtime.
- Owns `GET /api/ui/stream`, the sole browser application-data transport.
- Persists or dispatches a finite operation, enqueues its causal events, then
  returns an HTTP `202 Accepted` acknowledgement with exactly
  `{request_id,event_types}`.
- Sends generation deltas, log-export chunks, snapshots, and mutations through
  the canonical stream rather than successful HTTP response bodies.

### Frontend (`app/frontend/`)

- Provides the React/Vite web client.
- Opens exactly one `EventSource`, to `/api/ui/stream`.
- Uses Redux as the sole durable application-data owner. Stream events hydrate
  Redux; pages read Redux selectors.
- Generates an `X-Request-ID` UUID for every finite application request and
  waits for matching causal stream events after acknowledgement.

There are no browser `/api/ui/events` or `/api/ui/status` streams, no
response-body fallback, and no per-page EventSource.

## Request and event flow

1. The frontend creates one operation UUID and sends it in `X-Request-ID`.
2. The backend validates, persists or synchronously dispatches the operation,
   then enqueues every causal `ui.event` envelope with the UUID at
   `payload.request_id`.
3. The backend returns HTTP `202 Accepted` with only
   `{request_id, event_types}`.
4. The canonical stream delivers the event data. Redux applies an event
   idempotently and resolves only the pending operation with the same request
   ID and declared event type.
5. Components render selectors; they do not hydrate from acknowledgement data.

HTTP `202` means dispatched and accepted, not delivered or rendered. HTTP failures
keep their non-`202` status and produce no success event. Connection loss also
does not complete an operation: the frontend applies its explicit reconnect or
timeout policy.

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

Representative domain types include chat session and response events, agent and
inbox snapshots, knowledge search results, workflow/run events, kanban/task
updates, widget/MCP/schedule snapshots, prompt/skill snapshots, alerts, and
log page/export chunk events. See [BACKEND.md](BACKEND.md) for route-to-event
disposition.

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

- **Browser application transport:** `GET /api/ui/stream` only.
- **Finite browser routes:** retained `/api/ui/*` routes return only HTTP `202`
  acknowledgements; their results arrive in correlated stream events.
- **Operational probes:** no direct backend health route is part of this service's browser or backend API surface.
- **Internal runtime transport:** backend-to-runtime HTTP/SSE is internal and
  distinct from the browser UI stream.

The legacy `/health`, `/ready`, `/live`, `/health/detailed`, direct attachment/session/
conversation surfaces, response-body data routes, and legacy status/events
streams are not public browser interfaces.

## External integration

The runtime can use registered tools and optional external agent integrations.
Those internal integrations are outside this browser-client contract.
