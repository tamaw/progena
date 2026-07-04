# Progenitor Documentation

This folder intentionally contains a small, maintained set of docs centered on the checked-in main system: the Progenitor runtime, backend API, and frontend UI.

## Core system docs

- **[SPEC.md](SPEC.md)** - Checked-in main-system behavior, request flow, persistence, and public interfaces
- **[ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md)** - Runtime/backend/frontend topology, request path, and deployment shape
- **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** - Repository layout for the main stack
- **[BACKEND.md](BACKEND.md)** - FastAPI backend endpoints, runtime IPC dependency, and local development commands
- **[STATE_DOCUMENTS.md](STATE_DOCUMENTS.md)** - The mcp/schedule/event/registry/kanban state-document pattern: shape conventions, agent-facing vs. backend-facing surfaces, shared CRUD helpers, and the no-migrations rule
- **[DOCKER.md](DOCKER.md)** - Default container stack, networks, volumes, and health checks

## Project links

- [Main README](../README.md)
- [Tests](../tests/)
- [Scripts](../scripts/)
