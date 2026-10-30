# Frontend — Agent Guide

React + Vite SPA for Progena. Organize code by its owner: application wiring,
shared infrastructure, or a product feature. Feature names follow the backend
where responsibilities match; frontend-only composition such as the dashboard
and routine editor has its own feature.

## Stack and commands

React 19, Vite 7, Redux Toolkit 2, React Router 7, React Flow, Vitest 5,
Playwright and ESLint 9. JavaScript/JSX, plain CSS and CSS Modules.
Prefer function components and hooks; the route error boundary uses React's
class-based error-boundary API.

Use Node 24 as described in root `AGENTS.md`. Run npm commands from the repository root:

```bash
npm ci
npm run dev
npm run lint
npm run test:unit
npm run build
npm run test:e2e
```

Playwright uses an already-running stack; `FRONTEND_BASE_URL` overrides its
frontend URL. The Vite dev/preview proxy forwards `/api/*` and `/v1/*` to the
backend at `http://127.0.0.1:8000`, or `VITE_BACKEND_BASE_URL`. The browser never
calls the runtime directly.

All frontend tests live under root `tests/frontend/`: `unit/` mirrors the source
layout, `e2e/` holds browser specs, and `helpers/` / `fixtures/` hold support files.
Vitest maps `@frontend/` to `src/frontend/src/` for unit-test imports and mocks.
Its include pattern and Playwright's testDir keep the suites separate; pytest
skips the frontend subtree. Shared unit setup lives in `tests/frontend/unit/setup.js`.
Generated artifacts under `tests/frontend/test-results/` and report folders are
ignored. Browser discovery (`npm run test:e2e -- --list`) does not run scenarios.

The root `package.json` / lockfile and `eslint.config.js` own npm dependencies,
scripts and linting. Vite, Vitest and Playwright configs stay in `src/frontend/`;
Vite's application root is still that directory.

## Directory map

```text
src/
├── main.jsx                 # Entry point and initial workspace navigation
├── App.jsx                  # Providers and workspace-scoped application subtree
├── routes.jsx               # Lazy pages, route registration and error boundary
├── shell/                   # Navigation, header, panel placement and chrome
├── components/              # Domain-independent UI and shared layout primitives
│   ├── layout/              # PaneList, DetailHeader, DetailPane and PageShell
│   └── panels/              # Shared panel frame/buttons
├── context/                 # Configuration, notifications and workspace lifecycle
├── hooks/                   # Reusable selection, draft, menu and editor hooks
├── lib/                     # General utilities, Markdown and sanitization
├── store/                   # Redux assembly, HTTP helpers and request/draft lifecycle
├── realtime/                # Canonical SSE transport, event queue and correlation
├── styles/                  # Tokens, base styles and shared chrome foundations
└── features/
    ├── agents/              # Catalogue, agent details and catalogue/run state
    ├── alerts/              # Alert panel, model and state
    ├── canvases/            # Canvas pages, sandbox host and generated vendor bundles
    ├── channels/            # Messaging channels (Telegram): page, model and state
    ├── chat/                # Conversation/sessions, quick chat and response streams
    │   ├── pages/
    │   ├── components/
    │   ├── hooks/
    │   ├── models/
    │   ├── state/
    │   └── styles/
    ├── dashboard/           # Overview page and cross-feature data composition
    ├── design-library/      # Development-only UI catalogue
    ├── documents/           # Shared document-type catalogue state
    ├── inbox/
    ├── insights/
    ├── instructions/
    ├── knowledge/
    ├── monitoring/          # Runtime status, logs and status presentation
    ├── plugins/             # Plugins, marketplaces and marketplace settings
    ├── projects/            # Browser-local projects/solutions
    ├── repositories/
    ├── routines/            # Routine editor, run subscriptions and pure models
    ├── schedules/           # Schedules and shared widget operations/state
    ├── search/
    ├── settings/            # Preferences, providers, profiles, MCP and policies
    ├── tasks/               # Tasks/Kanban, metadata models and detail components
    └── workflows/
        ├── WorkflowsPage.jsx # Page/data/selection coordination
        ├── canvas/           # Nodes, ports, palette controls and node registration
        ├── editor/           # Options editor and draft model
        ├── models/           # Pure graph conversion, schemas, templates and unpacking
        ├── runs/             # Run presentation and formatting
        └── state/
```

