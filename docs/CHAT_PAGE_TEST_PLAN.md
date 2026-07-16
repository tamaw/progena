# Chat Page — Functional Test Plan

> Iteration 3 (final). Each behavior is a discrete functional test with an
> expected result, test layer, priority, and current coverage status.

**Prerequisite:** the frontend has no test harness yet. Unit/comp/hook/e2e rows
assume adding Vitest + React Testing Library (and Playwright for e2e). Backend
(api) rows run under the existing `pytest tests/backend` setup. e2e flows can
run against `PROGENITOR_MOCK_LLM=true` for determinism. Background-error cases
inject a canonical `chat.response.failed` `ui.event` envelope after a matching
`chat.response.started` event; they MUST NOT mock a response-body SSE stream.

**Architecture note:** Chat still follows the event-driven browser contract. Finite HTTP requests return HTTP `202 Accepted` (`{request_id, event_types}`) with no application data in response bodies. Chat session state and chat response lifecycle events (`chat.sessions.snapshot`, `chat.session.upserted`, `chat.response.started`, `chat.response.completed`, etc.) arrive on canonical `/api/ui/stream` and hydrate Redux. Active streaming response content (`chat.response.delta` and `chat.response.event`) arrives on the request-scoped `/api/ui/responses/{request_id}/stream`. Tests MUST NOT assert chat data in HTTP response bodies or canonical delta frames; instead they assert the `202` acknowledgement, canonical lifecycle events, and the dedicated response stream.

## Scope
The chat page and its event-driven dependencies:
`pages/ConversationPage.jsx`, `components/chat/*`, chat request helpers,
`hooks/useChatSessionPrefs.js`, `store/*`, `chat_routes.py` /
`chat_sessions.py`, the canonical `/api/ui/stream` lifecycle contract, and the
request-scoped `/api/ui/responses/{request_id}/stream` contract used by active
chat responses.

## Test layers & tooling
- **unit** — pure functions/models (`conversationModel`, `chatSessionsModel`,
  event reducers, filters, `chatStorage`). Vitest.
- **comp** — React component behavior (`Message`, `ChatInput`,
  `StreamFilterMenu`, `QuickActions`). Vitest + React Testing Library.
- **hook** — request helpers, `RealtimeBridge`, `useChatApi`, reducers, and
  page lifecycle with mocked canonical and dedicated `EventSource` instances +
  Redux store.
- **e2e** — full chat page against a running/mock stack. Playwright.
- **api** — backend FastAPI routes plus captured canonical lifecycle events and
  dedicated response-stream frames.

## Legend
- Priority: **P1** core/high-risk · **P2** important UX · **P3** cosmetic/edge.
- Status: ✅ covered by an existing test · ◻ not yet covered.

---

## A. Conversation transcript — `Message.jsx`

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| TR-01 | Render user message | Badge "U", "Message" label, content shown | comp | P1 | ◻ |
| TR-02 | Render assistant message | Badge "P", "Progenitor" label, markdown HTML | comp | P1 | ◻ |
| TR-03 | Markdown rendering | Headings/lists/code/links/tables render as HTML | comp | P2 | ◻ |
| TR-04 | Token count when finished | `N tokens` (1→"token"); hidden while streaming/zero | comp | P2 | ◻ |
| TR-05 | Reasoning token count | `N reasoning tokens` shown when > 0 | comp | P3 | ◻ |
| TR-06 | Model name in header | `model` text shown in head-trailing when present | comp | P2 | ◻ |
| TR-07 | Reasoning summary entry | Reasoning content renders as a timeline entry | comp | P2 | ◻ |
| TR-08 | Tool rows | tool_start/result/error → kind, desc, command, body, duration | comp | P2 | ◻ |
| TR-09 | Image strip + lightbox | tool_result images render; click opens modal; Esc closes | comp | P2 | ◻ |
| TR-10 | Streaming caret | `.message.streaming` shows blinking caret | comp | P3 | ◻ |
| TR-11 | Scheduled badge | `isScheduled` renders the "Scheduled" badge | comp | P3 | ◻ |
| TR-12 | Error rendering | `error` → error styling + `Error: …` content | comp | P2 | ◻ |
| TR-13 | Cancelled suffix | Cancelled finalization appends ` [Cancelled]` | comp/hook | P2 | ◻ |
| TR-14 | Copy / Regenerate | Copy copies content; Regenerate only on assistant | comp | P3 | ◻ |
| TR-15 | Agent filter hides content | `hideContent` hides bubble but keeps connected tool rows | comp | P2 | ◻ |
| TR-16 | Image filter | `showImages=false` hides the image strip | comp | P3 | ◻ |
| TR-17 | Memoization | Streaming one message does not re-render finished ones | comp | P2 | ◻ |

