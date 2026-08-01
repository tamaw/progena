---
name: prompt-maintenance
description: Safely update prompt assets through /prompts with minimal, auditable edits while preserving tone and operating constraints
allowed-tools: read_file shell
---

# Prompt Maintenance Skill

## When to Use
- User asks to adjust system prompt behavior
- User asks to add or refine skills under prompts/skills
- Prompt drift or inconsistency needs correction

## Mount Usage
- `/prompts/` is read-only in runtime.
- Propose minimal patch-ready edits and delegate file mutation to Builder Agent/git workflow.
- Avoid rewriting large prompt sections unless explicitly requested.

## Editing Rules
1. Preserve existing voice and constraints.
2. Add concrete, testable instructions.
3. Prefer additive edits over broad rewrites.
4. Keep safety boundaries explicit (what is allowed vs delegated).

## Validation Checklist
- Frontmatter is valid where required.
- Skill name matches directory.
- Changes are concise and reversible.
- Updated prompt text matches actual runtime capabilities.
