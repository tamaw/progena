# Progenitor Chat UI (React)

A modern React-based chat interface for the Progenitor AI agent system.

## Features

- **Chat Interface**: Real-time chat with streaming responses
- **Dashboard**: Metrics, charts, and system status
- **Agents Page**: View and manage registered AI agents
- **Logs Viewer**: Real-time log streaming with filtering

## Quick Start

### Development

```bash
# Install dependencies
npm install

# Start development server with hot reload
npm run dev
```

The dev server runs at `http://localhost:5173/`. Its built-in proxy forwards only `/api/*` and `/v1/*` requests to the backend, defaulting to `http://localhost:8000`. Set `VITE_BACKEND_BASE_URL` in `.env` or `.env.local` to point the standalone Vite server at a different backend.

### Hidden event debugger

Press **Ctrl+Shift+E** (**Cmd+Shift+E** on macOS) to toggle the right-side **Event stream** panel. It has no header action or navigation entry and is available in development and production. Escape or the close button dismisses it; pin it to keep interacting with the page while watching events. The debugger itself never restores open on reload.

The panel reads Redux's existing canonical UI event queue: the latest 500 accepted events, including heartbeats, newest first. Expand a row for the complete envelope/payload JSON; filter by event type, event ID, or request ID. **Clear view** only hides currently retained rows in this open view, without clearing Redux data or stopping capture; reopening shows the retained queue again. Workspace changes reset the queue with the rest of Redux.

**Hide status updates** is enabled by default to suppress periodic `status.updated` rows. Turn it off to inspect them. This composes with the text filter and changes only visibility: all events still enter Redux and count toward its 500-event buffer.

Offscreen event rows use browser-native layout containment to avoid laying out the entire buffer on every panel open; the full retained list remains available for filtering, browser search and expansion.

This is a debugging view, not an access-control boundary. It shows data already received by the current browser session, not every Redux action or the separate chat-content stream.

### Connection recovery

The shared **Waiting to reconnect** modal appears only after **20 seconds without a `status.updated` event**, including on an initial connection that never becomes healthy. A single transport error or late status message does not open it. The socket opening, `connection.ready`, heartbeats and unrelated data events do not dismiss it; a new `status.updated` event does.

While the modal is visible, a countdown schedules a fresh stream connection every **10 seconds**. **Reconnect now** makes the attempt immediately and resets that countdown, without reloading the page. Existing EventSource retries continue as normal before the modal appears. Replacing a stream settles requests still waiting on that old connection as failed, and late frames from the replaced stream are ignored.

The modal cannot be dismissed while updates are missing. **Refresh page** reloads the browser and discards unsaved changes; this is stated in the dialog.

### Session drafts

Unsaved editor changes stay in memory for the current workspace and browser tab. Switching pages or selecting another item does not discard them: returning restores the last selected item and its edit/source context, including unfinished JSON and Markdown. Routine autosave and other pages' explicit Save buttons keep their existing behavior; navigation itself does not submit edits.

Inline title edits survive blur and navigation. Escape or an explicit Cancel/Reset discards the corresponding draft. A rejected save retains the exact text for correction or retry. HTTP 202 is only an acknowledgement: correlated canonical events complete saves, and a late completion for one item cannot clear another item's draft or a newer edit.

Reconnect and incoming snapshots preserve dirty drafts. Reloading, closing the tab, or switching workspaces clears drafts; nothing is written to localStorage. There is no cross-tab draft synchronization or automatic merge with concurrent server edits. Profile conflict checks still require resolving a changed baseline before saving.

### Local design library

Open `/design` directly on the local Vite development server; it is intentionally absent from navigation and navigation search. The route requires a development build and the browser hostname `localhost`, `127.0.0.1`, or `[::1]`. It is unavailable on LAN/public hostnames, and the catalogue module and its styles are excluded from production builds, including `vite preview`.

