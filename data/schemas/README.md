# State document schemas

JSON Schema files in this directory describe the shape of runtime **state
documents** (or one entry within one) stored in the shared Postgres-backed
store (the `("progenitor", "state")` namespace). This directory is also
mounted read-only in the agent's virtual filesystem at `/schemas/`, so a skill
can `ls`/`read` a schema directly instead of only referencing it by name.

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
| `agent_entry.schema.v1.json` | One object in `registry`'s `agents` array | object | `managing-agents` |
| `knowledge_base_entry.schema.v1.json` | One object in `knowledge`'s `bases` array | object | `managing-knowledge` |
| `task.schema.v1.json` | A single kanban card (`kanban_card:<id>`) | object | not currently wired to a skill |

**Every schema here describes one entry, never a whole document.** `mcp.json`
(`mcpServers`) and `schedule.json` are JSON objects keyed by name/id, so adding
one is inserting a new key and updating one is replacing a single key's value
— `edit_file` never has to touch anything else. `agents.json`'s `agents` and
`knowledge.json`'s `bases` are arrays instead (each item carries its own
`name`/`id`), so adding is appending and updating is replacing one array
element — still never the whole file. This matters most for `agents.json`,
whose `tools` array is runtime-managed and must survive byte-for-byte; asking
`structured_output` to regenerate the *whole* document would mean the model
retyping data nobody asked it to touch. Keeping every schema entry-scoped
avoids that everywhere, not just there.

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
