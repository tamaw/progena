# Canvases

Agent-built ES modules (`.mjs`) — dashboards, interactive diagrams, small games —
authored by Progenitor through a skill, stored as Postgres JSONB documents, pushed
live to the UI, and rendered in a sandbox under **Canvases**.

## Design decisions

| Topic | Decision |
|---|---|
| Naming | "canvas" end to end: agent path `/canvases/<name>/`, keys `canvas:`, UI **Canvases** |
| Rendering | Full page inside the app shell: nav rail stays, canvas fills the main pane at `/canvases/:name` under a thin header |
| Data access | Canvases may read every document type and subscribe to every document event. The agent wrote the code and can already see all of this data |
| Canvas writes | Own `canvas_data:<name>/…` documents only |
| Libraries (v1) | three.js + OrbitControls, d3 |
| Revisions | Immutable per-file history captured in `canvas_revisions`; stored only, no browser or restore |

## Loading design

The iframe runs with `sandbox="allow-scripts"` (no `allow-same-origin`), so its origin is
opaque. An opaque-origin document never sends the oauth2-proxy cookie with subresource
requests, and the backend is event-stream-only, so **the frame makes no network requests
at all**:

1. The parent (authenticated app) loads canvas files over the UI stream
   (`canvas.files.loaded`) and vendored library source from Vite `?raw` chunks.
2. The parent rewrites each canvas file's relative import specifiers
   (`./x.mjs`, `../lib/y.mjs`, static and dynamic) to absolute virtual URLs
   `https://canvas.invalid/<name>/<path>` using `es-module-lexer`. Bare specifiers must be
   catalogue library specifiers.
3. The parent builds `srcdoc`: CSP meta, an import map from every virtual URL and library
   specifier to a `data:text/javascript;base64,…` URL, theme variables, and a bootstrap
   that imports the entry and calls `mount(root, host)`.

CSP: `default-src 'none'; script-src 'unsafe-inline' data:; style-src 'unsafe-inline' data:;
img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'`.

Verified in headless Chromium with the real bundles (Firefox not checked): three r186 + OrbitControls + d3 load,
static and dynamic relative imports resolve, `fetch`/`localStorage`/`parent.document`/
`document.cookie` are all blocked, `location.origin === "null"`.

## Canvas format

```
/canvases/<name>/
  manifest.json
  index.mjs          # entry; may import ./other.mjs relatively
  *.mjs *.css *.json *.svg *.md *.txt
```

- `<name>`: `^[a-z][a-z0-9-]{1,47}$`; `_`-prefixed names are reserved.
- Paths: relative POSIX, no `..`, no leading `/`, each segment `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
- Caps: 256 KiB per file; 1 MiB and 32 files per canvas.
- Only `.mjs` files are importable. Other files are read with `host.asset(path)`.

`manifest.json` (schema `data/schemas/canvas_manifest.schema.v1.json`):

```json
{
  "name": "task-graph",
  "title": "Task Graph",
  "description": "Live force graph of tasks and their projects",
  "entry": "index.mjs",
  "libraries": ["d3"],
  "artifacts": ["sales/"]
}
```

`name` must equal the directory; `entry` must be an `.mjs` path; `libraries` ids must
exist in `data/canvases/libraries.json`. Optional `artifacts` lists the artifact paths or
folder prefixes (ending `/`) the canvas visualises; the page shows them as data sources but
reads are not restricted to them. Manifest is written first; files may follow.

Entry contract:

```js
import * as d3 from 'd3';
export default async function mount(root, host) {
  const { items } = await host.documents.list('kanban_card', { limit: 200 });
  const one = await host.documents.get('kanban_card', 'abc123');
  const types = await host.documentTypes();
  const off = host.events.subscribe(['kanban_card.*'], (event) => {});
  const best = await host.data.get('high-score');
  await host.data.set('high-score', 42);
  const sales = await host.artifacts.get('sales/q3.csv'); // {path, content, data, updated_at} | null
  const offSales = host.artifacts.subscribe('sales/', ({ path, op, artifact }) => {});
  const css = host.asset('style.css');
  return () => { off(); offSales(); };
}
```

Light and dark theme: the frame's `<html>` carries `data-theme="light|dark"` and `color-scheme`,
and the app's CSS custom properties are installed on `:root`. When the user switches theme the
host pushes the new mode and tokens over postMessage without remounting, so `var(--…)` styles
and `[data-theme="dark"]` selectors restyle immediately. `host.theme` exposes `mode`,
`tokens`, `get(name)` and `onChange(callback)` (returns unsubscribe) for colours computed in JS
(canvas, WebGL, d3 scales), which must be re-applied in the callback.

## Artifacts

Data a canvas visualises lives at `/artifacts/<path>` (`artifact:<path>`, see
[STATE_DOCUMENTS.md](STATE_DOCUMENTS.md#artifacts)) so other agents and workflows can
update it without touching the canvas. The iframe bootstrap implements `host.artifacts` on
the existing `documents` bridge request (`type: 'artifact'`); no artifact-specific route exists.

- `get(path)` fetches one row and parses it with `artifactFromDocument`
  (`canvasHostModel.js`, embedded into the bootstrap by source): JSON, JSONL as an array,
  CSV/TSV (RFC 4180 quoting, header row) as string-valued records, otherwise the text. A parse
  failure rejects.
- `list(prefix)` pages the `documents` request with its `prefix` filter and returns
  `{path, updated_at, bytes}`.
- `subscribe(prefix, callback)` subscribes to `artifact.*` and filters by id prefix; the event
  carries the new document, so no extra fetch.

## Storage

All keys live in the `progenitor.state` store namespace, so the existing `store_changed`
NOTIFY and `document_events` triggers fire with no trigger change.

| Key | Payload | Writer |
|---|---|---|
| `canvas:<name>/<path>` | `{content: str, updated_at: iso}` (content document) | Agent mount; backend restore/delete |
| `canvas_data:<name>/<key>` | `{value: <any JSON>, updated_at: iso}` | Backend on canvas request |
| `canvas_status:<name>` | `{state: "loaded"\|"error", revision: str, errors: [{message, stack?}], reported_at: iso}` | Backend on host report |

Shared code in `src/progenitor/canvases/contracts.py` (pure, used by runtime and backend):
key prefixes re-exported from `document_kinds.py` (`CANVAS_DOCUMENT_KEY_PREFIX`,
`CANVAS_DATA_KEY_PREFIX`, `CANVAS_STATUS_KEY_PREFIX`), name/path/extension validation,
caps, manifest validation, `load_canvas_libraries()`, and
`canvas_revision(files: Mapping[str, str]) -> str` = first 12 hex of sha256 over sorted
`path\0content\0`.

`canvas_data` keys: `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, value ≤ 256 KiB serialized.

