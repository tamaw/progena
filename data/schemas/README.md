# Runtime document and agent schemas

JSON Schema files in this directory describe runtime documents, entries within
configuration documents, and native agent manifest front matter. Runtime
documents are stored in the shared Postgres-backed store; bundled native agent
manifests remain read-only source files.
All schemas are mounted read-only at `/schemas/` for agents and skills.

They are consumed by the `structured_output` tool, which runs a
structured-output LLM call constrained by the schema and validates the result
(via `jsonschema`). The tool does not persist anything itself — a skill reads
or names a schema, calls `structured_output` to produce a validated JSON
value, then writes that value into the document's mounted virtual file with
the native `write`/`edit` file tools.

## Shipped schemas

| Schema | Scope | Root | Used by |
| --- | --- | --- | --- |
| `schedule_entry.schema.v1.json` | One entry's value in `schedule` (keyed by id) | object | `managing-schedules` |
| `mcp_server_entry.schema.v1.json` | One server under `mcpServers` (keyed by name) | object | `managing-mcp` |
| `mcp_tool_entry.schema.v1.json` | One object in `mcp_tools`'s `tools` array (only `enabled` is editable) | object | `managing-mcp` |
| `plugin_entry.schema.v1.json` | One plugin under `plugins` (keyed by `<marketplace>/<plugin>`) | object | `managing-plugins` |
| `marketplace_entry.schema.v1.json` | One marketplace under `marketplaces` (keyed by marketplace name) | object | `managing-marketplaces` |
| `agent_entry.schema.v1.json` | One object in `registry`'s `agents` array | object | registry validation, `managing-a2a` |
| `knowledge_base_entry.schema.v1.json` | One object in `knowledge`'s `bases` array | object | `managing-knowledge` |
| `task.schema.v1.json` | A single kanban card (`kanban_card:<id>`) | object | `managing-tasks` |
| `inbox_message.schema.v1.json` | A single inbox message (`inbox_message:<id>`) | object | `managing-inbox` |
| `preferences.schema.v1.json` | The whole `preferences` document | object | `managing-preferences` |
| `definition.workflow.schema.v1.json` | A whole `workflow:<id>` document | object | `managing-workflows` |
| `workflow_run.schema.v1.json` | A whole `workflow_run:<id>` document | object | `workflow_run` document type |
| `content_document.schema.v1.json` | A whole text document (`agent:`/`skill:`/`memory:`/`instruction:`/`kanban_task_content:`/`module:`) | object | content document types |
| `module_manifest.schema.v1.json` | One module's required `manifest.json` | object | `building-modules` |
| `acp_task.schema.v1.json` | A whole `acp_task:<id>` document | object | ACP supervisor, workflows |
| `alert.schema.v1.json` | A whole `alert:<id>` document | object | alert validation, `raising-alerts` |
| `agent-manifest.schema.v1.json` | Native object-agent YAML front matter | object | agent manifest validation, `managing-agents` |
| `graph.routine.schema.v1.json` | A routine graph definition | object | routine validation, `managing-routines` |
| `indexer.schema.v1.json` | The whole `indexer` configuration document | object | indexer validation, `managing-knowledge` |
| `knowledge_index.schema.v1.json` | A whole `knowledge_index:<id>` status document | object | indexer, workflow document ports |
| `policy.schema.v1.json` | A policy document | object | policy validation, `managing-policies` |
| `repo_entry.schema.v1.json` | A repository declaration | object | repository validation, `managing-repos` |
| `session.schema.v1.json` | A whole `session:<id>` document | object | sessions, workflow document ports |

Whole-document schemas also back **document types** (`document_type:<id>` rows
in Postgres, see `app/progenitor/document_types.py`): a type's `schema` names
the file here that types its documents. Workflow event/read/query nodes expose
each document as one port typed with the document type's label (e.g. `after:
Kanban Card`); an Unpack node exposes its top-level properties as ports.

Entry schemas scope changes to one item in a collection: `mcp.json` and
`schedule.json` are keyed maps, while `agents.json` and `knowledge.json`
contain arrays. Preserve unrelated entries and runtime-managed fields rather
than asking the model to regenerate the whole collection.

Whole-document schemas cover standalone records such as workflows, routines,
sessions, alerts and preferences. Native agent manifests are a separate case:
their schema validates the YAML front matter of `SYSTEM.md`, not a Postgres
state document.

## Naming convention

Schema files are named:

```text
<document>.schema.v<n>.json
```

e.g. `schedule_entry.schema.v1.json`. Versioning lets a document's contract
evolve without breaking older references. The `structured_output` tool's
`schema_file` argument accepts either:

- a **bare logical name** (`schedule_entry`) — resolves to the highest available version, or
- an **exact filename** (`schedule_entry.schema.v1.json`) — pins a specific version.

## How it's used

A skill calls `structured_output` with:

- `schema_file` — a logical name or filename in this directory (e.g. `schedule_entry`)
- `instruction` — a complete description of the desired end state
- `input` — (optional) the current file content, read first, as context

The tool loads the schema, asks the model to emit a value conforming to it,
validates the result, and returns it to the agent — which then persists it
itself with `write`/`edit` against the mounted virtual file.

## Authoring constraints

- **Array-root documents are auto-wrapped.** The provider requires an object
  root, so a top-level `{"type": "array", ...}` schema is transparently wrapped
  in `{"items": [...]}` for the request and unwrapped before validation.
  Author the schema as the document's true shape (array or object) — the tool
  handles the rest.
- **Non-strict mode.** `structured_output` uses the provider's non-strict mode
  and enforces conformance locally with `jsonschema`, so arbitrary-key maps
  (`additionalProperties`), optional/nullable fields, and unions are all fine.
- **Keep it self-describing.** Use `description` on fields — the schema is sent
  to the model verbatim and good descriptions improve results.
- **Match the reader.** Whatever the agent writes is what the mount's own
  validator (and whatever code reads the document back) sees — make sure this
  schema's shape agrees with the mount's `validate_*_document` function.

Set `PROGENITOR_SCHEMAS_DIR` to override this location.
