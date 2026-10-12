---
name: curating-knowledge
description: "Read, write, and maintain knowledge-base pages under /knowledge/<base-id>/ (OKF markdown wiki): ingest sources such as /conversations/ chats into pages, answer questions from pages, file good answers back as pages, and lint for contradictions, stale claims, orphans, and missing links."
allowed-tools: ls read_file write_file edit_file grep glob
---

# Curating knowledge

Read `/knowledge/index.md`, then the base's `index.md` before curating pages; its `## Instructions` section is the user's brief for the base, so follow it. Each base has generated `index.md` files, markdown concept pages, and root `log.md`. An `index.md` lists only the pages in its own directory; subfolders appear under `## Directories`, so follow those links (or `glob **/*.md` in the base) to see every page.

Concept pages use OKF v0.2 YAML front matter. `type` is required; use `title`, `description`, `tags`, and `sources` such as `/conversations/<file>.txt` or URLs. `generated.at` is stamped automatically. Use relative markdown links and lowercase kebab-case `.md` paths.

`log.md` is append-only. Add entries such as `## [YYYY-MM-DD] ingest | Title`. Agents cannot delete pages; mark obsolete pages with `status: deprecated`.

## Ingest

Read the base index (with its instructions), read the source, create or update focused concept pages, then append an ingest entry to `log.md`.

## Query

Read the base index, follow its `## Directories` into subfolders, and `grep` the base for the question's key terms so pages in subfolders are not missed. Read the relevant pages and answer with citations to `/knowledge/<base>/<path>.md`; optionally file a durable answer as a page.

## Lint

Use `grep` to find broken relative links, missing descriptions, stale or contradictory claims, and orphan pages. Report findings without deleting pages.
