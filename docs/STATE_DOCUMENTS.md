# State documents and virtual filesystem mounts

Progenitor persists runtime documents in the LangGraph store's Postgres-backed
`("progenitor", "state")` namespace. JSON-looking paths are agent-facing
virtual files; they are not files written under `data/state/` or another
repository state directory. The backend and runtime use the same persisted
documents, while static assets remain checked in under `data/`.

`app/progenitor/agent/backends.py:create_progenitor_backend` is the source of
truth for mounts. A route is present only when its dependencies are configured:
the Postgres document routes require a `StateManager`, and `/attachments/`
requires a database URL.

## Disk-backed versus Postgres-backed mounts

The agent sees both ordinary read-only filesystem mounts and virtual document
mounts:

- Disk-backed, read-only: `/self/` (`app/`), `/repos/` (managed worktrees),
  `/logs/`, `/schemas/`, `/skills/`, and bundled native-agent paths under
  `/agents/objects/` and `/agents/native/`.
- Postgres-backed content: `/memories/`, `/instructions/`, `/knowledge/`, and
  runtime or user agent manifests under `/agents/`. Installed marketplace
  plugin skills and agents receive scoped read-only paths below `/skills/` and
  `/agents/`; native skills and native agent manifests remain bundled files.
- Postgres-backed virtual document directories: `/tasks/`, `/workflows/`,
  `/routines/`, `/policies/`, `/inbox/`, `/alerts/`, `/sessions/`, and
  `/config/repos/`. These are collections of documents, not subdirectories on
  disk. `/config/repos/` shadows the broader `/config/` route for repository
  declarations.
- Postgres attachment blobs: `/attachments/` is read-only and exposes uploaded
  files for the active conversation. It is not a state-document mount.

`/tasks/` exposes `board.json` plus one Markdown file per card; `/workflows/`,
`/routines/`, `/policies/`, `/inbox/`, `/alerts/`, `/sessions/`, and
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
