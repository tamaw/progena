# Event-Stream Playwright Test Plan

## Scope

Validate the browser transport contract against the isolated local stack:

- Frontend: `http://127.0.0.1:5174`
- Backend: `http://127.0.0.1:8001`
- Runtime: `http://127.0.0.1:9502`
- PostgreSQL: `127.0.0.1:15434`

The checked-in contract now uses **two browser SSE transports** during active
chat:

- canonical `GET /api/ui/stream` for `ui.event` envelopes and chat lifecycle
- dedicated `GET /api/ui/responses/{request_id}/stream` for one request's
  `chat.response.delta` / `chat.response.event` frames

## Browser setup

1. Open the frontend with Playwright.
2. Capture console messages, page errors, failed requests, and response status
   codes.
3. Confirm the app opens one canonical `EventSource` to `/api/ui/stream` during
   shell bootstrap.
4. Preserve the browser context long enough to observe `connection.ready` and
   subsequent domain events.
5. During active chat, also observe the request-scoped
   `/api/ui/responses/{request_id}/stream` connection.

## Critical scenarios

### 1. Shell bootstrap
- Load `/`.
- Confirm the application shell renders navigation, conversation content,
  notification UI, and no uncaught error occurs.
- Confirm the canonical realtime connection reaches `connection.ready`.
- Confirm no deleted `/health`, `/ready`, `/live`, session, workspace, or
  widget-schema browser requests are made.
- Confirm no dedicated response stream is opened before a chat request is
  acknowledged.

### 2. Event-owned catalogue hydration
For `/dashboard`, `/tasks`, `/logs`, `/instructions`, `/skills`, `/schedules`,
`/knowledge`, `/tools`, and `/workflows`:

- Load the route.
- Record the finite request and its HTTP `202 Accepted` acknowledgement
  response.
- Confirm the page data arrives from a matching canonical `ui.event`, not the
  acknowledgement body.
- Confirm loading/error state settles only after the causal canonical event or a
  rejected request.

### 3. Chat lifecycle and split transport
- Create/select a draft thread.
- Send a message.
- Confirm `POST /api/ui/responses` returns HTTP `202` with only
  `{request_id,event_types}`.
- Confirm the browser opens
  `/api/ui/responses/{request_id}/stream` after the acknowledgement.
- Confirm canonical `/api/ui/stream` carries `chat.response.started` and the
  eventual terminal lifecycle event (`completed`, `failed`, or `cancelled`).
- Confirm the dedicated response stream carries ordered
  `chat.response.delta` / `chat.response.event` frames for the same request.
- Confirm the visible reply is driven by dedicated frames, not canonical deltas.

### 4. Cancellation semantics
- Press Stop during generation.
- Confirm `PUT /api/ui/responses/{generation_id}/cancel` returns `202` whose
  `event_types` contains `chat.response.cancel_requested`.
- Confirm canonical `/api/ui/stream` does **not** treat
  `chat.response.cancel_requested` as terminal.
- Confirm the target request later receives canonical
  `chat.response.cancelled`.
- Confirm no late dedicated deltas mutate the canceled message.

### 5. Dedicated replay and reconnect
- Start a long-running chat response.
- Force-close the dedicated `/api/ui/responses/{request_id}/stream` request or
  temporarily offline/online the page.
- Confirm the browser reopens the dedicated stream with
  `?after=<lastEventId>` when it is reconnecting manually.
- Confirm replayed dedicated frames resume without duplicate visible text.
- Confirm the canonical stream remains connected throughout unless explicitly
  testing canonical reconnect.

### 6. Dedicated resync_required behavior
- Reconnect the dedicated stream with an invalid cursor or an artificially old
  cursor.
- Confirm the server emits exactly one `chat.response.resync_required` frame and
  closes the dedicated stream.
- Confirm the UI stops appending live content on that stream and waits for the
  canonical terminal lifecycle event.
- Confirm the final persisted reply or terminal error still resolves the request
  without inventing extra deltas.

### 7. Cross-tab and canonical replay behavior
- Open two pages/contexts.
- Mutate a shared non-chat resource in one context.
- Confirm global snapshots/mutations update both contexts through the canonical
  stream.
- Disconnect/reconnect canonical `/api/ui/stream` and verify buffered replay or
  an explicit canonical resynchronization path.

## Evidence to capture

- Route URL and visible state.
- HTTP method/status/headers/body shape for finite requests.
- Canonical SSE envelope type, sequence, ID, request ID, and payload.
- Dedicated SSE event name, frame ID, request ID, and payload.
- Reconnect URLs, including `?after=<lastEventId>` when used.
- Console/page errors.
- Failed network requests and unexpected requests.
- Screenshots for shell, empty/error/loading, and successful event-hydrated
  states.

## Exit criteria

- No uncaught browser errors.
- The app opens exactly one canonical `EventSource` to `/api/ui/stream` during
  steady state.
- Active chat opens one additional request-scoped `EventSource` to
  `/api/ui/responses/{request_id}/stream`.
- All retained finite requests return exact HTTP `202` acknowledgements.
- Every acknowledgement resolves through the correct canonical lifecycle event.
- No domain data is read from acknowledgement bodies.
- Dedicated replay, reconnect, and `resync_required` behavior are observed and
  recorded.
- Any deviation is recorded with route, request ID, event type, frame/envelope
  ID, and reproduction steps.
