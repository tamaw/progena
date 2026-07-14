# Chat Frontend Notes

Quick reference for the event-stream-only chat page.

## Event-driven chat contract

Chat state and generation output do not arrive in a finite HTTP response.

1. The frontend generates one UUID for each finite chat operation and sends it
   in `X-Request-ID`.
2. The backend persists or dispatches work, enqueues the causal canonical
   `ui.event` envelopes, then returns HTTP `202 Accepted`
   (`{request_id, event_types}`).
3. `realtimeListener` receives `event: ui.event` frames from the one
   `/api/ui/stream` EventSource, deduplicates envelope IDs, and dispatches
   typed reducers.
4. Redux owns durable chat data. Pages read selectors and use the matching
   request-correlated event—not the acknowledgement—to complete an operation.

A causal envelope repeats the request ID in `payload.request_id`. The pending
operation registry may be resolved only when both the request ID and declared
event type match. **Do not discard an event merely because its request ID is not
pending in this tab.** A global event from another tab/process still updates the
shared Redux chat state; it simply must not resolve this tab's unrelated pending
operation.

Relevant types are:

- `chat.sessions.snapshot` for a durable session-list snapshot;
- `chat.session.created`, `chat.session.metadata.updated`, and
  `chat.session.upserted` for session data;
- request-scoped `chat.session.selected` for selecting/loading a detail;
- `chat.session.deleted` for durable removal;
- `chat.response.started`, `chat.response.delta`, `chat.response.completed`,
  `chat.response.failed`, and `chat.response.cancelled` for generation.

`POST /api/ui/responses`, including a body with `stream: true`, is a finite HTTP
`202` acknowledgement request. `PUT /api/ui/responses/{generation_id}/cancel`
is likewise an acknowledged finite operation followed by its correlated terminal
event. Token consumption is from `chat.response.*` canonical stream envelopes,
not a response-body SSE parser.

## Stream reconnect and pending operations

The stream has a bounded replay window. On reconnect, retained envelopes after
`Last-Event-ID` arrive in order and `connection.ready` marks the end of the
available replay. A stale/invalid cursor is not proof that nothing was missed.
Reducers must be idempotent for replay, and the page must request the needed
chat snapshot/detail again if its state cannot safely continue. A `202`
acknowledgement remains pending across reconnect until its matching event, a
request failure, or the explicit UI timeout/reconnect policy.

## Session card layout

Cards have three rows: time/pin (top), title (middle), and counts (bottom).
`meta` drives the top-row timestamp from backend `last_activity`, not
`created_at`. `summary` remains hidden from the DOM but is searched by the
sidebar filter.

## Thread routing

When sending a response for an existing backend chat, include that chat's
`thread_id` in request metadata. Otherwise the runtime creates a different
LangGraph thread and history is not continued. The request is acknowledged by
`POST /api/ui/responses`; its actual lifecycle is represented by canonical
`chat.response.*` events.

## Background generation and indicators

Changing selected chats must not turn the canonical stream into a
page-local stream or make a response-body parser the source of truth. Track
background generation from `chat.response.*` events in Redux/model state:

- an in-progress response for a non-selected chat renders that chat's orange
  generating indicator;
- a terminal event for a background chat may mark it unread and re-sort it by
  updated activity;
- selecting a chat clears its local unread presentation, not its durable chat
  data;
- concurrent generations are identified by their request/thread metadata and
  must not overwrite each other.

## Model and persisted history

Model metadata and message details come from canonical chat response/detail
event payloads. Backend chat transcripts must not be written to `localStorage`:
when a backend chat is selected, remove any local-history key that could create
a stale “Current” ghost card.

Auto-selection should run once after the correlated session snapshot hydrates
the Redux list, respecting pinned and bucket ordering; it must not reselect on
unrelated replay or later global updates.
