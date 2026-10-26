# State documents and virtual filesystem mounts

Progena persists runtime documents in the LangGraph store's Postgres-backed
`("progena", "state")` namespace. JSON-looking paths are agent-facing
virtual files; they are not files written under `data/state/` or another
repository state directory. The backend and runtime use the same persisted
documents, while static assets remain checked in under `data/`.

`src/progena/agent/filesystem/mounts.py:create_progena_backend` is the source of
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

### Mount access

`MOUNT_ACCESS` in `src/progena/agent/filesystem/mounts.py` declares the main
agent's access to every mount as `read`, `read-write`, or `read-write-delete`;
the longest matching prefix wins and an undeclared mount fails backend
construction. `read` mounts refuse every mutation. `delete` removes single
files only on `read-write-delete` mounts (`/routines/`, `/workflows/`,
`/tasks/`, `/instructions/`, `/canvases/`, `/artifacts/`, `/shared/`) and in
scratch state; directories, mount roots, and `..` paths are refused. Domain
rules still apply: runtime `progena/` instructions, knowledge pages, sessions,
and alerts cannot be deleted. Routine Code nodes share this backend, and object
agents keep their own per-manifest `read`/`read-write` mounts.

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

Each `/inbox/<id>.md` message is a separate `progena.state` /
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
`src/progena/agent/filesystem/canvases.py`). Each file is one `canvas:<name>/<path>`
content document (`{content, updated_at}`); `manifest.json`
(`canvas_manifest.schema.v1.json`) must be written first. Names, paths, file
types and size caps are shared by the runtime mount and the backend in
`src/progena/canvases/contracts.py`. The mount also projects read-only
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
(`ArtifactDirectoryBackend`, `src/progena/agent/filesystem/artifacts.py`), one
`artifact:<path>` content document (`{content, updated_at}`) per file. Paths,
types (`.csv`, `.tsv`, `.json`, `.jsonl`, `.md`, `.txt`) and the 1 MiB cap are in
`src/progena/artifacts/contracts.py`; workflow `document_write` and
`document_create` nodes on the `artifact` type are held to the same rules. Only
the latest content is kept. Progena has the whole mount; an object agent
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

`profiles.json` is also an agent-writable `/config/` virtual file. Progena can
write the complete validated map to add, edit, or remove profiles; its
`StateManager` write lands in the same Postgres row as the UI route.

An optional positive-integer `context_window` on a profile declares its token
capacity, for example `"context_window": 128000`. The chat composer shows a
small context pie to the left of voice only when that capacity can be matched
to the latest parent-model call. It uses that call's reported input plus output
tokens, not accumulated conversation/billing totals. Hover shows the token
counts and percentage. Missing capacity or unknown usage hides the indicator;
no model limits are inferred. PATCH `context_window: null` removes the capacity.

An optional positive-integer `cache_ttl_seconds` configures a **display-only
prompt-cache retention estimate**. For example, `"cache_ttl_seconds": 3600`
selects a one-hour estimate for that profile. Bold text immediately left of
the context pie counts down each minute (`60m`, `59m`, …), then shows `0m`.
Longer durations use hours. Missing retention, unknown/ambiguous profile identity,
or an unavailable response timestamp hides it. PATCH `cache_ttl_seconds: null`
removes the estimate.

The live countdown starts on successful parent-response completion, not on text
chunks, tool/subagent events, errors, or cancellation. Reload uses the persisted
assistant timestamp rather than the browser's load time; historical creation
timestamps can precede completion, so this is conservative and may shorten the
estimate. Tool-only synthetic rows do not start a timer. Switching conversations
or profile identities never reuses a different conversation/model's deadline.

This value neither configures provider caching nor proves an entry exists.
[OpenAI's prompt-cache lifetimes](https://developers.openai.com/api/docs/guides/prompt-caching#cache-lifetime)
vary by model: GPT-5.6+ documents 30-minute or 12-hour minimum lifetimes; older
`in_memory` caches typically survive 5–10 minutes of inactivity, up to one hour,
with separate extended-retention policies.
[Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
documents a default five-minute lifetime and an optional paid one-hour lifetime,
measured from request start rather than response completion. Configure the
estimate deliberately; no universal retention duration is inferred.

Postgres changes to the `progena.state` / `profile` document trigger an
unsolicited `profiles.snapshot`. This key bypasses coarse `StateSnapshot` diff
filtering because profiles are not runtime-health fields. The existing canonical
stream is reused: no profile-specific stream or polling. On `connection.ready`
and reconnect, the frontend hydrates the shared catalogue consumed by the
Profiles page and routine model-profile fields.
