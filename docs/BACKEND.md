# Backend API — Client Contract

This is the authoritative browser-client contract. The browser uses two SSE
transports:

- **Canonical lifecycle/state stream:** `GET /api/ui/stream`
- **Request-scoped response-content stream:**
  `GET /api/ui/responses/{request_id}/stream`

Do not infer an alternate contract from historical response bodies or from an
individual handler's implementation details.

## Transport boundary

`GET /api/ui/stream` establishes the app-lifetime canonical `200 OK`
`text/event-stream` connection. It carries `UiEventEnvelope` frames for
connection lifecycle, retained UI state, and chat response lifecycle.

`GET /api/ui/responses/{request_id}/stream` establishes a request-scoped
`200 OK` `text/event-stream` connection for one accepted streaming chat
response. It carries only response-content frames for that request plus its
transport keepalive/resync signals.

Every retained finite application route—whether it is `GET`, `POST`, `PUT`,
`PATCH`, or `DELETE`—returns only an HTTP `202 Accepted` acknowledgement. Its
body is exactly `{request_id, event_types}`. A finite response MUST NOT contain
an entity, list, query result, status snapshot, generated text, attachment,
download, or an inline response-body SSE stream. When browser chat requests use
streaming, the client opens the dedicated response SSE endpoint separately after
the `202` acknowledgement.

Direct `/health`, `/ready`, `/live`, and `/health/detailed` routes are absent.
`/healthz` is the sole explicit Docker/container liveness exception: it returns
an empty `200 OK`, carries no application data, and is not a browser API route.

## Canonical SSE frames

Each canonical application frame has the SSE event name `ui.event`, an SSE `id`
equal to the envelope `id`, and a JSON `data` value with this exact outer
shape:

