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
- Prepare a usable worktree for the task before any code editing or Copilot build work.
- Use `create_worktree` if this thread does not already have the planned worktree path.
- Always call `prepare_worktree` on the worktree you intend to use.
- If setup finds a blocker such as local changes or a non-fast-forward upstream, stop and report the blocker instead of continuing.
- Do not call `copilot_build`, `commit_changes`, or `create_pull_request` in this phase.
- End with the chosen worktree path, branch, and whether setup is ready for execution.
