---
name: managing-knowledge
description: Create, rename, describe, tag, or remove knowledge bases by editing /config/knowledge.json, and configure knowledge search indexing in /config/indexer.json.
allowed-tools: read_file edit_file write_file structured_output
---

# Managing knowledge bases

Knowledge-base catalog settings live in `/config/knowledge.json`. Read it before editing. A base id is a stable lowercase kebab-case slug and becomes `/knowledge/<id>/`; never change it after creation. Omit `id` for a new base and the runtime generates one from its name, adding a numeric suffix on collision.

Create, rename, describe, tag, or remove entries in the `bases` array. Removing an entry hides its pages but keeps their stored content. Put what the base is for in its `description`; it appears in `/knowledge/index.md`.

Page work belongs to the `curating-knowledge` skill. Use `/knowledge/<id>/` for pages, not this catalog file.

## Workflow

1. `read_file /config/knowledge.json`.
2. Preserve existing ids and entries.
3. Make the smallest valid JSON edit.
4. Use the curating-knowledge skill to ingest, query, or lint pages.

The persisted catalog keeps each base's name, description, owner, scope, and tags. Derived `path`, `page_count`, and search-index fields shown by the UI are read-only snapshot fields.

## Search indexing

Pages are embedded for the `search` tool by the backend indexer. Its settings are `/config/indexer.json`:

```json
{"profile": "embeddings", "dimensions": 1536, "min_similarity": 0.25}
```

`profile` must name an entry in `/config/profile.json` whose `provider_id` explicitly selects an existing Foundry connection from **Settings → Providers** and whose `deployment` is an embeddings deployment (e.g. `{"provider_id": "<foundry-connection-id>", "deployment": "text-embedding-3-small"}`). OpenCode Go is not an embeddings provider. Legacy `provider: "azure_openai"` profiles are migrated to a Foundry connection when available. `dimensions` is 1–2000. Changing the profile, deployment, provider, or dimensions rebuilds the index. With no valid profile set, every base reports `failed` and `search` explains what to set.

`min_similarity` (0–1, default 0.25) is the search cut-off: a page is returned only if it matches the query by keyword or its cosine similarity is at least this value. Raise it when searches return loosely related pages; lower it when relevant pages are missed. Changing it takes effect immediately and does not rebuild the index.

Progress is read-only in `/knowledge/indexer.json` (per base: `indexing` / `indexed` / `failed`, page counts, pending pages, error) and summarized at the top of each base's `index.md`.