```json
{
  "id": "ui-42",
  "seq": 42,
  "type": "chat.sessions.snapshot",
  "scope": "chat",
  "session_id": null,
  "payload": {
    "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100"
  },
  "timestamp": "2026-01-01T00:00:00+00:00"
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `string` | Stream-global identifier, currently `ui-{seq}`. It is the deduplication and canonical replay cursor value. |
| `seq` | `integer` | Increasing process-local sequence number. It defines the order of retained canonical replay events. |
| `type` | `string` | Typed domain event, such as `chat.sessions.snapshot` or `logs.page.loaded`. |
| `scope` | `"global" \| "session" \| "chat"` | Event routing scope. It is not an arbitrary nullable string. |
| `session_id` | `string \| null` | Session scope identifier when applicable. |
| `payload` | `object` | Event-specific result data. Every event caused by a finite request also has `payload.request_id`. |
| `timestamp` | `string` | ISO-8601 envelope creation time. |

The SSE event name remains `ui.event` for **all** canonical envelopes,
including connection events. There is no `server.shutdown` SSE event name.

### Canonical connection events

The canonical stream also emits these `ui.event` envelope types:

| Type | Payload | Meaning |
| --- | --- | --- |
| `connection.ready` | `{ "stream": "ui", "status": <status>, "alerts": <alerts> }` | Sent after replay on each connection/reconnection; it marks the canonical live-stream boundary. It is not a request-correlated domain snapshot. |
| `connection.heartbeat` | `{ "timestamp": <unix-seconds> }` | Keepalive frame emitted while the subscriber queue is idle (15 seconds by default, configurable through `SSE_HEARTBEAT_INTERVAL`). |
| `connection.shutdown` | `{ "reason": "server_shutdown", "message": "Server is shutting down" }` | Final canonical envelope before a graceful server-side close. |

Connection events do not resolve a pending finite operation unless their payload
has the matching request ID, which these connection events do not.

## Canonical replay, reconnect, and resync

The backend retains the most recent **200 broadcast application envelopes** in
memory. A reconnect may supply `Last-Event-ID: ui-N`; the server replays
retained envelopes whose `seq` is greater than `N`, in order, then emits
`connection.ready`, then delivers new queued events.

This is bounded best-effort replay, not durable catch-up:

- An absent, malformed, or evicted cursor cannot prove that no event was
  missed. The server does not synthesize an unbounded history or a gap marker.
- `connection.ready` marks the end of whatever replay was available. A client
  that cannot safely continue from its Redux state MUST issue the appropriate
  finite snapshot requests and wait for their correlated stream events.
- Reducers MUST be idempotent for replayed snapshots, updates, and deletes and
  MUST deduplicate by envelope `id`.
- A connection loss does not cancel a finite request. An acknowledgement means
  the causal event was enqueued, not delivered to or applied by this browser.

The app shell owns one canonical `EventSource` for `/api/ui/stream`. There is
no `GET /api/ui/events` stream and no `/api/ui/status` stream.

## Dedicated chat response SSE

Accepted browser chat requests with `stream: true` use a second, request-scoped
transport:

- **Route:** `GET /api/ui/responses/{request_id}/stream`
- **Purpose:** replayable response content for that one request
- **SSE event names:** `chat.response.delta`, `chat.response.event`,
  `chat.response.heartbeat`, `chat.response.resync_required`

This stream does **not** emit `ui.event` envelopes. Its `data` payload is the
response payload directly.

### Replayable dedicated frames

Only these event types are retained and replayed:

- `chat.response.delta`
- `chat.response.event`

Each replayable frame has SSE `id` `chat-response:{request_id}:{seq}`.

Representative payloads:

```json
{
  "id": "response-123",
  "model": "progenitor-v1",
  "thread_id": "thread_abc",
  "delta": "Hello",
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "runtime_emitted_at_ms": 1730000000123,
  "runtime_stream_seq": 7
}
```

```json
{
  "id": "response-123",
  "model": "progenitor-v1",
  "thread_id": "thread_abc",
  "event_type": "tool_start",
  "content": "python",
  "metadata": {"tool_name": "python"},
  "images": [],
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "runtime_emitted_at_ms": 1730000000456,
  "runtime_stream_seq": 8
}
```

Structured `chat.response.event` frames preserve the runtime's `metadata` and,
when present, the runtime `images` list.

### Dedicated replay and resync semantics

The backend retains the most recent **1024** replayable response-content frames
per request in memory.

Replay cursors are resolved in this order:

1. `Last-Event-ID` header, when supplied
2. `after` query parameter otherwise

Behavior is exact:

- Missing or blank `request_id` → `404 Unknown response stream`
- Unknown `request_id` → `404 Unknown response stream`
- Missing cursor → replay from the start of the retained frame list
- Invalid cursor or a cursor for another request → emit exactly one
  `chat.response.resync_required` frame with
  `{ "request_id": <id>, "reason": "invalid_cursor" }`, then close
- Expired cursor older than the retained buffer → emit exactly one
  `chat.response.resync_required` frame with
  `{ "request_id": <id>, "reason": "cursor_expired" }`, then close
- Valid retained cursor → replay only frames whose retained `seq` is strictly
  greater than the cursor sequence

While the request is still non-terminal and no content frame arrives for
`RESPONSE_STREAM_KEEPALIVE_SECONDS` (currently 15 seconds), the stream emits a
non-replayed transport heartbeat:

```json
{
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "timestamp": 1730000000.123
}
```

as SSE event `chat.response.heartbeat` without an SSE `id`.

The dedicated stream never carries lifecycle ownership. It does **not** emit
`chat.response.started`, `chat.response.completed`, `chat.response.failed`, or
`chat.response.cancelled`. Once the backend knows the request has reached a
terminal lifecycle outcome, the dedicated stream closes after it has replayed or
flushed every queued retained content frame.

## Finite request acknowledgement and correlation

For every finite browser application request, the frontend generates one UUID
and sends it as `X-Request-ID`. The successful response is exactly:

```json
{
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "event_types": ["chat.sessions.snapshot"]
}
```

An acknowledgement has no optional data field. `request_id` MUST equal the
client header value. The nonempty `event_types` array declares the causal
canonical event type or types that the operation awaits; each declared causal
canonical envelope MUST carry that same value at `payload.request_id`.

The required success ordering is:

1. Validate the request and persist/dispatch its synchronous work.
2. Enqueue every causal canonical `ui.event` envelope, each with
   `payload.request_id`.
3. Return the HTTP `202 Accepted` acknowledgement.

HTTP `202` therefore means **dispatched and accepted**, not browser completion.
The frontend records the acknowledgement as pending and completes the operation
only when an event with both a declared type and the same `request_id` arrives.
An event from another tab or process may update durable Redux data, but MUST NOT
resolve an unrelated local pending operation.

Failures retain a non-`202` HTTP status (for example validation `400`/`422`,
not found `404`, conflict `409`, unavailable `503`, or timeout `504`) and MUST
NOT emit a success event. The client clears a pending operation on HTTP failure
or an explicit UI timeout/reconnect policy; it does not treat the
acknowledgement itself as completion.

For browser chat generation, the acknowledgement and streams divide ownership:

- `POST /api/ui/responses` returns `202 {request_id,event_types}` with
  `event_types: ["chat.response.started"]`
- canonical `/api/ui/stream` carries lifecycle `ui.event` envelopes:
  `chat.response.started`, `chat.response.completed`, `chat.response.failed`,
  `chat.response.cancelled`
- dedicated `/api/ui/responses/{request_id}/stream` carries only
  `chat.response.delta` and `chat.response.event` content frames, plus
  `chat.response.heartbeat` and `chat.response.resync_required`
- `PUT /api/ui/responses/{generation_id}/cancel` returns
  `202 {request_id,event_types}` with
  `event_types: ["chat.response.cancel_requested"]`
- canonical terminal cancellation still arrives only as
  `chat.response.cancelled` correlated to the **target** response request ID

## Browser-retained route disposition

The browser-facing retained route families in this table use the acknowledgement
contract above. The listed stream(s) are the result/data source; route
responses never are.

| Domain | Browser routes | Result contract |
| --- | --- | --- |
| Chat sessions | `GET/POST /api/ui/chats`; `POST /api/ui/chats/draft`; `GET/PATCH/DELETE /api/ui/chats/{thread_id}`; `GET /api/ui/chats/{thread_id}/messages` | Canonical `ui.event` envelopes: `chat.sessions.snapshot`, `chat.session.created`, `chat.session.metadata.updated`, `chat.session.upserted`, request-correlated `chat.session.selected` (capped to the newest `CHAT_MESSAGE_PAGE_SIZE` messages, with `has_more_messages`), `chat.messages.page.loaded` (older-page pagination), `chat.session.deleted` |
| Generation | `POST /api/ui/responses`; `GET /api/ui/responses/{request_id}/stream`; `PUT /api/ui/responses/{generation_id}/cancel` | Canonical lifecycle envelopes: `chat.response.started`, `chat.response.completed`, `chat.response.failed`, `chat.response.cancelled`; dedicated response frames: `chat.response.delta`, `chat.response.event`; dedicated transport-only frames: `chat.response.heartbeat`, `chat.response.resync_required`; cancel acknowledgement declares `chat.response.cancel_requested` |
| Agent profiles | `GET /api/ui/agent-profiles`; `GET /api/ui/agent-profiles/{agent_id}`; `PATCH /api/ui/agent-profiles/{agent_id}/graph` | Canonical `ui.event` envelopes: `agents.snapshot`, `agent.upserted`, request-correlated `agent.selected`, `agent.graph.updated` |
| Inbox | `GET /api/ui/inbox`; `POST /api/ui/inbox/{message_id}/read`; `POST /api/ui/inbox/{message_id}/decision`; `DELETE /api/ui/inbox/{message_id}` | Canonical `ui.event` envelopes: `inbox.snapshot`, `inbox.message.upserted`, `inbox.message.deleted` |
| Search | `GET /api/ui/search` | Request-scoped `search.results` (sections: Inbox, Tasks, Agents, Routines, Workflows, Prompts, Knowledge, Tools, Schedules) |
| Knowledge | `GET/PUT /api/ui/knowledge`; `GET /api/ui/knowledge/{base_id}/pages`; `GET /api/ui/knowledge/{base_id}/page`; `GET /api/ui/knowledge/{base_id}/page/revisions`; `GET /api/ui/knowledge/{base_id}/page/revisions/{revision_id}` | Canonical `ui.event` envelopes: `knowledge.snapshot`, `knowledge.pages.snapshot`, `knowledge.page.loaded`, `knowledge.page.revisions.loaded`, `knowledge.page.revision.loaded`; indexer-originated `knowledge.index.started`, `knowledge.index.progress`, `knowledge.index.completed`, `knowledge.index.failed` |
| Workflows | `GET/POST /api/ui/workflows`; `GET/PUT /api/ui/workflows/content`; `DELETE /api/ui/workflows/{workflow_id}`; `POST /api/ui/workflows/{workflow_id}/pause`; `POST /api/ui/workflows/{workflow_id}/resume`; `POST /api/ui/workflows/{workflow_id}/trigger`; `GET /api/ui/workflow-runs` | Canonical `ui.event` envelopes: `workflows.snapshot`, `workflow.updated`, `workflow.deleted`, `workflow.runs.snapshot`, `workflow.run.started`, `workflow.run.updated`, `workflow.run.completed`, `workflow.run.failed` |
| Kanban and tasks | `GET /api/ui/kanban`; `POST /api/ui/kanban/cards`; `PATCH/DELETE /api/ui/kanban/cards/{card_id}`; `GET/PUT /api/ui/tasks/{card_id}/content`; `POST /api/ui/tasks/{card_id}/activity` | Canonical `ui.event` envelopes: `kanban.snapshot`, `task.content.updated`, `task.activity.updated` |
| Widgets and MCP | `GET /api/ui/widgets`; `GET /api/ui/mcp`; `GET /api/ui/mcp/content`; `PATCH/DELETE /api/ui/widgets/{widget_id}`; `POST /api/ui/widgets/{widget_id}/refresh` | Canonical `ui.event` envelopes: `widgets.updated`, `mcp.snapshot` (lean list, no per-tool/log detail), `mcp.server.detail.updated` (full tools/logs for one server) |
| Schedules and event widgets | `GET/POST /api/ui/schedules`; `POST /api/ui/schedules/{schedule_id}/trigger`; `POST /api/ui/events` | Canonical `ui.event` envelopes: `schedule.snapshot`, `widgets.updated` |
| Prompts | `GET /api/ui/prompts`; `GET/PUT /api/ui/prompts/content` | Canonical `ui.event` envelopes: `prompts.snapshot` (lean list, no file body), `prompt.content.updated` |
| Marketplaces and plugins | `GET/PUT /api/ui/marketplaces`; `GET /api/ui/marketplaces/{name}/plugins`; `GET/POST /api/ui/plugins`; `DELETE /api/ui/plugins/{marketplace}/{plugin}` | Canonical `ui.event` envelopes: `marketplaces.snapshot`, `plugins.snapshot`, request-scoped `plugin.catalog.snapshot`. Skills are no longer authored directly — they ship inside installed marketplace plugins. |
| Alerts | `GET /api/ui/alerts`; `POST /api/ui/alerts/viewed`; `POST /api/ui/alerts/{alert_id}/dismiss`; `POST /api/ui/alerts/{alert_id}/respond` | Canonical `ui.event` envelopes: `alerts.updated` and `alert` for independently generated alerts |
| Logs | `GET /api/ui/logs`; `GET /api/ui/logs/sources`; `GET /api/ui/logs/export` | Canonical `ui.event` envelopes: `logs.page.loaded`, `logs.sources.loaded`, `logs.export.started`, `logs.export.chunk`, `logs.export.completed` |

For log export, chunk events carry the download data; the browser reconstructs
the download locally. For browser generation with `stream: true`, lifecycle data
is canonical and response content is request-scoped on the dedicated response
stream. There is no response-body token stream.

Browser generation uses the `/api/ui/responses` routes above. It is distinct
from the backend-to-runtime `/v1/responses` and `/v1/events` protocols, which remain
internal and are not browser APIs.

`POST /api/ui/events` is a finite **event-widget creation** route if present in
the retained widget surface; it is not an EventSource endpoint and returns only
a `202` acknowledgement. Do not confuse it with the deleted `GET /api/ui/events`
legacy stream.

## Removed client surface

The following are intentionally absent rather than compatibility endpoints:

- `GET /health`, `GET /health/detailed`, `GET /ready`, `GET /live`,
  `GET /v1/models`, and `POST /chat`.
- Attachment upload/download routes and legacy session, preferences, settings,
  and conversation APIs.
- `POST /agents/register`.
- `GET /api/ui/status/snapshot`, `/loops`, `/agents`, `/backlog`, `/schedule`,
  `/tasks`, `/agents/{agent_name}`, `/database/schema`, and
  `/telegram/status`, plus `POST /api/ui/telegram/test`.
- `GET /api/ui/agent-profiles/graph-schema`, and
  the unmounted workspace-only surface, including `/api/ui/workspace`,
  `/api/ui/widget-schemas`, and their consumers.
- `GET /api/ui/events` and every `/api/ui/status` EventSource variant.

A deleted path returns `404`; it is not a fallback for a stream failure.

## Local development

Start the runtime and backend separately:

```bash
source .venv/bin/activate
python -m app.progenitor.main
```

```bash
source .venv/bin/activate
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

The frontend development server proxies `/api`, `/v1`, and operational health
requests to the backend. Client code should use this document—not proxy details
or historical endpoint responses—as its API contract.
