# Frontend — Agent Guide

React + Vite SPA for Progenitor. This file is the orientation for the next agent:
the real architecture, the conventions to follow, and the gotchas to avoid.

## Stack

- **React 19** (function components + hooks only; no class components)
- **Vite 7** dev server / build (`type: module`, JSX in `.jsx`)
- **Redux Toolkit 2** + **react-redux 9** for shared/realtime state
- **react-router-dom 7** for routing (`App.jsx` wraps routes in `AppRuntimeShell`)
- **@xyflow/react** (React Flow) for the workflow/sequence canvases
- **chart.js / react-chartjs-2** for dashboards
- **vitest 3** for unit tests, **@playwright/test** for e2e
- **ESLint 9** flat config (`eslint.config.js`)

No TypeScript, no Tailwind/styled-components — styling is plain CSS + CSS Modules.

## Commands (run from `app/frontend`)

```bash
npm install            # after pulling dependency changes
npm run dev            # Vite dev server on :5173 (proxies to backend :8000)
npm run build          # production build (must stay green)
npm run lint           # eslint . — must stay clean
npm run test           # vitest run (unit tests)
npm run test:watch     # vitest watch
npm run test:e2e       # playwright (needs the full stack running)
```

> Shell note: the workspace shell is **fish**, not bash. Avoid bash-only syntax
> (`for..do..done`, `(cmd || cmd)` in command position, `/dev/tcp`).

## Backend wiring

The frontend never talks to the runtime directly. `vite.config.js` has a custom
proxy plugin that forwards `/api/*` and `/v1/*` to the backend at
`http://127.0.0.1:8000` (dev **and** preview). Everything else is served by Vite.

## Directory map (`src/`)

```
App.jsx                 # routes + global CSS imports, wrapped in AppRuntimeShell
main.jsx                # entry point
components/
  layout/               # app chrome + shared master-detail primitives
    AppRuntimeShell.jsx #   the chrome (rail, header, panels) — large, behaviour-heavy
    navIcons.jsx        #   presentational SVG icons used by the shell
    quickChatConfig.js  #   quick-chat panel config + outbound-message helpers (pure)
    PaneList.jsx        #   list-pane primitives (PaneListSearch/Filters/Group/...)
    DetailHeader.jsx    #   detail-pane header + shared action icons
    DetailPane.jsx / PageShell.jsx / Sidebar.jsx
  common/               # DetailCards (MetricCard/CardFact/CapabilityChip), NumberStepper,
                        #   NotificationContainer (+ co-located .module.css)
  chat/                 # chat input/messages + pure models (conversationModel, etc.)
  canvas/                # React Flow nodes + panels for the Workflows canvas
  agents/               # AgentGraph (React Flow), AgentDetailBodies, agentFormat
  dashboard/
context/                # React context providers
  WorkspaceContext.jsx  #   active multi-backend workspace host + in-place switching
  ConfigContext, HealthContext, AlertCenterContext, ChromeRuntimeContext, NotificationContext
hooks/
  api/                  # API layer (see "API hooks" below)
  useListSelection.js   #   shared list filter/group/select hook used by master-detail pages
  useChatStream.js, useSSE.js, useTheme.js, useChatSessionPrefs.js
pages/                  # one component per route; pure logic lives in *PageModel.js siblings
store/                  # Redux Toolkit (see "State & realtime" below)
lib/                    # markdown, indexedDb, low-level utils
styles/                 # global stylesheets imported by App.jsx (+ canvas/nodes by components)
```

## Core conventions (follow these)

### API requests — `store/`
`store/http.js` owns acknowledged finite requests and request-ID helpers. Domain
operations live in the Redux thunks under `store/uiData/`; pages consume their
selectors and do not add direct JSON API hooks.

### State & realtime — `store/`
- `store.js` wires the Redux store; `uiDataSlice.js` is split into per-domain modules
  under `store/uiData/` (`agents`, `chats`, `tasks`, `knowledge`, `mcp`, `runtime`).
- **Single SSE source**: `realtimeListener.js` owns the one `EventSource` and dedupes
  by event id (`realtimeSlice.js`); incoming events update `uiData*` reducers.
  Pages should read via selectors — **don't** open their own streams.
- `http.js` holds shared fetch helpers for the store layer.

### Master-detail pages — `pages/`
Most list/detail pages compose the same primitives instead of re-rolling layout:
`PaneList*` (left list), `DetailHeader` + `DetailPane` (right detail),
`DetailCards` (metric/fact/chip cards), and `useListSelection` (filter/group/select).
Examples: `ChannelsPage`, `ModelsPage`, `RepositoriesPage`, `SessionsPage`,
`SkillsPage`, `PromptsPage`, `KnowledgePage`, `TasksPage`.
Don't build a generic `<MasterDetailPage>` — the primitives are the abstraction.

### Keep components thin — extract pure logic
Pure, React-free logic lives in sibling modules so it stays testable and the
component stays focused on rendering/state:
- Page logic → `pages/<name>PageModel.js` (+ `.test.js`), e.g. `tasksPageModel.js`,
  `knowledgePageModel.js`.
- Chat parsing/formatting → `components/chat/*Model.js`.
- Workflow graph helpers → co-located in `pages/WorkflowsPage.jsx` (`workflowToFlow` is exported for its unit test).
- Presentational icon sets / static config → dedicated modules
  (`components/layout/navIcons.jsx`, `quickChatConfig.js`).
When you delete a component's last caller of an imported helper, drop the import
too (eslint `no-unused-vars` will flag it).

### CSS
- Global stylesheets are imported once in `App.jsx` (`main.css` is the design-token
  base — keep it). `workspace.css` / `nodes.css` are imported by their components.
- New component styles should be **CSS Modules** co-located next to the component
  (`Foo.module.css`), like `components/common/NotificationContainer.module.css`.
- `styles/chrome-frame.css` is a large monolith — migrate off it incrementally with
  visual QA, not in one pass. The Kanban page intentionally keeps hardcoded colours
  for now (not wired up yet).

## Testing

- Unit tests are `*.test.js` / `*.unit.test.js` next to the code; run with
  `npm run test`. Mirror the pattern in `pages/tasksPageModel.test.js` — test the
  pure model module, not the React component.
- Playwright e2e lives in `e2e/*.spec.js` (not picked up by vitest) and runs against
  the **live** local stack (postgres :15433, runtime :9501, backend :8000, frontend
  :5173). `e2e/discovery.spec.js` mounts every route and asserts no uncaught errors.

## Repo safety

Create PRs and let a human merge — do not merge PRs or trigger deployments. The
realtime/agent data loads asynchronously over SSE, so e2e/visual checks should wait
for content (the agents list can take several seconds to populate).