Built-in document types: `canvas` (prefix `canvas:`, schema `content_document`),
`canvas_data` (prefix `canvas_data:`, no schema), `canvas_status` (prefix `canvas_status:`, no schema).

### Revision history

`canvas_revisions` table installed alongside the other revision installers in
`src/backend/storage/documents.py`, modelled on `instruction_revisions`:
`(id, name, path, op 'write'|'delete', content NULL on delete, content_hash, captured_at)`,
captured by trigger on `canvas:` rows (insert/update with changed content, delete), with an
immutability trigger. History is stored only: there is no revision route, browser or restore.

## Agent side (runtime)

- `CanvasDirectoryBackend` (`src/progenitor/agent/filesystem/canvases.py`) at `routes["/canvases/"]` in `src/progenitor/agent/filesystem/mounts.py`,
  on top of `ContentDocumentDirectoryBackend` with content kind `canvas`, enforcing
  validation and caps on `awrite`/`aedit`.
- Read-only virtual files: `/canvases/_catalogue.json` (libraries from
  `data/canvases/libraries.json`, document type ids/labels/key/key_prefix/schema, host API
  summary) and `/canvases/<name>/status.json` (projection of `canvas_status:<name>` plus
  `current_revision` and `stale` when the status revision differs).
- No agent delete (DeepAgents <0.7 never routes `delete_file`); deletion is a UI action.
- Skill `data/skills/building-canvases/SKILL.md`.

## Backend

`src/backend/canvases/routes.py`, router prefix `/api/ui`, included in `main.py`. Every route
returns HTTP 202 `UiRequestAcknowledgement` and replies on `/api/ui/stream`. Requests carry
`X-Request-ID`; detail replies are **targeted** with `broadcast_ui_event(..., request_id=,
client_id=)`. `client_id` is the tab's `realtimeClientId` (query param on GET/DELETE, body on
POST/PUT).

| Route | Reply event | Payload |
|---|---|---|
| `GET /canvases` | `canvases.snapshot` (broadcast) | `{items: [CanvasSummary]}` |
| `GET /canvases/{name}/files?client_id=` | `canvas.files.loaded` | `{name, revision, files: {path: content}}` |
| `DELETE /canvases/{name}?client_id=` | `canvas.deleted` (broadcast) | `{name}` |
| `POST /canvases/{name}/status` `{client_id, state, revision, errors}` | `canvas.status.saved` | `{name, state, revision}` |
| `POST /canvases/{name}/documents` `{client_id, type, id?, prefix?, limit?, cursor?}` | `canvas.documents.loaded` | `{name, type, items: [{key, id, document}], cursor}`; `prefix` narrows a collection listing by id prefix |
| `POST /canvases/{name}/data` `{client_id, op: get\|set\|delete\|list, key?, value?}` | `canvas.data.loaded` | `{name, op, key, value, items?}` |
| `PUT /canvases/{name}/subscription` `{client_id, events: [pattern]}` | `canvas.subscription.updated` | `{name, events}` |
| `DELETE /canvases/{name}/subscription?client_id=` | `canvas.subscription.updated` | `{name, events: []}` |

`CanvasSummary`: `{name, title, description, entry, libraries, artifacts, files: [path], revision,
updated_at, manifest_error: str|null, status: {state, revision, errors, reported_at}|null}`.

