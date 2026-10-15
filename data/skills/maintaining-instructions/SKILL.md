---
name: maintaining-instructions
description: Read and propose changes to agent instructions under /instructions/ (front matter + markdown bodies that Progenitor and the A2A agents send to their models), with minimal, auditable edits that keep their variables intact
allowed-tools: ls read_file grep glob
---

# Maintaining Instructions

## When to Use
- The user asks why Progenitor or an agent behaves a certain way, or to change that behaviour
- An instruction contradicts what the runtime can actually do
- The user asks what an instruction is for or what fills its variables

## Layout
- `/instructions/<group>/<file>.md` is read-only for you. The group is the agent that reads it.
- `progenitor/` instructions are read by this runtime: `genesis.md` is your own system prompt.
  The user edits them on the Instructions page; an edit applies on the next model call.
- Every other group (`builder_agent/`, `builder_progenitor/`, `observe_agent/`) is bundled with
  that A2A agent, which reads its own copy from disk. They change only through the git-tracked
  `data/instructions/` directory (a code change).

Each file starts with front matter:

```yaml
---
title: Short name
description: What the instruction is for.
variables: [tool_context]   # {placeholders} the code fills in; omit when none
---
```

`variables` is fixed by the code that sends the instruction: every listed `{name}` must stay in
the body, and none can be added or removed.

## Proposing a change
1. Read the instruction and quote the part that causes the behaviour.
2. Propose the smallest replacement text; keep the existing voice, constraints and variables.
3. For `progenitor/` instructions, give the user the exact text to paste on the Instructions page.
   For bundled ones, propose the patch to `data/instructions/<group>/<file>.md`.

## Editing Rules
1. Preserve existing voice and constraints.
2. Add concrete, testable instructions.
3. Prefer additive edits over broad rewrites.
4. Keep safety boundaries explicit (what is allowed vs delegated).
5. Match what the runtime can actually do.
