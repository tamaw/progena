---
name: reading-conversations
description: Browse, list, search, or read the user's past and current chats in the read-only /conversations/ directory (one IRC-style log per chat). Use whenever the user asks which chats or conversations exist or you can see, refers to an earlier or other chat, asks what they or you said or did before, or wants chat history summarised or searched.
allowed-tools: ls read_file grep glob
---

# Reading past conversations

`/conversations/` holds one read-only log per chat, generated from chat
history:

```
/conversations/<date>_<title-slug>_<id8>.txt
```

- `<date>` is the day the chat started (user's timezone), `<title-slug>` is the
  chat's sidebar title, `<id8>` is the first 8 characters of its thread id.
- `ls` lists newest activity first. Archived chats have no file; a renamed chat
  gets a new name, so `ls` again if a path stops resolving.
- The chat you are in right now is included, up to its last completed step.
- Files cannot be written, edited, or deleted.

## Line format

Every line starts with `HH:MM:SS` in the user's timezone. The header gives the
topic (chat title), who set it, and everyone who took part (`@` marks agents).

| Line | Meaning |
|---|---|
| `<Tama> text` | The user said this (their display name, or `user`). |
| `<@progenitor> text` | An agent said this. One line per line of the message. |
| `* progenitor is checking the schedule` | Reasoning: what the agent was doing. |
| `* progenitor reads /config/schedule.json` | A tool call. `can't read … (reason)` means it failed; `(exit 1)` after `runs` means the command failed. |
| `-progenitor- [success] Title: body` | A notification, message, or question sent to the user outside the chat. |
| `-!- repo-manager [subagent] has joined #id8` | A delegation starts. `[subagent]`, `[async]`, or `[a2a]` says how. |
| `-> *repo-manager* request` | The request handed to that agent. Its own thinking and tool calls follow, then `has left`. |
| `<Tama> greetings_agent: hi` | The user typed `@greetings_agent hi`, talking to that agent directly instead of to you. |
| `-!- nobody_agent: No such nick/channel` | A direct agent call failed; the reason follows the colon. |
| `-!- progenitor has quit [cancelled by Tama]` | The user stopped that reply part-way. |
| `--- Day changed Tue Oct 13 2026` | Later lines are on that day. |

Tool results are not in the log. The agent's next message says what came
back.

## Recipes

- Find a chat: `ls /conversations/`, then narrow by date or title in the name.
- Search everything: `grep` with a literal pattern over `/conversations/`, e.g.
  `schedule` or `<Tama>`.
- Search one period first: `grep` with `glob` set to a date prefix such as
  `2026-10-*` reads far fewer logs.
- What the user asked: grep for `<Tama>` (their nick) in that file.
- Failures: grep for `can't` or `has quit`.
- Delegations: grep for `has joined`.
- Read a long chat in pages with `read_file` offset/limit.

Grep searches each chat as it was last read, so very recent turns in another
chat can be missing from grep results; `read_file` on that chat is always
current.
