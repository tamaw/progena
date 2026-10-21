# Progenitor Documentation

This folder intentionally contains a small, maintained set of docs centered on the checked-in main system: the Progenitor runtime, backend API, and frontend UI.

## Core system docs

- **[SPEC.md](SPEC.md)** - Checked-in main-system behavior, event-only browser request flow, persistence, and public interfaces
- **[ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md)** - Runtime/backend/frontend topology and the canonical stream path
- **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** - Repository layout for the main stack
- **[BACKEND.md](BACKEND.md)** - **Authoritative browser-client contract**: sole `/api/ui/stream` transport, exact `ui.event` envelope, bounded replay/resync, HTTP `202` acknowledgement correlation, route disposition, and removed surfaces
- **[STATE_DOCUMENTS.md](STATE_DOCUMENTS.md)** - State-document shapes, persistence-before-event publishing, Redux ownership, and the no-migrations rule
- **[DOCKER.md](DOCKER.md)** - Default container stack, networks, volumes, and health checks

## Project links

- [Main README](../README.md)
- [Tests](../tests/)
