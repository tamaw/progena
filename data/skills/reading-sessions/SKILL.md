---
name: reading-sessions
description: Browse, list, search, or read the user's past and current chats in the /sessions/ directory (one YAML-front-matter + IRC-style markdown file per chat). Use whenever the user asks which chats or conversations exist or you can see, refers to an earlier or other chat, asks what they or you said or did before, or wants chat history summarised or searched.
allowed-tools: ls read_file grep glob
---

# Reading past sessions

`/sessions/` holds one file per chat, backed by the `session:` documents in
Postgres:

```
/sessions/<date>_<title-slug>_<id8>.md
```

- `<date>` is the day the chat started (user's timezone), `<title-slug>` is the
  chat's sidebar title, `<id8>` is the first 8 characters of its thread id.
- `ls` lists newest activity first. Archived chats have no file.
- The file text is YAML front matter (all fields except `content`, plus a
  computed `display_title`) followed by a blank line and the IRC-style
  `content`.
- Only the `title`, `pinned`, and `archived` front-matter fields are editable;
  the body is read-only.

## Line format

After a short header (topic = chat title, who set it, and everyone who took
part; `@` marks agents), each line starts with `HH:MM:SS` in the user's
timezone, or `--:--:--` when the time is unknown.

`Alex` and `Progena` below are example names: the user's display name and your
configured agent name (spaces removed). Use the actual nicks from the session
header when searching; both can change between sessions.

| Line | Meaning |
|---|---|
| `<Alex> text` | The user said this (their display name, or `user`). |
| `<@Progena> text` | An agent said this. One line per line of the message. |
| `* Progena sends an image` | An image content block (human or AI). |
| `* Progena is checking the schedule` | Reasoning: what the agent was doing. |
| `* Progena reads /config/schedule.json` | A tool call. `can't read … (reason)` means it failed; `(exit 1)` after `runs` means the command failed. |
| `-Progena- [success] Title: body` | A notification, message, or question sent to the user outside the chat. |
| `-!- repo-manager [subagent] has joined #id8` | A delegation starts. `[subagent]`, `[async]`, or `[a2a]` says how. |
| `-> *repo-manager* request` | The request handed to that agent. For `[subagent]` its own thinking and tool calls follow, then `has left`. `[async]` and `[a2a]` agents only reply later as `<@agent>` lines. |
| `-!- repo-manager [subagent] has left #id8 [reason]` | The delegation ended; a `[reason]` at the end means it failed. |
| `<Alex> research-agent: hi` | The user typed `@research-agent hi`, talking to a registered agent directly instead of to you. |
| `-!- nobody_agent: No such nick/channel` | A direct agent call failed; the reason follows the colon. |
| `-!- Progena has quit [cancelled by Alex]` | The user stopped that reply part-way. |
| `--- Day changed Tue Oct 13 2026` | Later lines are on that day. |

Tool results are not in the log. The agent's next message says what came
back.

## Recipes

- Find a chat: `ls /sessions/`, then narrow by date or title in the name.
- Search everything: `grep` with a literal pattern over `/sessions/`, e.g.
  `schedule` or the user's nick from a session header.
- Search one period first: `grep` with `glob` set to a date prefix such as
  `2026-10-*` reads far fewer logs.
- What the user asked: grep for their `<nick>` in that file.
- Failures: grep for `can't` or `has quit`; for delegations grep `has left` and
  look for a trailing `[reason]`.
- Delegations: grep for `has joined`.
- Read a long chat in pages with `read_file` offset/limit.

Grep searches each chat as it was last rendered, so very recent turns in another
chat can be missing from grep results; `read_file` on that chat is always
current.
