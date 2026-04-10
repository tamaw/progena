# Progenitor System Specification

This document describes the checked-in main system only: the Progenitor runtime, the backend API, and the frontend UI.

## Purpose

Progenitor is a long-running conversational system. The runtime maintains conversation state, executes the main agent graph, and returns responses through the backend APIs used by the web UI and compatible clients.

## Core components

### Runtime (`app/progenitor/`)
- Runs the perpetual conversation service.
- Restores and checkpoints runtime state.
- Builds the DeepAgent-backed conversation graph.
- Exposes IPC for the backend and processes scheduled work through the same core path as user input.

### Backend (`app/backend/`)
- Exposes the HTTP API used by the frontend and local clients.
- Bridges requests to the runtime over IPC.
- Handles streaming responses, sessions, attachments, and UI data routes.

### Frontend (`app/frontend/`)
- Provides the React/Vite web client.
- Talks to the backend rather than to the runtime directly.

## Request flow

1. A client sends a request to the backend.
2. The backend forwards the request to the runtime over IPC.
3. The runtime executes the conversation graph and any required runtime tools.
4. The result streams back through the backend to the caller.

## Scheduled work

- Scheduled entries are stored with runtime state.
- Due work is re-enqueued through the same input path as user messages.
- Runtime state is updated after scheduled work is processed.

## Persistence

The main system persists data under `data/` and `logs/`, including:
- runtime conversation and schedule state;
- health snapshots;
- backend session and attachment data;
- operational logs.

When the SQLite checkpointer is available, runtime conversation checkpoints are stored in `data/state/conversations.db`.

## Public interfaces

- Backend HTTP routes for health, models, chat/responses, sessions, attachments, conversations, and UI data.
- Internal IPC between the backend and the runtime.

## External integration

The runtime can use registered tools and optional external agent integrations, but their internal implementations are outside the scope of this core-system specification.
