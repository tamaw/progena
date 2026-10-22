# State documents and virtual filesystem mounts

Progenitor persists runtime documents in the LangGraph store's Postgres-backed
`("progenitor", "state")` namespace. JSON-looking paths are agent-facing
virtual files; they are not files written under `data/state/` or another
repository state directory. The backend and runtime use the same persisted
documents, while static assets remain checked in under `data/`.

`src/progenitor/agent/filesystem/mounts.py:create_progenitor_backend` is the source of
truth for mounts. A route is present only when its dependencies are configured:
the Postgres document routes require a `StateManager`, and `/attachments/`
requires a database URL.

## Disk-backed versus Postgres-backed mounts

The agent sees both ordinary read-only filesystem mounts and virtual document
mounts:

- Disk-backed, read-only: `/self/` (`src/`), `/repos/` (managed worktrees),
  `/logs/`, `/schemas/`, `/skills/`, and bundled native-agent paths under
  `/agents/objects/` and `/agents/native/`.
- Postgres-backed content: `/memories/`, `/instructions/`, `/knowledge/`, and
  runtime or user agent manifests under `/agents/`. Installed marketplace
  plugin skills and agents receive scoped read-only paths below `/skills/` and
  `/agents/`; native skills and native agent manifests remain bundled files.
  Progenitor's system prompt is `instruction:progenitor/SYSTEM.md`; boot moves
  a stored legacy `instruction:progenitor/genesis.md` (edits included) there
  before seeding missing instructions, then deletes the legacy key. If both
  exist with different text, `SYSTEM.md` is used and the legacy key is kept.
  `python scripts/smoke_system_prompt.py` (run in CI) checks this migration and
  next-call prompt edits against an in-memory store, without Postgres.
- Postgres-backed virtual document directories: `/tasks/`, `/workflows/`,
  `/routines/`, `/policies/`, `/inbox/`, `/alerts/`, `/sessions/`, `/canvases/`,
  `/artifacts/`, and `/config/repos/`. These are collections of documents, not subdirectories on
  disk. `/config/repos/` shadows the broader `/config/` route for repository
  declarations.
- Postgres attachment blobs: `/attachments/` is read-only and exposes uploaded
  files for the active conversation. It is not a state-document mount.

`/tasks/` exposes `board.json` plus one Markdown file per card; `/workflows/`,
`/routines/`, `/policies/`, `/inbox/`, `/alerts/`, `/sessions/`, `/canvases/`, `/artifacts/`, and
`/config/repos/` likewise map document collections to files. Session files
render the chat body with YAML front matter; only supported front-matter fields
are editable.

## Singleton configuration documents (`/config/`)

`StateDocumentBackend` registers individual `StateFile` views over singleton
Postgres documents. The current factory registry supplies:

- `mcp.json` — MCP server configuration.
- `mcp_tools.json` — discovered MCP tools; only each tool's `enabled` flag can
  be changed.
- `plugins.json` and `marketplaces.json` — installed-plugin and marketplace
  configuration.
- `knowledge.json` and `indexer.json` — knowledge-base catalogue and its
  indexing configuration.
- `schedule.json` — schedule entries keyed by ID. The virtual view omits each
  entry's redundant `id` field and records schedule changes through the normal
  audit path.
- `agents.json` — the runtime registry document.
- `preferences.json` and `profiles.json` — user preferences and model-profile
  configuration.
- `repo_scan.json` and `health.json` — read-only heartbeat-owned snapshots.

These filenames describe views over documents, not a filesystem layout or a
complete inventory of persisted records. Other state-manager records and every
directory-backed route above have their own keys, shapes, and lifecycle rules.
`StateFile` handles parsing and validation before saving through `StateManager`;
the underlying store and its change pipeline remain the persistence authority.

## Backend surface

The FastAPI backend reads and writes the same Postgres-backed state through its
document-store and route services. It is a separate deployable from the runtime,
so validation and presentation logic are implemented at the relevant boundary
rather than by sharing an in-memory filesystem.

## Inbox message revisions

