# Event Stream Transport Refactor Plan

## Decision

The checked-in implementation uses a **two-stream browser contract**:

1. **Canonical app-lifetime SSE:** `GET /api/ui/stream`
   - carries `UiEventEnvelope` frames as SSE event `ui.event`
   - owns connection lifecycle, retained UI state, and chat response lifecycle
     (`chat.response.started|completed|failed|cancelled`)
2. **Dedicated request-scoped SSE:** `GET /api/ui/responses/{request_id}/stream`
   - carries direct `chat.response.delta` and `chat.response.event` payloads for
     one accepted streaming chat request
   - also carries transport-only `chat.response.heartbeat` and
     `chat.response.resync_required`

This is the contract to preserve. Successful finite `/api/ui/*` requests still
return only `202 {request_id,event_types}` acknowledgements; they do not carry
application data in the HTTP body or as an inline response-body stream.

`GET /healthz` remains the sole operational `200 OK` exception for container
liveness.

## Target contract

### Canonical stream

`GET /api/ui/stream` remains the app-wide browser event bus. It returns
`200 text/event-stream` and emits only `ui.event` envelopes.

Responsibilities:

- connection lifecycle (`connection.ready`, `connection.heartbeat`,
  `connection.shutdown`)
- retained domain snapshots and mutations
- chat response lifecycle (`chat.response.started`, `chat.response.completed`,
  `chat.response.failed`, `chat.response.cancelled`)
- request correlation through `payload.request_id`
- bounded `Last-Event-ID` replay over the canonical envelope buffer

### Dedicated response stream

`GET /api/ui/responses/{request_id}/stream` is opened only for active browser
chat requests that were accepted with `stream: true`.

Responsibilities:

- live text deltas: `chat.response.delta`
- structured live response events: `chat.response.event`
- bounded per-request replay using SSE id `chat-response:{request_id}:{seq}`
- reconnect cursor via `Last-Event-ID` or `?after=`
- explicit loss signaling via one `chat.response.resync_required` frame with
  reason `invalid_cursor` or `cursor_expired`
- idle keepalive via `chat.response.heartbeat`

Non-responsibilities:

- no `UiEventEnvelope`
- no lifecycle ownership
- no `chat.response.started|completed|failed|cancelled`
- no global Redux hydration outside the active chat transport owner

### Finite request acknowledgement

All retained finite browser routes keep the acknowledgement contract:

```json
{
  "request_id": "<client-supplied X-Request-ID>",
  "event_types": ["<causal canonical event type>"]
}
```

Rules:

1. The frontend generates one UUID operation ID per finite request and sends it
   in `X-Request-ID`.
2. The backend validates and persists/dispatches work before acknowledging.
3. The backend enqueues causal canonical `ui.event` envelopes before returning
   `202`.
4. Browser chat generation may additionally require the client to open the
   dedicated response stream after the acknowledgement.
5. `202` means accepted and enqueued, not delivered or rendered.
6. Failures keep their non-`202` HTTP status and emit no success event.

## Route disposition

| Domain | Browser routes | Result transport |
| --- | --- | --- |
| Chat sessions | `GET/POST /api/ui/chats`; `POST /api/ui/chats/draft`; `GET/PATCH/DELETE /api/ui/chats/{thread_id}` | canonical `ui.event` envelopes only |
| Generation | `POST /api/ui/responses`; `GET /api/ui/responses/{request_id}/stream`; `PUT /api/ui/responses/{generation_id}/cancel` | canonical lifecycle on `/api/ui/stream`; content/replay/resync on `/api/ui/responses/{request_id}/stream`; cancel acknowledgement declares `chat.response.cancel_requested` |
| All other retained `/api/ui/*` domains | existing retained routes | canonical `ui.event` envelopes only |

## Frontend ownership

- `RealtimeBridge` / `realtimeListener.js` own the canonical app-lifetime
  stream.
- `useChatApi()` owns the request-scoped response stream for active chat.
- Redux remains the durable owner of app state hydrated from canonical events.
- Live-turn rendering consumes the dedicated response stream directly and relies
  on canonical lifecycle for terminal ownership.

## Backend ownership

- `app/backend/ui_routes.py` owns canonical stream broadcast/replay.
- `app/backend/main.py` owns request-scoped response-stream state, replay,
  resync signaling, and chat lifecycle splitting.
- `POST /api/ui/responses` acknowledges with `chat.response.started`; the
  dedicated stream is keyed by that acknowledged request ID.
- `PUT /api/ui/responses/{generation_id}/cancel` acknowledges with
  `chat.response.cancel_requested`; canonical `chat.response.cancelled` remains
  the authoritative terminal cancellation signal for the target request.

## Documentation updates required

Authoritative docs MUST describe the real split above:

- `docs/BACKEND.md` — normative route/transport contract
- `docs/SPEC.md` — system summary
- `docs/ARCHITECTURE_DIAGRAM.md` — topology and flow diagrams
- `CLAUDE.md` — contributor guidance
- test plans and operator notes that previously assumed one browser EventSource

They must no longer claim that canonical `/api/ui/stream` carries response
deltas or structured response events, and must no longer claim that active chat
uses exactly one `EventSource`.

## Verification focus

### Backend / contract verification

- Open canonical `GET /api/ui/stream`; confirm chat requests publish only
  lifecycle envelopes there.
- Open dedicated `GET /api/ui/responses/{request_id}/stream`; confirm it emits
  `chat.response.delta` / `chat.response.event` frames with replay IDs
  `chat-response:{request_id}:{seq}`.
- Verify `?after=` and `Last-Event-ID` replay only retained frames newer than
  the cursor.
- Verify invalid/expired cursors emit exactly one
  `chat.response.resync_required` frame and close.
- Verify cancel acknowledgement is `chat.response.cancel_requested` while only
  the target request receives canonical terminal `chat.response.cancelled`.

### Frontend verification

- Confirm the app keeps one canonical `EventSource` to `/api/ui/stream`.
- Confirm active streaming chat opens an additional request-scoped
  `EventSource` to `/api/ui/responses/{request_id}/stream`.
- Confirm reconnects rebuild the dedicated URL with `?after=<lastEventId>`.
- Confirm dedicated replay avoids duplicate live content and dedicated resync
  falls back to canonical terminal reconciliation.

## Final proof

The transport split is correct only when all of the following are true:

- Successful finite browser requests return exact `202 UiRequestAcknowledgement`
  bodies and no application data.
- Canonical `/api/ui/stream` remains the app-wide lifecycle/state bus.
- Canonical `/api/ui/stream` does **not** carry `chat.response.delta` or
  `chat.response.event` for browser streaming chat.
- Active browser chat opens and consumes the dedicated
  `/api/ui/responses/{request_id}/stream` transport.
- Dedicated replay, reconnect, and resync behavior are explicitly tested.
- No documentation or test plan still describes a single-stream browser chat
  transport.
