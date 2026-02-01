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

The dev server runs at `http://localhost:5173/ui/` and proxies API calls to `http://localhost:8000`.

### Production Build

```bash
# Build for production
npm run build

# Preview the production build
npm run preview
```

The build output goes to `dist/` and is automatically served by the FastAPI backend.

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

## Integration with FastAPI

The React app is served by the FastAPI backend at `/ui`. The build output in `dist/` is automatically detected and served when the backend starts.

Routes:
- `/ui/` - Chat interface
- `/ui/dashboard` - Dashboard
- `/ui/agents` - Agents management
- `/ui/logs` - Log viewer

## API Endpoints Used

- `GET /health` - Server health check
- `GET /health/detailed` - Detailed status
- `POST /v1/chat/completions` - Chat API (streaming)
- `GET /api/ui/events` - SSE for real-time updates
- `GET /metrics/json` - Metrics data