## B. Composer — `ChatInput.jsx`

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| CO-01 | Enter sends | Non-empty trimmed message sent; input cleared | comp | P1 | ◻ |
| CO-02 | Shift+Enter newline | Newline inserted, not sent | comp | P2 | ◻ |
| CO-03 | Send disabled states | Disabled when empty / generating / `connectionState==='error'` | comp | P1 | ◻ |
| CO-04 | Stop button while generating | Stop shows in place of Send; click → onStop | comp | P1 | ◻ |
| CO-05 | Esc stops generation | Esc while generating → onStop (when allowStop) | comp | P2 | ◻ |
| CO-06 | Slash menu opens | Typing `/` opens menu; `/new` listed and filtered | comp | P2 | ◻ |
| CO-07 | Slash menu keyboard nav | Arrow up/down cycles; Enter runs highlighted | comp | P2 | ◻ |
| CO-08 | `/new` executes command | Sends command `new`, clears input | comp | P2 | ◻ |
| CO-09 | Textarea auto-resize | Height grows with content up to cap (200px) | comp | P3 | ◻ |
| CO-10 | Autofocus / focusToken | Focuses on mount and when `focusToken` changes | comp | P3 | ◻ |
| CO-11 | Keep focus after send | Focus retained when `keepFocusAfterSend` | comp | P3 | ◻ |

## C. Generation lifecycle — canonical lifecycle + dedicated response stream

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| ST-01 | Acknowledged dispatch remains pending | `POST /api/ui/responses` returns only an HTTP `202` acknowledgement; no transcript is hydrated until matching lifecycle/content events arrive | hook/api | P1 | ◻ |
| ST-02 | Started lifecycle event | Canonical `chat.response.started` creates/marks the in-progress assistant state | hook | P1 | ◻ |
| ST-03 | Dedicated stream opens by request ID | After acknowledgement, the client opens `/api/ui/responses/{request_id}/stream` for that request | hook/e2e | P1 | ◻ |
| ST-04 | Delta accumulation | Dedicated `chat.response.delta` frames append visible content and token metadata in order | hook | P1 | ◻ |
| ST-05 | Structured live events | Dedicated `chat.response.event` frames preserve metadata, reasoning/tool events, and optional images without a response-body parser | hook/unit | P2 | ◻ |
| ST-06 | Normal completion | Canonical `chat.response.completed` finalizes the message and refreshes durable chat state through correlated canonical events | hook | P1 | ◻ |
| ST-07 | Failure path | Canonical `chat.response.failed` shows an error and clears the operation/generation state | hook | P1 | ◻ |
| ST-08 | Cancellation semantics | `PUT /api/ui/responses/{generation_id}/cancel` returns `202`/`chat.response.cancel_requested`; only canonical `chat.response.cancelled` finalizes cancellation | hook/api | P2 | ◻ |
| ST-09 | Concurrent attribution | Interleaved lifecycle events and dedicated content frames remain attached to their request/thread; no response overwrites another | hook | P1 | ◻ |
| ST-10 | No response-body stream | `/api/ui/responses` never returns token SSE or response data, including `stream: true` | api | P1 | ◻ |
| ST-11 | Thread routing | A generation request includes the selected session `thread_id`; canonical lifecycle and dedicated content frames preserve that thread context | api | P1 | ◻ |
| ST-12 | Dedicated replay | Reconnecting `/api/ui/responses/{request_id}/stream?after=<frame-id>` replays only newer retained frames and does not double-append content | hook/api | P1 | ◻ |
| ST-13 | Dedicated resync_required | Invalid/expired dedicated cursors emit `chat.response.resync_required`; the UI stops appending live content and waits for canonical terminal reconciliation | hook/api | P1 | ◻ |
| ST-14 | Request-ID correlation | Only matching canonical lifecycle events and dedicated frames for the submitted `request_id` resolve pending generation state | hook/api | P1 | ◻ |

