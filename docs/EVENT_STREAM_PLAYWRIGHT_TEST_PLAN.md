# Event-Stream Playwright Test Plan

## Scope
Validate the browser contract after the event-stream refactor against the isolated local stack:

- Frontend: `http://127.0.0.1:5174`
- Backend: `http://127.0.0.1:8001`
- Runtime: `http://127.0.0.1:9502`
- PostgreSQL: `127.0.0.1:15434`

## Browser setup

1. Open the frontend with Playwright.
2. Capture console messages, page errors, failed requests, and response status codes.
3. Confirm exactly one `EventSource` targets `/api/ui/stream`.
4. Preserve the browser context long enough to observe `connection.ready` and subsequent domain events.

## Critical scenarios

### 1. Shell bootstrap
- Load `/`.
- Confirm the application shell renders navigation, conversation content, notification UI, and no uncaught error occurs.
- Confirm the realtime connection reaches `connection.ready`.
- Confirm no deleted `/health`, `/ready`, `/live`, session, workspace, or widget-schema browser requests are made.

### 2. Event-owned catalogue hydration
For `/dashboard`, `/tasks`, `/logs`, `/prompts`, `/skills`, `/schedules`, `/knowledge`, `/tools`, and `/workflows`:

- Load the route.
- Record the finite request and its HTTP `202 Accepted` acknowledgement response.
- Confirm the page data arrives from a matching `ui.event`, not the acknowledgement body.
- Confirm loading/error state settles only after the causal event or a rejected request.

### 3. Chat lifecycle
- Create/select a draft thread.
- Send a message.
- Confirm the acknowledgement is HTTP `202`/data-free.
- Confirm ordered `chat.response.started`, delta/event frames, and a terminal frame update the UI.
- Press Stop during generation and confirm cancellation targets the active generation.
- Confirm no late deltas mutate the canceled message.

### 4. CRUD failure semantics
- Submit valid and invalid prompt/skill/knowledge/task mutations.
- Confirm mutation controls remain pending until the matching snapshot/event.
- Confirm rejected acknowledgements surface an error and do not navigate, dismiss, or optimistically mutate durable state.

### 5. Cross-tab and replay behavior
- Open two pages/contexts.
- Mutate a shared resource in one context.
- Confirm global snapshots/mutations update both contexts.
- Disconnect/reconnect a stream and verify buffered replay or an explicit resynchronization path.

## Evidence to capture

- Route URL and visible state.
- HTTP method/status/headers/body shape for finite requests.
- SSE envelope type, sequence, ID, request ID, and payload.
- Console/page errors.
- Failed network requests and unexpected requests.
- Screenshots for shell, empty/error/loading, and successful event-hydrated states.

## Exit criteria

- No uncaught browser errors.
- One canonical EventSource.
- All retained finite requests return exact HTTP `202` acknowledgements.
- Every acknowledgement resolves through a matching causal event.
- No domain data is read from acknowledgement bodies.
- Failures remain visible and do not cause false success.
- Any deviation is recorded with route, request ID, event type, and reproduction steps.
