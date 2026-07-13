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

The dev server runs at `http://localhost:5173/` and proxies API calls to the backend at `http://localhost:8000`.

### Production Build

```bash
# Build for production
npm run build

# Preview the production build (with proxy)
npm run preview

# Or build and preview in one command
npm run prod
```

## Architecture

The frontend runs as a standalone application:

- **Development**: Vite dev server at port 5173 with HMR
- **Production**: Vite preview server at port 4173 (or deploy static files)

All `/api`, `/v1`, and `/metrics` requests are proxied to the backend.

### Data flow

Application data moves through the event-stream-only architecture:

1. **One event transport**: The application opens one `EventSource` to `GET /api/ui/stream`. Every application frame is `event: ui.event` with a typed `UiEventEnvelope` JSON payload.
2. **Finite acknowledgement requests**: Every finite application request sends a freshly generated UUID in `X-Request-ID` and receives exactly HTTP `202 Accepted` with `{request_id, event_types}`. This body is operation metadata, never application data.
3. **Event-confirmed state**: The matching causal event repeats the UUID at `payload.request_id`. `realtimeListener` deduplicates envelope IDs and Redux reducers hydrate durable data; only a matching event resolves the local pending operation.
4. **Selector-driven UI**: Pages/components read Redux selectors. Events from another tab/process still update Redux but never resolve an unrelated local operation.

The stream has bounded replay via `Last-Event-ID`. `connection.ready` follows available replay; if a cursor is too old to recover safely, request fresh snapshots and wait for their correlated events. There is no `/api/ui/events` or `/api/ui/status` EventSource, no per-page stream, and no HTTP-body fallback.

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

- `GET /api/ui/stream` - the sole browser application-data stream (`event: ui.event`)
- `POST /api/ui/responses` - accepts chat generation with HTTP `202`; generation data arrives as canonical `chat.response.*` events
- `PUT /api/ui/responses/{generation_id}/cancel` - accepts cancellation with HTTP `202`; terminal state arrives as a canonical event
- `GET/POST /api/ui/chats`, `GET/PATCH/DELETE /api/ui/chats/{id}` - HTTP `202` acknowledgements; chat data arrives as `chat.*` events
- `GET /api/ui/agent-profiles`, `/api/ui/knowledge`, `/api/ui/inbox`, and other retained UI routes - HTTP `202` acknowledgements; their typed snapshot/mutation events hydrate Redux

Browser generation uses `/api/ui/responses`. The backend-to-runtime `/v1/chat`
and `/v1/events` protocols are internal and are not frontend APIs.

`/ready`, `/live`, `/health/detailed`, legacy session/attachment/conversation endpoints, response-body data routes, and `/api/ui/events` or `/api/ui/status` streams are not frontend APIs.

## VS Code Tasks

Use the VS Code tasks to run the full stack:

- **Start All (dev)**: Runs progenitor agent, backend, and frontend (dev mode)
- **Start All (prod)**: Runs progenitor agent, backend, and frontend (prod mode)
- **Start frontend (dev)**: Just the frontend in dev mode
- **Start frontend (prod)**: Frontend build + preview