## D. Background canonical generation

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| BG-01 | Switch mid-generation keeps it alive | Canonical response events continue after navigation away | e2e/hook | P1 | ◻ |
| BG-02 | Orange dot on origin | Origin card shows pulsing orange dot while a response is in progress | e2e | P1 | ◻ |
| BG-03 | Multiple orange dots | N concurrent background generations each show their own dot | e2e/unit | P1 | ◻ |
| BG-04 | Complete → unread + top | Background terminal event → green dot + card sorts to top | e2e | P1 | ◻ |
| BG-05 | Switch-back reload (no false unread) | Returning to a generating chat renders its canonical events in place, not as unread | e2e/hook | P1 | ◻ |
| BG-06 | Select clears dot | Selecting an unread/errored chat clears its local presentation state | e2e | P1 | ◻ |
| BG-07 | Cross-chat send does not cancel | A request in chat B leaves chat A's generation pending | e2e/hook | P1 | ◻ |
| BG-08 | Concurrent attribution | Two generations' event sequences land in their correct chats (no swap) | e2e/hook | P1 | ◻ |
| BG-09 | Background error → red dot | A background terminal failure shows a red dot in place of green | e2e/hook | P1 | ◻ |
| BG-10 | Error cleared by later success | A later completed event reverts red → green | hook | P2 | ◻ |

## E. Session sidebar — `chatSessionsModel` + `conversationModel`

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| SB-01 | Time-bucket grouping | Items grouped Pinned/Today/Yesterday/Last Week/Earlier | unit | P1 | ◻ |
| SB-02 | Sort order | Pinned first, then most-recent activity | unit | P1 | ◻ |
| SB-03 | Search filter | Filters by title + hidden summary substring | unit | P2 | ◻ |
| SB-04 | Tab filters | All/Unread/Shared/Archived each show the right subset | unit | P1 | ◻ |
| SB-05 | Empty-state text | Correct message per tab + loading | unit | P3 | ◻ |
| SB-06 | Card counts | msgs/tools shown; error count only when > 0 | unit/comp | P2 | ◻ |
| SB-07 | Pin toggle (local) | Local pin persists id to localStorage | hook | P2 | ◻ |
| SB-08 | Pin toggle (backend) | Backend pin PATCHes metadata | hook | P2 | ◻ |
| SB-09 | Share toggle | Local persists + success toast; backend PATCHes | hook | P2 | ◻ |
| SB-10 | Archive | Backend PATCH archived; local archives + starts new chat | hook | P2 | ◻ |
| SB-11 | Delete | Backend soft-delete; local starts new chat | hook | P2 | ◻ |
| SB-12 | Auto-select top on load | Top backend card selected once after sessions load | e2e/hook | P1 | ◻ |
| SB-13 | Ghost-card prevention | No "Current" local card once a backend chat is selected | unit/e2e | P1 | ◻ |
| SB-14 | Backend/local dedupe | Backend card matching the local session id is filtered out | unit | P2 | ◻ |
| SB-15 | Prune stale pin/share ids | Persisted ids whose chats vanished are pruned | hook | P3 | ◻ |

## F. Chat lifecycle & title

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| LC-01 | New chat | "+ New"/`/new` creates a backend chat, clears transcript | e2e/hook | P1 | ◻ |
| LC-02 | Title rename | Edit + Enter saves; Esc/blur cancels | comp/e2e | P2 | ◻ |
| LC-03 | Derived title | Title derives from first user message when not custom (≤72 fe / 64 be) | unit | P2 | ◻ |
| LC-04 | Custom title resets on session change | Switching sessions clears a session-scoped custom title | hook | P2 | ◻ |
| LC-05 | Thread routing preserves history | Continuing a backend chat reuses its thread_id | e2e/api | P1 | ◻ |

