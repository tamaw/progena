---
name: self-improvement
description: Reflect on completed conversations, delegated work, routines, or incidents; record durable lessons in persistent memory without modifying source skills.
allowed-tools: ls read_file write_file edit_file grep glob
---

# Self-Improvement

## When to Use
- After a completed conversation, delegated task, routine, or incident
- The user asks for a retrospective or reusable lessons
- Repeated successes or failures suggest a durable operating pattern

## Reflection Framework

Keep the review tied to evidence:

1. **Outcome** — compare the intended result with what actually happened.
2. **Evidence** — identify the relevant conversation, runtime status, logs, or work artifact.
3. **What worked** — capture repeatable decisions or conditions.
4. **What failed** — identify root causes and anti-patterns rather than suppressing symptoms.
5. **Adjustment** — state one concrete behavior for a similar future situation.

Do not treat a single event as a universal rule. Record confidence from `0.0` to `1.0` and raise it only with independent supporting examples.

## Store Lessons in Memory

`/memories/` is the writable persistent-memory domain. Organize concise Markdown notes by category, for example:

- `/memories/patterns/code-quality.md`
- `/memories/patterns/testing.md`
- `/memories/patterns/architecture.md`
- `/memories/patterns/performance.md`
- `/memories/patterns/user-interaction.md`
- `/memories/anti-patterns.md`

Before adding a lesson:

1. Read the relevant memory file.
2. Merge equivalent observations instead of duplicating them.
3. Record the pattern, evidence or conditions, confidence, and next action as short bullets.
4. Keep negative guidance in `anti-patterns.md` rather than mixing it with positive patterns.

When a file becomes repetitive, compact it: retain durable high-confidence lessons and useful recent evidence, while removing duplicates and stale low-confidence notes.

## Writable Documents and Source Assets

- `/memories/` is writable for durable lessons.
- `/instructions/` contains validated, Postgres-backed instruction documents and can be changed only when an instruction change is deliberately requested; runtime instruction placeholders must remain valid.
- `/skills/` is read-only. Native skills are static source assets from `data/skills/`; marketplace-plugin skills are also exposed read-only.
- `/self/`, `/repos/`, `/logs/`, and `/schemas/` are read-only mounts.

A lesson does **not** promote itself to a skill. For a high-confidence capability change, propose the concrete skill or instruction change with its evidence. A maintainer must change the source skill and deliver that change; never attempt to write it through `/skills/`. Existing stored instructions are user-owned, so a changed seed does not overwrite them on restart.

## Source Analysis

When a retrospective needs code context, summarize the component's purpose, key collaborators, observed behavior, and the narrow lesson supported by the evidence. Do not make source changes as part of reflection unless the user separately asks for them.
