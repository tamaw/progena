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

## Message output usage and completed work

Message `tokenCount` is provider-reported output for the root-model calls grouped
with that reply; `finalCallTokenCount` is the final visible model call's output.
The tooltip separates the response total, intermediate calls and final call.
Input and delegated-agent usage are excluded; reasoning is a subset of output,
not a second amount to add. A known zero is valid. Missing usage remains unknown:
neither stream-chunk counts nor text-length estimates may become exact output
usage. A known final-call count remains visible if an earlier call makes the
whole-response total unavailable.

The existing dedicated-stream `usage_update` carries authoritative nullable
`output_tokens`, `final_call_output_tokens` and canonical `response_status`.
Provider/native interruption survives a completed transport callback. Actual
transport errors and cancellation remain terminal. Canonical reconciliation
compares usage and status as well as content, so unchanged text can still receive
correct completed counts.

Scratch history and handoff messages retain exact counts only with
`usageProvenance: 'provider'`. Legacy local transport-frame counters are cleared
from both the message and any denormalized assistant timeline rows; text and
other transcript data remain available. A canonical history read supplies fresh
provider counts. This does not authorize storing backend transcripts locally.

All assistant rendering uses the same timeline path. Root work phases are
sibling keyboard-operable disclosures, not children of a whole-turn summary.
The initial generic `Working` phase ends when reasoning arrives; each reasoning
section's title becomes the next top-level header and owns the tools that follow.
Unheaded reasoning uses the neutral title `Reasoning`. Completed phases start
collapsed. One expansion reveals the full available reasoning and named tool
rows; each tool keeps its own input/output disclosure. A title-only provider
summary has no additional body to invent. Tool results arriving across a reasoning
boundary remain with their initiating call, and do not extend the prior phase's
elapsed time past that boundary. Delegated-agent cards, the answer and conversation
summary markers retain their existing placement. Native reasoning headings remain
verbatim; they are not rewritten as evidence that an action succeeded.

Work state comes from actual response/provider/native interrupt evidence:
completed, failed, cancelled, interrupted, or neutral unknown. A failed tool is
shown as a failure count, not automatically as a failed whole response. Elapsed
work time remains the transcript span through answer start, not tool CPU time.
Provider-only failure with no visible answer/work produces a terminal failure
row even when transport completes normally; canonical reload preserves it.
Partial answers and visible work keep their failure status without a duplicate
synthetic row. Background completion may use provider status only from its own
stream ID, never from the currently selected conversation's live turn.

The conversation header uses backend `session.usage_totals`, not sums over the
currently loaded message page or streamed reasoning text. These are cumulative
root output tokens and their reasoning subset; partial coverage is shown as a
recorded lower bound. Unknown output stays unavailable; unreported reasoning
displays `0` in the header without changing backend coverage. Selection and accepted canonical
completion reconciliation replace the totals within the same thread/ownership
fences as the transcript. The live answer is not added twice.
Absent reasoning/cache counters normalize to the canonical zero for equivalence,
so materializing user-message defaults does not retire/remount a frozen answer.
Unknown output usage remains distinct from reported zero.

The composer circle remains the latest provider-reported context footprint,
updated through existing context-only `usage_update` events during root tool
loops. After automatic native compaction, its styled tooltip can show the
observed input decrease from `context_usage.compaction`. The reduction is
explicitly not exact tokenizer savings, is available only after comparable
provider usage arrives, and never subtracts from lifetime header totals.
The retained summary/recent messages still occupy context; do not reset to zero.
New ChatGPT reasoning streams attach model attribution only once so chunk merging
does not concatenate deployment names and hide the circle through a failed
profile match. Existing malformed historical attribution is not guessed or
rewritten. Old summaries without recorded comparable footprints do not acquire
invented savings figures.

Message token labels omit model names. Token, context and cache metric details
use `ChatInfoTooltip`, a portal reusing the shared `chat-info-tooltip` styling.
Hovered details follow the pointer; keyboard focus anchors them beside the
trigger. Bubbles flip and clamp inside the viewport instead of appearing at the
page top. Details are compact label/value rows; observed input reductions and
cache estimates remain labelled. Hover keeps the default cursor. Escape,
blur/pointer exit and scrolling dismiss details without moving focus.



## Conversation summaries

Native summarization uses the existing response-content activity and committed
chat history. `Compacting conversation` is transient model-generation activity;
it is never promoted to a completed marker merely because a callback finished.
Committed `summary_markers` render as keyboard-expandable transcript rows with
an amber dot, shared message header/body styling, and sanitized Markdown.
The expanded body is the actual generated summary, not its model-facing wrapper.
Stable scope/anchor identities preserve placement and deduplication during paging
and canonical reconciliation; collapsed rows do not parse Markdown.
The committed row is labelled `Conversation compacted`, avoiding regional
summarise/summarize spelling differences without changing event names.

Transcript gutters keep user messages standalone. Intermediate response rows
connect to the next response dot across the actual row gap, including expanded
summaries/work; they stop before another user message. Assistant and pending
answer dots receive from above and never continue below. Live compaction
activity precedes the pending answer cursor, so it remains inside the response
chain rather than following its terminal dot.

Original messages remain visible and searchable. Nested summaries stay inside
their subagent activity. Completed markers must remain visible with debug events
filtered out, and when a frozen live owner suppresses its historical answer twin.
Do not introduce another EventSource, polling, or exact duration/savings claims.
Tool-output offload and prompt-argument trimming indicators are not implemented.


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