Errors (unknown canvas/type/revision, invalid key or pattern) are HTTP 4xx before the 202,
like other UI routes.

Documents request: resolve `type` with the live registry; exact-key types return one item;
prefix types list by `key_prefix`, sorted by key, `limit` default 100, max 500, `cursor` =
last key returned.

Event patterns: `<document_type|*>.<created|updated|deleted|*>`. Interests are keyed by
`(client_id, name)` and dropped on stream disconnect, mirroring `routine_events.interests`.
A new `DocumentEventDispatcher` handler sends matching typed events as
`canvas.document.event` `{name, event: {event_id, document_type, key, id, op, timestamp,
changed_fields, document}}` to each interested client (`document` = `after`, null on delete).

Watcher: `_queue_state_watch_status` rebroadcasts `canvases.snapshot` for `canvas:` and
`canvas_status:` keys (and on resync); add `canvas:`, `canvas_data:`, `canvas_status:` to the
snapshot-diff bypass in `src/backend/monitoring/state.py`.

## Vendored libraries

`src/frontend/scripts/build-canvas-vendor.mjs` (`npm run build:canvas-vendor`) bundles
pinned `three@0.186.1` and `d3@7.9.0` with esbuild into `src/frontend/src/features/canvases/vendor/`
(`three.mjs`, `three-orbit-controls.mjs` with `three` external, `d3.mjs`), generates
`libraries.js` (specifier → lazy `?raw` loader) and `data/canvases/libraries.json` (agent
catalogue). Bundles, `libraries.js` and the catalogue are committed (the pre-commit large-file
limit is 1 MB to fit the 742 KB three.js bundle); rerun the script after changing a pinned version.

## Frontend

- Routes `/canvases` and `/canvases/:name`; **Canvases** nav item and icon in the Spectate group;
  `'canvases.snapshot': '/canvases'` in `NAV_ACTIVITY_EVENT_ROUTES`; `/api/ui/canvases` in
  `ConfigContext`.
- `src/features/canvases/state.js`: `canvases.snapshot` / `canvas.deleted` handlers and
  thunks for list, files, delete, status; canvas-scoped
  requests (documents, data, subscription) resolve through `subscribeUiEvent` +
  `request_id`, like `routineSubscriptions.js`.
- `CanvasesPage` (`/canvases/:name?`): the standard list/detail layout from the design library.
  The list pane uses `PaneListFilters` (All / Available / Errors, **+ New** opens a
  Quick Chat draft), `PaneListSearch` and `sess-row` rows (status dot, title, description,
  files, libraries, age). It opens the first canvas when none is selected.
- `CanvasPage`, the detail pane: `DetailHeader` with `DetailStatus`, file count, libraries,
  data sources, revision and updated meta; icon actions (Ask Progenitor, Reload, Delete via
  `useConfirmAction` + `PopupModal`); the live preview (`CanvasHost`, remounted when the
  summary `revision` changes) fills the rest of the pane, with no tabs. Filters and state
  labels live in `canvasesPageModel.js`.
- `CanvasHost` + pure `canvasHostModel.js`: specifier rewrite, import map, srcdoc, bridge
  protocol. Reports `loaded`/`error` status when it differs from the stored status for the
  current revision.

## Health status

Colour is saved for problems. A healthy canvas is quiet: the default is a hollow ring, and only
states that need the user are filled. Deferred: "Writing" (edit in progress; no reliable signal
without hooking the agent's turn) and canvas-raised attention (`host.status`, e.g. a green
"new high score"; canvas code only runs while open, so its signal goes stale).

### Statuses

Derived in `canvasesPageModel.js` from the summary (`manifest_error`, `files`, `entry`,
`status.state`, `status.revision`, `revision`); no backend change. Checked in this order:

| Status | Indicator | When |
|---|---|---|
| **Error** | filled red, label "Error" | `manifest_error` set, the manifest's `entry` file is missing, or the last load report is `error` for the **current** revision. The alert under the header says which |
| **Updated** | filled blue (`info`), label "Updated" | last load report is `error` for an **older** revision (changed since; probably fixed, unconfirmed until it is opened and loads) |
| **Available** | hollow ring (new shared `idle` tone), muted label "Available" | otherwise, including never opened and loaded fine |

"Loaded" and "Not opened" stop being user-facing states; the agent still sees the load report
in `status.json`. The list dot, header `DetailStatus` badge and errors-under-header alert all use
the same derived status.

### Shared status tones

`DetailStatus` has `tone="idle"` (hollow ring, muted) in `detail-header.css`, and list rows have
matching `sess-row__dot--{idle,muted,ok,info,warning,error}` classes in `chrome-frame.css`; both
are shown on `/design` ("Status tones"). Inbox "Read" and Plugins "Not installed" now use the
shared idle tone instead of their own hollow-ring overrides.

### List filters

**All / Available / Errors**, where Errors = Error + Updated (Updated stays unresolved until
the canvas is opened and loads).
