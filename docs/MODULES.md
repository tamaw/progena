# Modules

Agent-built ES modules (`.mjs`) — dashboards, interactive diagrams, small games —
authored by Progenitor through a skill, stored as Postgres JSONB documents, pushed
live to the UI, and rendered in a sandbox under **Modules**.

## Design decisions

| Topic | Decision |
|---|---|
| Naming | "module" end to end: agent path `/modules/<name>/`, keys `module:`, UI **Modules** |
| Rendering | Full page inside the app shell: nav rail stays, module fills the main pane at `/modules/:name` under a thin header |
| Data access | Modules may read every document type and subscribe to every document event. The agent wrote the code and can already see all of this data |
| Module writes | Own `module_data:<name>/…` documents only |
| Libraries (v1) | three.js + OrbitControls, d3 |
| Revisions | Immutable per-file history with module timeline and restore |

## Loading design

The iframe runs with `sandbox="allow-scripts"` (no `allow-same-origin`), so its origin is
opaque. An opaque-origin document never sends the oauth2-proxy cookie with subresource
requests, and the backend is event-stream-only, so **the frame makes no network requests
at all**:

1. The parent (authenticated app) loads module files over the UI stream
   (`module.files.loaded`) and vendored library source from Vite `?raw` chunks.
2. The parent rewrites each module file's relative import specifiers
   (`./x.mjs`, `../lib/y.mjs`, static and dynamic) to absolute virtual URLs
   `https://module.invalid/<name>/<path>` using `es-module-lexer`. Bare specifiers must be
   catalogue library specifiers.
3. The parent builds `srcdoc`: CSP meta, an import map from every virtual URL and library
   specifier to a `data:text/javascript;base64,…` URL, theme variables, and a bootstrap
   that imports the entry and calls `mount(root, host)`.

CSP: `default-src 'none'; script-src 'unsafe-inline' data:; style-src 'unsafe-inline' data:;
img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'`.

Verified in headless Chromium with the real bundles (Firefox not checked): three r186 + OrbitControls + d3 load,
static and dynamic relative imports resolve, `fetch`/`localStorage`/`parent.document`/
`document.cookie` are all blocked, `location.origin === "null"`.

## Module format

```
/modules/<name>/
  manifest.json
  index.mjs          # entry; may import ./other.mjs relatively
  *.mjs *.css *.json *.svg *.md *.txt
```

- `<name>`: `^[a-z][a-z0-9-]{1,47}$`; `_`-prefixed names are reserved.
- Paths: relative POSIX, no `..`, no leading `/`, each segment `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
- Caps: 256 KiB per file; 1 MiB and 32 files per module.
- Only `.mjs` files are importable. Other files are read with `host.asset(path)`.

`manifest.json` (schema `data/schemas/module_manifest.schema.v1.json`):

```json
{
  "name": "task-graph",
  "title": "Task Graph",
  "description": "Live force graph of tasks and their projects",
  "entry": "index.mjs",
  "libraries": ["d3"]
}
```

`name` must equal the directory; `entry` must be an `.mjs` path; `libraries` ids must
exist in `data/module-libraries.json`. Manifest is written first; files may follow.

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
  const css = host.asset('style.css');
  return () => off();
}
```

Light and dark theme: the frame's `<html>` carries `data-theme="light|dark"` and `color-scheme`,
and the app's CSS custom properties are installed on `:root`. When the user switches theme the
host pushes the new mode and tokens over postMessage without remounting, so `var(--…)` styles
and `[data-theme="dark"]` selectors restyle immediately. `host.theme` exposes `mode`,
`tokens`, `get(name)` and `onChange(callback)` (returns unsubscribe) for colours computed in JS
(canvas, WebGL, d3 scales), which must be re-applied in the callback.

## Storage

All keys live in the `progenitor.state` store namespace, so the existing `store_changed`
NOTIFY and `document_events` triggers fire with no trigger change.

| Key | Payload | Writer |
|---|---|---|
| `module:<name>/<path>` | `{content: str, updated_at: iso}` (content document) | Agent mount; backend restore/delete |
| `module_data:<name>/<key>` | `{value: <any JSON>, updated_at: iso}` | Backend on module request |
| `module_status:<name>` | `{state: "loaded"\|"error", revision: str, errors: [{message, stack?}], reported_at: iso}` | Backend on host report |

