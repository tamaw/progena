---
name: managing-knowledge
description: Create, rename, describe, tag, or configure knowledge bases, and add or remove folder sources, by editing the /state/knowledge.json file. Use when a user asks to add, rename, describe, tag, configure, or remove a knowledge base or one of its sources. To recall stored facts, use knowledge_search instead.
allowed-tools: read_file edit_file write_file structured_output knowledge_search
---

# Managing Knowledge Bases

Knowledge-base **settings** live in a single virtual file: `/state/knowledge.json`.
It is backed by the runtime state document (Postgres is the source of truth), so
editing it is the supported way to manage knowledge bases — there is no separate
"add knowledge base" tool. Every successful write is validated and the Knowledge
page updates live.

The documents themselves and their vector embeddings do **not** live in this file —
they live in the `knowledge_documents` SQL table. Use `knowledge_search` to recall
stored content. Folder **indexing** (reading a folder's files and embedding them)
runs from the Knowledge page UI; editing a source's `path` here only changes the
setting.

## When to Use
- User asks to create / add a new knowledge base
- User asks to rename a base, edit its description, or change its tags
- User asks to change a base's embedding model, dimensions, metric, or index type
- User asks to register a folder source path on a base, or remove one
- User asks to delete a knowledge base

For "what do you know about X" / "recall …", use `knowledge_search`, not this file.

## Always Read Before You Edit
`/state/knowledge.json` is serialized with sorted keys and 2-space indentation. Read
it first so your `edit_file` `old_string` matches exactly:

1. `read_file /state/knowledge.json`
2. Make the change with `edit_file` (preferred — preserves the rest of the file) or
   `write_file` (only when rewriting the whole document).

## File Schema
```json
{
  "bases": [
    {
      "id": "research-notes-a1b2c3",
      "name": "Research Notes",
      "description": "What this base is for.",
      "owner": "user",
      "scope": "Mine",
      "status": "connected",
      "embedding_model": "progenitor-hash-256",
      "dimensions": 256,
      "metric": "cosine",
      "index_type": "Flat",
      "tags": ["research", "notes"],
      "connection_string": "",
      "sources": [
        { "id": "src-0a1b2c3d4e", "path": "/abs/path/to/folder" }
      ]
    }
  ]
}
```

Rules enforced on every write (a violating edit is rejected and nothing changes):
- Top level must be a JSON object with a `bases` array.
- Each base needs a non-empty `name`. `id` may be omitted on a **new** base — it is
  generated from the name; keep the existing `id` when editing a base.
- `metric` must be one of `cosine`, `l2`, `inner_product`.
- `index_type` must be one of `Flat`, `HNSW`, `IVFFlat`.
- `status` must be one of `connected`, `syncing`, `error`, `idle`.
- `dimensions` is 1–4096. `tags` are trimmed and de-duplicated automatically.
- Each source needs a non-empty `path`; omit `id` on a **new** source (it is
  generated). The `status`, `document_count`, `error`, and `last_indexed_at` fields
  are set by indexing — leave them alone.

## Common Operations

### Add a knowledge base
1. `read_file /state/knowledge.json`
2. `read_file /schemas/knowledge_base_entry.schema.v1.json`
3. `structured_output json_schema=<content from step 2> instruction="Build a knowledge base entry named '<name>' with description '<what it's for>' and tags [<tags>]. Omit id."`
4. `edit_file /state/knowledge.json` to append the object from step 3 to `bases`,
   keeping every existing base untouched. Minimal shape (omit `id` to have one
   generated):
```json
{ "name": "Release Notes", "description": "Changelog snippets.", "tags": ["release"] }
```
For a name-only base, hand-authoring it directly is fine — reach for
`structured_output` when several fields (embedding config, tags, sources) are
involved.

### Rename / edit description / tags
Change `name`, `description`, or the `tags` array in place with `edit_file`.

### Change embedding configuration
For one field, `edit_file` it directly. For several at once (`embedding_model`,
`dimensions`, `metric`, `index_type`), `read_file` the schema and run
`structured_output` with that base's current object as `input` and an instruction
describing the new end state, preserving `id` and everything else, then `edit_file`
the result back in.

### Add a folder source
Add an object with a `path` to that base's `sources` array. The path is registered
immediately; ask the user to index it from the Knowledge page (or do so there) to
embed the folder's files.

### Remove a source / base
Delete the source object from `sources`, or the whole base object from `bases`.
Removing a base or source also drops its indexed documents from SQL.

## Confirmation Pattern
After a successful edit, reply with one short sentence confirming what changed
(added/renamed/updated/removed) and the base (and source path, if relevant). Only ask
a follow-up if a required detail (a base `name`, or a source `path`) is missing.

## Troubleshooting
- Edit rejected as invalid: re-read the file and check the schema rules above (most
  often a missing `name`/`path`, or an out-of-range `metric`/`index_type`).
- `edit_file` old_string not found: re-read the file — keys are sorted and indented
  with 2 spaces, so match the on-disk formatting exactly.
- Documents not appearing after adding a source: indexing runs from the Knowledge
  page UI; editing the `path` here does not embed the folder by itself.
