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
      | HTTP 202 Accepted acknowledgement            +--> runtime tools and integrations
      | { request_id, event_types }                  |
      | (no application data)                     +--> persisted runtime state and logs
      |
      | GET /api/ui/stream (the one EventSource)
      v
SSE event: ui.event / UiEventEnvelope
      |
      v
realtimeListener + Redux reducers (idempotent/deduplicated)
      |
      v
Redux selectors -> Pages and components
```

## Browser data flow

Application data moves in one direction: canonical stream envelopes populate
Redux. A page submits a finite request, records the acknowledgement as a
pending operation, and observes the result only after a matching event updates
the store.

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

The ordering is **persist/dispatch → enqueue causal event(s) → acknowledge**.
The acknowledgement means enqueued, not delivered; it never carries application
state, generated output, log content, a download, or a direct SSE response.
Events caused by other tabs still update Redux but do not resolve this tab's
pending operation.

## Stream lifecycle

The single stream uses `text/event-stream`; every application frame has SSE
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

There is no `/api/ui/events` EventSource, `/api/ui/status` EventSource, direct
chat token SSE, or component-owned stream.

## Invocation entry points

- **Chat sessions:** chat metadata routes establish/select persisted chat
  sessions. `POST /api/ui/responses` accepts browser generation work and then
  emits `chat.response.started`, delta, and terminal events on the canonical stream.
- **Automation, workflow, schedule, MCP, widget, and task operations:** retained
  operations acknowledge acceptance and publish typed state events. Their UI
  data is never returned in the finite response body.

## Responsibilities

### Runtime

- Owns the long-running conversation graph, checkpoints, scheduled work, and
  runtime tools.

### Backend

- Owns finite browser routes and the canonical `/api/ui/stream` transport.
- Publishes typed events after state changes succeed.
- Retains bounded replay and emits the connection lifecycle envelopes.

### Frontend

- Opens one canonical EventSource and sends a request ID for every finite
  application request.
- Keeps Redux as durable application state and uses selector-driven pages.
- Treats HTTP acknowledgements as operation metadata, not data hydration.

## State and logging

Runtime and backend state is persisted under the configured data stores;
operational logs are written to the configured log store. State changes that are
relevant to the browser are represented as typed canonical stream events after
persistence.

## Deployment shape

The default checked-in container stack runs frontend, backend, and Progenitor
runtime services. The backend-to-runtime transport is internal; it must not be
confused with the browser's sole `/api/ui/stream` transport.