Each `/inbox/<id>.md` message is a separate `progenitor.state` /
`inbox_message:<id>` store row. Backend startup installs a trigger that captures
its complete JSON payload into `inbox_message_revisions` on insert, update, and
delete, and backfills currently stored messages. Like `routine_graph_revisions`,
each revision is immutable and keyed by message ID plus a content hash. The
payload includes the body and frontmatter fields, so user-owned read, archive,
and decision changes can also produce revisions; writing identical content
again does not. History is stored only; the inbox has no revision browser or
restore action.

## Canvases

Agent-built UI canvases (dashboards, diagrams, games) are directories the agent
writes at `/canvases/<name>/` (`CanvasDirectoryBackend`,
`src/progenitor/agent/filesystem/canvases.py`). Each file is one `canvas:<name>/<path>`
content document (`{content, updated_at}`); `manifest.json`
(`canvas_manifest.schema.v1.json`) must be written first. Names, paths, file
types and size caps are shared by the runtime mount and the backend in
`src/progenitor/canvases/contracts.py`. The mount also projects read-only
`/canvases/_catalogue.json` (vendored libraries from `data/canvases/libraries.json`,
document types, host API) and `/canvases/<name>/status.json` (last load result
reported by the browser, `canvas_status:<name>`).

A canvas's own runtime state lives in `canvas_data:<name>/<key>` documents
(`{value, updated_at}`), written only by the backend on the canvas's behalf; a
canvas cannot write any other document. Backend startup installs
`canvas_revisions`, an immutable per-file history captured by trigger on
`canvas:` rows (writes with changed content and deletes); like inbox history it
is stored only, with no browser or restore. `canvas`, `canvas_data` and `canvas_status` are built-in document
types, so workflows and other canvases can react to them. Routes, events and the
sandbox loading design are in [CANVASES.md](CANVASES.md).

## Artifacts

Data files that canvases visualise live at `/artifacts/<path>`
(`ArtifactDirectoryBackend`, `src/progenitor/agent/filesystem/artifacts.py`), one
`artifact:<path>` content document (`{content, updated_at}`) per file. Paths,
types (`.csv`, `.tsv`, `.json`, `.jsonl`, `.md`, `.txt`) and the 1 MiB cap are in
`src/progenitor/artifacts/contracts.py`; workflow `document_write` and
`document_create` nodes on the `artifact` type are held to the same rules. Only
the latest content is kept. Progenitor has the whole mount; an object agent
writes only below a declared `mounts` path such as `/artifacts/research/`.
Canvases read artifacts with `host.artifacts` and stay live through `artifact.*`
document events (see [CANVASES.md](CANVASES.md)).

## Model profiles

`GET /api/ui/model-profiles`, `PATCH /api/ui/model-profiles/{name}`, and
`DELETE /api/ui/model-profiles/{name}` publish `profiles.snapshot` with
`{profiles, request_id}` before returning the standard `202` acknowledgement.
Each public entry contains `name`, `provider`, `deployment`, supported reasoning
overrides, and optional `pricing`; unrelated private fields are never published.
PATCH accepts deployment, `reasoning_enabled`, `reasoning_effort`,
`reasoning_summary`, and `pricing`. A null optional field removes its override;
deployment remains required. Pricing is an object with a three-letter currency
and nonnegative finite decimal-string `input_per_million`,
`cached_input_per_million`, and `output_per_million` rates. Zero is valid;
absent pricing means unpriced, and PATCH `pricing: null` removes all rates.
Insights applies current configured rates to retained checkpoint usage, with
separate currency totals and explicit cache/attribution coverage. See
[BACKEND.md](BACKEND.md#insights-and-estimated-model-cost).
DELETE removes the named raw entry while retaining raw sibling configuration.
Unsupported fields and malformed configuration produce `422`; unknown profiles
produce `404`. The full public snapshot is validated before persistence, and
unchanged raw siblings/private properties are retained.

`profiles.json` is also an agent-writable `/config/` virtual file. Progenitor can
write the complete validated map to add, edit, or remove profiles; its
`StateManager` write lands in the same Postgres row as the UI route.

Postgres changes to the `progenitor.state` / `profile` document trigger an
unsolicited `profiles.snapshot`. This key bypasses coarse `StateSnapshot` diff
filtering because profiles are not runtime-health fields. The existing canonical
stream is reused: no profile-specific stream or polling. On `connection.ready`
and reconnect, the frontend hydrates the shared catalogue consumed by the
Profiles page and routine model-profile fields.
