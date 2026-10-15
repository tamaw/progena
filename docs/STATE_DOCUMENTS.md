# State documents: Postgres records with virtual-file views

This describes Progenitor's state-document pattern. Runtime state and structured
logs live in the LangGraph-backed Postgres store; the `.json` names are stable
virtual-file views exposed to the agent, not files written under the repository's
state directory. The same documents are editable from two independent surfaces:
the agent's virtual filesystem and backend HTTP/API operations used by the
frontend.

Read this before adding a new document type or asking why a document is exposed
as a virtual file instead of a physical JSON file.

## Why two surfaces, not one

```
Postgres store, namespace ("progenitor", "state")
        |
        +-- app/progenitor (runtime process, :9501)
        |     StateManager (state/manager.py) <-- LangGraph store
        |     StateDocumentBackend (agent/state_backend.py)
        |       exposes documents as virtual files under /config/*.json
        |       so the DeepAgent edits them with normal file tools
        |
        +-- app/backend (FastAPI process, :8000)
              StateReader / PostgresDocumentStore (state_reader.py, document_store.py)
              ui_routes.py widget CRUD endpoints, used by the React frontend
```

Both processes read/write the *same* Postgres rows, but there is no shared
Python module between them — the runtime and the backend are separate
deployables. That means each document's shape assumptions (dict-keyed-by-id,
defaults, validation) are duplicated once per process, not once total. This is
a known, accepted cost, not an oversight — don't try to merge the two into one
shared package; it would mean a hard dependency between two currently-independent
processes.

## The canonical shape: dict keyed by id

`mcp.json`'s `mcpServers` map was the original shape and is now the model
every other document should match unless it's a genuinely poor fit:

```json
{ "mcpServers": { "my-server": { "url": "...", "enabled": true } } }
```

i.e. top-level (or nested) **object keyed by the entry's id**, not a list of
objects each carrying its own `id` field. `schedule.json` and the
`event_widgets` document were both migrated to this shape (mid-2026) for one
reason: a list requires scan/filter/append code (`for e in entries: if
e["id"] == x`) at every call site, while a dict gives you `.get(id)` /
`[id] = value` / `del entries[id]` for free. When adding a new document type,
default to this shape.

`agents.json` (`registry`) is `{"agents": [...], "tools": [...]}` — a list —
because agent identity for this document is `name`, and preserving a specific
list order matters for how the doc is displayed; it wasn't converted and
shouldn't be forced to match without a reason.

## No migrations, no compat shims

**Explicit, durable rule** (see `feedback_no_migrations` memory): when a state
document's shape changes, do not write migration-on-read or legacy-shape
tolerance code. Change the reader/writer to assume the new shape, accept that
existing local/dev Postgres rows for that document are now stale, and move on.
This was applied retroactively to strip migration code that had been added
during the schedule list→dict conversion. It mirrors `CLAUDE.md`'s stated
preference for breaking changes over compatibility scaffolding for *internal*
state — it does not necessarily extend to user-facing data without checking
first.

The one deliberate exception left in the codebase is kanban's
`_migrate_inline_cards` (`ui_routes.py`) — a precedent from before this rule
## Surface 1: agent-facing virtual files (`app/progenitor/agent/state_backend.py`)

`StateFile` is the unit: a virtual file backed by `load`/`save`/`validate`
callables plus an optional `on_change` hook (for example, live MCP reload after
a write). `StateDocumentBackend` dispatches `aread`/`awrite`/`aedit` across the
registered documents. A successful write validates the text and persists the
document through `StateManager` into the Postgres store.

The current `/config/` document views are:

- `mcp.json` — MCP server configuration; agent-writable.
- `knowledge.json` — knowledge-base catalog; agent-writable. Pages live in
  `knowledge:<base-id>/<path>.md` content documents, mounted at `/knowledge/`,
  with immutable history in the `knowledge_page_revisions` table.
- `schedule.json` — schedule entries keyed by ID; agent-writable with audit diffing.
- `agents.json` — registry agents and runtime tools; agent-writable.
- `health.json` — heartbeat status; read-only and runtime-owned.

The JSON suffix describes the virtual view. It does not imply a local file under
`data/state`.

Four documents (`mcp.json`, `knowledge.json`, `agents.json`, and `health.json`)
use the simple get/save shape. Schedule has unique semantics that a generic
factory would have to special-case: `id` lives in the dict key, exactly one of
`cron_expression`/`scheduled_at` is required, and changes emit the same
`entry_added`/`entry_removed`/`entry_updated` audit events as direct
`StateManager` mutations.

