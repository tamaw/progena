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
{"profile": "embeddings", "dimensions": 1536}
```

`profile` must name an entry in `/config/profile.json` whose `deployment` is an Azure OpenAI embeddings model (e.g. `{"provider": "azure_openai", "deployment": "text-embedding-3-small"}`). `dimensions` is 1–2000. Changing either rebuilds the whole index. With no profile set, every base reports `failed` and `search` explains what to set.

Progress is read-only in `/knowledge/indexer.json` (per base: `indexing` / `indexed` / `failed`, page counts, pending pages, error) and summarized at the top of each base's `index.md`.
