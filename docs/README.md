# Progenitor Documentation

This folder intentionally contains a small, maintained set of docs centered on the checked-in main system: the Progenitor runtime, backend API, and frontend UI.

## Core system docs

- **[SPEC.md](SPEC.md)** - Checked-in main-system behavior, event-only browser request flow, persistence, and public interfaces
- **[ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md)** - Runtime/backend/frontend topology and the canonical stream path
- **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** - Repository layout for the main stack
- **[BACKEND.md](BACKEND.md)** - **Authoritative browser-client contract**: app-lifetime `/api/ui/stream`, request-scoped chat response streams, `ui.event` envelopes, bounded replay/resync, HTTP `202` acknowledgement correlation, and route disposition
- **[STATE_DOCUMENTS.md](STATE_DOCUMENTS.md)** - State-document shapes, persistence-before-event publishing, Redux ownership, and the no-migrations rule
- **[DOCKER.md](DOCKER.md)** - Default container stack, networks, volumes, and health checks
- **[LOCAL_STARTUP.md](LOCAL_STARTUP.md)** - Local development prerequisites and startup commands
- **[CHAT_FRONTEND_NOTES.md](CHAT_FRONTEND_NOTES.md)** - Chat rendering, streaming, and frontend implementation notes
- **[ACP_AGENTS.md](ACP_AGENTS.md)** - ACP sidecars and runtime integration

## Project links

- [Main README](../README.md)
- [Tests](../tests/)
