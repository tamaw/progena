---
name: curating-knowledge
description: "Read, write, search, and maintain knowledge-base pages under /knowledge/<base-id>/ (OKF markdown wiki): ingest sources such as /conversations/ chats into pages, answer questions by searching and reading pages, file good answers back as pages, and lint for contradictions, stale claims, orphans, and missing links."
allowed-tools: ls read_file write_file edit_file grep glob search
---

# Curating knowledge

Read `/knowledge/index.md`, then the base's `index.md` before curating pages. Each base has generated `index.md` files, markdown concept pages, and root `log.md`. An `index.md` lists only the pages in its own directory; subfolders appear under `## Directories`, so follow those links (or `glob **/*.md` in the base) to see every page. A base's `index.md` starts with its search-index status (`Search index: indexed …`, `indexing 3/7`, or `failed: …`).

Concept pages use OKF v0.2 YAML front matter. `type` is required; use `title`, `description`, `tags`, and `sources` such as `/conversations/<file>.txt` or URLs. List sources only in front matter; don't repeat them in a body `Sources` section, since the UI already shows them under the page. `generated.at` is stamped automatically. Use relative markdown links and lowercase kebab-case `.md` paths. Use `##`/`###` headings for distinct topics: search indexes pages section by section.

`log.md` is append-only. Add entries such as `## [YYYY-MM-DD] ingest | Title`. Agents cannot delete pages; mark obsolete pages with `status: deprecated`.

## Ingest

Read the base index, `search` for existing pages on the topic so you update rather than duplicate, read the source, create or update focused concept pages, then append an ingest entry to `log.md`. New and edited pages become searchable once the indexer catches up; that does not block you.

## Query

`search` the question (optionally `base: <id>`), then `read_file` the hits — and any pages the result lists as `pending` that look relevant, since those aren't indexed yet. Follow links from those pages when they matter. Answer with citations to `/knowledge/<base>/<path>.md`; optionally file a durable answer as a page. If `search` fails (e.g. indexing not configured), fall back to the base index and `grep`.

## Lint

Use `grep` to find broken relative links, missing descriptions, stale or contradictory claims, and orphan pages; use `search` to find pages that cover the same topic. Report findings without deleting pages.
