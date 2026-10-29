---
name: driving-acp-agents
description: Delegate coding through asynchronous ACP sessions, waking only for completion, failure, questions or stalls. Use explicit result reads for evidence, not status-polling supervision.
allowed-tools: acp_start acp_send acp_status acp_get_result acp_answer acp_cancel acp_list task
---

# Driving ACP agents

ACP sessions run Claude over the Agent Client Protocol. Start and control them
with the `acp_*` tools; the `a2a_*` tools are for remote A2A agents only.

## Working safely

1. Ask the `repo-manager` agent to create a worktree for the requested repository
   and branch. Pass that returned worktree path as `cwd` to `acp_start`.
2. For a non-trivial change, start Claude in `plan` mode. End the supervising
   turn after starting it. A new pending-input event wakes the originating chat;
   retrieve the exact plan/question with `acp_get_result(source="input")` before answering.
   Use `acp_answer(task_id, input_id, {"approve": true})` for a sound plan, or
   include specific `feedback` when rejecting it.
3. Use `acp_send(task_id, message)` for short, specific corrections. It injects
   the message into a running turn (`outcome: "injected"`), or starts the next
   turn on the same session when Claude is idle (`outcome: "started_turn"`).
   Cancel only when the session is going wrong: `acp_cancel` stops the current turn.
4. Every `pending_input` blocks Claude. Answer every question, plan, or other
   input promptly with `acp_answer`. Question answers are an object of form field
   values, or `{"action": "decline"}`. Plan answers are
   `{"approve": true|false, "feedback": "..."}`.
5. Do not poll or long-poll `acp_status` to supervise work. Completion, failure,
   new input and a newly detected stall wake the originating conversation.
   Normal progress costs no supervising model turns. `acp_status` is for an
   explicit manual inspection only. A task cannot exceed `PROGENA_ACP_MAX_TURNS`.
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

End your turn and wait for actionable events; do not repeatedly check status.
When input is needed, read it with acp_get_result(source="input") and answer it.
Approve a sound plan, or reject it with precise feedback. Steer with acp_send
only when needed. On completion, read selected build evidence with acp_get_result,
then ask repo-manager to inspect and publish the branch and pull request.
```

For workflow/routine automation, do not wrap this entire wait in one agent step.
Create an `acp_task` from document nodes and react to its terminal status,
`pending_input`, or new `stalled` document events. Match the workflow stage so
unrelated tasks do not trigger it; leave chat notifications disabled for
workflow-owned tasks. See the managing-workflows skill.
