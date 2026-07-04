---
name: checking-status
description: Report Progenitor's own current runtime status (running/idle, uptime, cycle count, git commit/version, LLM model and reasoning config) by reading the /state/health.json file. Use when a user asks "are you running", "what's your status", "what version are you on", or "what model are you using".
allowed-tools: read_file
---

# Checking Status

Progenitor's own system status lives in a single read-only virtual file:
`/state/health.json`. It is backed by the runtime `health` state document (the
heartbeat loop writes it every cycle; Postgres is the source of truth) - there is
no separate "get status" tool, and the file cannot be edited (writes are rejected).

## How to Answer

1. `read_file /state/health.json`
2. Summarize the relevant fields in plain language - don't dump the raw JSON at
   the user unless they ask for it.

## Field Reference

| Field | Meaning |
|---|---|
| `status` | `running` under normal operation; anything else signals a problem. |
| `last_active` | ISO8601 timestamp of the most recent heartbeat write. |
| `thread_id` | Conversation thread the loop last processed. |
| `cycle_count` | Total heartbeat cycles since process start. |
| `idle_cycles` | Consecutive idle cycles right now (0 means actively working). |
| `pid` | OS process id of the running runtime. |
| `version` | Short git commit SHA the running process was built/started from. |
| `started_at` | ISO8601 timestamp of when this process started. |
| `llm_model` | Configured default LLM model. |
| `llm_reasoning_enabled` | Whether reasoning mode is on. |
| `llm_reasoning_effort` | Reasoning effort level, when enabled. |
| `queues` | Queue depth/pending-count stats, if present. |
| `error` | Present only when the last cycle hit an error. |

`version`/`started_at` exist specifically to catch a stale process still running
old code after a deploy - if the user reports behavior that doesn't match a
recent change, compare `version` against the expected commit before assuming the
code itself is wrong.

## Response Pattern

One short paragraph, not a table dump:
- Lead with running/not running and how long it's been up (compute uptime from
  `started_at`, not from `last_active`).
- Mention `cycle_count` and whether it's idle or mid-task (`idle_cycles`).
- Only surface `version`/model/reasoning details if asked, or if they're
  relevant to diagnosing a reported problem.
- If `error` is present, lead with that instead - it means the last cycle
  didn't complete cleanly.

## Troubleshooting

- If `read_file /state/health.json` returns `{}`, the heartbeat loop hasn't
  written a status yet (e.g. moments after a fresh deploy) - say so rather than
  reporting "unknown" as if it were an error.
- For deeper failure diagnosis (stalls, loops, log evidence) beyond a status
  summary, use the `runtime-inspection` skill instead - this skill only covers
  reporting current status.
