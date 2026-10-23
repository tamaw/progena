---
name: driving-acp-agents
description: Drive interactive ACP sessions with Claude through acp_start, acp_send, acp_status, acp_answer, acp_cancel, and acp_list. Use for delegated coding work that needs steering, plan review, or answers to Claude questions.
allowed-tools: acp_start acp_send acp_status acp_answer acp_cancel acp_list task
---

# Driving ACP agents

ACP sessions run Claude over the Agent Client Protocol. Start and control them
with the `acp_*` tools; the `a2a_*` tools are for remote A2A agents only.

## Working safely

1. Ask the `repo-manager` agent to create a worktree for the requested repository
   and branch. Pass that returned worktree path as `cwd` to `acp_start`.
2. For a non-trivial change, start Claude in `plan` mode. Read the task with
   `acp_status`; when `pending_input` contains a plan, review it and use
   `acp_answer(task_id, input_id, {"approve": true})` to approve it, or include
   specific `feedback` when rejecting it.
3. Use `acp_send(task_id, message)` for short, specific corrections. It injects
   the message into a running turn (`outcome: "injected"`), or starts the next
   turn on the same session when Claude is idle (`outcome: "started_turn"`).
   Cancel only when the session is going wrong: `acp_cancel` stops the current turn.
4. Every `pending_input` blocks Claude. Answer every question, plan, or other
   input promptly with `acp_answer`. Question answers are an object of form field
   values, or `{"action": "decline"}`. Plan answers are
   `{"approve": true|false, "feedback": "..."}`.
5. Wait with `acp_status(task_id, wait_seconds=...)`, never fast-poll. The wait
   may be at most 300 seconds. A task cannot exceed `PROGENA_ACP_MAX_TURNS`.
6. Claude commits its own work (repo-manager never commits): say so in the
   prompt, e.g. "commit your changes on this branch with a clear message". Claude
   has no GitHub credentials, so have `repo-manager` publish the branch and pull
   request after Claude finishes.

Use `acp_list(card_id=None, status=None, agent=None, limit=20)` to find existing
sessions. Each session is an `acp_task:<id>` document, so workflows can react to
its `status`, `stalled`, and `pending_input` document events.

## Template example

Create a reusable instruction at `/instructions/workflows/build-card.md`:

```markdown
---
title: Build a card with Claude
inputs: {repo_id: string, branch: string, card: kanban_card}
---
Ask the repo-manager agent to create a worktree for repository {repo_id} on
branch {branch}. Start Claude with acp_start in plan mode, using that worktree as
cwd, to implement card {card.id}: {card.title}.

Wait with acp_status. Review and answer every pending_input. Approve a sound
plan, or reject it with precise feedback. Steer Claude with acp_send if needed.
When complete, ask repo-manager to inspect and publish the branch and pull request.
```

A workflow `agent` node can use that template:

```json
{"kind":"agent","title":"Build card","instruction":"workflows/build-card.md"}
```

A routine `agent` node can give a local build-supervisor agent the same rendered
instruction:

```json
{"id":"build","kind":"agent","config":{"agent_name":"build-supervisor","instruction":"workflows/build-card.md"}}
```
