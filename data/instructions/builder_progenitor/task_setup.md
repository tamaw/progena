---
title: Task setup
description: Brief for preparing a build task's worktree before execution.
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

Setup requirements:
- This is the task setup phase only.
- Create a fresh worktree for this task from `origin/main`.
- Use `create_worktree` with the planned branch name and `base_ref="origin/main"`.
- In that worktree, fetch the latest default branch state and ensure the task branch is ready to build on top of `origin/main`.
- Use git tools for the setup steps instead of summarizing what should happen.
- Do not call `copilot_build`, `commit_changes`, `create_pull_request`, or `remove_worktree` in this phase.
- End with the chosen worktree path, the active branch, and a concise readiness summary.
