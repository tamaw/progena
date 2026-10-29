---
name: reading-runs
description: Browse, search, or read what routines, workflows and schedules did in the /runs/ directory (one YAML-front-matter + IRC-style log per run). Use whenever the user asks what an automation did, why a run failed, stopped or is waiting, what a step produced, or what ran recently.
allowed-tools: ls read_file grep glob
---

# Reading runs

`/runs/` holds one read-only file per top-level run, rendered from the run's
stored records each time it is read:

```
/runs/routines/<date>_<routine-slug>_<request_id>.md
/runs/workflows/<date>_<workflow-slug>_<run_id>.md
/runs/schedules/<date>_<schedule-slug>_<run_id>.md
```

- `<date>` is the day the run started (user's timezone). The final component
  is the full persisted ID: `request_id` for routines, `workflow_run_…` or
  `schedrun_…` otherwise. Short-ID paths are not supported. `run_workflow`
  returns the path; no database migration is needed for these generated names.
- `ls` lists newest first. A routine started by a workflow step or by another
  routine has no file of its own: it is inside its parent's file.
- The file is YAML front matter (identity, `status`, times, `inputs`, `output`,
  `error`) followed by a blank line and the log. `status` is worked out when
  the file is read: `interrupted` means the run was still executing when the
  runtime that owned it restarted. `tokens` contains recorded `input_tokens`,
  `output_tokens`, and `total_tokens`; missing usage or components are `null`,
  not zero. Nested usage is counted once. Child routines follow persisted
  invocation/audit links, never timestamp proximity; missing links are not guessed.

## Line format

The topic says what started the run: the document change (only the fields that
changed), the cron time, or the inputs it was started with. Each line starts
with `HH:MM:SS` in the user's timezone, or `--:--:--` when unknown.

| Line | Meaning |
|---|---|
| `* review reads Read card → /tasks/card_1.md "Title"` | A step done by the run itself (no `@`): verb, step title, what it produced. |
| `* review branches at Ready? → false` | Which branch a condition took. |
| `<@slow-work> text` | A model or agent reply, in full. |
| `-!- build-manager [agent] has joined #chan` … `has left` | An agent step; its own conversation is between the two lines. |
| `-!- ci-gate [routine] has joined #chan` … `has left` | A child routine run, inlined; `<ci-gate> …` is its result, `(the reply above)` when it is the last reply. |
| `-review- Merge PR #450? [approve / reject]` | The run is asking the user; `<user> approve` is the answer once given. |
| `-!- review has quit [failed at Step: error]` | Failed, `[cancelled]`, `[stopped]`, or `[runtime restarted after Step; interrupted]`. |

## Long values

Data is summarised so a run reads like a chat: changed fields, a document's
mount path and title, `N items`, or both ends of a long text:

```
→ "I've written the regression tests…" ⟨48 KB · /runs/workflows/x.md#2⟩ "…All 6 tests pass."
```

The pointer says where the whole value is. A mount path (`/tasks/…`,
`/inbox/…`, `/alerts/…`) or `acp_task <id8>` names the stored document;
`<file>#<n>` returns step `n`'s full value from the run itself (read it with
`read_file`). `#n` paths are never listed, globbed or searched.

## Recipes

- What ran recently: `ls /runs/workflows/` (or routines, schedules).
- Why it failed: grep `has quit` over `/runs/`, then read that file.
- Narrow first: grep with `glob` set to a date prefix such as `2026-10-2*`.
- A step's exact output: follow its `⟨… · <file>#<n>⟩` pointer.
- A workflow and the routine it ran: one file; look between `has joined` and
  `has left`.
