---
name: maintaining-instructions
description: Read, create and edit instructions under /instructions/ — the markdown templates Progenitor, the A2A agents and workflow steps send to models — keeping front matter valid and placeholders intact
allowed-tools: ls read_file write_file edit_file grep glob
---

# Maintaining Instructions

## When to Use
- A workflow `extract`/`agent` step or routine `agent` node needs a reusable template
- The user asks why Progenitor or an agent behaves a certain way, or to change that behaviour
- The user asks what an instruction is for or what fills its placeholders
- An agent needs instructions for driving Claude through `acp_*` tools
## Layout
`/instructions/<group>/<file>.md`. The group decides who fills the placeholders and who may edit:

| Group | Read by | Editable | Placeholders |
|---|---|---|---|
| `progenitor/` | this runtime (`SYSTEM.md` is your own system prompt) | yes; applies on the next model call | fixed by code: keep exactly the same `{names}` |
| `builder_agent/`, `builder_progenitor/`, `observe_agent/` | that A2A agent, from its own disk copy | no (writes fail); change `data/instructions/` in git | fixed by that agent's code |
| anything else, e.g. `workflows/` | workflow steps and routine `agent` nodes that reference the path | yes | free; each root becomes an input port or resolves from routine state |

## Format

```markdown
---
title: Card summary
description: Summarises a kanban card for the team.
inputs:
  card: kanban_card          # optional: type a placeholder root (document type id, schema file, or inline schema)
output_schema: kanban_card   # optional: what an extract step returns
system: You are terse.       # optional: prepended as its own section
---
Summarise "{card.title}" in two sentences.
```

All front matter keys are optional and no others are allowed. `{name}` and `{name.path}` are placeholders; every
`inputs` key must be used in the body.

An `inputs` type is one of: a document type id (`card: kanban_card`), a schema file name under `/schemas/`
(`entry: schedule_entry`), or an inline JSON schema (`card_id: {type: string}`). A bare JSON type name such as
`string` or `integer` is not a type and is rejected with `unknown type ref`; write it inline as `{type: string}`.
Prefer typing the whole document (`card: kanban_card`, then `{card.title}`) over passing its id.

Writes are validated and rejected with the reason. A rejected write is feedback, not a blocker: read the reason,
fix the file, and write it again. Keep going until it saves; report only once it has, or after the same error
repeats with no fix left to try.

## Editing Rules
1. Read the instruction first; quote the part you change.
2. Make the smallest change; keep the existing voice, constraints and placeholders.
3. New workflow templates go under `/instructions/workflows/` with a `title` and `description`.
4. Match what the runtime can actually do.

## ACP-driving templates

An instruction may direct its runner to use `acp_start`, `acp_send`,
`acp_status`, `acp_answer`, `acp_cancel`, or `acp_list`. The runner's agent
manifest must list those tools and include the `driving-acp-agents` skill.
For code changes, have it request a `repo-manager` worktree and pass that path
as `cwd` to `acp_start`.