The catalogue shows the current theme tokens, typography, branding, controls, list/detail layouts, navigation, feedback and data/chat patterns. Source captions distinguish shared React components from existing markup patterns. Demos use local fixture state and do not submit cloud or application changes. The surrounding app shell still performs its normal backend reads.

Use this page to review the existing UI before extracting or migrating more shared components. It is not a new component framework. Catalogue code lives in `src/pages/DesignLibraryPage.jsx` and `src/pages/design-library/`.

Component reuse conventions:
- Use `components/common/Select` for themed choices; pass `portal` inside transformed graph nodes or clipped panels. Portal menus stay within the viewport and close on outside wheel/resize.
- Use `NumberStepper` only for bounded, non-null numeric values with a defined step. Keep native number fields where blank means inherited/unset, decimals are unrestricted, or parsing semantics differ.
- Reuse `DetailCards` and `DetailHeader` action icons rather than copying their markup. Use the existing `task-btn`, `set-text-input` and `set-switch` patterns when they fit the control; switches retain native checkbox behavior, keyboard focus and disabled styling.
- Keep detail section headings/actions in `agents-section__head` above the content surface; `agents-section` supplies spacing, not an outer card. Use `agents-card-facts` for fact/form layouts or `agents-section__body` for grouped content without a flex layout. Preserve independently styled item cards and editor panels.
- Use `--font-family`, `--font-family-mono` and the defined `--color-*` theme tokens. Keep intentional handwritten branding and data-driven agent/chart colors.
- Use `Modal` for dialog focus, Escape/backdrop handling and portal placement; use `PopupModal` for confirmations. Pass busy state to prevent dismissal during a pending action. Keep domain forms and submit logic in the caller.
- Use `MarkdownContent` for read-only Markdown bodies. Preserve the caller's renderer/sanitization policy, link transformation and click handling; Mermaid rendering is explicit opt-in. Structured inbox documents and interactive chat messages retain their specialized renderers.
- Use `JsonCodeEditor` for the shared highlighted JSON input. Parsing, validation, reset/apply actions and persistence belong to the containing workflow/routine panel. Its syntax colors follow the current theme.

The catalogue includes interactive JSON and Markdown examples. Larger list/detail surfaces already use the existing layout components in most pages; domain-specific trees, transcript panes and editors should not be forced into a shared wrapper unless their interaction contracts match.

Shared JavaScript behavior:
- Use `failureMessage` / `reportFailure` (`src/lib/failures.js`) and `InlineFailure` for operation-specific errors. Load failures need a real retry callback and must not hide cached data or masquerade as an empty result. `requestAcknowledged` throws `RequestError` with status, request ID, stable code and readable validation details; the code survives Redux error serialization. Cancellation is not a notification.
- Use `useUiAction(entityScope)` for one mutation busy/error lifecycle. `run(operation, task)` returns an explicit result instead of throwing. Use separate instances for independent controls. After an await, guard entity-local draft changes/navigation with the task's `isCurrent()` callback.
- Use `useConfirmAction(entityScope)` with `PopupModal` for destructive actions. Capture the target when requesting confirmation; read mutable collections from current store state when confirming a whole-document write. Pass `busy` and `error` separately so a rejected operation keeps its original warning and can be retried.
- Use `useInlineTitleEditor` with `EditableDetailTitle` for short detail titles. Callers supply entity `kind`, identity, persistence and length limits; the shared behavior handles normalization, focus, keyboard interaction, duplicate submissions and retained rejected drafts. Blur preserves the edit; Escape explicitly discards it.
- Agents and Routines share `useAgentCatalogue` and `agentCatalogueModel`, not a configurable page framework. Domain filters, graph drafts and run behavior remain explicit.
- Detail caches use `addKeyedRequestLifecycle`, `completeKeyedRequest` and per-domain request selectors. HTTP 202 acknowledges dispatch only; a matching canonical event ends loading and can recover an acknowledgement failure. A failed request retains its causal ID until superseded or completed; cancellation clears it. Retain cached content on rejection, but never substitute current content for an unavailable historical revision.
- Use `useMenuDismiss` for ordinary non-portal menus, `useMissingEntityRoute` only after collection hydration, and `useListSelection` where selection is local rather than SSE/route-owned. Portal dropdowns and modal focus retain their specialized behavior.
- Matching compact ages and local date/time labels share `lib/displayTime.js`. Keep schedule timezone/cron formatting and domain-specific timestamps separate.
- Use `useEntityDraft(entityDraftKey(kind, id, editor), initialValue)` for serializable per-entity editor state. Leave clean canonical hydration in `initialValue`; retain records only for real edits or edit sessions. Save tickets bind the originating key and revision to canonical completion, not component lifetime or HTTP acknowledgement. `EditorNavigationBoundary` restores remembered detail URLs before default-selection effects mount; missing/deleted-item replacements bypass restoration.