## G. Stream filters — `streamFilters` + `StreamFilterMenu`

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| FL-01 | Category visibility | `isEventVisible` honors per-category flags | unit | P2 | ◻ |
| FL-02 | Persist filters | Toggles persist to localStorage; reload restores | comp | P2 | ◻ |
| FL-03 | "All" chip | Enables every category | comp | P3 | ◻ |
| FL-04 | Event counts | Per-category counts match the transcript | comp/unit | P3 | ◻ |
| FL-05 | Chat-tab only | Filter toolbar hidden on the Text tab | e2e | P2 | ◻ |

## H. Chat / Text tabs

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| TB-01 | Tabs render, Chat default | Chat/Text in header; Chat active initially | e2e | P2 | ◻ |
| TB-02 | Text transcript format | `HH:MM <nick> message`, one line per message | unit/e2e | P2 | ◻ |
| TB-03 | Nick colors | Per-nick hashed colors; own nick fixed/highlighted | unit | P3 | ◻ |
| TB-04 | No images in Text | Text view never renders images | e2e | P3 | ◻ |
| TB-05 | Tab switch swaps content | Chat shows bubbles, Text shows transcript | e2e | P2 | ◻ |

## I. Realtime store — `realtimeListener` / `realtimeSlice` / `uiDataSlice`

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| RT-01 | Canonical stream + dedupe | One canonical `EventSource` targets `/api/ui/stream`; duplicate envelope ids are ignored and active chat may additionally own a request-scoped response stream | hook | P1 | ◻ |
| RT-02 | sessions snapshot | `chat.sessions.snapshot` replaces the chat list from its envelope payload | unit | P1 | ◻ |
| RT-03 | Durable chat mutations | Created, metadata-updated, and upserted events update Redux whether locally requested or global | unit | P1 | ◻ |
| RT-04 | Global event does not resolve local work | An event from another request/tab updates state but leaves this tab's unrelated pending operation pending | unit | P1 | ◻ |
| RT-05 | Selected correlation | `chat.session.selected` applies detail and resolves only its matching request ID | unit | P1 | ◻ |
| RT-06 | Deleted replay | `chat.session.deleted` removes state safely, including a replayed delete | unit | P1 | ◻ |
| RT-07 | Ready/resync | `connection.ready` after reconnect triggers needed correlated snapshot requests when store state is not safe to continue | hook | P1 | ◻ |
| RT-08 | Selector memoization | `selectChatSessions` has a stable result when chat state is unchanged | unit | P3 | ◻ |

## J. Backend chat routes — `chat_routes` / `chat_sessions`

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| BE-01 | List + snapshot | `GET /api/ui/chats` returns HTTP **202** acknowledgement; emits correlated `chat.sessions.snapshot` on `/api/ui/stream` | api | P1 | ◻ |
| BE-02 | Detail selection event | `GET /api/ui/chats/{id}` returns HTTP **202** acknowledgement; emits request-scoped `chat.session.upserted` and `chat.session.selected` | api | P1 | ◻ |
| BE-03 | Rehydrate from checkpoint | A detail envelope rebuilds card history from the checkpoint thread | api | P1 | ◻ |
| BE-04 | Soft-delete hides from list | DELETE persists soft deletion and emits a correlated deleted/snapshot result; the list hides it | api | P1 | ◻ |
| BE-05 | Materialize current-state card | PATCH/DELETE on the live current-state card materializes then applies; both acknowledge with HTTP **202** and stream causal events | api | P1 | ◻ |
| BE-06 | PATCH on soft-deleted → 404 | Patching a deleted chat returns 404 and emits no success event | api | P2 | ◻ |
| BE-07 | Create returns acknowledgement | `POST /api/ui/chats` returns only an HTTP `202` acknowledgement; chat detail arrives via `chat.session.created` and snapshot events | api | P2 | ◻ |
| BE-08 | Title display derivation | Default titles derive from the first user message | api/unit | P2 | ◻ |
| BE-09 | Detail payload completeness | Detail event carries tool calls and images from the checkpoint | api | P2 | ◻ |
| BE-10 | Event request ID | Every causal envelope `payload.request_id` equals the submitted `X-Request-ID` | api | P1 | ◻ |
| BE-11 | No application data in acknowledgement | The only body is `{request_id, event_types}` | api | P1 | ◻ |

