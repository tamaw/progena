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

`src/components/common/StructuredDocumentBody.jsx` renders Markdown with nested XML-like sections from `content` using `parseStructuredContent` in `src/lib/structuredContent.js`. It owns section selection, question options, and the inline AI-instruction popover. Supply `onAskAgent(instruction, { draft, inline, clientId })` for section instructions and `onAnswerQuestion({ question, choice, content })` for interactive questions; unavailable actions are hidden. The caller owns agent prompts and event-driven document refreshes. Inbox uses `src/pages/StructuredInboxBody.jsx`; Tasks does not use this component yet. The viewer never merges agent writes or attributes an inbox snapshot to a particular prompt.

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

Browser generation uses `/api/ui/responses`. The backend-to-runtime `/v1/responses`
and `/v1/events` protocols are internal and are not frontend APIs.

`/healthz` is a Docker-only Nginx-to-backend liveness probe returning an empty `200 OK`; it is not a browser application API, and the standalone Vite proxy does not forward it. `/ready`, `/live`, `/health`, `/health/detailed`, legacy session/attachment/conversation endpoints, response-body data routes, and `/api/ui/events` or `/api/ui/status` streams are not frontend APIs.

## VS Code Tasks

Use the VS Code tasks to run the full stack:

- **Start All (dev)**: Runs progenitor agent, backend, and frontend (dev mode)
- **Start All (prod)**: Runs progenitor agent, backend, and frontend (prod mode)
- **Start frontend (dev)**: Just the frontend in dev mode
- **Start frontend (prod)**: Frontend build + preview
