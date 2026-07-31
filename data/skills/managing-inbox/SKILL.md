---
name: managing-inbox
description: Send the user rich markdown messages by writing files into the /inbox/ directory (one file per message). Use when you want to deliver a report, digest, summary, or any message the user should read later in their Inbox rather than in chat.
allowed-tools: read_file write_file edit_file ls generate_id current_time
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

Full message body in **markdown** — headings, lists, links, and code blocks
all render in the Inbox UI.
```

- The filename is authoritative for the message id.
- `title` is required (the write is rejected without one) and is what the user
  sees in their message list — make it specific.
- `created_at` is stamped automatically on write; don't set it yourself.
- `read` / `read_at` are **user-owned** — they flip when the user opens the
  message in the UI. Leave them alone: never set `read: true` yourself, and
  preserve them verbatim if you edit an existing message.

## Common operations

### Send a message
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
3. Never touch `read`/`read_at`.
