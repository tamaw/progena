# Frontend Functional Integration Plan Using Existing Progenitor State

## Summary
Implement the frontend so it talks only to the backend, while the backend acts as an in-memory adapter over Progenitor's existing `data/state`, `data/sessions`, `data/config`, and `logs` content. The backend will not introduce new durable state files. Workspace canvas state stays client-side in browser storage, but UI-visible agent, MCP, schedule, session, status, and log data should be loaded from backend APIs that read the Progenitor files and existing logs.

## Execution Setup
1. Fetch `origin/main`.
2. Create a new clean worktree from `origin/main`.
3. Create a feature branch there for this work.
4. Implement and validate only in that clean branch.
5. Open a PR when complete.

## Backend and State Consumption
The backend should read and cache in memory:
- `data/state/current_state.json`
- `data/state/build_state.json`
- `data/state/schedule.json`
- `data/state/health.json`
- `data/config/agents.json`
- `data/config/mcp.json` when present
- `data/sessions/sessions.json`
- existing JSONL and plain log files under `logs/`

Required backend API surface:
- `GET /workspace`
- `GET /widgets`
- `PATCH /widgets/{id}`
- `GET /schedules`
- `GET /logs`
- `POST /actions/run`
- `POST /actions/generate`

Backend behavior:
- keep hot snapshots in memory
- refresh by polling or watching state/config/log files
- normalize agent and MCP registry entries into widget-ready payloads
- use existing IPC request flow unchanged for actions
- after action submission, observe file/state changes and push live updates to frontend
- keep chat session support wired through existing backend session endpoints and Progenitor session/state reads

## Frontend Changes
### Workspace storage
Keep workspace/canvas persistence in the browser, but move it from the current `localStorage` implementation to IndexedDB:
- store viewport, node positions, local-only layout, and UI preferences in IndexedDB
- merge browser-owned layout state with backend-derived widgets at load time
- do not make backend the source of truth for canvas layout yet

### Widget model
Frontend widgets should include:
- agent widgets derived from `agents.json`
- MCP widgets derived from `mcp.json` when present
- schedule widgets derived from `schedule.json`

`GET /widgets` should return a unified widget list with enough metadata for the frontend to render:
- type: `agent`, `mcp`, `schedule`
- stable id
- display name
- status/summary
- recent logs where applicable

### Agent and MCP logs
Populate agent and MCP widgets by consuming existing logs only:
- derive per-agent log sources from `agents.json` names and existing `logs/` layout
- derive per-MCP log sources from `mcp.json` names when possible
- parse existing JSONL logs where present, and plain log files as fallback
- expose normalized recent log entries per widget without changing source file formats
- feed the existing `AgentNode` `data.logs` shape from backend responses

### Action flow
Keep IPC unchanged:
- `/actions/run` and `/actions/generate` call the existing backend-to-Progenitor IPC path
- backend does not introduce new structured IPC command types
- frontend sees action progress/results through backend APIs plus live backend updates after file/state changes

## Progenitor-Side Improvements To Recommend
No JSONL format changes are part of this work, but recommend:
- documenting `agents.json` and optional `mcp.json` as backend-consumed registry contracts
- adding shared schemas for `current_state.json`, `schedule.json`, `agents.json`, and `mcp.json`
- making runtime/status fields in registry/state files more explicit so the backend does less inference from logs
- documenting how agent and MCP names map to log directories/files

## Test Plan
- backend reader tests for state, config, session, and log inputs
- backend API tests for widgets, logs, schedules, and actions
- frontend tests for IndexedDB workspace persistence and backend-driven widget rendering
- end-to-end validation that frontend changes only go through backend and backend only consumes existing Progenitor files

## Assumptions and Defaults
- `agents.json` is the primary registry file.
- `mcp.json` is optional.
- Workspace canvas/layout is browser-owned and stored in IndexedDB.
- Backend remains non-persistent and uses only in-memory caches over existing Progenitor files.
- Existing JSONL files are consumed as-is.
- Existing IPC request shapes remain unchanged.
