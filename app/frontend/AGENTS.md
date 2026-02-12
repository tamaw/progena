# Frontend - GitHub Copilot Agent

## React/Vite Frontend Agent

Modern React application providing the chat interface for Progenitor.

### Agent Capabilities
- React 18 component development
- Vite build tooling and configuration
- TypeScript/JavaScript development
- CSS/styling (Tailwind, styled-components, etc.)
- API integration with backend
- Real-time streaming chat UI

### Architecture
```
frontend/
├── src/
│   ├── components/      # React components
│   ├── pages/           # Page components
│   ├── hooks/           # Custom React hooks
│   ├── utils/           # Utility functions
│   ├── App.jsx          # Main app component
│   └── main.jsx         # Entry point
├── public/              # Static assets
├── index.html           # HTML template
├── vite.config.js       # Vite configuration
└── package.json         # Dependencies
```

### Development
```bash
cd app/frontend

# Install dependencies
npm install

# Run dev server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

### Key Features
- Chat interface with message history
- Streaming response support
- Code highlighting and formatting
- Copy-to-clipboard functionality
- Responsive design

### Integration Points
- **Backend API**: Proxies to `http://localhost:8000` in dev mode
- **Chat Endpoint**: `/v1/responses`
- **Streaming**: SSE for real-time responses

### Development Server
- Port: `5173` (default Vite port)
- Auto-reload on file changes
- Hot Module Replacement (HMR)

### Key Patterns
- Functional components with hooks
- Async/await for API calls
- Component composition
- State management (React hooks or context)

### Configuration
- `vite.config.js` - Vite configuration
- `package.json` - Dependencies and scripts
- Proxy configured to forward API calls to backend

### Important Notes
- Run `npm install` after pulling changes
- Dev server must run concurrently with backend
- Production build creates optimized static files
- Assets served from `public/` directory

### Testing
```bash
# Run tests (if configured)
npm test

# Run linting
npm run lint
```

### References
- Vite docs: https://vitejs.dev
- React docs: https://react.dev