Keep small features flat. Introduce subdirectories when they describe a real
responsibility, such as workflow `canvas/`, `editor/` and `runs/`. CSS Modules live beside their implementation; unit tests mirror those paths
under `tests/frontend/unit/`. Do not create empty standard folders.

## Ownership and dependencies

- Application wiring (`App.jsx`, `routes.jsx`, `shell/`, workspace lifecycle and
  store assembly) may compose features and shared code.
- Features import their own state directly and use explicit imports for another
  feature's capability. Do not import the assembled store, `uiDataSlice`, or
  shell implementation from feature code. Supply chrome-owned values as props;
  for example, the shell supplies navigation items to the search panel.
- Shared `components/`, `hooks/`, `lib/` and `realtime/` code must not import
  features, shell code, or the assembled store. ESLint enforces these boundaries.
- Pure models and feature state stay independent of page/editor components.
  Store registration must not import React Flow, pages, panels or editors.
- Keep route imports lazy and direct. Avoid a feature barrel that combines state
  with UI and pulls every editor into the initial bundle.

The shell owns opening, closing, pinning and placing panels. Features own panel
behavior: `chat/QuickChatPanel`, `alerts/AlertsPanel`, `search/SearchPanel`,
`insights/InsightsPanel`, and the settings/account panels. Shared panel framing
lives in `components/panels/PanelFrame.jsx`.

## State, requests and realtime

`store/uiDataSlice.js` composes feature modules into the stable `state.uiData.*`
shape. Each domain exports `initialState`, `eventHandlers`, and optionally
`reducers` and `buildExtraReducers`. Every registered handler receives a shared
event such as `connection.ready` or `status.updated`; status, alerts and widgets
have separate owners. Add an event handler to its feature, not the transport.

Feature actions/selectors/operations are imported directly from their owning
files. Standalone synchronous action creators retain the existing `uiData/*`
action types so feature code does not depend on the composer.

Finite domain operations use `store/async.js` and `store/http.js`. The HTTP 202
acknowledgement confirms dispatch; the correlated canonical event is the
completion signal. Hooks such as `useTasksApi` and `useWidgetsApi` bind feature
operations to Redux and the active workspace. Draft saves retain their supplied
request IDs, including when the stream event arrives before the HTTP reply.
Uploads, preview content and cron-preview responses retain their dedicated
transport contracts.

`realtime/realtimeListener.js` owns the single canonical `EventSource`, validates
and deduplicates envelopes, applies feature events and tracks pending operations.
`store/RealtimeBridge.jsx` is the application adapter that starts the transport
and hydrates cross-feature catalogues. Chat's request-scoped response streams
remain separate under `features/chat/hooks/`; do not combine their generation
lifecycle with the global UI event feed.

Workspace changes clear backend-scoped local caches in
`context/workspaceCache.js`, reset Redux, and remount the subtree keyed by
`workspaceHost`. Preserve this boundary: data, streams and local page state from
one backend must not leak into another workspace.

## UI and styling

Master-detail features compose `PaneList*`, `DetailHeader`, `DetailPane`,
`PageShell`, shared detail cards and `useListSelection`. Keep feature differences
in the feature rather than introducing a generic master-detail page framework.

General Markdown rendering/sanitization lives in `lib/markdown.js` and
`lib/safeMarkdown.js`; instructions, knowledge and canvases do not depend on chat
rendering helpers. Chat-specific event presentation stays in chat.

`styles/main.css` owns design tokens. `App.jsx` preserves the order of existing
global/feature stylesheets. `styles/chrome-frame.css` remains shared legacy
styling; extract styles incrementally with visual checks. New component styles
should use colocated CSS Modules. The generated canvas vendor bundles under
`features/canvases/vendor/` are rebuilt with `npm run build:canvas-vendor`.

## Validation and repository safety

Run lint, unit tests and the production build after structural changes. Check
both route rendering and the affected interactive editor/panel in the browser;
wait for asynchronous SSE data before assessing the result. Keep tests focused
on models and meaningful behavior such as request correlation, stale events,
workspace resets and draft completion.

Create PRs and wait for human merge. Do not merge or deploy unless the human
explicitly requests it.
