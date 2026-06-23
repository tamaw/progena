---
name: repo-readonly
description: Read-only repository inspection via /self for architecture understanding, code tracing, and change planning without mutating files
allowed-tools: read_file shell
---

# Repo Readonly Skill

## When to Use
- User asks how the codebase works
- User asks for debugging hypotheses based on source
- User asks for change planning before edits

## Scope
- Use `/self/` for source inspection.
- Treat `/self/` as read-only.
- Do not propose direct mutations in this skill; produce analysis and plans.

## Workflow
1. Identify the likely entrypoint module(s).
2. Trace imports and runtime wiring.
3. Summarize behavior as data flow, not file dumps.
4. Call out uncertainty and exact files that need confirmation.

## Output Pattern
- `What it does`
- `Where it is wired`
- `Risks / unknowns`
- `Proposed next edit targets`