Registration is data-driven in `agent/backends.py`:

```python
state_file_factories = [
    create_mcp_state_file,
    create_knowledge_state_file,
    create_schedule_state_file,
    create_registry_state_file,
    create_health_state_file,
]
routes["/config/"] = StateDocumentBackend(
    [factory(state_manager) for factory in state_file_factories]
)
```

Kanban is deliberately not in this list. `KanbanTaskDirectoryBackend` is a
different architecture: one Postgres document per card
(`kanban_card:<id>`) plus a structure document (`board.json` with columns and
`card_order`), surfaced as per-card `.md` virtual files with YAML frontmatter
and Markdown bodies. Workflow definitions have their own
`WorkflowDirectoryBackend` and are exposed as `/workflows/<id>.json`.

JSON Schemas under `data/schemas/` validate entry shapes and workflow
definitions. The state backend also performs semantic validation such as cron
parsing and timestamp checks; schema validation is an enforced floor, not a
replacement for document-specific rules.

## Surface 2: backend acknowledgement routes (`app/backend/ui_routes.py`)

The retained widget, schedule, event-widget, MCP, kanban, and task routes are
finite command/query routes. Each browser request carries `X-Request-ID`,
persists its document change or builds its snapshot, enqueues the causal
canonical `/api/ui/stream` event, and then returns only an HTTP `202 Accepted`
acknowledgement with exactly `{request_id, event_types}`. No state-document payload is a successful HTTP response
body.

The document writers still use the same narrow state operation shape: read the
whole document, locate an entry, mutate it, write the whole document, and
invalidate the `StateReader` cache. The shared helpers keep that mechanical
work local without hiding domain validation:

```python
def _patch_document_entry(*, entries, entry_id, apply, save, not_found_detail="Widget not found") -> dict:
    entry = entries.get(entry_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=not_found_detail)
    apply(entry)
    save()
    return entry

def _delete_document_entry(*, entries, entry_id, save) -> bool:
    if not isinstance(entries.get(entry_id), dict):
        return False
    del entries[entry_id]
    save()
    return True
```

Schedule, event-widget, and MCP entries retain their own validation and save
closures because their document shapes differ. Kanban is deliberately separate:
it is one document per card and maintains card order and task activity; it is
not a candidate for dict-entry helper reuse.

### Publish after persistence

After a document write succeeds, the backend enqueues the complete event data
required for Redux hydration and includes the request ID in `payload.request_id`.
Typical event types are `widgets.updated`, `mcp.snapshot`, `schedule.snapshot`,
`kanban.snapshot`, `task.content.updated`, and `task.activity.updated`.
The acknowledgement is returned only after those causal event(s) have been
enqueued. Deletes follow this same `202` acknowledgement pattern; none return
`204 No Content`.

## Frontend state ownership

**Redux is the sole durable frontend application-data owner.** Events from
`/api/ui/stream` are the only hydration path for application state:

1. `realtimeListener.js` owns the single `EventSource` to `/api/ui/stream`.
2. `realtimeSlice.js` deduplicates incoming envelope `id` values and records
   pending finite operations by `request_id`.
3. Domain reducers in `uiDataSlice.js` apply typed events idempotently.
4. Components render memoized selectors.

An acknowledgement records acceptance only. A matching event type and
`payload.request_id` resolve the originating pending operation. Events from
other tabs/processes still update Redux but must not resolve this tab's
unrelated operation. There are no legacy response-body hydration paths, direct
state reads, `/api/ui/events` streams, or `/api/ui/status` streams.

## Adding a new state document: checklist

1. Shape: default to dict-keyed-by-id unless there's a concrete reason not to
   (see `agents.json` above for what a legitimate exception looks like).
2. Add a JSON Schema at `data/schemas/<name>_entry.schema.v1.json` and wire it
   into a `validate_<name>_document` function via `_check_entry_schema`.
3. Agent surface: if it's a simple get/save pass-through, use
   `_simple_state_file()` and add the factory to `backends.py`'s
   `state_file_factories` list. If it has unique semantics (like schedule),
   write a dedicated factory instead of stretching the shared helper.
4. Backend surface: if it needs widget CRUD, add a `widget_id` prefix branch
   in `patch_widget`/`delete_widget` using `_patch_document_entry` /
   `_delete_document_entry` / `_widget_or_fallback`, keeping the field-mutation
   logic as a local closure.
5. No migration code for the initial shape or any later shape change — see
   "No migrations, no compat shims" above.
