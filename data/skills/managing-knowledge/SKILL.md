---
name: managing-knowledge
description: Create, rename, describe, tag, or remove knowledge bases by editing /config/knowledge.json.
allowed-tools: read_file edit_file write_file structured_output
---

# Managing knowledge bases

Knowledge-base catalog settings live in `/config/knowledge.json`. Read it before editing. A base id is a stable lowercase kebab-case slug and becomes `/knowledge/<id>/`; never change it after creation. Omit `id` for a new base and the runtime generates one from its name, adding a numeric suffix on collision.

Create, rename, describe, tag, or remove entries in the `bases` array. Removing an entry hides its pages but keeps their stored content. After creating a base, write the user's brief to `/knowledge/<id>/INSTRUCTIONS.md`.

Page work belongs to the `curating-knowledge` skill. Use `/knowledge/<id>/` for pages, not this catalog file.

## Workflow

1. `read_file /config/knowledge.json`.
2. Preserve existing ids and entries.
3. Make the smallest valid JSON edit.
4. If creating a base, write its `INSTRUCTIONS.md` with the user's purpose and curation rules.
5. Use the curating-knowledge skill to ingest, query, or lint pages.

The persisted catalog keeps the base's name, description, owner, scope, status, tags, and legacy vector configuration. Derived `path` and `page_count` fields shown by the UI are read-only snapshot fields.
