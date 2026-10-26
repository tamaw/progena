# Chat Frontend Notes

Quick reference for the two-stream chat page.

## Event-driven chat contract

Durable application state and chat lifecycle arrive on the app-lifetime
`/api/ui/stream` as `ui.event` envelopes; streaming chat content arrives only
on the request-scoped `/api/ui/responses/{request_id}/stream`. Neither arrives
in a finite HTTP response.

1. The frontend generates one UUID for each finite chat operation and sends it
   in `X-Operation-Id`.
2. The backend persists or dispatches work, enqueues the causal canonical
   `ui.event` lifecycle envelope, then returns HTTP `202 Accepted`
   (`{request_id, event_types}`).
3. `realtimeListener` receives canonical `event: ui.event` frames from
   `/api/ui/stream`, deduplicates envelope IDs, and dispatches typed reducers.
   It carries application data and chat lifecycle, never high-rate response content.
4. After an acknowledged streaming dispatch, `useChatApi` opens
   `GET /api/ui/responses/{request_id}/stream` for only that response's
   `chat.response.delta` and `chat.response.event` frames.
5. Redux owns durable chat data and canonical lifecycle. Pages use the matching
   request-correlated terminal lifecycle event—not the acknowledgement—to
   complete an operation.

A causal envelope repeats the request ID in `payload.request_id`. The pending
operation registry may be resolved only when both the request ID and declared
event type match. **Do not discard an event merely because its request ID is not
pending in this tab.** A global event from another tab/process still updates the
shared Redux chat state; it simply must not resolve this tab's unrelated pending
operation.

Relevant canonical types are:

- `chat.sessions.snapshot` for a durable session-list snapshot;
- `chat.session.created`, `chat.session.metadata.updated`, and
  `chat.session.upserted` for session data;
- request-scoped `chat.session.selected` for selecting/loading a detail;
- `chat.session.deleted` for durable removal;
- `chat.response.started`, `chat.response.completed`,
  `chat.response.failed`, and `chat.response.cancelled` for generation lifecycle.

The dedicated response stream carries only `chat.response.delta` and
`chat.response.event`. `POST /api/ui/responses`, including a body with
`stream: true`, remains a finite HTTP `202` acknowledgement request.
`PUT /api/ui/responses/{generation_id}/cancel` acknowledges the cancellation
operation with `chat.response.cancel_requested`; only the target request's
canonical `chat.response.cancelled` is terminal. Content is not sent through
canonical stream envelopes or a response-body SSE parser.

## Stream reconnect and pending operations

The canonical UI stream has a bounded envelope replay window. A dedicated
response stream has an independent bounded content replay window. `useChatApi`
recreates a response EventSource with `?after=<last-frame-id>` when its content
transport stalls or errors, avoiding duplicate deltas. If the cursor is invalid
or expired, the dedicated endpoint emits `chat.response.resync_required` and
closes. The hook stops appending live content but waits for the canonical terminal
lifecycle event, then reconciles the persisted chat detail. A `202`
acknowledgement remains pending across reconnect until its matching terminal,
request failure, or explicit cancellation policy.

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

## Streaming-render performance invariants

The active response has a separate, intentionally narrow render owner. Preserve
these boundaries when changing chat state or presentation:

1. `useChatStream` owns a `liveTurnStore` for the active response.
   `LiveAssistantTurn` is the only full-turn `useSyncExternalStore` subscriber.
   Ordinary `chat.response.delta` updates MUST NOT append to page-level
   `messages` or trigger a `ConversationPage` render.
2. Publish the first accepted text delta immediately. Aggregate later text
   deltas and publish at most once per animation frame. Structural stream events
   flush pending text first and publish immediately so their order remains
   correct.
3. Render live assistant and subagent text literally. Markdown formatting and
   sanitization run only after a turn or its trailing subagent entry settles.
   Do not add a growing-response Markdown pass, HTML sanitization pass, or DOM
   parse to any live-delta path; otherwise a closed fenced block is repeatedly
   re-highlighted as later deltas arrive.
4. A foreground terminal event MUST flush its final aggregate, leave the live
   turn present through a paint opportunity, then freeze that same live owner
   with `isStreaming: false`. Its committed local-history twin is suppressed
   until the next live turn or session switch clears/replaces the owner. This
   keeps the cursor-to-final transition in place rather than replacing the
   transcript row.
5. A foreground terminal event MUST NOT call `reloadOpenChat`. The selected
   detail request sets the active chat to loading and can replace the live turn
   before it paints. After the local handoff, request canonical persisted detail
   through the isolated non-blocking reconciliation path. It must apply only if
   the displayed thread, foreground ownership token, and local transcript
   snapshot still match; otherwise discard it. Background-stream completion
   retains its explicit refresh/reconciliation path.
6. Final Markdown rendering is cached by source text so a live-to-history
   handoff or canonical merge does not parse, sanitize, and highlight the same
   code block again. Preserve historical message object identity for
   rendering-equivalent canonical rows. Fences above the highlighter budget
   remain escaped visual code blocks without synchronous syntax coloring.
7. Do not pass generation-wide changing values through every historical message
   or timeline item. `RegenerateDisabledContext` updates only
   `MessageActions`; historical `Message` and `TimelineItem` memo boundaries
   stay intact when generation begins or ends. The page-level regenerate
   dispatcher must keep a stable identity and read current transcript state at
   click time rather than close over `messages`.

### Performance regression checks

When modifying these paths, run:

```bash
npm test -- tests/frontend/unit/features/chat/hooks/useChatStream.test.js tests/frontend/unit/realtime/realtimeListener.unit.test.js tests/frontend/unit/features/chat/components/Message.copy.unit.test.jsx tests/frontend/unit/features/chat/models/conversationModel.unit.test.js tests/frontend/unit/features/chat/models/messageRenderUtils.unit.test.js tests/frontend/unit/features/chat/components/TimelineItem.unit.test.jsx tests/frontend/unit/features/chat/components/SubagentCard.unit.test.jsx tests/frontend/unit/lib/markdown.unit.test.js tests/frontend/unit/store/uiDataSlice.unit.test.js
npm run build
```

For an end-to-end check, send a response that emits tool events followed by a
multi-paragraph Markdown reply. Confirm that the first text arrives promptly,
later text advances at visual-frame cadence, and the last streamed text remains
visible before the historical Markdown rendering takes over. Do not treat a
same-route transition to `Loading...`, a blank transcript, or a full-detail
reload at foreground completion as acceptable reconciliation behavior.

For very long chats, measure before adding virtualization or `content-visibility`.
Those techniques can interfere with sticky-bottom scroll anchoring and require
their own accessibility and scroll-position tests.
