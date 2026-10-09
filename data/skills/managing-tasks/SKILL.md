---
name: managing-tasks
description: Create, edit, move, or delete kanban task cards by writing one markdown file per card under /tasks/. Use when a user asks to add a task, create a backlog item, move a card to a different column, change a task's status/title/labels, or remove a task from the board.
allowed-tools: read_file write_file edit_file delete_file ls generate_id structured_output json_to_yaml
---

# Managing Kanban Tasks

The kanban board lives as one virtual file per card, plus a read-only structure
document:

- `/tasks/board.json` — columns + card order, assembled automatically from
  the cards below. Do not write this directly.
- `/tasks/<id>.md` — one task card, YAML frontmatter + markdown body.
  `/tasks/<project-id>/<id>.md` scopes a card to a project instead.

Writing a card file persists it immediately (Postgres is the source of truth) and
the Tasks page updates live — no separate "create task" tool, no index to maintain.

## When to Use
- User asks to add / create a task or backlog item
- User asks to move a card to a different column, or change its status
- User asks to edit a task's title, description, owner, or labels
- User asks to delete / remove a task

## File Schema
Use these exact YAML keys — not synonyms (`column_id`, never `column`; `status`,
never `state`; `labels`, never `tags`). Writing an unrecognized key silently
creates a stray field instead of setting the real one, and the card falls back
to defaults for whatever you missed.

```
---
title: Short task title          # required — the only hard requirement
summary: One-sentence synopsis of what this task is and why it exists.  # ALWAYS write a real one
column_id: backlog               # exact key is column_id -- backlog | plan | build | validate | done (default: backlog)
status: draft                    # free-form; draft is the default for a new card
owner: user                      # who owns the card (default: user)
agent_name: user                 # agent responsible for the column's stage
labels:
  - tag-one
  - tag-two
---
Everything after the closing `---` is the full task description — as much detail
as the request warrants.
```

Required for every write, no exceptions, regardless of how sparse the request is:
- `title` — non-empty, the only field the backend actually enforces.
- `summary` — one real sentence (what the task is and why it exists), never an
  empty string and never omitted. This is true even for a two-word request like
  "add a task for X" — write the sentence yourself from context.

Do not include `id` in the frontmatter — the filename is authoritative for a card's
id (e.g. `card_<short-hex>.md`), same convention as inbox messages. Beyond
title/summary, everything else is optional and defaults sensibly.
`/schemas/task.schema.v1.json` documents the full assembled card shape (useful as
a reference; not required reading to create one).

Every field except `description` lives in the frontmatter, not just `title`/`summary`.
The frontmatter is what `ls`/a quick `read_file` of just the header shows; the
point of `summary` is that the task is inferable from it alone, without needing
the full body. Save the body's markdown description for detail that genuinely
doesn't fit in one sentence — background, acceptance criteria, links.

Columns and their typical responsible agent: `backlog` (user), `plan`
(planner_agent), `build` (builder_progenitor), `validate` (validate_agent), `done`
(release_agent). A card can be written straight into any column, not just backlog.

## Common Operations

### Create a task (recommended — schema-validated)
Hand-written YAML is where field names and the `summary` requirement above tend
to slip. Building the card through the schema instead makes both mistakes
impossible rather than just documented:
1. `generate_id prefix=card`
2. `structured_output schema_file=task.schema.v1.json instruction="<what the
   card should contain, from the user's request>"` — the schema requires
   `title`, `summary`, `column_id`, and `status`, so the call fails validation
   (and you retry) rather than silently omitting one.
3. Drop `id` from the returned object — it was only required to satisfy the
   schema; the filename is what's actually authoritative.
4. `json_to_yaml data=<object from step 3> body_field=description` — renders
   valid YAML frontmatter + the description as the trailing body.
5. `write_file /tasks/<id>.md` with the returned `content`.

### Create a task (manual)
For a trivial one-liner, hand-writing the frontmatter directly (matching the
File Schema above exactly) is fine:
1. `generate_id prefix=card`
2. `write_file /tasks/<id>.md` with frontmatter (including a real `summary`)
   plus the description body.

### Move a card / change status
1. `read_file /tasks/<id>.md`
2. `edit_file` to change `column_id` and/or `status` in place.

### Edit title, labels, owner, or description
1. `read_file /tasks/<id>.md`
2. `edit_file` the relevant frontmatter key or the body text.

### Delete a task
`delete_file /tasks/<id>.md` removes the card from the board.

## After the Change
Writing, editing, or deleting a card file triggers the Tasks page's live update
automatically — no follow-up action needed.

## Confirmation Pattern
After a successful change, reply with one short sentence confirming the task title
and column (or what changed, for an edit/move/delete). Only ask a follow-up
question if the task title itself is unclear; otherwise make the change.

## Troubleshooting
- Card not appearing on the board: confirm the path is under `/tasks/`, not
  `/state/tasks/` or anywhere else — only `/tasks/` is backed by the kanban
  store and fires the live update.
- `edit_file` old_string not found: re-read the file first — hand-authored YAML
  frontmatter can vary in spacing/quoting, so match the on-disk content exactly.
