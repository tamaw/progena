# Progenitor Chat UI

A modern web-based chat interface for communicating with the Progenitor AI agent.

## Features

### Chat
- **Real-time Streaming**: Watch responses stream in token-by-token with token counter
- **Rich Markdown**: Headers, tables, code blocks with syntax highlighting, task lists, blockquotes
- **Message History**: Persistent chat history with localStorage
- **Input History**: Navigate through previously sent messages with Alt+↑/↓
- **Message Search**: Find content in chat history (Ctrl+F)
- **Conversation Export**: Download chat as Markdown file

### Status & Monitoring
- **Agent Status Panel**: Real-time health monitoring
- **Sub-Agent Tracking**: Monitor active sub-agents and their tasks
- **Task Queue**: View pending and running tasks
- **SSE Updates**: Server-Sent Events for real-time notifications

### Notifications
- **Browser Notifications**: Desktop alerts when tab is unfocused
- **Toast Notifications**: In-app notification system
- **Sound Alerts**: Optional audio notifications
- **Alert System**: Handle agent interrupts that require user input

### User Experience
- **Dark/Light Theme**: System preference support
- **Keyboard Shortcuts**: Extensive keyboard navigation (30+ shortcuts)
- **Settings Persistence**: Remember user preferences
- **Connection Recovery**: Auto-reconnect with exponential backoff
- **Enhanced Error Handling**: Custom error classes with user-friendly messages

## Accessing the UI

Once the chat server is running, access the UI at:

```
http://localhost:8000/ui
```

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Enter` | Send message |
| `Ctrl+Enter` | Send message (alternative) |
| `Shift+Enter` | New line in message |
| `Escape` | Stop generation / Close modals |
| `Ctrl+/` | Focus chat input |
| `Ctrl+N` | New conversation |
| `Ctrl+F` | Search messages |
| `Ctrl+,` | Open settings |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+Shift+D` | Toggle dark/light theme |
| `Ctrl+Shift+E` | Export conversation |
| `Ctrl+Shift+S` | Toggle status panel |
| `Ctrl+Shift+R` | Refresh status |
| `Alt+↑/↓` | Navigate input history |
| `Shift+?` or `F1` | Show keyboard shortcuts |
| `Ctrl+Delete` | Dismiss all notifications |

Press `Shift+?` or `F1` in the UI for the full keyboard shortcuts help modal.

## Architecture

### Frontend Structure

```
chat-ui/
├── index.html           # Main HTML page
├── README.md            # This file
├── BACKLOG.md           # Development backlog
├── css/
│   ├── main.css         # Core styles and theme
│   ├── chat.css         # Chat message styles, markdown, search
│   ├── status.css       # Status panel styles
│   └── notifications.css # Notification toast styles
└── js/
    ├── config.js        # Configuration management with persistence
    ├── api.js           # API client with streaming, error handling, recovery
    ├── chat.js          # Chat functionality with markdown rendering
    ├── status.js        # Status monitoring with SSE
    ├── notifications.js # Browser notification system
    ├── keyboard.js      # Keyboard shortcuts manager
    ├── search.js        # Message search functionality
    └── main.js          # Application initialization
```

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /v1/chat/completions` | Send chat messages (with streaming) |
| `GET /health/detailed` | Get server and agent status |
| `GET /api/ui/status` | Combined UI status |
| `GET /api/ui/agents` | Sub-agent status |
| `GET /api/ui/tasks` | Active tasks |
| `GET /api/ui/alerts` | Pending alerts |
| `GET /api/ui/events` | SSE endpoint for real-time updates |
| `POST /api/ui/alerts/{id}/dismiss` | Dismiss an alert |
| `POST /api/ui/alerts/{id}/respond` | Respond to an alert |

## Configuration

Settings are persisted in localStorage:

| Setting | Key | Description |
|---------|-----|-------------|
| API URL | `progenitor_api_url` | Backend server URL |
| Theme | `progenitor_theme` | UI theme (system/dark/light) |
| Notifications | `progenitor_notifications` | Browser notifications |
| Sounds | `progenitor_sounds` | Sound effects |
| Chat History | `progenitor_chat_history` | Saved messages |
| Input History | `progenitor_input_history` | Previously sent messages |

Settings can be exported/imported via the settings panel.

## Browser Support

- Chrome 80+
- Firefox 75+
- Safari 13+
- Edge 80+

## Development

The UI is built with vanilla JavaScript for simplicity and minimal dependencies. No build step required.

### Running Locally

1. Start the chat server:
   ```bash
   cd D:\tmp\agent\progenitor
   python -m app.chat_server.main
   ```

2. Open in browser:
   ```
   http://localhost:8000/ui
   ```

### Running Tests

```bash
python -m pytest tests/chat_server/test_ui_routes.py -v
```

### Customization

- **Themes**: Modify CSS custom properties in `css/main.css`
- **API Integration**: Extend `js/api.js` for new endpoints
- **Components**: Add new modules in `js/` directory
- **Keyboard Shortcuts**: Register in `js/keyboard.js`

## Completed Features

- [x] Core chat interface with streaming
- [x] Rich markdown rendering (tables, code blocks, task lists)
- [x] Agent status panel with health monitoring
- [x] Sub-agent tracking and task display
- [x] Browser notification system
- [x] Alert handling with respond/dismiss
- [x] Dark/light theme with system preference
- [x] Extensive keyboard shortcuts
- [x] Message search (Ctrl+F)
- [x] Conversation export (Markdown)
- [x] Settings persistence and export/import
- [x] Connection recovery with exponential backoff
- [x] Enhanced typing indicators with states
- [x] Token counter during generation
- [x] Input history navigation

## Future Enhancements

- [ ] WebSocket support for bidirectional communication
- [ ] File upload support
- [ ] Voice input/output
- [ ] Multi-agent chat rooms
- [ ] Conversation branching