## K. Persistence — `chatStorage` + page effects

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| PST-01 | Local history persisted | Local session history written to localStorage (cap 100) | hook | P2 | ◻ |
| PST-02 | Ephemeral events stripped | Only persisted categories kept; images dropped | unit | P2 | ◻ |
| PST-03 | Backend chats not persisted | Selecting a backend chat removes local chat history key | hook | P1 | ◻ |
| PST-04 | Pinned/shared/archived ids | Round-trip through localStorage with validation | unit | P3 | ◻ |
| PST-05 | Quick chat handoff | Loaded once on mount; cleared after | hook | P2 | ◻ |

## L. Agent / scheduled messages, notifications & initial states

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| AG-01 | Scheduled/agent message appended | `agent_message`/`agent_question`/`scheduled_response` adds an assistant message + notification | hook | P2 | ◻ |
| AG-02 | `SAY:` prefix stripped | Leading `SAY: ` removed from content | unit/hook | P3 | ◻ |
| AG-03 | Dedup within 5s | Same content within 5s is not added twice | hook | P2 | ◻ |
| AG-04 | `agent_question` styling | Question shows as a warning-style/question message | hook/comp | P3 | ◻ |
| AG-05 | `message_queued` toast | Queued event shows an info notification | hook | P2 | ◻ |
| AG-06 | `alert` toast | Alert event shows a notification with type/title/message | hook | P3 | ◻ |
| AG-07 | UI event dedup by id | Same `lastUiEvent.id` processed only once | hook | P2 | ◻ |
| AG-08 | Quick-chat handoff promotion | `progenitor:load-chat-history` replaces transcript + session | hook | P2 | ◻ |
| AG-09 | Initial loading state | `isContentLoading` shows "Loading…" (header/meta/tabs hidden) | e2e/comp | P2 | ◻ |
| AG-10 | Welcome screen | Empty transcript shows weekday welcome + QuickActions | comp | P3 | ◻ |
| AG-11 | QuickAction sends | Clicking a quick action sends its preset message | comp | P3 | ◻ |

---

## Regression tests for bugs fixed in this PR
These guard the specific defects found and fixed during development.

| Maps to | Bug | Guard |
|---------|-----|-------|
| BG-05 | Switching away and back to a generating chat marked the open chat unread and hid the reply | After switch-away→back, on completion: reply visible, no unread dot |
| BG-07/08 | Sending in another chat aborted the first chat's in-flight stream (reply silently lost) | First chat's stream survives and is attributed correctly |
| BG-03 | Only one orange dot showed with multiple concurrent backgrounds | Each concurrent background chat shows its own dot |
| BG-09 | A background error left no indication | Errored background turn shows a red dot |
| BE-05 | Pin/archive/delete of the live current-state card returned 404 | Materializes then applies through **202 acknowledgement + causal events** |

## Coverage summary
- **Total:** update this count when tests are implemented; contract rows above
  deliberately test canonical lifecycle plus dedicated response-stream
  behavior rather than historical direct SSE.
- **Backend (api):** assert HTTP `202` acknowledgements, persistence-before-event,
  causal request IDs, dedicated replay/resync behavior, and no response-body
  data.
- **Frontend (unit/comp/hook/e2e):** assert one canonical `EventSource`, a
  request-scoped response `EventSource` during active chat, event-only Redux
  hydration, bounded replay/resync, and pending-operation correlation.

### Suggested build order
1. **Harness:** add Vitest + RTL (and Playwright config) to `app/frontend`.
2. **Unit:** event reducers, pending-operation correlation, canonical replay,
   dedicated response-frame dedup/replay, session models, filters, and storage
   rules.
3. **Hook:** canonical `EventSource` lifecycle plus dedicated response-stream
   ownership for foreground generation; never mock a response-body stream.
4. **Component:** `Message`, `ChatInput`, `StreamFilterMenu`, and
   `QuickActions`.
5. **E2E:** event-driven background generation, dedicated reconnect/resync,
   auto-select, ghost-card prevention, tabs, and thread routing.
6. **Backend:** table-drive acknowledgement/event correlation, dedicated replay,
   and failures.
