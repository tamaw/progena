# Chat Page — Functional Test Plan

> Iteration 3 (final). Each behavior is a discrete functional test with an
> expected result, test layer, priority, and current coverage status.

**Prerequisite:** the frontend has no test harness yet. Unit/comp/hook/e2e rows
assume adding Vitest + React Testing Library (and Playwright for e2e). Backend
(api) rows run under the existing `pytest tests/backend` setup. e2e flows can
run against `PROGENITOR_MOCK_LLM=true` for determinism; background-error cases
(BG-09/10) can be forced by intercepting the `/v1/responses` stream to emit an
SSE `error` event after a delay.

## Scope
The chat page and everything it backs onto, per PR #270:
`pages/ConversationPage.jsx`, `components/chat/*`, `hooks/useChatStream.js`,
`hooks/useChatSessionPrefs.js`, `hooks/useApi.js`, `store/*`, plus the backend
`chat_routes.py` / `chat_sessions.py` read-model and the `/v1/responses`
streaming endpoint the page consumes.

## Test layers & tooling
- **unit** — pure functions/models (`conversationModel`, `chatSessionsModel`, `streamEventsModel`, `streamFilters`, `chatStorage`). Vitest. *No frontend harness exists yet — adding Vitest is a prerequisite.*
- **comp** — React component behavior (`Message`, `ChatInput`, `StreamFilterMenu`, `QuickActions`). Vitest + React Testing Library.
- **hook** — `useChatStream` / `useApi` / reducers with a mocked stream + store.
- **e2e** — full chat page against a running/mock stack. Playwright.
- **api** — backend FastAPI routes. pytest (`tests/backend`), already established.

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

## C. Streaming lifecycle — `useChatStream` + `useApi`

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| ST-01 | Optimistic placeholders | User message + streaming assistant placeholder added | hook | P1 | ◻ |
| ST-02 | Chunk append + tokens | Content concatenates; tokenCount += 1 per chunk | hook | P1 | ◻ |
| ST-03 | Model from node_update | `metadata.model_name` sets message model once | hook | P2 | ◻ |
| ST-04 | Reasoning accumulation | Deltas append; summary updates reasoning tokens | hook/unit | P2 | ◻ |
| ST-05 | Normal completion | isStreaming cleared; `fetchChatSessions` dispatched; isGenerating false | hook | P1 | ◻ |
| ST-06 | Error path (foreground) | Toast shown; inline `Error:` message; isGenerating false | hook | P1 | ◻ |
| ST-07 | Stop → cancelled | onComplete('cancelled') finalizes with ` [Cancelled]` | hook | P2 | ◻ |
| ST-08 | Supersede same chat | Second send finalizes prior placeholder (no stuck spinner) | hook | P2 | ◻ |
| ST-09 | Backend streaming (server) | `/v1/responses` stream emits structured events | api | P1 | ✅ `test_async_streaming_response`, `test_stream_response_unified_emits_structured_stream_events` |
| ST-10 | Thread id from session | Streaming uses the session's thread_id | api | P1 | ✅ `test_streaming_responses_use_session_thread_id` |
| ST-11 | Cancel endpoint | Cancel sets active-stream event; 404 unknown id | api | P1 | ✅ `test_cancel_response_sets_active_stream_event`, `..._returns_404_for_unknown_request` |
| ST-12 | Reasoning output (non-stream) | Reasoning included in response output | api | P2 | ✅ `test_non_streaming_responses_include_reasoning_output` |
| ST-13 | appendStreamEvent rules | Skips message_delta; merges reasoning; dedups identical; caps at 200 | unit | P2 | ◻ |