Shared code in `app/progenitor/modules.py` (pure, used by runtime and backend):
key prefixes re-exported from `document_kinds.py` (`MODULE_DOCUMENT_KEY_PREFIX`,
`MODULE_DATA_KEY_PREFIX`, `MODULE_STATUS_KEY_PREFIX`), name/path/extension validation,
caps, manifest validation, `load_module_libraries()`, and
`module_revision(files: Mapping[str, str]) -> str` = first 12 hex of sha256 over sorted
`path\0content\0`.

`module_data` keys: `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, value ≤ 256 KiB serialized.

Built-in document types: `module` (prefix `module:`, schema `content_document`),
`module_data` (prefix `module_data:`, no schema), `module_status` (prefix `module_status:`, no schema).

### Revision history

`module_revisions` table installed alongside the other revision installers in
`app/backend/storage/documents.py`, modelled on `instruction_revisions`:
`(id, name, path, op 'write'|'delete', content NULL on delete, content_hash, captured_at)`,
captured by trigger on `module:` rows (insert/update with changed content, delete), with an
immutability trigger. Restore to time T: every path whose latest revision ≤ T is a write is
rewritten to that content; current paths absent at T are deleted. Restores are normal store
writes, so they are themselves revisions and fire NOTIFY.

## Agent side (runtime)

- `ModuleDirectoryBackend` (`app/progenitor/agent/module_backend.py`) at `routes["/modules/"]` in `app/progenitor/agent/backends.py`,
  on top of `ContentDocumentDirectoryBackend` with content kind `module`, enforcing
  validation and caps on `awrite`/`aedit`.
- Read-only virtual files: `/modules/_catalogue.json` (libraries from
  `data/module-libraries.json`, document type ids/labels/key/key_prefix/schema, host API
  summary) and `/modules/<name>/status.json` (projection of `module_status:<name>` plus
  `current_revision` and `stale` when the status revision differs).
- No agent delete (DeepAgents <0.7 never routes `delete_file`); deletion is a UI action.
- Skill `data/skills/building-modules/SKILL.md`.

## Backend

`app/backend/modules/routes.py`, router prefix `/api/ui`, included in `main.py`. Every route
returns HTTP 202 `UiRequestAcknowledgement` and replies on `/api/ui/stream`. Requests carry
`X-Request-ID`; detail replies are **targeted** with `broadcast_ui_event(..., request_id=,
client_id=)`. `client_id` is the tab's `realtimeClientId` (query param on GET/DELETE, body on
POST/PUT).

| Route | Reply event | Payload |
|---|---|---|
| `GET /modules` | `modules.snapshot` (broadcast) | `{items: [ModuleSummary]}` |
| `GET /modules/{name}/files?client_id=` | `module.files.loaded` | `{name, revision, files: {path: content}}` |
| `GET /modules/{name}/revisions?client_id=` | `module.revisions.loaded` | `{name, revisions: [{id, path, op, content_hash, captured_at}]}` newest first |
| `GET /modules/{name}/revisions/{id}?client_id=` | `module.revision.loaded` | `{name, revision: {id, path, op, content, content_hash, captured_at}}` |
| `POST /modules/{name}/restore` `{client_id, captured_at}` | `module.restored` | `{name, captured_at, revision}` |
| `DELETE /modules/{name}?client_id=` | `module.deleted` (broadcast) | `{name}` |
| `POST /modules/{name}/status` `{client_id, state, revision, errors}` | `module.status.saved` | `{name, state, revision}` |
| `POST /modules/{name}/documents` `{client_id, type, id?, limit?, cursor?}` | `module.documents.loaded` | `{name, type, items: [{key, id, document}], cursor}` |
| `POST /modules/{name}/data` `{client_id, op: get\|set\|delete\|list, key?, value?}` | `module.data.loaded` | `{name, op, key, value, items?}` |
| `PUT /modules/{name}/subscription` `{client_id, events: [pattern]}` | `module.subscription.updated` | `{name, events}` |
| `DELETE /modules/{name}/subscription?client_id=` | `module.subscription.updated` | `{name, events: []}` |

`ModuleSummary`: `{name, title, description, entry, libraries, files: [path], revision,
updated_at, manifest_error: str|null, status: {state, revision, errors, reported_at}|null}`.

Errors (unknown module/type/revision, invalid key or pattern) are HTTP 4xx before the 202,
like other UI routes.

Documents request: resolve `type` with the live registry; exact-key types return one item;
prefix types list by `key_prefix`, sorted by key, `limit` default 100, max 500, `cursor` =
last key returned.

Event patterns: `<document_type|*>.<created|updated|deleted|*>`. Interests are keyed by
`(client_id, name)` and dropped on stream disconnect, mirroring `routine_events.interests`.
A new `DocumentEventDispatcher` handler sends matching typed events as
`module.document.event` `{name, event: {event_id, document_type, key, id, op, timestamp,
changed_fields, document}}` to each interested client (`document` = `after`, null on delete).

Watcher: `_queue_state_watch_status` rebroadcasts `modules.snapshot` for `module:` and
`module_status:` keys (and on resync); add `module:`, `module_data:`, `module_status:` to the
snapshot-diff bypass in `app/backend/monitoring/state.py`.

## Vendored libraries

`app/frontend/scripts/build-module-vendor.mjs` (`npm run build:module-vendor`) bundles
pinned `three@0.186.1` and `d3@7.9.0` with esbuild into `app/frontend/src/features/modules/vendor/`
(`three.mjs`, `three-orbit-controls.mjs` with `three` external, `d3.mjs`), generates
`libraries.js` (specifier → lazy `?raw` loader) and `data/modules/libraries.json` (agent
catalogue). Bundles, `libraries.js` and the catalogue are committed (the pre-commit large-file
limit is 1 MB to fit the 742 KB three.js bundle); rerun the script after changing a pinned version.

## Frontend

- Routes `/modules` and `/modules/:name`; **Modules** nav item and icon in the Spectate group;
  `'modules.snapshot': '/modules'` in `NAV_ACTIVITY_EVENT_ROUTES`; `/api/ui/modules` in
  `ConfigContext`.
- `src/features/modules/state.js`: `modules.snapshot` / `module.deleted` handlers and
  thunks for list, files, revisions, revision, restore, delete, status; module-scoped
  requests (documents, data, subscription) resolve through `subscribeUiEvent` +
  `request_id`, like `routineSubscriptions.js`.
- `ModulesPage` (`/modules/:name?`): the standard list/detail layout from the design library.
  The list pane uses `PaneListFilters` (All / Available / Errors, **+ New** opens a
  Quick Chat draft), `PaneListSearch` and `sess-row` rows (status dot, title, description,
  files, libraries, age). It opens the first module when none is selected.
- `ModulePage`, the detail pane: `DetailHeader` with `DetailStatus`, file count, libraries,
  revision and updated meta; icon actions (Ask Progenitor, Reload or Restore, Delete via
  `useConfirmAction` + `PopupModal`); `DetailTabs` for Preview (`ModuleHost`, remounted when
  the summary `revision` changes), Files (read-only highlighted source) and Revisions. The
  agent saves after every `edit_file`, so one request leaves a burst of file revisions;
  Revisions groups bursts (gaps under 2 minutes) into editing sessions showing the files as
  each session left them, and Restore returns the module to the end of a session.
  Filters, file-fence logic and state labels live in `modulesPageModel.js`.
- `ModuleHost` + pure `moduleHostModel.js`: specifier rewrite, import map, srcdoc, bridge
  protocol. Reports `loaded`/`error` status when it differs from the stored status for the
  current revision.
- Revisions panel: timeline, view file at a revision, restore to a point.

## Health status

Colour is saved for problems. A healthy module is quiet: the default is a hollow ring, and only
states that need the user are filled. Deferred: "Writing" (edit in progress; no reliable signal
without hooking the agent's turn) and module-raised attention (`host.status`, e.g. a green
"new high score"; module code only runs while open, so its signal goes stale).

### Statuses

Derived in `modulesPageModel.js` from the summary (`manifest_error`, `files`, `entry`,
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
the module is opened and loads).
