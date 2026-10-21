---
name: managing-schedules
description: Create, update, pause/resume, reschedule, or remove scheduled tasks (one-off and recurring) by editing the /config/schedule.json config file. Use when users ask to schedule work, review upcoming jobs, pause/resume, reschedule, or debug task timing.
allowed-tools: read_file write_file edit_file ls generate_id current_time structured_output
---

# Managing Schedules

All scheduled tasks live in a single virtual file: `/config/schedule.json` — a JSON
object keyed by schedule id, one key per task (mirrors `/config/mcp.json`'s
`mcpServers` map). It is backed by the runtime state document (Postgres is the
source of truth), so editing it is the supported way to manage schedules — there is
no separate "add task" or "delete task" tool. Every successful write is validated
and picked up by the scheduler automatically, and the Schedules page updates live.

## Always Read Before You Edit
`/config/schedule.json` is serialized with sorted keys and 2-space indentation. Read
it first so your `edit_file` `old_string` matches exactly:

1. `read_file /config/schedule.json`
2. Make the change with `edit_file` (preferred — touches only the one key you're
   adding/changing/removing, leaving every other task untouched) or `write_file`
   (only when the file is empty, i.e. adding the very first task).

## Entry shape
The id is the object key, not a field inside the value:
```json
{
  "sched_abc123": {
    "message": "Say good morning to the user.",
    "cron_expression": "0 9 * * *",
    "scheduled_at": null,
    "timezone": "Europe/London",
    "paused": false,
    "user_request": "say good morning every day at 9am"
  }
}
```
- `message` is required. Everything else is optional.
- Set **exactly one** of `cron_expression` (recurring) or `scheduled_at` (one-off
  ISO8601) — leave the other `null`.
- `created_at`, `last_run`, `next_run` are runtime-owned: preserve the existing value
  on an edit, never invent one. `next_run` is computed by the scheduler at read time.

Rules enforced on every write (a violating edit is rejected and nothing changes):
top level must be a JSON object; every entry value must be an object with a
`message`; a present `cron_expression` must be valid cron syntax; a present
`scheduled_at` must parse as a timestamp.

## Golden rules
1. **Never regenerate the whole file.** Only touch the one key you're adding,
   changing, or removing with `edit_file` — don't ask `structured_output` to produce
   the entire object, and don't hand-author multiple entries' worth of JSON yourself.
2. **New entries get a generated id.** Call `generate_id prefix=cron` (one-off) or
   `prefix=sched` (recurring) for the object key — don't invent ids yourself. The id
   is never repeated inside the entry's value.
3. **Resolve times first.** Use `current_time` to turn "in 30 minutes" / "tomorrow
   9am" into a concrete `scheduled_at`, or into the right `cron_expression`.
4. **Use `structured_output` for anything beyond a single-field tweak.** `read_file
   /schemas/schedule_entry.schema.v1.json` (`ls /schemas/` if you're unsure of the
   exact filename) and pass its content as `json_schema` — it describes one entry's
   value, so the result is guaranteed to parse without you having to touch any other
   task.

## Common Operations

### Add a task (recurring: "every day at 9am")
1. `generate_id prefix=sched` → `<id>`
2. `current_time` if you need to resolve relative wording into the cron expression.
3. `read_file /schemas/schedule_entry.schema.v1.json`
4. `structured_output json_schema=<content from step 3> instruction="Build a schedule entry with message '<what to do>' and cron_expression '0 9 * * *'."`
5. `edit_file /config/schedule.json` to insert `"<id>": <result from step 4>` as a
   new key (or `write_file` if the file is currently `{}`), keeping every other task
   untouched.

### Add a task (one-off: "tomorrow at 3pm")
Same as above but with `generate_id prefix=cron` for the id, and resolve
`current_time` to an absolute ISO8601 timestamp first and set `scheduled_at`
instead of `cron_expression`.

### Enable / disable (pause / resume)
Flip that entry's `paused` boolean with `edit_file` — a single-field change, no need
for `structured_output`.

### Edit a field (e.g. change several things at once)
For one field, `edit_file` it directly. For several fields on one entry, `read_file`
the schedule and (if not already in context) `/schemas/schedule_entry.schema.v1.json`,
run `structured_output` with that one entry's current value as `input` and an
instruction describing its new end state (preserve `created_at`/`last_run`
verbatim), then `edit_file` that one key's value back in.

### Reschedule from a natural-language time description
`read_file` the schedule, use `current_time` to interpret the phrase in that entry's
`timezone` (default UTC), run `structured_output` (that entry's current value as
`input`) to produce its updated value (new `cron_expression` or `scheduled_at`,
everything else preserved), `edit_file` that one key's value back in.

### Remove a task
Delete its `"<id>": { ... }` key (and its trailing comma, if any) from the object
with `edit_file`.

## Cron quick reference
- `0 9 * * *` — daily 09:00
- `0 9 * * 1-5` — weekdays 09:00
- `0 10 * * 1` — Mondays 10:00
- `0 */2 * * *` — every 2 hours

## Confirmation Pattern
After a successful edit, reply with one short sentence confirming what changed, when
it next runs, and whether it's recurring or one-off. Only ask a follow-up question if
a required detail (the time) is missing; otherwise make the change.

## Troubleshooting
- Write rejected as invalid: re-read the file and check the rules above (most often
  a missing `message`, or an unparseable `cron_expression`/`scheduled_at`).
- Didn't fire: `read_file` and check `cron_expression`/`scheduled_at` and that
  `paused` is not true. `next_run` is recomputed by the scheduler, not stored by you.
- `edit_file` old_string not found: re-read the file — keys are sorted and indented
  with 2 spaces, so match the on-disk formatting exactly.