## D. Background streaming (hardened in this PR)

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| BG-01 | Switch mid-stream keeps it alive | Stream continues after navigating away | e2e/hook | P1 | ◻ |
| BG-02 | Orange dot on origin | Origin card shows pulsing orange dot while streaming | e2e | P1 | ◻ |
| BG-03 | Multiple orange dots | N concurrent background chats each show their own orange dot | e2e/unit | P1 | ◻ |
| BG-04 | Complete → unread + top | Background completion → green dot + card sorts to top | e2e | P1 | ◻ |
| BG-05 | Switch-back reload (no false unread) | Returning to a still-streaming chat: reply reloads in place, NOT marked unread | e2e/hook | P1 | ◻ |
| BG-06 | Select clears dot | Selecting an unread/errored chat clears its dot | e2e | P1 | ◻ |
| BG-07 | Cross-chat send doesn't abort | Sending in chat B leaves chat A's stream running | e2e/hook | P1 | ◻ |
| BG-08 | Concurrent attribution | Two concurrent streams' replies land in the correct chats (no swap) | e2e/hook | P1 | ◻ |
| BG-09 | Background error → red dot | A background turn that errors shows a red dot in place of green | e2e/hook | P1 | ◻ |
| BG-10 | Error cleared by later success | A later successful background completion reverts red → green | hook | P2 | ◻ |

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
| RT-01 | Single stream + dedupe | One EventSource; duplicate event ids ignored | hook | P2 | ◻ |
| RT-02 | sessions.snapshot | Replaces the chat list | unit | P1 | ◻ |
| RT-03 | created / metadata.updated | Upsert + merge into active detail | unit | P2 | ◻ |
| RT-04 | upserted | Adds chat if missing | unit | P3 | ◻ |
| RT-05 | selected request_id match | Applied only when request_id matches active | unit | P1 | ◻ |
| RT-06 | deleted | Removes chat; clears active if it was selected | unit | P2 | ◻ |
| RT-07 | Bridge fetch on connect | RealtimeBridge fetches chats once when connected | hook | P2 | ◻ |
| RT-08 | selectChatSessions memo | Stable array reference when chat state unchanged | unit | P3 | ◻ |

## J. Backend chat routes — `chat_routes` / `chat_sessions`

| ID | Behavior | Expected | Layer | Pri | Status |
|----|----------|----------|-------|-----|--------|
| BE-01 | List + snapshot | GET /chats lists (include_archived); emits snapshot | api | P1 | ✅ `test_chat_endpoints_store_metadata_without_transcripts` |
| BE-02 | Detail selection event | GET /chats/{id} emits request-scoped selection | api | P1 | ✅ `test_chat_detail_request_emits_request_scoped_selection_event` |
| BE-03 | Rehydrate from checkpoint | Card history rebuilt from checkpoint thread | api | P1 | ✅ `test_chat_card_rehydrates_history_from_checkpoint_thread` |
| BE-04 | Soft-delete hides from list | DELETE sets deleted flag; hidden from list | api | P1 | ✅ `test_chat_delete_sets_soft_delete_flag_and_hides_from_list` |
| BE-05 | Materialize current-state card | PATCH/DELETE on the live current-state card materializes + applies | api | P1 | ✅ `test_chat_patch_and_delete_materialize_current_state_card` |
| BE-06 | PATCH on soft-deleted → 404 | Patching a deleted chat returns 404 | api | P2 | ◻ |
| BE-07 | Create returns detail | POST /chats returns the created session detail | api | P2 | ◻ (partial via BE-01) |
| BE-08 | Title display derivation | Default titles derive from first user message | api/unit | P2 | ◻ |
| BE-09 | Tool calls / images in detail | Detail surfaces tool_calls + images from checkpoint | api | P2 | ◻ |

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
| BE-05 | Pin/archive/delete of the live current-state card returned 404 | Materializes then applies (200 / 204) |

## Coverage summary
- **Total:** ~95 functional tests across 12 areas (A–L). ~10 already covered (all backend).
- **Backend (api):** core chat routes + responses streaming/cancel/thread/reasoning largely covered (✅). Gaps: BE-06, BE-08, BE-09.
- **Frontend (unit/comp/hook/e2e):** essentially **uncovered** — no frontend test harness exists.

### Suggested build order
1. **Harness:** add Vitest + RTL (and Playwright config) to `app/frontend`.
2. **Unit (fast, high ROI):** `streamEventsModel` (ST-13, ST-04), `chatSessionsModel`/`conversationModel` (E rows, SB-13/14), `streamFilters` (FL-01/04), `chatStorage` (PST-02/04), `uiDataSlice` reducers (RT-02..06/08).
3. **Hook:** `useChatStream` lifecycle + background (C, D, BG regressions) with a mocked `sendMessageStream`.
4. **Component:** `Message`, `ChatInput`, `StreamFilterMenu`, `QuickActions` (A, B, FL, TB-02/03, AG-10/11).
5. **e2e (P1 only):** the background-streaming flows (BG-01..09), auto-select (SB-12), ghost-card (SB-13), tabs (TB), thread routing (LC-05).
6. **Backend gaps:** BE-06, BE-08, BE-09.
