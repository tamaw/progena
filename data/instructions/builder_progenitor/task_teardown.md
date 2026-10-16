---
title: Task teardown
description: Brief for wrapping up a build task after execution.
---
Task metadata:
- Task name: {task_name}
- Work item id: {work_item_id}
- Branch name: {branch_name}
- Planned at (UTC): {planned_at_utc}
- Description: {description}

Planner reasoning:
{reasoning}

User request:
{request_text}

Execution context (JSON):
{context_json}

Repository skills:
{skill_context}

Teardown requirements:
- This is the teardown phase only.
- Review the work completed earlier in this thread before deciding what to do.
- If `commit_changes` produced a commit, push the branch and call `create_pull_request`.
- If no commit was created, say that clearly and do not invent a PR.
- Always clean up the task worktree with `remove_worktree` before finishing.
- Do not run `copilot_build` or `validate_code` in this phase.
- End with the cleanup result and PR URL when one exists.
