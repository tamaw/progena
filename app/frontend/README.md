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

All `/api`, `/v1`, `/health`, and `/metrics` requests are proxied to the backend.

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

- `GET /health` - Server health check
- `GET /health/detailed` - Detailed status
- `POST /v1/chat/completions` - Chat API (streaming)
- `GET /api/ui/events` - SSE for real-time updates
- `GET /metrics/json` - Metrics data

## VS Code Tasks

Use the VS Code tasks to run the full stack:

- **Start All (dev)**: Runs progenitor agent, backend, and frontend (dev mode)
- **Start All (prod)**: Runs progenitor agent, backend, and frontend (prod mode)
- **Start frontend (dev)**: Just the frontend in dev mode
- **Start frontend (prod)**: Frontend build + preview
