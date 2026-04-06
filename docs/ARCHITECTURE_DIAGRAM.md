# Progenitor Runtime Architecture

This document describes the main running stack. For the checked-in repository layout, see [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md).

## High-level topology

```text
Browser / API client
        |
        v
Frontend
        |
        v
Backend HTTP API
        |
        v
Progenitor runtime
   |        \
   |         +--> runtime tools and optional external integrations
   |
   +--> persisted state, sessions, attachments, and logs
```

## Request path

```text
HTTP request
  -> backend request handler
  -> IPC call to runtime
  -> conversation graph
  -> streamed response back through backend
```

Scheduled work enters the runtime through the same core path as normal user input.

## Runtime responsibilities

- Owns the long-running conversation service.
- Restores persisted state on startup and checkpoints state while running.
- Executes the DeepAgent-backed conversation graph.
- Processes scheduled work and runtime tool calls.

## Backend responsibilities

- Provides the HTTP surface used by the frontend and compatible clients.
- Manages streaming responses, sessions, attachments, and UI-facing data routes.
- Translates HTTP requests into runtime IPC calls.

## Frontend responsibilities

- Provides the web chat and runtime status interface.
- Reads and mutates application state through the backend only.

## State and logging

- Runtime state is stored under `data/state/`.
- Backend-managed session and attachment data are stored under `data/`.
- Operational logs are written under `logs/`.

## Deployment shape

The default checked-in container stack runs three main services: frontend, backend, and the Progenitor runtime.
