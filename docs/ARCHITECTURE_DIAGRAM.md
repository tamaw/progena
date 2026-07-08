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

## Invocation entry points

Two distinct ways a prompt reaches the runtime through the backend:

- **Chat session** (quick chat / conversation pages) — `POST /api/ui/chats`
  creates a persisted session, then messages stream through it
  (`sendMessageStream`). This is the normal user-facing path; the agent has
  its full tool set here, including `structured_output`.
- **Direct action, no session** — `POST /api/ui/actions/run` (and
  `/actions/generate`), used by frontend-provided triggers (workflow "Run
  now", a workflow's prompt/user/code node execution, workspace run/generate
  buttons). This only records a lightweight thread entry (`upsert_thread`)
  for the automation/thread list UI, not a real chat session. It's a valid
  way to reach the agent, but isn't currently the intended path for
  `structured_output`-driven authoring flows (e.g. building a workflow
  definition) — that happens through a chat session.

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
