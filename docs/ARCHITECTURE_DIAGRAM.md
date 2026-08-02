# Progenitor Runtime Architecture

This document describes the running stack. For the checked-in repository layout,
see [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md). The normative browser-client
protocol is [BACKEND.md](BACKEND.md).

## High-level topology

```text
Browser frontend
      |
      | finite commands/queries
      | X-Request-ID: <operation UUID>
      v
Backend HTTP API ---- internal HTTP/SSE ----> Progenitor runtime
      |                                           |
      | HTTP 202 Accepted acknowledgement         +--> runtime tools and integrations
      | { request_id, event_types }              |
      | (no application data)                    +--> persisted runtime state and logs
      |
      +--> GET /api/ui/stream ------------------> realtimeListener + Redux reducers
      |       event: ui.event / UiEventEnvelope          |
      |                                                  v
      |                                           Redux selectors -> pages/components
      |
      +--> GET /api/ui/responses/{request_id}/stream --> useChatApi live callbacks
              event: chat.response.*                     (active streaming chat only)
```

## Browser data flow

Application data moves in one direction, but on two SSE lanes with different
responsibilities:

- **Canonical lane:** typed `ui.event` envelopes populate Redux and carry app
  lifecycle/state, including chat response lifecycle.
- **Dedicated chat-content lane:** request-scoped `chat.response.delta` and
  `chat.response.event` frames feed the active live-turn renderer.

### Finite request flow

```text
frontend generates UUID
        |
        +-- X-Request-ID --> retained finite route
                                      |
                         validate / persist or dispatch
                                      |
                       enqueue causal ui.event envelope(s)
                       payload.request_id = UUID
                                      |
                 HTTP 202 Accepted { request_id, event_types }
                                      |
                                      v
  GET /api/ui/stream: event: ui.event, data: UiEventEnvelope
                                      |
        Redux applies typed result and resolves only UUID's pending operation
```

The ordering is **persist/dispatch → enqueue causal canonical event(s) →
acknowledge**. The acknowledgement means enqueued, not delivered; it never
carries application state, generated output, log content, a download, or an
inline SSE response body. Events caused by other tabs still update Redux but do
not resolve this tab's pending operation.

### Streaming chat flow

```text
POST /api/ui/responses (stream:true)
        |
        +-- 202 { request_id, event_types:["chat.response.started"] }
        |
        +-- canonical /api/ui/stream -----------------> chat.response.started
        |                                              chat.response.completed|failed|cancelled
        |
        +-- dedicated /api/ui/responses/{request_id}/stream
               chat.response.delta
               chat.response.event
               chat.response.heartbeat
               chat.response.resync_required
```

The dedicated stream is keyed by the acknowledged `request_id`. It carries only
response-content transport for that request; canonical lifecycle ownership stays
on `/api/ui/stream`.

## Stream lifecycle

### Canonical stream

The canonical stream uses `text/event-stream`; every application frame has SSE
event name `ui.event`. Its envelope has `id`, increasing `seq`, typed `type`,
`scope` (`global`, `session`, or `chat`), optional `session_id`, event-specific
`payload`, and ISO-8601 `timestamp`.

- A bounded, in-memory 200-event broadcast replay buffer supports
  `Last-Event-ID` reconnect replay in sequence order.
- `connection.ready` follows the available replay and marks the live boundary;
  it includes initial status and alerts but is not a correlated domain snapshot.
- Replay is not durable resynchronization. If a cursor is old or invalid, the
  frontend must request needed snapshots and wait for their correlated events.
- Idle connections receive `connection.heartbeat`; graceful close is the
  canonical `ui.event` envelope `connection.shutdown` with shutdown reason and
  message.
- Reducers deduplicate envelope IDs and are safe for replayed snapshots,
  updates, and deletes.

### Dedicated response stream

The dedicated response stream also uses `text/event-stream`, but it does not use
`UiEventEnvelope`. It serves direct response payloads on the SSE event names
`chat.response.delta`, `chat.response.event`, `chat.response.heartbeat`, and
`chat.response.resync_required`.

- Only `chat.response.delta` and `chat.response.event` are replayable.
- Replayable frame IDs are `chat-response:{request_id}:{seq}`.
- `Last-Event-ID` takes precedence over the `after` query parameter.
- Invalid or expired cursors emit one `chat.response.resync_required` frame and
  then close.
- Once canonical terminal state is reached, the stream closes after queued
  retained content frames have been delivered.

There is no `/api/ui/events` EventSource, no `/api/ui/status` EventSource, and
no response-body token SSE.

## Invocation entry points

- **Chat sessions:** chat metadata routes establish/select persisted chat
  sessions and publish canonical state envelopes.
- **Chat generation:** `POST /api/ui/responses` acknowledges the request,
  canonical `/api/ui/stream` publishes lifecycle, and dedicated
  `/api/ui/responses/{request_id}/stream` publishes content.
- **Automation, workflow, schedule, MCP, widget, and task operations:**
  retained operations acknowledge acceptance and publish typed canonical state
  events. Their UI data is never returned in the finite response body.

## Responsibilities

### Runtime

- Owns the long-running conversation graph, checkpoints, scheduled work, and
  runtime tools.

### Backend

- Owns finite browser routes plus both browser SSE transports.
- Publishes canonical typed events after state changes succeed.
- Retains bounded replay for canonical envelopes and per-request response
  content frames.

### Frontend

- Opens one canonical `EventSource` for `/api/ui/stream`.
- Opens request-scoped response `EventSource` instances only while a chat stream
  is active.
- Keeps Redux as durable application state and uses selector-driven pages.
- Treats HTTP acknowledgements as operation metadata, not data hydration.

## State and logging

Runtime and backend state, including the Progenitor state documents and
operational logs, are persisted in the configured Postgres stores. The
Progenitor agent receives selected documents through a validated virtual
filesystem (`/config/*.json`, task Markdown files under `/tasks/`, and
workflow JSON files under `/workflows/`); these are database-backed views, not
local files. State changes relevant to the browser are represented as typed
canonical stream events after persistence.
Streaming chat content is transient transport data unless it is persisted into
the underlying conversation state and later surfaced again via canonical
snapshots/detail loads.

## Deployment shape

The default checked-in container stack runs frontend, backend, and Progenitor
runtime services. The backend-to-runtime transport is internal; it must not be
confused with the browser's canonical lifecycle stream or its request-scoped
chat response stream.
