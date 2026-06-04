# Chat Frontend Notes

Quick reference for the chat page implementation — things that aren't obvious from reading the code.

---

## Session card layout

Cards have three rows: time/pin (top), title (middle), counts (bottom). The `meta` field drives the top-row timestamp — it comes from `last_activity` on backend chats, not `created_at`. The `summary` field is hidden from the DOM but still searched by the JS sidebar filter (`sidebarQuery` filters against `item.title + item.summary` directly).

## Thread routing

When replying to a backend chat, `thread_id` must be passed in the request metadata or the backend creates a new LangGraph thread and the full conversation history is lost. See `handleSend` in `ConversationPage.jsx` and `_build_chat_request_metadata` in `app/backend/main.py`.

## Background streaming

Switching chats no longer aborts the in-progress stream. `selectSessionItem` nulls `activeAssistantIdRef.current` (making `isStale() = true`) but does not call `cancelStream()`. Stale chunks are silently dropped. When `onComplete` fires stale with `status !== 'cancelled'`:
- `fetchChatSessions` refreshes the sidebar (new `last_activity` sorts the card to the top)
- `unreadChatIds` set gains the origin chat ID → green dot appears
- `backgroundGeneratingChatId` is cleared → orange dot disappears

If the user sends a new message in the new chat, `sendMessageStream` aborts the old controller — this fires `onComplete('cancelled')` which does nothing (guarded by `status !== 'cancelled'`).

## Unread / generating dots

- Orange pulsing dot: `item.generatingInBackground` — set in `selectSessionItem` when switching away from an active stream
- Green dot: `item.unread` — set on stale stream completion, cleared when the card is selected
- Both live in `ConversationPage` state (`backgroundGeneratingChatId: string|null`, `unreadChatIds: Set<string>`) and are passed into `buildChatSessionItems`

## Model name in message headers

Model comes from two places:
1. **Live streams**: `node_update` stream event, `metadata.model_name`
2. **Loaded messages**: `raw.response_metadata.model_name` (stored in checkpoint, returned by the chat detail API)

`backgroundOriginChatIdRef` uses `activeBackendChat?.chat_session_id` (in `handleSend` deps) not `selectedBackendChatId` (not in deps) to avoid a stale closure.

## localStorage / ghost card fix

Backend chat messages must never be persisted to `localStorage`. When `selectedBackendChatId` is set, the persistence `useEffect` calls `localStorage.removeItem` instead of writing. Otherwise a "Current" ghost card appears on the next page visit driven by stale local data.

## Auto-select on mount

`hasAutoSelectedOnMountRef` fires exactly once per component lifetime after `backendChatSessionsState.loaded` flips true. It selects `visibleSessionGroups[0]?.items[0]` (respects pinned/bucket ordering). Does not re-fire on subsequent renders or if the user manually selects a card.

## isContentLoading

Only true on the initial load (`!backendChatSessionsState.loaded && messages.length === 0 && !selectedBackendChatId`). Deliberately excludes any per-card loading state so there's no flash when switching between cards.

## Pre-commit / bandit

`try/except/pass` and `try/except/continue` patterns fail bandit B110/B112. Suppress with `# nosec B110` or `# nosec B112` inline — don't restructure the code to avoid them if the silent-fallback is intentional.
