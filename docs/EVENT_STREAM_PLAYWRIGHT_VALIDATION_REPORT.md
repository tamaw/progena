# Event-Stream Playwright Validation Report

Date: 2026-07-13

## Stack exercised

- Production Vite preview: `http://127.0.0.1:5175`
- Vite dev server: `http://127.0.0.1:5176`
- Backend: `http://127.0.0.1:8001`
- Runtime: `http://127.0.0.1:9502`
- PostgreSQL: `127.0.0.1:15434`

## Working

- Production frontend preview rendered the full Progenitor shell and Chat page.
- Browser-captured requests included one canonical `GET /api/ui/stream` EventSource request.
- Homepage finite requests returned HTTP `202` acknowledgements; the shell eventually hydrated chat state from stream events.
- Route checks rendered without uncaught page errors for Dashboard, Tasks, Logs, Prompts, Skills, Knowledge, Tools, Workflows, and Inbox.
- Captured route request statuses were HTTP `202` for retained event-only requests:
  - `/api/ui/kanban`
  - `/api/ui/logs`
  - `/api/ui/prompts`
  - `/api/ui/skills`
  - `/api/ui/knowledge`
  - `/api/ui/mcp`
  - `/api/ui/workflows`
  - `/api/ui/workflow-runs`
- Direct browser stream validation received `connection.ready` with HTTP 200 and `text/event-stream`.
- A browser-issued workflow request with a fixed UUID received HTTP `202` and an acknowledgement whose `request_id` matched the request. The subsequent `workflows.snapshot` event carried the same request ID and event type.
- Safe error checks returned 404 for the deleted `/api/ui/threads` and `/api/ui/widget-schemas` routes and for missing task activity.
- Production preview audit reported no console errors, page errors, or failed network requests.
- Production build succeeded before this validation.

## Failures and unexpected behavior

### Vite dev server is blank

Both the existing isolated dev server on port 5174 and a freshly started dev server on port 5176 rendered an empty `#root`. A direct Playwright dynamic import of `/src/main.jsx` failed with:

`@vitejs/plugin-react can't detect preamble. Something is wrong.`

The production Vite preview on port 5175 does not have this failure and renders correctly. This is a dev-server/HMR execution issue, not a production bundle failure.

### Knowledge remains in `Syncing`

The Knowledge route rendered its shell but remained in `Syncing` during the observation window, with no knowledge bases. The request itself returned HTTP `202`; the page needs further investigation if the matching snapshot is delayed or not applied.

### Schedule loading is slow/event-dependent

The Schedules route displayed `Loading schedules…` during the observation window. This is consistent with event-confirmed loading but should be checked for a missing/delayed `schedule.snapshot` if it does not settle after reconnect.

### Malformed workflow request returns 500

A deliberately malformed `POST /api/ui/workflows` returned HTTP 500 with a schema-directory error (`/tmp/progenitor-isolated/data/schemas` missing), rather than a client-facing validation error. This is isolated-environment configuration/error handling behavior and should be corrected if malformed requests must produce 4xx responses.

### Browser root paths are Vite fallbacks

Fetching `/health`, `/ready`, and `/live` from the frontend origin returned the Vite index HTML with 200 because the preview fallback serves SPA content. Direct backend checks are required to validate deleted backend routes; direct backend route checks returned the expected 404s where tested.

## Not exercised

- Real chat generation/cancellation was not triggered to avoid submitting an uncontrolled LLM operation.
- Destructive CRUD mutations were not executed. The acknowledgement/event correlation path was validated with a non-destructive workflow GET.
- Cross-tab replay/resynchronization was not exercised in this pass.