### Production Build

```bash
# Build for production
npm run build

# Preview the production build (with the same `/api/*` and `/v1/*` proxy behavior)
npm run preview

# Or build and preview in one command
npm run prod
```

## Architecture

The frontend runs as a standalone application:

- **Development**: Vite dev server at port 5173 with HMR
- **Production**: Vite preview server at port 4173 (or deploy static files)

When you run the standalone Vite server (`npm run dev` or `npm run preview`), its built-in proxy forwards only `/api/*` and `/v1/*` requests to the configured backend. It does **not** proxy `/metrics`, `/healthz`, `/ready`, or arbitrary backend routes; those paths stay on the Vite server unless another reverse proxy handles them.

### Data flow

Application data uses a canonical lifecycle transport and a request-scoped chat-content transport:

1. **Canonical UI transport**: The application opens one `EventSource` to `GET /api/ui/stream`. Every frame is `event: ui.event` with a typed `UiEventEnvelope`; it carries all non-chat UI data plus `chat.response.started`, `chat.response.completed`, `chat.response.failed`, and `chat.response.cancelled` lifecycle envelopes.
2. **Dedicated chat-content transport**: After `POST /api/ui/responses` is acknowledged, `useChatApi` opens `GET /api/ui/responses/{request_id}/stream`. It carries only that response's `chat.response.delta` and `chat.response.event` frames; it replays bounded content with `Last-Event-ID` or `?after=<frame-id>` and returns `chat.response.resync_required` if replay cannot continue safely.
3. **Finite acknowledgement requests**: Every finite application request sends a freshly generated UUID in `X-Request-ID` and receives exactly HTTP `202 Accepted` with `{request_id, event_types}`. This body is operation metadata, never application data.
4. **Event-confirmed state**: Canonical lifecycle envelopes repeat the UUID at `payload.request_id`. `realtimeListener` deduplicates envelope IDs and Redux reducers hydrate durable data; a matching lifecycle terminal resolves the local chat operation.
5. **Selector-driven UI**: Pages/components read Redux selectors. Events from another tab/process still update Redux but never resolve an unrelated local operation.

The canonical UI stream has bounded replay via `Last-Event-ID`; `connection.ready` follows available replay. The dedicated response stream has its own bounded content replay. On `chat.response.resync_required`, stop appending live content and reconcile from persisted chat data after the canonical terminal lifecycle event. There is no `/api/ui/events` or `/api/ui/status` EventSource and no HTTP-body fallback.

### Structured document content

`src/components/common/StructuredDocumentBody.jsx` renders Markdown with selectable heading sections (`##`–`######`) from `content` using `parseStructuredContent` in `src/lib/structuredContent.js`. It owns section selection, the inline AI-instruction popover, and optional question controls; both question text and answer buttons share one section bracket. Supply `onAskAgent(instruction, { draft, inline, clientId })` for section instructions and `onAnswerQuestion({ question, choice, content })` for interactive questions; unavailable actions are hidden. The shared component refers to a document, not an Inbox file: its caller owns agent prompts and event-driven document refreshes. Inbox uses `src/pages/StructuredInboxBody.jsx`; Tasks does not use this component yet. The viewer never merges agent writes or attributes an inbox snapshot to a particular prompt.

## Project Structure

```
src/
├── components/
│   ├── layout/          # Header, Sidebar
│   ├── chat/            # Chat components
│   ├── dashboard/       # Dashboard widgets
│   ├── agents/          # Agent cards
│   ├── logs/            # Log viewer
│   └── common/          # Shared components
├── context/             # React Context providers
├── hooks/               # Custom React hooks
├── pages/               # Page components
└── styles/              # CSS files
```

## Tech Stack

- **React 19** - UI framework
- **React Router** - Client-side routing
- **Chart.js** - Data visualization
- **Vite** - Build tool and dev server

## Routes

- `/` - Chat interface
- `/dashboard` - Dashboard
- `/agents` - Agents management
- `/logs` - Log viewer

## API Endpoints Used

- `GET /api/ui/stream` - typed application envelopes and chat lifecycle only; it never carries response deltas or structured response frames
- `POST /api/ui/responses` - accepts chat generation with HTTP `202`; acknowledged requests open a dedicated response-content stream
- `GET /api/ui/responses/{request_id}/stream` - one active response's replayable `chat.response.delta` and `chat.response.event` frames
- `PUT /api/ui/responses/{generation_id}/cancel` - accepts cancellation with HTTP `202`; `chat.response.cancel_requested` acknowledges the operation and the target's canonical terminal event completes it
- `GET/POST /api/ui/chats`, `GET/PATCH/DELETE /api/ui/chats/{id}` - HTTP `202` acknowledgements; chat data arrives as `chat.*` events
- `GET /api/ui/agent-profiles`, `/api/ui/knowledge`, `/api/ui/inbox`, and other retained UI routes - HTTP `202` acknowledgements; their typed snapshot/mutation events hydrate Redux
- `GET /api/ui/model-profiles`, `PATCH/DELETE /api/ui/model-profiles/{name}` - HTTP `202` acknowledgements; `profiles.snapshot` hydrates the shared profile catalogue and confirms edits or deletion. Profile names are URL-encoded, including embedded slashes.

The **Profiles** page (`/profiles`) manages the model configurations stored in
`/config/profiles.json`; it is separate from the Models catalogue. Deployment,
reasoning enabled, effort, and summary are editable. Name and provider are
read-only. Inherit/cleared optional fields remove the override and use runtime
environment defaults. Only supported public fields are sent to the browser.
The canonical stream also keeps routine profile selectors current. On a remote
change, an open draft is retained and must be restarted or cancelled before
saving. Offline edits cannot be submitted; reconnect refreshes the catalogue.
Progenitor can add, edit, or remove entries by writing its agent-writable
`/config/profiles.json` virtual file; those Postgres writes update the page live.

Browser generation uses `/api/ui/responses`. The backend-to-runtime `/v1/responses`
and `/v1/events` protocols are internal and are not frontend APIs.

`/healthz` is a Docker-only Nginx-to-backend liveness probe returning an empty `200 OK`; it is not a browser application API, and the standalone Vite proxy does not forward it. `/ready`, `/live`, `/health`, `/health/detailed`, legacy session/attachment/conversation endpoints, response-body data routes, and `/api/ui/events` or `/api/ui/status` streams are not frontend APIs.

## VS Code Tasks

Use the VS Code tasks to run the full stack:

- **Start All (dev)**: Runs progenitor agent, backend, and frontend (dev mode)
- **Start All (prod)**: Runs progenitor agent, backend, and frontend (prod mode)
- **Start frontend (dev)**: Just the frontend in dev mode
- **Start frontend (prod)**: Frontend build + preview
