---
name: managing-inbox
description: Send the user rich markdown messages by writing files into the /inbox/ directory (one file per message). Use when you want to deliver a report, digest, summary, or any message the user should read later in their Inbox rather than in chat.
allowed-tools: read_file write_file edit_file ls generate_id current_time structured_output json_to_yaml
---

# Managing the Inbox

The user's Inbox is a virtual directory: `/inbox/` — one markdown file per
message, named `<id>.md`. It is backed by the runtime state store (Postgres is
the source of truth), so writing a file there is the supported way to send an
inbox message — there is no separate "send message" tool. Every successful
write appears in the user's Inbox page live, with an unread badge.

Use the inbox for content the user should read on their own time: reports,
digests, research results, long-form summaries. For quick conversational
replies, just answer in chat as usual. If a message is genuinely urgent, you
may additionally raise an alert (`notify_user`) pointing the user at it — the
alert is only a pointer; the message itself lives in the inbox.

## Message file shape

```markdown
---
title: Weekly repo digest ready
---

Full message body in **markdown** — headings, lists, links, code blocks, and
` ```mermaid ` fenced diagrams all render in the Inbox UI.
```

- The filename is authoritative for the message id.
- `title` is required (the write is rejected without one) and is what the user
  sees in their message list — make it specific.
- `created_at` is stamped automatically on write; don't set it yourself.
- `thread_id` is likewise stamped automatically from the conversation you're
  currently in — never set it yourself. It's what lets an attachment from this
  thread be referenced from the message body and still resolve later (the
  attachments API scopes downloads by thread).
- `read` / `read_at` are **user-owned** — they flip when the user opens the
  message in the UI. Leave them alone: never set `read: true` yourself, and
  preserve them verbatim if you edit an existing message.
- `requires_approval` (optional, default `false`) marks a message as a decision
  request instead of a plain report — set it `true` when you want the user to
  explicitly approve or reject something (e.g. a plan) rather than just read
  it. `task_id` (optional) links the message to the `kanban_card:<id>` it
  concerns, if any — a task can accumulate several approval messages over its
  lifecycle, so send a new message per round rather than reusing one.
- `decision` is **user-owned** — it's how the user's approve/reject choice
  comes back to you. Never set it yourself; absent/null means still pending.
  Check it on a message you sent with `requires_approval: true` to see whether
  (and how) the user decided.

`/schemas/inbox_message.schema.v1.json` documents the full message shape
(useful as a reference; not required reading to send one).

## Structured sections and questions

When a message needs individually selectable parts, use Markdown headings
`##`–`######`. Each heading starts a selectable section that continues until
the next same-or-higher heading, a `<questions>` block, or the end of the
document. The `#` title heading is not selectable.
No section starts selected. Hover a section to reveal its icon actions; each
icon expands to its full label on hover or keyboard focus. AI Edit opens a
small prompt below its icon: Send instructs the agent through quick chat;
Pop out moves the unsent draft into quick chat. Clicking away or pressing Escape
dismisses it. Click a heading or rendered Markdown to select a section.
Remove asks the agent to delete the section; the UI does not edit message
content directly. Literal examples belong in Markdown code spans or fenced
blocks.

Most of a message can stay ordinary Markdown. Add sub-headings only around
content that users may want to ask the agent to change independently. For
example:

```md
# Release brief

The candidate is ready for review. See the [test report](https://example.com/report).

- API checks passed
- Browser checks are in progress

## Decision

Ship after browser checks finish. **Do not** bypass the release checklist.

## Next steps

1. Review the report.
2. Confirm the release window.
```

Nested headings keep the larger section selectable while allowing a precise
instruction for one subsection:

```md
# Deployment review

## Implementation

The rollout has two independent stages.

### Database

1. Apply the migration.
2. Verify old readers still work.

### API

Deploy the new reader behind the feature flag.

#### Validation

| Check | Expected |
| --- | --- |
| Read consistency | No missing records |
| Error rate | Below baseline |

## Follow-up

Review the metrics tomorrow.
```

A question can also appear among normal Markdown paragraphs without a
surrounding heading:

```md
# Migration note

The current data remains available while we switch the reader.

<questions id="migration-window">
When should we switch traffic?
<options>
<option>During the next maintenance window</option>
<option>After a week of shadow reads</option>
</options>
</questions>

## Rollback

Restore the previous reader if validation fails.
```

For a choice, put one question and its options in each `<questions>` block:

```md
## Implementation

Choose an approach:

<questions id="implementation">
Which approach should we use?
<options>
<option>Use the existing API</option>
<option>Add a dedicated endpoint</option>
</options>
</questions>
```

The listed options appear directly below the question without a show/hide
step. The UI adds an "Enter your own" choice that opens a free-text answer
field; do not include that choice in the message's `<options>` data.

Use a stable `id` per question so the selected block is unambiguous. Multiple
`<questions>` blocks can appear in a section. When the user chooses an option
or enters their own answer, the quick-chat request identifies the question and
asks you to read its latest version, then rewrite that `<questions>` section in
your own words to include both the question and answer. Keep its wrapper, `id`,
and `<options>` list so the user can revise their choice. Do not insert an
`<answer>` tag or append the selected text verbatim. Preserve all unrelated
sections and user-owned frontmatter.

## Common operations

### Send a message (recommended — schema-validated)
Hand-written YAML is where the required `title` tends to slip or the body ends
up outside the frontmatter delimiters. Building the message through the schema
instead makes both mistakes impossible rather than just documented:
1. `generate_id prefix=msg` → `<id>`
2. `structured_output schema_file=inbox_message.schema.v1.json instruction="<what
   the message should say, from the user's request>"` — the schema requires
   `title` and `body`, so the call fails validation (and you retry) rather than
   silently omitting one.
3. Drop `id` from the returned object — it was only required to satisfy the
   schema; the filename is what's actually authoritative.
4. `json_to_yaml data=<object from step 3> body_field=body` — renders valid
   YAML frontmatter (`title`) plus the body as the trailing markdown.
5. `write_file /inbox/<id>.md` with the returned `content`.

### Send a message (manual)
For a trivial one-liner, hand-writing the frontmatter directly is fine:
1. `generate_id prefix=msg` → `<id>`
2. `write_file /inbox/<id>.md` with a `title` frontmatter line and a markdown
   body.

### Review what's in the inbox
`ls /inbox/` (newest first), then `read_file /inbox/<id>.md`.

### Update a message you already sent
`edit_file /inbox/<id>.md` — change only the body/title text; keep the
frontmatter fields you didn't author (`read`, `read_at`, `created_at`) exactly
as they are.

### Superseding an old message
There is no file-delete tool; the user deletes messages from the UI. If a
message is superseded (e.g. a newer digest replaces last week's), prefer
`edit_file` to update the existing message in place over sending a duplicate.

## Golden rules
1. One message per file; never pack multiple updates into one message — send
   separate messages with specific titles instead.
2. Don't spam: batch routine findings into a single digest rather than many
   small messages.
3. Never touch `read`/`read_at`/`decision` — all three are user-owned.
